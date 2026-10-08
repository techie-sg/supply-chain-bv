"""Shared fixtures."""

import sys
from uuid import uuid4

import pytest
from catalogue import definitions

from database.models import StorePreference
from domain.memory import PreferenceStatus
from service import preferences


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


class _IdleJob:
    def start(self) -> None:
        pass


@pytest.fixture(autouse=True)
def no_background_jobs(monkeypatch) -> None:
    """Tests that call the app's `main()` must not start real scheduler threads."""
    app_module = sys.modules.get("ui.gradio_app")
    if app_module is not None:
        monkeypatch.setattr(app_module, "idle_summary_job", _IdleJob)
        monkeypatch.setattr(app_module, "daily_review_job", _IdleJob)
