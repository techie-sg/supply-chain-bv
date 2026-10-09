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
from queries.preferences import persist_preference


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
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> Suggestion | None:
    with get_session(engine) as session:
        return session.scalar(
            select(Suggestion).where(
                Suggestion.id == suggestion_id,
                Suggestion.store_id == store_id,
                Suggestion.manager_id == manager_id,
            ),
        )


def resolve_suggestion(
    suggestion_id: UUID,
    status: SuggestionStatus,
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> bool:
    """Accept or dismiss a pending suggestion; False if it was already resolved."""
    statement = (
        update(Suggestion)
        .where(
            Suggestion.id == suggestion_id,
            Suggestion.store_id == store_id,
            Suggestion.manager_id == manager_id,
            Suggestion.status == SuggestionStatus.PENDING,
        )
        .values(status=status)
        .returning(Suggestion.id)
    )
    with get_session(engine) as session:
        return session.execute(statement).scalar_one_or_none() is not None


def replace_handover_draft(row: dict[str, Any], engine: Engine | None = None) -> None:
    """Replace pending drafts for this manager/day without an intermediate commit."""
    with get_session(engine) as session:
        # Serialize draft generation for the same manager, including an empty set.
        session.execute(
            select(Manager)
            .where(Manager.manager_id == row["manager_id"])
            .with_for_update(),
        )
        session.execute(
            update(Suggestion)
            .where(
                Suggestion.store_id == row["store_id"],
                Suggestion.manager_id == row["manager_id"],
                Suggestion.kind == SuggestionKind.HANDOVER_DRAFT,
                Suggestion.status == SuggestionStatus.PENDING,
                Suggestion.payload["shift"].astext == row["payload"]["shift"],
            )
            .values(status=SuggestionStatus.DISMISSED),
        )
        session.execute(insert(Suggestion).values(**row))


def apply_suggestion(
    suggestion_id: UUID,
    store_id: str,
    manager_id: str,
    expected_payload: dict[str, Any],
    *,
    preference: dict[str, Any] | None = None,
    handover: tuple[date, str] | None = None,
    engine: Engine | None = None,
) -> None:
    """Persist a validated action and resolve its pending suggestion atomically."""
    with get_session(engine) as session:
        suggestion = session.scalar(
            select(Suggestion)
            .where(
                Suggestion.id == suggestion_id,
                Suggestion.store_id == store_id,
                Suggestion.manager_id == manager_id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .with_for_update(),
        )
        if suggestion is None or suggestion.payload != expected_payload:
            raise LookupError("That suggestion is no longer pending or is not yours.")
        if suggestion.kind == SuggestionKind.SETTING:
            if preference is not None:
                persist_preference(session, store_id, manager_id, **preference)
        elif suggestion.kind == SuggestionKind.HANDOVER_DRAFT and handover is not None:
            shift, note = handover
            session.add(
                HandoverNote(
                    store_id=store_id,
                    manager_id=manager_id,
                    shift=shift,
                    note=note,
                ),
            )
        else:
            raise ValueError("The prepared action does not match the suggestion.")
        suggestion.status = SuggestionStatus.ACCEPTED


def review_answer_issues(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> int:
    """Review all pending answer issues in the acting manager's store scope."""
    with get_session(engine) as session:
        rows = session.execute(
            update(Suggestion)
            .where(
                Suggestion.store_id == store_id,
                Suggestion.manager_id == manager_id,
                Suggestion.kind == SuggestionKind.ANSWER_ISSUE,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(status=SuggestionStatus.DISMISSED)
            .returning(Suggestion.id),
        )
        return len(rows.all())


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
