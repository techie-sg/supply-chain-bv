"""Scoped dispatch reads against an explicitly configured test database."""

import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, delete, text

from database.models import HourlyMetric, Order, Rider, Zone
from database.session import Base, build_engine, get_session
from queries.tools import read_delivery_metrics, read_live_dispatch

AS_OF = datetime(2026, 10, 9, 19, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
SCENARIO = "tool-query-test"
STORE = "TOOL-STORE-1"
OTHER_STORE = "TOOL-STORE-2"
ZONE = "TOOL-ZONE"


@pytest.fixture
def tool_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        for model in (Zone, Rider, Order, HourlyMetric):
            Base.metadata.tables[f"app.{model.__tablename__}"].create(
                connection,
                checkfirst=True,
            )
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Order).where(Order.scenario_key == SCENARIO))
            connection.execute(delete(Rider).where(Rider.scenario_key == SCENARIO))
            connection.execute(
                delete(HourlyMetric).where(
                    HourlyMetric.store_id.in_((STORE, OTHER_STORE)),
                ),
            )
            connection.execute(delete(Zone).where(Zone.zone_id == ZONE))
        engine.dispose()


def order(identifier: str, status: str, store_id: str = STORE) -> Order:
    return Order(
        scenario_key=SCENARIO,
        order_id=identifier,
        store_id=store_id,
        as_of=AS_OF,
        placed_at=AS_OF - timedelta(minutes=30),
        zone_id=ZONE,
        item_count=1,
        has_frozen_items=False,
        status=status,
        assigned_rider_id=None,
    )


def add_zone(session) -> None:
    session.add(
        Zone(
            zone_id=ZONE,
            zone_name="Tool test zone",
            distance_from_store_km=1,
            avg_ride_min_dry=4,
            avg_ride_min_rain=6,
        ),
    )
    session.flush()


def test_live_reads_exclude_closed_orders_and_other_stores(tool_engine: Engine) -> None:
    with get_session(tool_engine) as session:
        add_zone(session)
        session.add_all(
            [order(f"closed-{i}", "delivered") for i in range(50)]
            + [
                order("cancelled", "CANCELLED"),
                order("waiting", "packed_waiting_rider"),
                order("other-store", "packed_waiting_rider", OTHER_STORE),
            ],
        )
    result = read_live_dispatch(STORE, tool_engine)
    assert [row.order_id for row in result.orders] == ["waiting"]
    assert result.as_of == AS_OF and result.scenario_key == SCENARIO


def test_store_with_only_closed_orders_still_has_a_snapshot(
    tool_engine: Engine,
) -> None:
    with get_session(tool_engine) as session:
        add_zone(session)
        session.add(order("done", "delivered"))
    result = read_live_dispatch(STORE, tool_engine)
    assert result.orders == [] and result.riders == []
    assert result.as_of == AS_OF and result.known_store_ids == [STORE]
    missing = read_live_dispatch(OTHER_STORE, tool_engine)
    assert missing.as_of is None and STORE in missing.known_store_ids


def test_conflicting_snapshot_timestamps_are_rejected(tool_engine: Engine) -> None:
    with get_session(tool_engine) as session:
        add_zone(session)
        older = order("older", "picking")
        older.as_of -= timedelta(minutes=1)
        session.add_all([older, order("newer", "picking")])
    with pytest.raises(ValueError, match="single scenario snapshot"):
        read_live_dispatch(STORE, tool_engine)


def metric(store_id: str, day: date, hour: int) -> HourlyMetric:
    return HourlyMetric(
        store_id=store_id,
        date=day,
        hour=hour,
        orders=10,
        avg_pick_pack_min=3,
        avg_rider_wait_min=1,
        avg_ride_min=5,
        sla_10min_pct=90,
        riders_online=2,
        rain_flag=False,
    )


def test_history_filters_store_date_and_exclusive_end_hour(tool_engine: Engine) -> None:
    day = date(2026, 10, 8)
    with get_session(tool_engine) as session:
        session.add_all(
            [metric(STORE, day, hour) for hour in (17, 18, 19, 20)]
            + [
                metric(STORE, day - timedelta(days=1), 18),
                metric(OTHER_STORE, day, 18),
            ],
        )
    result = read_delivery_metrics(STORE, day, 18, 20, tool_engine)
    assert [row.hour for row in result.rows] == [18, 19]
    assert all(row.store_id == STORE and row.date == day for row in result.rows)
    empty = read_delivery_metrics(STORE, day, 21, 22, tool_engine)
    assert empty.available_dates == [day - timedelta(days=1), day]
    assert empty.available_hours == [17, 18, 19, 20]
    unknown = read_delivery_metrics("TOOL-UNKNOWN", day, 18, 20, tool_engine)
    assert unknown.rows == [] and STORE in unknown.known_store_ids
