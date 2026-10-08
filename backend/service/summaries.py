"""Roll a conversation's older messages into its summary so prompts stay small.

Messages are never edited; only `summary` and `summary_covers_to` (the index of
the last message folded in) change. Two triggers fold messages:

- After an answer: when the raw messages (those after `summary_covers_to`)
  exceed the count or size limit, everything except the recent window is folded.
- Idle: a scheduled job folds every message of chats whose last message is
  older than the idle time, so a complete summary exists once a chat goes quiet.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import requests
import structlog
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from constants import (
    DEMO_MANAGER_ID,
    DEMO_STORE_ID,
    SUMMARY_IDLE_MINUTES,
    SUMMARY_JOB_BATCH,
    SUMMARY_JOB_INTERVAL_SECONDS,
    SUMMARY_MAX_RAW_MESSAGES,
    SUMMARY_MAX_RAW_TOKENS,
    SUMMARY_RECENT_MESSAGES,
)
from database.models import Conversation
from queries.conversations import (
    idle_unsummarized,
    latest_conversation,
    save_summary,
)
from service.factory import create_llm_service
from service.scenarios import TIMEZONE
from service.scheduler import IntervalJob

logger = structlog.stdlib.get_logger(__name__)

PROMPT_PATH = (
    Path(__file__).resolve().parent / "rag_data" / "prompts" / "conversation_summary.md"
)
Summarizer = Callable[[str, str], str]


def estimate_tokens(messages: Sequence[dict[str, str]]) -> int:
    """Rough token count: characters divided by four."""
    return sum(len(message["what"]) for message in messages) // 4


def raw_start(conversation: Conversation) -> int:
    """Index of the first message not covered by the summary."""
    covered = conversation.summary_covers_to
    return 0 if covered is None else covered + 1


def history_start(conversation: Conversation) -> int:
    """Index of the first message sent raw to the model.

    Uncovered messages are always raw; the recent window stays raw even when an
    idle summary already covers it, so follow-ups keep their detail.
    """
    recent = max(len(conversation.messages) - SUMMARY_RECENT_MESSAGES, 0)
    return min(raw_start(conversation), recent)


def needs_folding(conversation: Conversation) -> bool:
    """True when the raw messages exceed the count or size limit."""
    raw = conversation.messages[raw_start(conversation) :]
    return (
        len(raw) > SUMMARY_MAX_RAW_MESSAGES
        or estimate_tokens(raw) > SUMMARY_MAX_RAW_TOKENS
    )


def _transcript(messages: Sequence[dict[str, str]], first_index: int) -> str:
    return "\n\n".join(
        f"[{first_index + offset}] "
        f"{'Manager' if message['who'] == 'manager' else 'Assistant'} "
        f"({message['when']}): {message['what']}"
        for offset, message in enumerate(messages)
    )


class SummaryService:
    def __init__(
        self,
        summarize: Summarizer,
        engine: Engine | None = None,
    ) -> None:
        self.summarize = summarize
        self.engine = engine

    def fold(self, conversation: Conversation, keep_recent: int) -> bool:
        """Fold raw messages, except the latest `keep_recent`, into the summary."""
        start = raw_start(conversation)
        end = len(conversation.messages) - 1 - keep_recent
        if end < start:
            return False
        previous = conversation.summary or "(none yet)"
        user_message = (
            f"Previous summary:\n{previous}\n\n"
            f"Messages to add:\n\n"
            f"{_transcript(conversation.messages[start : end + 1], start)}"
        )
        summary = self.summarize(
            PROMPT_PATH.read_text(encoding="utf-8"),
            user_message,
        ).strip()
        if not summary:
            logger.warning(
                "Empty conversation summary",
                conversation_id=str(conversation.id),
            )
            return False
        saved = save_summary(
            conversation.id,
            summary,
            end,
            conversation.summary_covers_to,
            self.engine,
        )
        logger.info(
            "Conversation summarized" if saved else "Summary skipped; already moved",
            conversation_id=str(conversation.id),
            covers_to=end,
        )
        return saved

    def after_answer(self, store_id: str, manager_id: str) -> bool:
        """Fold older messages of the open chat if it exceeds a limit."""
        conversation = latest_conversation(store_id, manager_id, self.engine)
        if conversation is None or not needs_folding(conversation):
            return False
        return self.fold(conversation, keep_recent=SUMMARY_RECENT_MESSAGES)

    def summarize_now(self, store_id: str, manager_id: str) -> bool:
        """Fold every message of the open chat, recent ones included, on request."""
        conversation = latest_conversation(store_id, manager_id, self.engine)
        if conversation is None:
            return False
        return self.fold(conversation, keep_recent=0)

    def summarize_idle(self, now: datetime | None = None) -> int:
        """Summarize chats idle for the idle time in full; returns how many."""
        now = now or datetime.now(TIMEZONE)
        idle_before = now - timedelta(minutes=SUMMARY_IDLE_MINUTES)
        done = 0
        for conversation in idle_unsummarized(
            idle_before,
            SUMMARY_JOB_BATCH,
            self.engine,
        ):
            try:
                done += self.fold(conversation, keep_recent=0)
            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
                SQLAlchemyError,
            ):
                logger.warning(
                    "Could not summarize idle conversation",
                    conversation_id=str(conversation.id),
                    exc_info=True,
                )
        return done


def _summarize(system_prompt: str, user_message: str) -> str:
    return create_llm_service().generate(
        system_prompt=system_prompt,
        user_message=user_message,
    )


def summary_service() -> SummaryService:
    return SummaryService(summarize=_summarize)


def summarize_latest_conversation() -> bool:
    """UI entry point: fold the open chat after an answer if it is over a limit."""
    return summary_service().after_answer(DEMO_STORE_ID, DEMO_MANAGER_ID)


def summarize_open_conversation() -> bool:
    """UI entry point: bring the open chat's summary up to its latest message."""
    return summary_service().summarize_now(DEMO_STORE_ID, DEMO_MANAGER_ID)


def idle_summary_job() -> IntervalJob:
    """Every few minutes, summarize chats that have gone idle."""
    return IntervalJob(
        "idle-conversation-summaries",
        SUMMARY_JOB_INTERVAL_SECONDS,
        lambda: summary_service().summarize_idle(),
    )
