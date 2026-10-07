"""Integration checks against a dedicated TEST_DATABASE_URL."""

import os
from collections.abc import Iterator

import pytest
from catalogue import DEFINITIONS
from sqlalchemy import Engine, delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from database.models import PreferenceDefinition, StorePreference
from database.session import Base, build_engine, get_session
from queries.preferences import (
    active_preferences,
    list_definitions,
    remove_preference,
    save_preference,
)


@pytest.fixture
def preference_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            for table in ("app.preference_definitions", "app.store_preferences"):
                Base.metadata.tables[table].create(connection, checkfirst=True)
            connection.execute(delete(StorePreference))
            connection.execute(delete(PreferenceDefinition))
            connection.execute(insert(PreferenceDefinition), DEFINITIONS)
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(delete(StorePreference))
        engine.dispose()


def save(engine: Engine, code: str = "sla_dip_alert", **overrides) -> StorePreference:
    values = {"enabled": True, "value": 85, "options": None, **overrides}
    return save_preference("DS-1", "karthik", code, engine=engine, **values)


def test_catalogue_is_read_back_with_typed_values(preference_engine: Engine) -> None:
    catalogue = {item.code: item for item in list_definitions(preference_engine)}
    assert set(catalogue) == {row["code"] for row in DEFINITIONS}
    shortage = catalogue["rider_shortage_alert"]
    assert (shortage.min_value, shortage.max_value) == (0.5, 2)
    assert catalogue["incentive_cap"].default_value is None
    assert catalogue["briefing"].allowed_values == DEFINITIONS[-1]["allowed_values"]


def test_saving_again_supersedes_the_active_value(preference_engine: Engine) -> None:
    first = save(preference_engine, value=85)
    second = save(
        preference_engine,
        value=90,
        options={"days": ["sat"], "cooldown_min": 30},
    )
    [active] = active_preferences("DS-1", "karthik", preference_engine)
    assert active.id == second.id and active.value == 90
    assert active.options == {"days": ["sat"], "cooldown_min": 30}
    with get_session(preference_engine) as session:
        statuses = dict(
            session.execute(select(StorePreference.id, StorePreference.status)).all(),
        )
    assert statuses == {first.id: "superseded", second.id: "active"}


def test_values_are_scoped_to_store_and_manager(preference_engine: Engine) -> None:
    save(preference_engine)
    assert active_preferences("DS-2", "karthik", preference_engine) == []
    assert active_preferences("DS-1", "someone-else", preference_engine) == []


def test_removing_marks_the_value_removed(preference_engine: Engine) -> None:
    save(preference_engine)
    assert remove_preference("DS-1", "karthik", "sla_dip_alert", preference_engine)
    assert active_preferences("DS-1", "karthik", preference_engine) == []
    assert not remove_preference(
        "DS-1",
        "karthik",
        "sla_dip_alert",
        preference_engine,
    )


def test_unset_value_is_stored_as_sql_null(preference_engine: Engine) -> None:
    save(preference_engine, code="incentive_cap", enabled=False, value=None)
    with get_session(preference_engine) as session:
        assert session.scalar(
            text("SELECT value IS NULL FROM app.store_preferences"),
        )


def test_only_one_active_value_per_item(preference_engine: Engine) -> None:
    save(preference_engine)
    with pytest.raises(IntegrityError), get_session(preference_engine) as session:
        session.add(
            StorePreference(
                store_id="DS-1",
                manager_id="karthik",
                code="sla_dip_alert",
                enabled=True,
                value=70,
            ),
        )


def test_values_must_belong_to_a_catalogue_item(preference_engine: Engine) -> None:
    with pytest.raises(IntegrityError):
        save(preference_engine, code="speeding_alert")
