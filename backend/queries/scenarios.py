"""Persist synthetic dispatch scenarios in a single transaction."""

from sqlalchemy import Engine, select, text

from database.models import HourlyMetric, Order, Rider, Zone
from database.session import get_session


def read_scenario_rows(engine: Engine | None = None) -> list[object]:
    """Read the saved operational snapshot without replacing any data."""
    with get_session(engine) as session:
        rows: list[object] = []
        for model in (Order, Rider, HourlyMetric, Zone):
            rows.extend(
                session.scalars(select(model).order_by(*model.__table__.primary_key))
            )
        return rows


def replace_scenario(rows: list[object], engine: Engine | None = None) -> None:
    """Replace operational rows, preserving insertion order for foreign keys."""
    with get_session(engine) as session:
        session.execute(
            text("TRUNCATE TABLE app.orders, app.riders, app.hourly_metrics, app.zones")
        )
        for model in (Zone, HourlyMetric, Rider, Order):
            session.add_all(row for row in rows if isinstance(row, model))
            session.flush()
