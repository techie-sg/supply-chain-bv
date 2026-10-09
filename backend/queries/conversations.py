"""Store chat conversations and append messages to them."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Engine,
    Select,
    case,
    cast,
    func,
    literal_column,
    select,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.sql import ColumnElement

from database.models import Conversation
from database.session import get_session
from domain.chat import StoredMessage


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


def _read_selected(
    statement: Select,
    store_id: str,
    manager_id: str,
    conversation_id: UUID | None,
    engine: Engine | None,
) -> dict[str, Any] | None:
    statement = statement.where(
        Conversation.store_id == store_id,
        Conversation.manager_id == manager_id,
    )
    if conversation_id is not None:
        statement = statement.where(Conversation.id == conversation_id)
    else:
        statement = statement.order_by(
            Conversation.updated_at.desc(),
            Conversation.created_at.desc(),
        ).limit(1)
    with get_session(engine) as session:
        row = session.execute(statement).mappings().one_or_none()
    if row is None and conversation_id is not None:
        raise LookupError(f"Conversation {conversation_id} does not exist")
    return dict(row) if row is not None else None


def selected_conversation_id(
    store_id: str,
    manager_id: str,
    conversation_id: UUID | None = None,
    engine: Engine | None = None,
) -> UUID | None:
    """Resolve the current id without transferring or decoding the transcript."""
    row = _read_selected(
        select(Conversation.id),
        store_id,
        manager_id,
        conversation_id,
        engine,
    )
    return row["id"] if row else None


def read_summary(
    store_id: str,
    manager_id: str,
    conversation_id: UUID | None = None,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """Read summary text and coverage; the full messages stay in PostgreSQL."""
    return _read_selected(
        select(
            Conversation.summary,
            Conversation.summary_covers_to,
            Conversation.summarized_at,
            func.jsonb_array_length(Conversation.messages).label("total"),
        ),
        store_id,
        manager_id,
        conversation_id,
        engine,
    )


def read_answer_context(
    store_id: str,
    manager_id: str,
    recent_messages: int,
    conversation_id: UUID | None = None,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """Fetch uncovered messages plus the recent window needed for a follow-up.

    Older messages stay in the database and are represented by the summary.
    Uncovered messages are never truncated.
    """
    if recent_messages < 0:
        raise ValueError("recent_messages must not be negative")
    count = func.jsonb_array_length(Conversation.messages)
    start = func.least(
        func.coalesce(Conversation.summary_covers_to + 1, 0),
        func.greatest(count - recent_messages, 0),
    )
    # Match Python's slice semantics even for a legacy negative coverage value.
    slice_start = case((start < 0, func.greatest(count + start, 0)), else_=start)
    path = cast(func.format("$[%s to last]", slice_start), JSONPATH)
    return _read_selected(
        select(
            Conversation.id,
            Conversation.summary,
            func.jsonb_path_query_array(Conversation.messages, path, type_=JSONB).label(
                "messages",
            ),
        ),
        store_id,
        manager_id,
        conversation_id,
        engine,
    )


def read_details(
    store_id: str,
    manager_id: str,
    conversation_id: UUID,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Browser title only; skip JSON access altogether when a title is saved."""
    row = _read_selected(
        select(
            Conversation.id,
            func.coalesce(
                func.nullif(Conversation.title, ""),
                _first_message("manager"),
            ).label(
                "title",
            ),
        ),
        store_id,
        manager_id,
        conversation_id,
        engine,
    )
    assert row is not None  # A missing selected id raises in _read_selected.
    return row


def _first_message(who: Literal["manager", "assistant"]) -> ColumnElement[Any]:
    return func.jsonb_path_query_first(
        Conversation.messages,
        cast(f'$[*] ? (@.who == "{who}").what', JSONPATH),
    ).op("#>>")(literal_column("'{}'::text[]"))


def read_title_exchange(
    store_id: str,
    manager_id: str,
    conversation_id: UUID | None = None,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """First exchange for an untitled chat; titled chats never read messages."""
    untitled = func.nullif(Conversation.title, "").is_(None)
    return _read_selected(
        select(
            Conversation.id,
            Conversation.title,
            case((untitled, _first_message("manager"))).label("question"),
            case((untitled, _first_message("assistant"))).label("answer"),
        ),
        store_id,
        manager_id,
        conversation_id,
        engine,
    )


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
    return conversation


def append_message(
    conversation_id: UUID,
    message: StoredMessage,
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
            Conversation.title,
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
    """Read a past conversation without changing its message timestamp."""
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.store_id == store_id,
        Conversation.manager_id == manager_id,
    )
    with get_session(engine) as session:
        conversation = session.scalars(statement).one_or_none()
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} does not exist")
    return conversation


def set_title(
    conversation_id: UUID,
    title: str,
    engine: Engine | None = None,
) -> bool:
    """Store a title if the conversation has none; leaves its recency unchanged."""
    statement = (
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title.is_(None))
        .values(title=title)
        .returning(Conversation.id)
    )
    with get_session(engine) as session:
        return session.execute(statement).scalar_one_or_none() is not None


def save_summary(
    conversation_id: UUID,
    summary: str,
    covers_to: int,
    expected_covers_to: int | None,
    engine: Engine | None = None,
) -> bool:
    """Store a rolled-forward summary only if no other run moved it meanwhile.

    Messages are never touched; recency (`updated_at`) is left unchanged.
    """
    statement = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.summary_covers_to.is_not_distinct_from(expected_covers_to),
        )
        .values(
            summary=summary,
            summary_covers_to=covers_to,
            summarized_at=func.now(),
        )
        .returning(Conversation.id)
    )
    with get_session(engine) as session:
        return session.execute(statement).scalar_one_or_none() is not None


def idle_unsummarized(
    idle_before: datetime,
    limit: int,
    engine: Engine | None = None,
) -> list[Conversation]:
    """Chats whose last message is older than `idle_before` and not yet covered.

    Coverage is by position: the summary must reach the last message's index.
    """
    message_count = func.jsonb_array_length(Conversation.messages)
    last_message_at = cast(
        Conversation.messages[-1]["when"].astext,
        DateTime(timezone=True),
    )
    statement = (
        select(Conversation)
        .where(
            message_count > 0,
            func.coalesce(Conversation.summary_covers_to, -1) < message_count - 1,
            last_message_at < idle_before,
        )
        .order_by(last_message_at)
        .limit(limit)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))
