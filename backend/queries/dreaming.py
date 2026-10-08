"""Read chats for the daily review; store suggestions, handover notes and digests."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import (
    Conversation,
    HandoverNote,
    Manager,
    MemoryDigest,
    Suggestion,
)
from database.session import get_session
from domain.memory import SuggestionKind, SuggestionStatus


def conversations_to_review(engine: Engine | None = None) -> list[Conversation]:
    """Chats with messages after `dreamed_to`, across every store and manager."""
    message_count = func.jsonb_array_length(Conversation.messages)
    statement = (
        select(Conversation)
        .where(func.coalesce(Conversation.dreamed_to, -1) < message_count - 1)
        .order_by(Conversation.created_at)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))


def recent_conversations(
    store_id: str,
    manager_id: str,
    limit: int,
    engine: Engine | None = None,
) -> list[Conversation]:
    """The manager's most recently updated non-empty chats."""
    statement = (
        select(Conversation)
        .where(
            Conversation.store_id == store_id,
            Conversation.manager_id == manager_id,
            func.jsonb_array_length(Conversation.messages) > 0,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))


def get_conversation(
    conversation_id: UUID,
    engine: Engine | None = None,
) -> Conversation | None:
    with get_session(engine) as session:
        return session.get(Conversation, conversation_id)


def advance_dreamed_to(
    conversation_id: UUID,
    to_index: int,
    expected: int | None,
    engine: Engine | None = None,
) -> bool:
    """Move the review position forward, only if no other run moved it."""
    statement = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.dreamed_to.is_not_distinct_from(expected),
        )
        .values(dreamed_to=to_index)
        .returning(Conversation.id)
    )
    with get_session(engine) as session:
        return session.execute(statement).scalar_one_or_none() is not None


def add_suggestions(rows: list[dict[str, Any]], engine: Engine | None = None) -> int:
    if not rows:
        return 0
    with get_session(engine) as session:
        session.execute(insert(Suggestion), rows)
    return len(rows)


def list_suggestions(
    store_id: str,
    manager_id: str,
    kinds: list[str],
    statuses: list[str],
    limit: int = 50,
    engine: Engine | None = None,
) -> list[Suggestion]:
    statement = (
        select(Suggestion)
        .where(
            Suggestion.store_id == store_id,
            Suggestion.manager_id == manager_id,
            Suggestion.kind.in_(kinds),
            Suggestion.status.in_(statuses),
        )
        .order_by(Suggestion.created_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))


def get_suggestion(
    suggestion_id: UUID,
    engine: Engine | None = None,
) -> Suggestion | None:
    with get_session(engine) as session:
        return session.get(Suggestion, suggestion_id)


def resolve_suggestion(
    suggestion_id: UUID,
    status: SuggestionStatus,
    engine: Engine | None = None,
) -> bool:
    """Accept or dismiss a pending suggestion; False if it was already resolved."""
    statement = (
        update(Suggestion)
        .where(
            Suggestion.id == suggestion_id,
            Suggestion.status == SuggestionStatus.PENDING,
        )
        .values(status=status)
        .returning(Suggestion.id)
    )
    with get_session(engine) as session:
        return session.execute(statement).scalar_one_or_none() is not None


def dismiss_pending_drafts(
    store_id: str,
    manager_id: str,
    shift: str,
    engine: Engine | None = None,
) -> int:
    """Retire pending handover drafts for a day before a newer one replaces them."""
    statement = (
        update(Suggestion)
        .where(
            Suggestion.store_id == store_id,
            Suggestion.manager_id == manager_id,
            Suggestion.kind == SuggestionKind.HANDOVER_DRAFT,
            Suggestion.status == SuggestionStatus.PENDING,
            Suggestion.payload["shift"].astext == shift,
        )
        .values(status=SuggestionStatus.DISMISSED)
        .returning(Suggestion.id)
    )
    with get_session(engine) as session:
        return len(session.execute(statement).all())


def save_handover_note(
    store_id: str,
    manager_id: str,
    shift: date,
    note: str,
    engine: Engine | None = None,
) -> HandoverNote:
    handover = HandoverNote(
        store_id=store_id,
        manager_id=manager_id,
        shift=shift,
        note=note,
    )
    with get_session(engine) as session:
        session.add(handover)
        session.flush()
        session.refresh(handover)
    return handover


def latest_handover_notes(
    store_id: str,
    engine: Engine | None = None,
) -> list[HandoverNote]:
    """Every note of the most recent shift that has notes, oldest first."""
    latest_shift = (
        select(func.max(HandoverNote.shift))
        .where(HandoverNote.store_id == store_id)
        .scalar_subquery()
    )
    statement = (
        select(HandoverNote)
        .where(HandoverNote.store_id == store_id, HandoverNote.shift == latest_shift)
        .order_by(HandoverNote.created_at)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))


def all_managers(engine: Engine | None = None) -> list[Manager]:
    """Every store's managers; the review rebuilds a digest for each."""
    statement = select(Manager).order_by(Manager.store_id, Manager.manager_id)
    with get_session(engine) as session:
        return list(session.scalars(statement))


def get_digest(manager_id: str, engine: Engine | None = None) -> MemoryDigest | None:
    with get_session(engine) as session:
        return session.get(MemoryDigest, manager_id)


def save_digest(
    store_id: str,
    manager_id: str,
    digest: str | None,
    sources: list[dict[str, Any]],
    engine: Engine | None = None,
) -> None:
    """Replace the manager's digest; there is one row per manager."""
    values = {"digest": digest, "sources": sources, "built_at": func.now()}
    statement = (
        pg_insert(MemoryDigest)
        .values(store_id=store_id, manager_id=manager_id, **values)
        .on_conflict_do_update(index_elements=[MemoryDigest.manager_id], set_=values)
    )
    with get_session(engine) as session:
        session.execute(statement)
