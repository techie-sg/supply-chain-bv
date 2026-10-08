import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from threading import Thread

import pytest
from catalogue import DEFINITIONS
from conftest import ensure_managers
from sqlalchemy import Engine, delete, insert, text, update

from database.models import AlertEvent, PreferenceDefinition
from database.session import Base, build_engine, get_session
from queries.alerts import record_trigger, triggers_since
from service.scenarios import TIMEZONE

AS_OF = datetime(2026, 10, 8, 20, 14, tzinfo=TIMEZONE)


@pytest.fixture
def alert_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            ensure_managers(connection)
            Base.metadata.tables["app.preference_definitions"].create(
                connection,
                checkfirst=True,
            )
            Base.metadata.tables["app.alert_events"].create(connection, checkfirst=True)
            connection.execute(delete(AlertEvent))
            if not connection.execute(
                text("SELECT 1 FROM app.preference_definitions LIMIT 1"),
            ).first():
                connection.execute(insert(PreferenceDefinition), DEFINITIONS)
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(delete(AlertEvent))
        engine.dispose()


def values(code: str = "rider_shortage_alert", manager_id: str = "karthik") -> dict:
    return {
        "store_id": "DS-1",
        "manager_id": manager_id,
        "code": code,
        "value": 3.0,
        "threshold": 2,
        "snapshot_as_of": AS_OF,
        "details": {"name": "Rider shortage", "unit": "orders_per_rider"},
    }


def test_a_pop_up_is_recorded_once_per_cooldown(alert_engine: Engine) -> None:
    first = record_trigger(values(), 15, alert_engine)
    assert first is not None and first.value == 3.0
    assert first.details["name"] == "Rider shortage"
    assert record_trigger(values(), 15, alert_engine) is None
    # Other alerts and other managers have their own cooldowns.
    assert record_trigger(values("orders_piling_up_alert"), 15, alert_engine)
    assert record_trigger(values(manager_id="ananya"), 15, alert_engine)
    # Once the cooldown has passed, the same alert pops up again.
    with get_session(alert_engine) as session:
        session.execute(
            update(AlertEvent)
            .where(AlertEvent.id == first.id)
            .values(triggered_at=AlertEvent.triggered_at - timedelta(minutes=16)),
        )
    assert record_trigger(values(), 15, alert_engine) is not None


def test_two_tabs_checking_at_once_record_one_pop_up(alert_engine: Engine) -> None:
    results: list[object] = []
    threads = [
        Thread(
            target=lambda: results.append(record_trigger(values(), 15, alert_engine)),
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1


def test_triggers_since_filters_by_manager_alert_and_time(alert_engine: Engine) -> None:
    record_trigger(values(), 15, alert_engine)
    record_trigger(values("orders_piling_up_alert"), 15, alert_engine)
    record_trigger(values(manager_id="ananya"), 15, alert_engine)
    since = datetime.now(TIMEZONE) - timedelta(hours=1)
    mine = triggers_since("DS-1", "karthik", since, engine=alert_engine)
    assert [event.code for event in mine] == [
        "rider_shortage_alert",
        "orders_piling_up_alert",
    ]
    only = triggers_since(
        "DS-1",
        "karthik",
        since,
        "orders_piling_up_alert",
        alert_engine,
    )
    assert [event.code for event in only] == ["orders_piling_up_alert"]
    future = datetime.now(TIMEZONE) + timedelta(hours=1)
    assert triggers_since("DS-1", "karthik", future, engine=alert_engine) == []
