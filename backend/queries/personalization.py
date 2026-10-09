"""Small, manager-scoped profiles and raw evidence for personalization."""

from typing import Any

from sqlalchemy import Engine, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session

from constants import PERSONALIZATION_EVIDENCE_CHATS, PERSONALIZATION_EVIDENCE_MESSAGES
from database.models import Conversation, Manager, ManagerPersonalization, Suggestion
from database.session import get_session
from domain.memory import SuggestionKind, SuggestionStatus
from domain.personalization import Profile


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
        if code not in profile or profile[code].get("value") != item.get("value")
    }
    if not actual:
        return False
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
    return True


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


def propose_personalization(row: dict[str, Any], engine: Engine | None = None) -> bool:
    """A concurrent review or an old dismissed proposal must not be duplicated."""
    with get_session(engine) as session:
        manager = session.scalar(
            select(Manager)
            .where(
                Manager.store_id == row["store_id"],
                Manager.manager_id == row["manager_id"],
            )
            .with_for_update(),
        )
        if manager is None:
            raise LookupError("That manager does not belong to this store.")
        profile = (
            session.scalar(
                select(ManagerPersonalization.preferences).where(
                    ManagerPersonalization.store_id == row["store_id"],
                    ManagerPersonalization.manager_id == row["manager_id"],
                ),
            )
            or {}
        )
        if row["payload"]["code"] in profile:
            return False
        existing = session.scalar(
            select(Suggestion.id)
            .where(
                Suggestion.store_id == row["store_id"],
                Suggestion.manager_id == row["manager_id"],
                Suggestion.kind == SuggestionKind.PERSONALIZATION,
                Suggestion.payload["code"].astext == row["payload"]["code"],
                Suggestion.payload["value"].astext == row["payload"]["value"],
            )
            .limit(1),
        )
        if existing is not None:
            return False
        session.add(Suggestion(**row))
        return True
