"""Read the operational rows needed by the dispatch tools."""

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import Engine, func, select

from database.models import HourlyMetric, Order, Rider, Zone
from database.session import get_session


@dataclass(frozen=True)
class DispatchRows:
    known_store_ids: list[str]
    scenario_key: str | None = None
    as_of: datetime | None = None
    orders: list[Order] = field(default_factory=list)
    riders: list[Rider] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)


@dataclass(frozen=True)
class MetricRows:
    rows: list[HourlyMetric]
    known_store_ids: list[str] = field(default_factory=list)
    available_dates: list[date] = field(default_factory=list)
    available_hours: list[int] = field(default_factory=list)


def read_live_dispatch(
    store_id: str,
    engine: Engine | None = None,
) -> DispatchRows:
    with get_session(engine) as session:
        identities = session.execute(
            select(Order.scenario_key, Order.as_of)
            .where(Order.store_id == store_id)
            .union(
                select(Rider.scenario_key, Rider.as_of).where(
                    Rider.store_id == store_id,
                ),
            ),
        ).all()
        if not identities:
            known = list(
                session.scalars(
                    select(Order.store_id)
                    .union(select(Rider.store_id))
                    .order_by("store_id"),
                ),
            )
            return DispatchRows(known_store_ids=known)
        if len(identities) != 1:
            raise ValueError("Saved rows do not describe a single scenario snapshot")
        scenario_key, as_of = identities[0]
        return DispatchRows(
            known_store_ids=[store_id],
            scenario_key=scenario_key,
            as_of=as_of,
            orders=list(
                session.scalars(
                    select(Order)
                    .where(
                        Order.store_id == store_id,
                        Order.scenario_key == scenario_key,
                        func.lower(Order.status).not_in(("delivered", "cancelled")),
                    )
                    .order_by(Order.placed_at, Order.order_id),
                ),
            ),
            riders=list(
                session.scalars(
                    select(Rider)
                    .where(
                        Rider.store_id == store_id,
                        Rider.scenario_key == scenario_key,
                    )
                    .order_by(Rider.rider_id),
                ),
            ),
            zones=list(session.scalars(select(Zone).order_by(Zone.zone_id))),
        )


def read_delivery_metrics(
    store_id: str,
    day: date,
    start_hour: int,
    end_hour: int,
    engine: Engine | None = None,
) -> MetricRows:
    with get_session(engine) as session:
        rows = list(
            session.scalars(
                select(HourlyMetric)
                .where(
                    HourlyMetric.store_id == store_id,
                    HourlyMetric.date == day,
                    HourlyMetric.hour >= start_hour,
                    HourlyMetric.hour < end_hour,
                )
                .order_by(HourlyMetric.hour),
            ),
        )
        if rows:
            return MetricRows(rows=rows)
        dates = list(
            session.scalars(
                select(HourlyMetric.date)
                .where(HourlyMetric.store_id == store_id)
                .distinct()
                .order_by(HourlyMetric.date),
            ),
        )
        if not dates:
            known = list(
                session.scalars(
                    select(HourlyMetric.store_id)
                    .distinct()
                    .order_by(HourlyMetric.store_id),
                ),
            )
            return MetricRows(rows=[], known_store_ids=known)
        hours = list(
            session.scalars(
                select(HourlyMetric.hour)
                .where(HourlyMetric.store_id == store_id, HourlyMetric.date == day)
                .order_by(HourlyMetric.hour),
            ),
        )
        return MetricRows(
            rows=[],
            known_store_ids=[store_id],
            available_dates=dates,
            available_hours=hours,
        )
