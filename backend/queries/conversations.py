"""Store chat conversations and append messages to them."""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB

from database.models import Conversation
from database.session import get_session


def latest_conversation(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> Conversation | None:
    """Return the most recently updated conversation, if any exists."""
    statement = (
        select(Conversation)
        .where(
            Conversation.store_id == store_id,
            Conversation.manager_id == manager_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .limit(1)
    )
    with get_session(engine) as session:
        return session.scalars(statement).first()


def start_conversation(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> Conversation:
    """Create an empty conversation, which becomes the latest one."""
    conversation = Conversation(store_id=store_id, manager_id=manager_id)
    with get_session(engine) as session:
        session.add(conversation)
        session.flush()
        session.refresh(conversation)
    return conversation


def append_message(
    conversation_id: UUID,
    message: dict[str, str],
    engine: Engine | None = None,
) -> None:
    """Append one message atomically, so concurrent writers never lose each other."""
    statement = (
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            messages=Conversation.messages.op("||", return_type=JSONB)(
                type_coerce([message], JSONB),
            ),
            updated_at=func.now(),
        )
        .returning(Conversation.id)
    )
    with get_session(engine) as session:
        if session.execute(statement).scalar_one_or_none() is None:
            raise LookupError(f"Conversation {conversation_id} does not exist")


def list_conversations(
    store_id: str,
    manager_id: str,
    limit: int = 20,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    """Recent non-empty conversations, newest first, without their full messages."""
    if limit < 1:
        raise ValueError("limit must be positive")
    message_count = func.jsonb_array_length(Conversation.messages)
    statement = (
        select(
            Conversation.id,
            Conversation.created_at,
            Conversation.updated_at,
            Conversation.messages[0]["what"].astext.label("first_question"),
            message_count.label("message_count"),
        )
        .where(
            Conversation.store_id == store_id,
            Conversation.manager_id == manager_id,
            message_count > 0,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return [dict(row) for row in session.execute(statement).mappings()]


def resume_conversation(
    conversation_id: UUID,
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> Conversation:
    """Make a past conversation the latest one, so new messages continue it."""
    statement = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.store_id == store_id,
            Conversation.manager_id == manager_id,
        )
        .values(updated_at=func.now())
        .returning(Conversation)
    )
    with get_session(engine) as session:
        conversation = session.scalars(statement).one_or_none()
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} does not exist")
    return conversation
