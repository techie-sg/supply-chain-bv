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
from service.agent import run_agent
from service.factory import create_llm_service
from service.preferences import PreferenceService, demo_preferences, describe
from service.rag import answer_question, prepare_message
from service.scenarios import TIMEZONE, current_scenario
from service.summaries import history_start

logger = structlog.stdlib.get_logger(__name__)

# An answer is the reply text, or the reply with its trace (tools used, settings).
Answer = Callable[..., str | tuple[str, dict[str, Any]]]
Titler = Callable[[str, str], str]
TITLE_PROMPT_PATH = (
    Path(__file__).resolve().parent / "rag_data" / "prompts" / "conversation_title.md"
)
TITLE_MAX_LENGTH = 60


def new_message(
    who: Literal["manager", "assistant"],
    what: str,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "who": who,
        "what": what,
        "when": datetime.now(TIMEZONE).isoformat(),
    }
    if trace:
        message["trace"] = trace  # display only; never sent to the model
    return message


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
        return self.ask_traced(question)[0]

    def ask_traced(self, question: str) -> tuple[str, dict[str, Any] | None]:
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
        result = self.answer(question, history=history, **extra)
        reply, trace = result if isinstance(result, tuple) else (result, None)
        append_message(
            conversation.id,
            new_message("assistant", reply, trace),
            self.engine,
        )
        return reply, trace

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


def _trace(
    settings: PreferenceService,
    tools: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """What the answer was built from: tool calls and the manager's own settings."""
    return {
        "tools": list(tools),
        "preferences": [
            describe(item) for item in settings.effective() if item.customized
        ],
    }


def _answer(
    question: str,
    *,
    history: Sequence[ChatMessage],
    summary: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Answer with the manager's settings and the chat's summary in view.

    With a scenario loaded, the model also gets the live tools; without one the
    answer comes from the playbook alone. Returns the reply and its trace.
    """
    settings = demo_preferences()
    scenario = current_scenario()
    if scenario is None:
        reply = answer_question(
            question,
            history=history,
            preferences=settings,
            summary=summary,
        )
        return reply, _trace(settings)
    user_message = prepare_message(
        question,
        history=history,
        preferences=settings,
        summary=summary,
    )
    if user_message is None:
        return (
            "I could not find relevant guidance in the DispatchDesk knowledge base.",
            _trace(settings),
        )
    result = run_agent(
        question,
        store_id=scenario["store_id"],
        history=history,
        user_message=user_message,
    )
    return result["answer"], _trace(settings, result["trace"])


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


def ask_question(question: str) -> str:
    """UI entry point: answer using the stored history of the latest chat."""
    return _service().ask(question)


def ask_question_traced(question: str) -> tuple[str, dict[str, Any] | None]:
    """UI entry point: the answer with the trace to show under it."""
    return _service().ask_traced(question)


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
