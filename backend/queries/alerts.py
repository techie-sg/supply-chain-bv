"""Store alert pop-ups and read them back for cooldowns and daily counts."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select, text

from database.models import AlertEvent
from database.session import get_session


def record_trigger(
    values: dict[str, Any],
    cooldown_min: int,
    engine: Engine | None = None,
) -> AlertEvent | None:
    """Store a pop-up unless the same alert popped up within its cooldown.

    A transaction-scoped advisory lock per store, manager and alert makes the
    check and the insert atomic, so two open tabs never record the same pop-up.
    """
    key = f"{values['store_id']}|{values['manager_id']}|{values['code']}"
    with get_session(engine) as session:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": key},
        )
        recent = session.scalar(
            select(AlertEvent.id)
            .where(
                AlertEvent.store_id == values["store_id"],
                AlertEvent.manager_id == values["manager_id"],
                AlertEvent.code == values["code"],
                AlertEvent.triggered_at > func.now() - timedelta(minutes=cooldown_min),
            )
            .limit(1),
        )
        if recent is not None:
            return None
        event = AlertEvent(**values)
        session.add(event)
        session.flush()
        session.refresh(event)
        return event


def triggers_since(
    store_id: str,
    manager_id: str,
    since: datetime,
    code: str | None = None,
    engine: Engine | None = None,
) -> list[AlertEvent]:
    """The manager's pop-ups since `since`, oldest first."""
    statement = select(AlertEvent).where(
        AlertEvent.store_id == store_id,
        AlertEvent.manager_id == manager_id,
        AlertEvent.triggered_at >= since,
    )
    if code is not None:
        statement = statement.where(AlertEvent.code == code)
    with get_session(engine) as session:
        return list(session.scalars(statement.order_by(AlertEvent.triggered_at)))
