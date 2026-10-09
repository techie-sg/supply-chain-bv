"""Keep chat history in PostgreSQL so it survives refreshes and restarts."""

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import requests
import structlog
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID, SUMMARY_RECENT_MESSAGES, TIMEZONE
from database.models import Conversation
from domain.chat import (
    AnswerResult,
    AnswerTrace,
    ChatMessage,
    StoredMessage,
    ToolCallTrace,
)
from domain.tools import Tool
from queries.conversations import (
    append_message,
    latest_conversation,
    list_conversations,
    read_answer_context,
    read_details,
    read_summary,
    read_title_exchange,
    resume_conversation,
    selected_conversation_id,
    set_title,
    start_conversation,
)
from resources import PROMPTS
from service.alerts import alerts_block
from service.briefing import briefing, is_greeting
from service.factory import create_llm_service
from service.handover import (
    ShiftView,
    chat_handover,
    chat_handover_block,
    ensure_shift,
)
from service.managers import store_managers
from service.personalization import (
    PersonalizationChanges,
    explicit,
    manager_personalization,
)
from service.preferences import (
    PreferenceError,
    PreferenceService,
    describe,
    manager_preferences,
)
from service.rag import answer_question
from service.setting_changes import SettingChange, SettingChanges, tidy_reply
from service.tools import dispatch_tools, traced_tools

logger = structlog.stdlib.get_logger(__name__)

# An answer is the reply text, or the reply with its trace (tools used, settings).
Answer = Callable[..., AnswerResult]
Titler = Callable[[str, str], str]

TITLE_PROMPT_PATH = PROMPTS / "conversation_title.md"
TITLE_MAX_LENGTH = 60


def new_message(
    who: Literal["manager", "assistant"],
    what: str,
    trace: AnswerTrace | None = None,
) -> StoredMessage:
    message: StoredMessage = {
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


def to_chat_messages(messages: Sequence[StoredMessage]) -> list[ChatMessage]:
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
        conversation_id: str | None = None,
    ) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.answer = answer
        self.engine = engine
        self.titler = titler
        self.conversation_id = UUID(conversation_id) if conversation_id else None

    def selected(self) -> Conversation | None:
        """Resolve the selected chat independently of message recency."""
        if self.conversation_id is not None:
            return resume_conversation(
                self.conversation_id,
                self.store_id,
                self.manager_id,
                self.engine,
            )
        return latest_conversation(self.store_id, self.manager_id, self.engine)

    def history(self) -> list[StoredMessage]:
        """Stored messages of the latest conversation, without creating one."""
        conversation = self.selected()
        return list(conversation.messages) if conversation else []

    def summary_view(self) -> dict[str, Any] | None:
        """The open chat's summary state; None for no chat or an empty one."""
        view = read_summary(
            self.store_id,
            self.manager_id,
            self.conversation_id,
            self.engine,
        )
        if view is None or not view["total"]:
            return None
        covers_to = view["summary_covers_to"]
        if not view["summary"] or covers_to is None:
            return {
                "summary": None,
                "covered": 0,
                "total": view["total"],
                "summarized_at": None,
            }
        return {
            "summary": view["summary"],
            "covered": covers_to + 1,
            "total": view["total"],
            "summarized_at": view["summarized_at"],
        }

    def start_new(self) -> Conversation:
        conversation = start_conversation(self.store_id, self.manager_id, self.engine)
        self.conversation_id = conversation.id
        logger.info("Conversation started", conversation_id=str(conversation.id))
        return conversation

    def current_id(self) -> UUID | None:
        """The conversation new questions go to, if one exists."""
        return selected_conversation_id(
            self.store_id,
            self.manager_id,
            self.conversation_id,
            self.engine,
        )

    def past(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent non-empty conversations, newest first."""
        return list_conversations(self.store_id, self.manager_id, limit, self.engine)

    def resume(self, conversation_id: UUID) -> list[StoredMessage]:
        """Select a past conversation without changing its recency."""
        conversation = resume_conversation(
            conversation_id,
            self.store_id,
            self.manager_id,
            self.engine,
        )
        self.conversation_id = conversation.id
        logger.info("Conversation resumed", conversation_id=str(conversation.id))
        return list(conversation.messages)

    def ask(self, question: str) -> AnswerResult:
        """Store the question first, so a failed answer never loses it."""
        context = read_answer_context(
            self.store_id,
            self.manager_id,
            SUMMARY_RECENT_MESSAGES,
            self.conversation_id,
            self.engine,
        )
        if context is None:
            conversation = self.start_new()
            context = {"id": conversation.id, "messages": [], "summary": None}
        # The summary stands in for older messages; recent ones stay word for word.
        history = to_chat_messages(context["messages"])
        self.conversation_id = context["id"]
        extra: dict[str, Any] = {}
        if context["summary"]:
            extra["summary"] = context["summary"]
        if context.get("handover_note_id"):
            extra["handover_note_id"] = context["handover_note_id"]
        append_message(context["id"], new_message("manager", question), self.engine)
        result = self.answer(question, history=history, **extra)
        reply, trace = result.text, result.trace
        append_message(
            context["id"],
            new_message("assistant", reply, trace),
            self.engine,
        )
        return result

    def note(self, text: str) -> StoredMessage | None:
        """Add an assistant note to the open chat, such as a confirmed setting.

        Stored like any reply, so later answers know what happened.
        """
        conversation_id = self.current_id()
        if conversation_id is None:
            return None
        message = new_message("assistant", text)
        append_message(conversation_id, message, self.engine)
        return message

    def title_latest(self) -> str | None:
        """Give the latest conversation a title once it has its first answer.

        Runs after the answer is shown. Any failure leaves the title unset, and
        the sidebar keeps showing the first question instead.
        """
        exchange = read_title_exchange(
            self.store_id,
            self.manager_id,
            self.conversation_id,
            self.engine,
        )
        if self.titler is None or exchange is None or exchange["title"]:
            return None
        question, answer = exchange["question"], exchange["answer"]
        if question is None or answer is None:
            return None
        try:
            title = clean_title(self.titler(question, answer))
        except (requests.RequestException, RuntimeError, ValueError):
            logger.warning("Could not title conversation", exc_info=True)
            return None
        if title is None or not set_title(exchange["id"], title, self.engine):
            return None
        logger.info("Conversation titled", conversation_id=str(exchange["id"]))
        return title


def _trace(
    settings: PreferenceService,
    tools: Sequence[ToolCallTrace] = (),
) -> AnswerTrace:
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
    handover_note_id: UUID | None = None,
    preferences: PreferenceService | None = None,
    tools: Sequence[Tool] = (),
) -> AnswerResult:
    """Answer through RAG and the configured provider, recording local tool calls.

    A chat opened by a hand over answers with its own note in view.
    """
    preferences = preferences or manager_preferences()
    calls: list[ToolCallTrace] = []
    reply = answer_question(
        question,
        history=history,
        preferences=preferences,
        summary=summary,
        handover=chat_handover_block(handover_note_id, preferences.store_id),
        personalization=manager_personalization(preferences.manager_id).prompt_block(),
        tools=traced_tools([*tools, *dispatch_tools(preferences.store_id)], calls),
        alerts=_alerts(preferences.manager_id),
    )
    return AnswerResult(reply, _trace(preferences, calls))


def _alerts(manager_id: str) -> str | None:
    """The manager's firing alerts; an answer never waits on or fails for them."""
    try:
        return alerts_block(manager_id)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.warning("Could not read alerts for the answer", exc_info=True)
        return None


def _title(question: str, answer: str) -> str:
    """Ask the configured model for a short title of the first exchange."""
    return create_llm_service().generate(
        system_prompt=TITLE_PROMPT_PATH.read_text(encoding="utf-8"),
        user_message=f"Manager: {question}\n\nAssistant: {answer[:1500]}",
    )


def _service(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> ConversationService:
    return ConversationService(
        DEMO_STORE_ID,
        manager_id,
        answer=lambda question, **kwargs: _answer(
            question,
            preferences=manager_preferences(manager_id),
            **kwargs,
        ),
        titler=_title,
        conversation_id=conversation_id,
    )


def ask_question(
    question: str,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[str, list[SettingChange], AnswerTrace | None]:
    """UI entry point: answer using the stored history of the selected chat.

    Also returns the setting changes the assistant proposed in this answer
    (none is saved until the manager confirms it) and the trace to show under it.
    """
    try:
        ensure_shift(manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError):
        # A missing shift never blocks an answer.
        logger.warning("Could not start a shift", exc_info=True)
    preferences = manager_preferences(manager_id)
    changes = SettingChanges(preferences)
    personalization = PersonalizationChanges(
        manager_personalization(manager_id),
        question,
        lambda: service.conversation_id,
    )

    def reply(question: str, **kwargs: Any) -> AnswerResult:
        if is_greeting(question):
            greeting = _briefing(preferences, kwargs.get("handover_note_id"))
            if greeting is not None:
                text, calls = greeting
                return AnswerResult(text, _trace(preferences, calls))
        return _tidied(
            _answer(
                question,
                preferences=preferences,
                tools=[
                    changes.tool(),
                    *([personalization.tool()] if explicit(question) else []),
                ],
                **kwargs,
            ),
            changes,
        )

    service = ConversationService(
        DEMO_STORE_ID,
        manager_id,
        answer=reply,
        titler=_title,
        conversation_id=conversation_id,
    )
    result = service.ask(question)
    return result.text, changes.proposals, result.trace


def _briefing(
    preferences: PreferenceService,
    handover_note_id: UUID | None,
) -> tuple[str, list[ToolCallTrace]] | None:
    """The greeting briefing; None hands the greeting to the assistant instead."""
    try:
        name = next(
            (
                manager.name
                for manager in store_managers(preferences.store_id)
                if manager.manager_id == preferences.manager_id
            ),
            "there",
        )
        return briefing(preferences, name, handover_note_id)
    except (SQLAlchemyError, RuntimeError, PreferenceError):
        logger.warning("Could not build the greeting briefing", exc_info=True)
        return None


def _tidied(
    result: AnswerResult,
    changes: SettingChanges,
) -> AnswerResult:
    return AnswerResult(tidy_reply(result.text, changes.proposals), result.trace)


def add_note(
    text: str,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> StoredMessage | None:
    """UI entry point: record an assistant note in the open chat."""
    return _service(manager_id, conversation_id).note(text)


def latest_conversation_state(
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[list[StoredMessage], str | None]:
    """Restore messages and the selected id from the same database read."""
    conversation = _service(manager_id).selected()
    if conversation is None:
        return [], None
    return list(conversation.messages), str(conversation.id)


def start_new_conversation(manager_id: str = DEMO_MANAGER_ID) -> str:
    """UI entry point for Clear chat and scenario loads."""
    return str(_service(manager_id).start_new().id)


def past_conversations(manager_id: str = DEMO_MANAGER_ID) -> list[dict[str, Any]]:
    """UI entry point: conversations the manager can browse and reopen."""
    return _service(manager_id).past()


def resume_past_conversation(
    conversation_id: str,
    manager_id: str = DEMO_MANAGER_ID,
) -> list[StoredMessage]:
    """UI entry point: reopen a past conversation and continue it."""
    return _service(manager_id).resume(UUID(conversation_id))


def current_conversation_id(manager_id: str = DEMO_MANAGER_ID) -> str | None:
    """UI entry point: which chat the sidebar marks as open."""
    conversation_id = _service(manager_id).current_id()
    return str(conversation_id) if conversation_id else None


def title_latest_conversation(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> str | None:
    """UI entry point: title the open chat after its first answer, if untitled."""
    return _service(manager_id, conversation_id).title_latest()


def conversation_summary(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> dict[str, Any] | None:
    """UI entry point: the open chat's summary card, or None if not summarized."""
    return _service(manager_id, conversation_id).summary_view()


def conversation_handover(
    conversation_id: str,
    manager_id: str = DEMO_MANAGER_ID,
) -> ShiftView | None:
    """UI entry point: the handover the manager's chat was opened with, if any."""
    conversation = _service(manager_id, conversation_id).selected()
    if conversation is None:
        return None
    return chat_handover(conversation.handover_note_id)


def conversation_details(
    conversation_id: str,
    manager_id: str = DEMO_MANAGER_ID,
) -> dict[str, str | None]:
    """Read browser metadata scoped to the manager who owns the selected chat."""
    row = read_details(
        DEMO_STORE_ID,
        manager_id,
        UUID(conversation_id),
    )
    return {"id": str(row["id"]), "title": row["title"]}
