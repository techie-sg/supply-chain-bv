"""Persist synthetic dispatch scenarios in a single transaction."""

from sqlalchemy import Engine, text

from database.models import HourlyMetric, Order, Rider, Zone
from database.session import get_session


def replace_scenario(rows: list[object], engine: Engine | None = None) -> None:
    """Replace operational rows, preserving insertion order for foreign keys."""
    with get_session(engine) as session:
        session.execute(
            text("TRUNCATE TABLE app.orders, app.riders, app.hourly_metrics, app.zones")
        )
        for model in (Zone, HourlyMetric, Rider, Order):
            session.add_all(row for row in rows if isinstance(row, model))
            session.flush()
