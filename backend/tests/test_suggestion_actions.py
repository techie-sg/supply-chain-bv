"""Scoped suggestion actions and rollback against an explicit disposable database."""

import os

import pytest
from catalogue import DEFINITIONS
from conftest import ensure_managers
from sqlalchemy import delete, event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from database.models import (
    HandoverNote,
    PreferenceDefinition,
    StorePreference,
    Suggestion,
)
from database.session import build_engine
from domain.memory import SuggestionKind
from queries.dreaming import (
    add_suggestions,
    get_suggestion,
    list_suggestions,
    replace_handover_draft,
)
from queries.preferences import active_preferences, save_preference
from service.suggestions import (
    accept_suggestion,
    dismiss_suggestion,
    mark_answer_issues_reviewed,
)


@pytest.fixture
def action_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable database")
    engine = build_engine(url)
    with engine.begin() as connection:
        ensure_managers(connection)
        connection.execute(
            pg_insert(PreferenceDefinition)
            .values(DEFINITIONS)
            .on_conflict_do_nothing(),
        )
        for model in (Suggestion, HandoverNote, StorePreference):
            connection.execute(delete(model))
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            for model in (Suggestion, HandoverNote, StorePreference):
                connection.execute(delete(model))
        engine.dispose()


def row(kind, payload, manager="karthik", store="DS-1"):
    return {
        "store_id": store,
        "manager_id": manager,
        "kind": kind,
        "payload": payload,
        "reason": "Evidence",
        "evidence": [],
    }


def pending(engine, manager="karthik", store="DS-1"):
    return list_suggestions(
        store,
        manager,
        list(SuggestionKind),
        ["pending"],
        engine=engine,
    )


def test_manager_and_store_scope_apply_outside_the_ui(action_engine):
    add_suggestions(
        [row("handover_draft", {"shift": "2026-10-09", "note": "Next shift"})],
        action_engine,
    )
    [item] = pending(action_engine)
    for store, manager in [("DS-2", "karthik"), ("DS-1", "ananya")]:
        with pytest.raises(LookupError):
            accept_suggestion(item.id, store, manager, engine=action_engine)
        assert not dismiss_suggestion(item.id, store, manager, action_engine)
    assert get_suggestion(item.id, "DS-1", "karthik", action_engine).status == "pending"


def test_handover_acceptance_is_applied_only_once(action_engine):
    add_suggestions(
        [row("handover_draft", {"shift": "2026-10-09", "note": "Next shift"})],
        action_engine,
    )
    [item] = pending(action_engine)
    accept_suggestion(item.id, "DS-1", "karthik", engine=action_engine)
    with pytest.raises(LookupError):
        accept_suggestion(item.id, "DS-1", "karthik", engine=action_engine)
    with action_engine.connect() as connection:
        assert connection.execute(select(HandoverNote.note)).scalars().all() == [
            "Next shift",
        ]


@pytest.mark.parametrize("kind", ["setting", "handover_draft"])
def test_failure_after_applying_action_rolls_back_every_write(action_engine, kind):
    if kind == "setting":
        save_preference(
            "DS-1",
            "karthik",
            "incentive_cap",
            True,
            250,
            None,
            action_engine,
        )
        payload = {"code": "incentive_cap", "enabled": True, "value": 300}
    else:
        payload = {"shift": "2026-10-09", "note": "Next shift"}
    add_suggestions([row(kind, payload)], action_engine)
    [item] = pending(action_engine)

    def fail_after_writes(session, flush_context):
        if any(
            isinstance(obj, Suggestion) and obj.status == "accepted"
            for obj in session.dirty
        ):
            raise RuntimeError("Injected failure after writes")

    event.listen(Session, "after_flush", fail_after_writes)
    try:
        with pytest.raises(RuntimeError, match="Injected failure"):
            accept_suggestion(item.id, "DS-1", "karthik", engine=action_engine)
    finally:
        event.remove(Session, "after_flush", fail_after_writes)
    assert get_suggestion(item.id, "DS-1", "karthik", action_engine).status == "pending"
    if kind == "setting":
        [preference] = active_preferences("DS-1", "karthik", action_engine)
        assert preference.value == 250
    else:
        with action_engine.connect() as connection:
            assert connection.execute(select(HandoverNote)).all() == []


def test_draft_replacement_failure_preserves_previous_pending_draft(action_engine):
    old = row("handover_draft", {"shift": "2026-10-09", "note": "Original"})
    add_suggestions([old], action_engine)
    [item] = pending(action_engine)

    def reject_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO app.suggestions"):
            raise RuntimeError("Injected replacement failure")

    event.listen(action_engine, "before_cursor_execute", reject_insert)
    try:
        with pytest.raises(RuntimeError, match="replacement failure"):
            replace_handover_draft(
                row("handover_draft", {"shift": "2026-10-09", "note": "Replacement"}),
                action_engine,
            )
    finally:
        event.remove(action_engine, "before_cursor_execute", reject_insert)
    assert [entry.id for entry in pending(action_engine)] == [item.id]


def test_bulk_review_only_resolves_the_acting_scope_and_issue_kind(action_engine):
    issue = {"issue": "unanswered"}
    add_suggestions(
        [
            row("answer_issue", issue),
            row("answer_issue", issue, manager="ananya"),
            row("answer_issue", issue, store="DS-2"),
            row("handover_draft", {"shift": "2026-10-09", "note": "Draft"}),
        ],
        action_engine,
    )
    assert mark_answer_issues_reviewed("DS-1", "karthik", action_engine) == 1
    assert [item.kind for item in pending(action_engine)] == ["handover_draft"]
    assert len(pending(action_engine, manager="ananya")) == 1
    assert len(pending(action_engine, store="DS-2")) == 1
    assert mark_answer_issues_reviewed("DS-1", "karthik", action_engine) == 0


def test_app_owner_reuses_the_engine_across_callback_threads_and_closes_it(
    action_engine,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import Mock

    from database import session as resources
    from queries.preferences import list_definitions

    build = Mock(return_value=action_engine)
    dispose = Mock(wraps=action_engine.dispose)
    monkeypatch.setattr(resources, "build_engine", build)
    monkeypatch.setattr(action_engine, "dispose", dispose)
    with resources.database_resources():
        assert list_definitions()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(list_definitions).result()
        with resources.database_resources():
            assert list_definitions()
        build.assert_called_once_with()
        dispose.assert_not_called()
    dispose.assert_called_once_with()
