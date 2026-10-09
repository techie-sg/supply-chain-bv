from typing import Literal

from domain.chat import StoredMessage

"""Integration checks against a dedicated TEST_DATABASE_URL."""

import os
from collections.abc import Iterator
from datetime import date

import pytest
from conftest import ensure_managers
from sqlalchemy import Engine, delete, text

from database.models import Conversation, HandoverNote, Suggestion
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
    latest_handover_notes,
    list_suggestions,
    recent_conversations,
    replace_handover_draft,
    resolve_suggestion,
    save_handover_note,
)


@pytest.fixture
def review_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    tables = ("app.conversations", "app.handover_notes", "app.suggestions")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            ensure_managers(connection)
            for table in tables:
                Base.metadata.tables[table].create(connection, checkfirst=True)
            for model in (Suggestion, HandoverNote, Conversation):
                connection.execute(delete(model))
        yield engine
    finally:
        with engine.begin() as connection:
            for model in (Suggestion, HandoverNote, Conversation):
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
            suggestion("handover_draft", {"shift": "2026-10-08", "note": "Old"}),
            suggestion("handover_draft", {"shift": "2026-10-07", "note": "Other day"}),
            suggestion("setting", {"code": "sla_dip_alert"}),
        ],
        review_engine,
    )
    replace_handover_draft(
        suggestion("handover_draft", {"shift": "2026-10-08", "note": "New"}),
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


def test_latest_shift_notes_are_returned_together(review_engine: Engine) -> None:
    assert latest_handover_notes("DS-1", review_engine) == []
    save_handover_note("DS-1", "karthik", date(2026, 10, 7), "Older", review_engine)
    save_handover_note("DS-1", "karthik", date(2026, 10, 8), "First", review_engine)
    save_handover_note("DS-1", "karthik", date(2026, 10, 8), "Second", review_engine)
    save_handover_note(
        "DS-2",
        "karthik",
        date(2026, 10, 9),
        "Other store",
        review_engine,
    )
    notes = latest_handover_notes("DS-1", review_engine)
    assert [(note.shift, note.note) for note in notes] == [
        (date(2026, 10, 8), "First"),
        (date(2026, 10, 8), "Second"),
    ]
