"""Read chats for the daily review and store suggestions."""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, insert, select, update

from database.models import (
    Conversation,
    Manager,
    Suggestion,
)
from database.session import get_session
from domain.memory import SuggestionKind, SuggestionStatus
from queries.personalization import persist_profile
from queries.preferences import persist_preference
from queries.shifts import write_note


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
    """Replace pending drafts for this shift without an intermediate commit."""
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
                Suggestion.payload["shift_id"].astext == row["payload"]["shift_id"],
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
    handover: tuple[UUID, str] | None = None,
    personalization: dict[str, Any] | None = None,
    engine: Engine | None = None,
) -> None:
    """Persist a validated action and resolve its pending suggestion atomically."""
    with get_session(engine) as session:
        if personalization is not None:
            # Match the lock order used by direct profile saves and proposals.
            session.execute(
                select(Manager)
                .where(
                    Manager.store_id == store_id,
                    Manager.manager_id == manager_id,
                )
                .with_for_update(),
            )
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
        if (
            suggestion.kind == SuggestionKind.PERSONALIZATION
            and personalization is not None
        ):
            persist_profile(session, store_id, manager_id, personalization, expected={})
        elif suggestion.kind == SuggestionKind.SETTING:
            if preference is not None:
                persist_preference(session, store_id, manager_id, **preference)
        elif suggestion.kind == SuggestionKind.HANDOVER_DRAFT and handover is not None:
            shift_id, note = handover
            if write_note(session, shift_id, manager_id, note) is None:
                raise LookupError("That shift has already ended.")
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
