"""Keep chat history in PostgreSQL so it survives refreshes and restarts."""

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import requests
import structlog
from sqlalchemy import Engine

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID
from database.models import Conversation
from domain.chat import ChatMessage
from queries.conversations import (
    append_message,
    latest_conversation,
    list_conversations,
    resume_conversation,
    set_title,
    start_conversation,
)
from service.dreaming import handover_block
from service.factory import create_llm_service
from service.llm_service import Tool
from service.preferences import PreferenceService, demo_preferences
from service.rag import answer_question
from service.scenarios import TIMEZONE
from service.setting_changes import SettingChange, SettingChanges, tidy_reply
from service.summaries import history_start

logger = structlog.stdlib.get_logger(__name__)

Answer = Callable[..., str]
Titler = Callable[[str, str], str]
TITLE_PROMPT_PATH = (
    Path(__file__).resolve().parent / "rag_data" / "prompts" / "conversation_title.md"
)
TITLE_MAX_LENGTH = 60


def new_message(who: Literal["manager", "assistant"], what: str) -> dict[str, str]:
    return {"who": who, "what": what, "when": datetime.now(TIMEZONE).isoformat()}


def clean_title(text: str) -> str | None:
    """First line of the model's reply, without quotes or markup, at most 60 chars."""
    line = next((item for item in text.splitlines() if item.strip()), "")
    quotes = "\"'*`“”‘’ #"
    line = re.sub(r"^title:\s*", "", line.strip().strip(quotes), flags=re.IGNORECASE)
    line = " ".join(line.strip(quotes).split()).rstrip(".!?:;,")
    if len(line) > TITLE_MAX_LENGTH:
        line = line[:TITLE_MAX_LENGTH].rsplit(" ", 1)[0]
    return line or None


def to_chat_messages(messages: Sequence[dict[str, str]]) -> list[ChatMessage]:
    """Map stored messages to provider-independent chat roles, in order."""
    return [
        {
            "role": "user" if message["who"] == "manager" else "assistant",
            "content": message["what"],
        }
        for message in messages
    ]


class ConversationService:
    def __init__(
        self,
        store_id: str,
        manager_id: str,
        answer: Answer,
        engine: Engine | None = None,
        titler: Titler | None = None,
    ) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.answer = answer
        self.engine = engine
        self.titler = titler

    def history(self) -> list[dict[str, str]]:
        """Stored messages of the latest conversation, without creating one."""
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        return list(conversation.messages) if conversation else []

    def summary_view(self) -> dict[str, Any] | None:
        """The open chat's summary state; None for no chat or an empty one."""
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        if conversation is None or not conversation.messages:
            return None
        covers_to = conversation.summary_covers_to
        if not conversation.summary or covers_to is None:
            return {
                "summary": None,
                "covered": 0,
                "total": len(conversation.messages),
                "summarized_at": None,
            }
        return {
            "summary": conversation.summary,
            "covered": covers_to + 1,
            "total": len(conversation.messages),
            "summarized_at": conversation.summarized_at,
        }

    def start_new(self) -> Conversation:
        conversation = start_conversation(self.store_id, self.manager_id, self.engine)
        logger.info("Conversation started", conversation_id=str(conversation.id))
        return conversation

    def current_id(self) -> UUID | None:
        """The conversation new questions go to, if one exists."""
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        return conversation.id if conversation else None

    def past(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent non-empty conversations, newest first."""
        return list_conversations(self.store_id, self.manager_id, limit, self.engine)

    def resume(self, conversation_id: UUID) -> list[dict[str, str]]:
        """Continue a past conversation: it becomes the latest one."""
        conversation = resume_conversation(
            conversation_id,
            self.store_id,
            self.manager_id,
            self.engine,
        )
        logger.info("Conversation resumed", conversation_id=str(conversation.id))
        return list(conversation.messages)

    def ask(self, question: str) -> str:
        """Store the question first, so a failed answer never loses it."""
        conversation = (
            latest_conversation(
                self.store_id,
                self.manager_id,
                self.engine,
            )
            or self.start_new()
        )
        # The summary stands in for older messages; recent ones stay word for word.
        history = to_chat_messages(conversation.messages[history_start(conversation) :])
        extra = {"summary": conversation.summary} if conversation.summary else {}
        append_message(conversation.id, new_message("manager", question), self.engine)
        reply = self.answer(question, history=history, **extra)
        append_message(conversation.id, new_message("assistant", reply), self.engine)
        return reply

    def note(self, text: str) -> dict[str, str] | None:
        """Add an assistant note to the open chat, such as a confirmed setting.

        Stored like any reply, so later answers know what happened.
        """
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        if conversation is None:
            return None
        message = new_message("assistant", text)
        append_message(conversation.id, message, self.engine)
        return message

    def title_latest(self) -> str | None:
        """Give the latest conversation a title once it has its first answer.

        Runs after the answer is shown. Any failure leaves the title unset, and
        the sidebar keeps showing the first question instead.
        """
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        if self.titler is None or conversation is None or conversation.title:
            return None
        question = next(
            (
                item["what"]
                for item in conversation.messages
                if item["who"] == "manager"
            ),
            None,
        )
        answer = next(
            (
                item["what"]
                for item in conversation.messages
                if item["who"] == "assistant"
            ),
            None,
        )
        if question is None or answer is None:
            return None
        try:
            title = clean_title(self.titler(question, answer))
        except (requests.RequestException, RuntimeError, ValueError):
            logger.warning("Could not title conversation", exc_info=True)
            return None
        if title is None or not set_title(conversation.id, title, self.engine):
            return None
        logger.info("Conversation titled", conversation_id=str(conversation.id))
        return title


def _answer(
    question: str,
    *,
    history: Sequence[ChatMessage],
    summary: str | None = None,
    preferences: PreferenceService | None = None,
    tools: Sequence[Tool] = (),
) -> str:
    """Answer with the settings, the chat's summary and the last handover in view."""
    handover = handover_block(DEMO_STORE_ID)
    return answer_question(
        question,
        history=history,
        preferences=preferences or demo_preferences(),
        summary=summary,
        handover=handover,
        tools=tools,
    )


def _title(question: str, answer: str) -> str:
    """Ask the configured model for a short title of the first exchange."""
    return create_llm_service().generate(
        system_prompt=TITLE_PROMPT_PATH.read_text(encoding="utf-8"),
        user_message=f"Manager: {question}\n\nAssistant: {answer[:1500]}",
    )


def _service() -> ConversationService:
    return ConversationService(
        DEMO_STORE_ID,
        DEMO_MANAGER_ID,
        answer=_answer,
        titler=_title,
    )


def ask_question(question: str) -> tuple[str, list[SettingChange]]:
    """UI entry point: answer using the stored history of the latest chat.

    Also returns the setting changes the assistant proposed in this answer;
    none is saved until the manager confirms it.
    """
    preferences = demo_preferences()
    changes = SettingChanges(preferences)
    service = ConversationService(
        DEMO_STORE_ID,
        DEMO_MANAGER_ID,
        answer=lambda question, **kwargs: tidy_reply(
            _answer(
                question,
                preferences=preferences,
                tools=[changes.tool()],
                **kwargs,
            ),
            changes.proposals,
        ),
        titler=_title,
    )
    return service.ask(question), changes.proposals


def add_note(text: str) -> dict[str, str] | None:
    """UI entry point: record an assistant note in the open chat."""
    return _service().note(text)


def conversation_history() -> list[dict[str, str]]:
    """UI entry point: stored messages to show when the page loads."""
    return _service().history()


def start_new_conversation() -> None:
    """UI entry point for Clear chat and scenario loads."""
    _service().start_new()


def past_conversations() -> list[dict[str, Any]]:
    """UI entry point: conversations the manager can browse and reopen."""
    return _service().past()


def resume_past_conversation(conversation_id: str) -> list[dict[str, str]]:
    """UI entry point: reopen a past conversation and continue it."""
    return _service().resume(UUID(conversation_id))


def current_conversation_id() -> str | None:
    """UI entry point: which chat the sidebar marks as open."""
    conversation_id = _service().current_id()
    return str(conversation_id) if conversation_id else None


def title_latest_conversation() -> str | None:
    """UI entry point: title the open chat after its first answer, if untitled."""
    return _service().title_latest()


def conversation_summary() -> dict[str, Any] | None:
    """UI entry point: the open chat's summary card, or None if not summarized."""
    return _service().summary_view()
