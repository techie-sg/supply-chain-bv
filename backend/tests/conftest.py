"""Shared fixtures and isolation from local application configuration."""

import os
from uuid import uuid4

import pytest
from catalogue import definitions
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url

from config import Settings
from database.models import Manager, StorePreference
from database.session import Base, normalize_pg_url
from domain.memory import PreferenceStatus
from service import preferences

_test_environment = pytest.MonkeyPatch()


def pytest_configure() -> None:
    """Disable local dotenv loading before test modules are collected."""
    _test_environment.setitem(Settings.model_config, "env_file", None)
    for name in ("DATABASE_URL", "DB_URL", "JINA_API_KEY", "GROQ_API_KEY"):
        _test_environment.delenv(name, raising=False)


def pytest_unconfigure() -> None:
    _test_environment.undo()


@pytest.fixture(autouse=True)
def restrict_database_connections(monkeypatch):
    """Unit tests mock the database; integration tests explicitly opt in."""
    test_url = os.environ.get("TEST_DATABASE_URL")
    allowed = make_url(normalize_pg_url(test_url)) if test_url else None
    raw_connection = Engine.raw_connection

    def connect(engine):
        if allowed is None or engine.url != allowed:
            raise AssertionError(
                "Mock database access in unit tests. Integration tests must use "
                "an explicit TEST_DATABASE_URL.",
            )
        return raw_connection(engine)

    monkeypatch.setattr(Engine, "raw_connection", connect)


class FakeStore:
    """In-memory stand-in for queries.preferences."""

    def __init__(self) -> None:
        self.rows: list[StorePreference] = []

    def list_definitions(self, engine=None):
        return list(reversed(definitions()))

    def active(self, store_id, manager_id, engine=None):
        return [
            row
            for row in self.rows
            if (row.store_id, row.manager_id, row.status)
            == (store_id, manager_id, PreferenceStatus.ACTIVE)
        ]

    def save(self, store_id, manager_id, code, enabled, value, options, engine=None):
        self._retire(store_id, manager_id, code, PreferenceStatus.SUPERSEDED)
        row = StorePreference(
            id=uuid4(),
            store_id=store_id,
            manager_id=manager_id,
            code=code,
            enabled=enabled,
            value=value,
            options=options,
            status=PreferenceStatus.ACTIVE,
        )
        self.rows.append(row)
        return row

    def remove(self, store_id, manager_id, code, engine=None):
        return self._retire(store_id, manager_id, code, PreferenceStatus.REMOVED)

    def _retire(self, store_id, manager_id, code, status) -> bool:
        retired = False
        for row in self.active(store_id, manager_id):
            if row.code == code:
                row.status = status
                retired = True
        return retired

    def statuses(self, code: str) -> list[str]:
        return [row.status for row in self.rows if row.code == code]


@pytest.fixture
def preference_store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(preferences, "list_definitions", fake.list_definitions)
    monkeypatch.setattr(preferences, "active_preferences", fake.active)
    monkeypatch.setattr(preferences, "save_preference", fake.save)
    monkeypatch.setattr(preferences, "remove_preference", fake.remove)
    return fake


# Manager ids the database tests write as. Conversations and preferences have a
# foreign key to app.managers, so these rows must exist first.
TEST_MANAGERS = ("karthik", "ananya", "imran", "someone-else")


def ensure_managers(connection: Connection) -> None:
    """Create app.managers if needed and add the test managers, each on a shift."""
    Base.metadata.tables["app.managers"].create(connection, checkfirst=True)
    connection.execute(
        insert(Manager)
        .values(
            [
                {
                    "manager_id": manager_id,
                    "store_id": "DS-1",
                    "name": manager_id.title(),
                    "shift_id": f"SHIFT-{manager_id.upper()}",
                    "shift_name": "Test",
                    "shift_start": "00:00",
                    "shift_end": "23:59",
                }
                for manager_id in TEST_MANAGERS
            ],
        )
        .on_conflict_do_nothing(index_elements=["manager_id"]),
    )
