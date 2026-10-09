from typing import Literal

from domain.chat import StoredMessage

"""Integration checks against a dedicated TEST_DATABASE_URL."""

import os
from collections.abc import Iterator

import pytest
from conftest import ensure_managers
from sqlalchemy import Engine, delete, text

from database.models import Conversation, HandoverNote, Shift, Suggestion
from database.session import Base, build_engine
from domain.memory import SuggestionStatus
from queries.conversations import (
    append_message,
    resume_conversation,
    start_conversation,
)
from queries.dreaming import (
    add_suggestions,
    advance_dreamed_to,
    conversations_to_review,
    get_suggestion,
    list_suggestions,
    recent_conversations,
    replace_handover_draft,
    resolve_suggestion,
)


@pytest.fixture
def review_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    tables = (
        "app.shifts",
        "app.handover_notes",
        "app.conversations",
        "app.suggestions",
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            ensure_managers(connection)
            for table in tables:
                Base.metadata.tables[table].create(connection, checkfirst=True)
            for model in (Suggestion, HandoverNote, Shift, Conversation):
                connection.execute(delete(model))
        yield engine
    finally:
        with engine.begin() as connection:
            for model in (Suggestion, HandoverNote, Shift, Conversation):
                connection.execute(delete(model))
        engine.dispose()


def message(who: Literal["manager", "assistant"], what: str) -> StoredMessage:
    return {"who": who, "what": what, "when": "2026-10-08T19:00:00+05:30"}


def suggestion(kind: str, payload: dict, status: str = "pending") -> dict:
    return {
        "store_id": "DS-1",
        "manager_id": "karthik",
        "kind": kind,
        "payload": payload,
        "reason": "r",
        "evidence": [],
        "status": status,
    }


def test_chats_are_reviewed_from_their_position(review_engine: Engine) -> None:
    started = start_conversation("DS-1", "karthik", review_engine)
    start_conversation("DS-1", "karthik", review_engine)
    for what in ("q", "a"):
        append_message(started.id, message("manager", what), review_engine)
    assert [item.id for item in conversations_to_review(review_engine)] == [started.id]
    assert advance_dreamed_to(started.id, 1, None, review_engine)
    assert not advance_dreamed_to(started.id, 1, None, review_engine)
    assert conversations_to_review(review_engine) == []
    reloaded = resume_conversation(started.id, "DS-1", "karthik", review_engine)
    assert reloaded is not None and reloaded.dreamed_to == 1
    assert [
        item.id for item in recent_conversations("DS-1", "karthik", 5, review_engine)
    ] == [
        started.id,
    ]


def test_suggestions_are_listed_resolved_and_replaced(review_engine: Engine) -> None:
    assert add_suggestions([], review_engine) == 0
    add_suggestions(
        [
            suggestion("handover_draft", {"shift_id": "a", "note": "Old"}),
            suggestion("handover_draft", {"shift_id": "b", "note": "Other shift"}),
            suggestion("setting", {"code": "sla_dip_alert"}),
        ],
        review_engine,
    )
    replace_handover_draft(
        suggestion("handover_draft", {"shift_id": "a", "note": "New"}),
        review_engine,
    )
    pending = list_suggestions(
        "DS-1",
        "karthik",
        ["handover_draft", "setting"],
        ["pending"],
        engine=review_engine,
    )
    assert sorted(item.kind for item in pending) == [
        "handover_draft",
        "handover_draft",
        "setting",
    ]
    setting = next(item for item in pending if item.kind == "setting")
    assert resolve_suggestion(
        setting.id,
        SuggestionStatus.ACCEPTED,
        "DS-1",
        "karthik",
        review_engine,
    )
    assert not resolve_suggestion(
        setting.id,
        SuggestionStatus.DISMISSED,
        "DS-1",
        "karthik",
        review_engine,
    )
    reloaded = get_suggestion(setting.id, "DS-1", "karthik", review_engine)
    assert reloaded is not None and reloaded.status == "accepted"


def test_shifts_open_once_keep_notes_until_ended(review_engine: Engine) -> None:
    from datetime import timedelta

    from queries.shifts import (
        end_shift,
        ended_shifts,
        handover_by_note,
        latest_handover,
        latest_shift,
        note_for,
        open_shift,
        save_note,
        start_shift,
    )

    assert latest_shift("karthik", review_engine) is None
    assert latest_handover("DS-1", review_engine) is None
    first = start_shift("DS-1", "karthik", review_engine)
    assert start_shift("DS-1", "karthik", review_engine).id == first.id
    assert save_note(first.id, "karthik", "Draft", review_engine)
    assert save_note(first.id, "karthik", "Edited", review_engine)
    assert note_for(first.id, review_engine) == "Edited"
    assert not save_note(first.id, "ananya", "Not theirs", review_engine)
    result = end_shift(first.id, "karthik", "Final", review_engine)
    assert result is not None
    ended, note_id = result
    assert end_shift(first.id, "karthik", "Again", review_engine) is None
    assert not save_note(first.id, "karthik", "Too late", review_engine)
    assert note_for(first.id, review_engine) == "Final"
    assert open_shift("karthik", review_engine) is None
    second = start_shift("DS-1", "karthik", review_engine)
    assert second.id != first.id
    current = latest_shift("karthik", review_engine)
    assert current is not None and current.id == second.id
    empty = start_shift("DS-1", "ananya", review_engine)
    end_shift(empty.id, "ananya", "  ", review_engine)
    found = latest_handover("DS-1", review_engine)
    assert found is not None
    shift, manager, note = found
    assert (shift.id, note, manager.manager_id) == (first.id, "Final", "karthik")
    by_note = handover_by_note(note_id, review_engine)
    assert by_note is not None and by_note[2] == "Final"
    rows = ended_shifts("DS-1", ended - timedelta(days=1), 10, review_engine)
    assert [(item.manager_id, note) for item, _, note in rows] == [
        ("ananya", "  "),
        ("karthik", "Final"),
    ]
