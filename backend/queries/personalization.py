"""Small, manager-scoped profiles and raw evidence for personalization."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session

from constants import (
    PERSONALIZATION_EVIDENCE_CHATS,
    PERSONALIZATION_EVIDENCE_MESSAGES,
    TIMEZONE,
)
from database.models import Conversation, Manager, ManagerPersonalization, Suggestion
from database.session import get_session
from domain.memory import SuggestionKind, SuggestionStatus
from domain.personalization import (
    FIELDS,
    PersonalizationCandidate,
    Profile,
    validate_value,
)


def read_profile(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> Profile:
    with get_session(engine) as session:
        return (
            session.scalar(
                select(ManagerPersonalization.preferences).where(
                    ManagerPersonalization.store_id == store_id,
                    ManagerPersonalization.manager_id == manager_id,
                ),
            )
            or {}
        )


def persist_profile(
    session: Session,
    store_id: str,
    manager_id: str,
    changes: Profile,
    expected: Profile | None = None,
) -> bool:
    """Serialize changes per manager, preserve other fields, reject stale edits."""
    manager = session.scalar(
        select(Manager)
        .where(
            Manager.store_id == store_id,
            Manager.manager_id == manager_id,
        )
        .with_for_update(),
    )
    if manager is None:
        raise LookupError("That manager does not belong to this store.")
    return bool(_merge_profile(session, store_id, manager_id, changes, expected))


def _merge_profile(
    session: Session,
    store_id: str,
    manager_id: str,
    changes: Profile,
    expected: Profile | None = None,
    *,
    only_unset: bool = False,
) -> int:
    """Merge under the caller's manager lock; never hold it during model calls."""
    row = session.scalar(
        select(ManagerPersonalization).where(
            ManagerPersonalization.store_id == store_id,
            ManagerPersonalization.manager_id == manager_id,
        ),
    )
    profile = dict(row.preferences) if row else {}
    if expected is not None and any(
        profile.get(code) != expected.get(code) for code in changes
    ):
        raise ValueError(
            "Personalization changed since you opened it. Refresh and try again.",
        )
    actual = {
        code: item
        for code, item in changes.items()
        if (not only_unset or code not in profile)
        and (code not in profile or profile[code].get("value") != item.get("value"))
    }
    if not actual:
        return 0
    profile.update(actual)
    if row is None:
        session.add(
            ManagerPersonalization(
                store_id=store_id,
                manager_id=manager_id,
                preferences=profile,
            ),
        )
    else:
        row.preferences = profile
    # Existing proposals for changed fields can no longer be applied.
    session.execute(
        update(Suggestion)
        .where(
            Suggestion.store_id == store_id,
            Suggestion.manager_id == manager_id,
            Suggestion.kind == SuggestionKind.PERSONALIZATION,
            Suggestion.status == SuggestionStatus.PENDING,
            Suggestion.payload["code"].astext.in_(list(actual)),
        )
        .values(status=SuggestionStatus.DISMISSED),
    )
    return len(actual)


def save_profile(
    store_id: str,
    manager_id: str,
    changes: Profile,
    expected: Profile | None = None,
    engine: Engine | None = None,
) -> bool:
    with get_session(engine) as session:
        return persist_profile(session, store_id, manager_id, changes, expected)


def evidence_chats(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    """Project at most 20 chats and their last 60 messages, never full histories."""
    count = func.jsonb_array_length(Conversation.messages)
    start = func.greatest(count - PERSONALIZATION_EVIDENCE_MESSAGES, 0)
    tail = func.jsonb_path_query_array(
        Conversation.messages,
        cast(f"$[last - {PERSONALIZATION_EVIDENCE_MESSAGES - 1} to last]", JSONPATH),
    )
    statement = (
        select(Conversation.id, start.label("start"), tail.label("messages"))
        .where(
            Conversation.store_id == store_id,
            Conversation.manager_id == manager_id,
            Conversation.summary.is_not(None),
            count > 0,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .limit(PERSONALIZATION_EVIDENCE_CHATS)
    )
    with get_session(engine) as session:
        return [dict(row) for row in session.execute(statement).mappings()]


def finish_review(
    conversation_id: UUID,
    store_id: str,
    manager_id: str,
    expected: int | None,
    covers_to: int,
    updates: list[dict[str, Any]],
    engine: Engine | None = None,
) -> int | None:
    """Save learned preferences and progress atomically, without approval rows."""
    if covers_to < 0 or (expected is not None and covers_to <= expected):
        raise ValueError("Personalization progress must move forward.")
    if any(
        row["store_id"] != store_id
        or row["manager_id"] != manager_id
        or row["kind"] != SuggestionKind.PERSONALIZATION
        for row in updates
    ):
        raise ValueError("Personalization evidence belongs to another manager.")
    with get_session(engine) as session:
        manager = session.scalar(
            select(Manager)
            .where(Manager.store_id == store_id, Manager.manager_id == manager_id)
            .with_for_update(),
        )
        if manager is None:
            raise LookupError("That manager does not belong to this store.")
        advanced = session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.store_id == store_id,
                Conversation.manager_id == manager_id,
                Conversation.personalization_covers_to.is_not_distinct_from(expected),
                Conversation.summary_covers_to >= covers_to,
                func.jsonb_array_length(Conversation.messages) > covers_to,
            )
            .values(personalization_covers_to=covers_to, personalized_at=func.now())
            .returning(Conversation.id),
        ).scalar_one_or_none()
        if advanced is None:
            return None
        changes: Profile = {}
        for row in updates:
            candidate = PersonalizationCandidate.model_validate(
                {
                    **row["payload"],
                    "reason": row["reason"],
                    "evidence": row["evidence"],
                },
            )
            if candidate.code not in FIELDS:
                raise ValueError("Dreaming can save only typed response preferences.")
            value = validate_value(candidate.code, candidate.value)
            if value is None or candidate.code in changes:
                raise ValueError("Invalid or duplicate learned preference.")
            evidence = next(
                (
                    entry
                    for entry in candidate.evidence
                    if entry.conversation_id == str(conversation_id)
                    and (expected is None or entry.message_index > expected)
                    and entry.message_index <= covers_to
                ),
                None,
            )
            if evidence is None:
                raise ValueError("Learned preference requires evidence in this batch.")
            changes[candidate.code] = {
                "value": value,
                "source": "dreaming",
                "conversation_id": str(conversation_id),
                "quote": evidence.quote,
                "reason": candidate.reason,
                "evidence": [entry.model_dump() for entry in candidate.evidence],
                "saved_at": datetime.now(TIMEZONE).isoformat(),
            }
        return _merge_profile(session, store_id, manager_id, changes, only_unset=True)


def pending_reviews(
    limit: int,
    exclude: Sequence[UUID] = (),
    engine: Engine | None = None,
) -> list[Conversation]:
    """Pick unfinished summary-backed reviews, including chats with no new summary."""
    statement = (
        select(Conversation)
        .where(
            Conversation.summary.is_not(None),
            func.coalesce(Conversation.personalization_covers_to, -1)
            < Conversation.summary_covers_to,
        )
        .order_by(Conversation.created_at, Conversation.id)
        .limit(limit)
    )
    if exclude:
        statement = statement.where(Conversation.id.not_in(exclude))
    with get_session(engine) as session:
        return list(session.scalars(statement))
