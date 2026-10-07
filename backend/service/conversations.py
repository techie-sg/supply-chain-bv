"""Keep chat history in PostgreSQL so it survives refreshes and restarts."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

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
    start_conversation,
)
from service.preferences import demo_preferences
from service.rag import answer_question
from service.scenarios import TIMEZONE

logger = structlog.stdlib.get_logger(__name__)

Answer = Callable[..., str]


def new_message(who: Literal["manager", "assistant"], what: str) -> dict[str, str]:
    return {"who": who, "what": what, "when": datetime.now(TIMEZONE).isoformat()}


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
    ) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.answer = answer
        self.engine = engine

    def history(self) -> list[ChatMessage]:
        """Messages of the latest conversation, without creating one."""
        conversation = latest_conversation(
            self.store_id,
            self.manager_id,
            self.engine,
        )
        return to_chat_messages(conversation.messages) if conversation else []

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

    def resume(self, conversation_id: UUID) -> list[ChatMessage]:
        """Continue a past conversation: it becomes the latest one."""
        conversation = resume_conversation(
            conversation_id,
            self.store_id,
            self.manager_id,
            self.engine,
        )
        logger.info("Conversation resumed", conversation_id=str(conversation.id))
        return to_chat_messages(conversation.messages)

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
        history = to_chat_messages(conversation.messages)
        append_message(conversation.id, new_message("manager", question), self.engine)
        reply = self.answer(question, history=history)
        append_message(conversation.id, new_message("assistant", reply), self.engine)
        return reply


def _answer(question: str, *, history: Sequence[ChatMessage]) -> str:
    """Answer with the manager's settings in view, so they are applied."""
    return answer_question(question, history=history, preferences=demo_preferences())


def _service() -> ConversationService:
    return ConversationService(DEMO_STORE_ID, DEMO_MANAGER_ID, answer=_answer)


def ask_question(question: str) -> str:
    """UI entry point: answer using the stored history of the latest chat."""
    return _service().ask(question)


def conversation_history() -> list[ChatMessage]:
    """UI entry point: messages to show when the page loads."""
    return _service().history()


def start_new_conversation() -> None:
    """UI entry point for Clear chat and scenario loads."""
    _service().start_new()


def past_conversations() -> list[dict[str, Any]]:
    """UI entry point: conversations the manager can browse and reopen."""
    return _service().past()


def resume_past_conversation(conversation_id: str) -> list[ChatMessage]:
    """UI entry point: reopen a past conversation and continue it."""
    return _service().resume(UUID(conversation_id))


def current_conversation_id() -> str | None:
    """UI entry point: which chat the sidebar marks as open."""
    conversation_id = _service().current_id()
    return str(conversation_id) if conversation_id else None
