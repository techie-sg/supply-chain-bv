"""Manager isolation, unchanged writes, proposal races and bounded raw evidence."""

import os
from uuid import uuid4

import pytest
from conftest import ensure_managers
from sqlalchemy import delete, select, text

from database.models import Conversation, ManagerPersonalization, Suggestion
from database.session import Base, build_engine
from domain.memory import SuggestionStatus
from queries.dreaming import apply_suggestion, get_suggestion, resolve_suggestion
from queries.personalization import (
    evidence_chats,
    propose_personalization,
    read_profile,
    save_profile,
)
from service.personalization import item


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Use a dedicated TEST_DATABASE_URL")
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        ensure_managers(connection)
        for table in (
            "app.conversations",
            "app.suggestions",
            "app.manager_personalization",
        ):
            Base.metadata.tables[table].create(connection, checkfirst=True)
        for model in (Suggestion, ManagerPersonalization, Conversation):
            connection.execute(delete(model))
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            for model in (Suggestion, ManagerPersonalization, Conversation):
                connection.execute(delete(model))
        engine.dispose()


def proposal(value="brief"):
    return {
        "store_id": "DS-1",
        "manager_id": "karthik",
        "kind": "personalization",
        "payload": {"code": "answer_length", "value": value},
        "reason": "Explicit preference",
        "evidence": [],
    }


def test_profile_noop_merges_removals_and_isolates_managers(engine):
    original = item("brief", "chat")
    assert save_profile("DS-1", "karthik", {"answer_length": original}, engine=engine)
    assert not save_profile(
        "DS-1",
        "karthik",
        {"answer_length": item("brief", "settings")},
        engine=engine,
    )
    assert read_profile("DS-1", "karthik", engine)["answer_length"] == original
    assert read_profile("DS-1", "ananya", engine) == {}
    assert read_profile("OTHER", "karthik", engine) == {}
    assert save_profile(
        "DS-1",
        "karthik",
        {"comparisons": item("alternatives", "settings")},
        engine=engine,
    )
    assert save_profile(
        "DS-1",
        "karthik",
        {"answer_length": item(None, "chat")},
        engine=engine,
    )
    assert (
        read_profile("DS-1", "karthik", engine)["comparisons"]["value"]
        == "alternatives"
    )
    assert read_profile("DS-1", "karthik", engine)["answer_length"]["value"] is None
    assert not propose_personalization(proposal(), engine)
    with pytest.raises(LookupError):
        save_profile("OTHER", "karthik", {"answer_length": original}, engine=engine)


def test_blank_explicit_removal_blocks_old_proposals_without_repeated_writes(engine):
    assert propose_personalization(proposal(), engine)
    assert save_profile(
        "DS-1",
        "karthik",
        {"answer_length": item(None, "chat")},
        engine=engine,
    )
    assert not save_profile(
        "DS-1",
        "karthik",
        {"answer_length": item(None, "chat")},
        engine=engine,
    )
    assert not propose_personalization(proposal(), engine)
    with engine.connect() as connection:
        assert connection.execute(select(Suggestion.status)).scalar_one() == "dismissed"


def test_stale_profile_edit_preserves_newer_value(engine):
    assert save_profile(
        "DS-1",
        "karthik",
        {"answer_length": item("brief", "chat")},
        engine=engine,
    )
    with pytest.raises(ValueError, match="changed"):
        save_profile(
            "DS-1",
            "karthik",
            {"answer_length": item("detailed", "settings")},
            expected={},
            engine=engine,
        )
    assert read_profile("DS-1", "karthik", engine)["answer_length"]["value"] == "brief"


def test_proposal_duplicate_dismiss_and_accept_are_atomic_and_scoped(engine):
    row = proposal()
    assert propose_personalization(row, engine)
    assert not propose_personalization(row, engine)
    with engine.connect() as connection:
        suggestion_id = connection.execute(select(Suggestion.id)).scalar_one()
    with pytest.raises(LookupError):
        apply_suggestion(
            suggestion_id,
            "DS-1",
            "ananya",
            row["payload"],
            personalization={"answer_length": item("brief", "suggestion")},
            engine=engine,
        )
    assert read_profile("DS-1", "ananya", engine) == {}
    apply_suggestion(
        suggestion_id,
        "DS-1",
        "karthik",
        row["payload"],
        personalization={"answer_length": item("brief", "suggestion")},
        engine=engine,
    )
    assert read_profile("DS-1", "karthik", engine)["answer_length"]["value"] == "brief"
    assert get_suggestion(suggestion_id, "DS-1", "karthik", engine).status == "accepted"
    with pytest.raises(LookupError):
        apply_suggestion(
            suggestion_id,
            "DS-1",
            "karthik",
            row["payload"],
            personalization={"answer_length": item("brief", "suggestion")},
            engine=engine,
        )
    other = {**row, "payload": {"code": "comparisons", "value": "alternatives"}}
    assert propose_personalization(other, engine)
    with engine.connect() as connection:
        other_id = connection.execute(
            select(Suggestion.id).where(Suggestion.status == "pending"),
        ).scalar_one()
    assert resolve_suggestion(
        other_id,
        SuggestionStatus.DISMISSED,
        "DS-1",
        "karthik",
        engine,
    )
    assert not propose_personalization(other, engine)


def test_evidence_query_clips_short_chats_and_limits_large_histories(engine):
    with engine.begin() as connection:
        for manager_id, count in [("karthik", 2), ("karthik", 100), ("ananya", 3)]:
            messages = [
                {
                    "who": "manager",
                    "what": str(index),
                    "when": "2026-10-09T10:00:00+05:30",
                }
                for index in range(count)
            ]
            connection.execute(
                Conversation.__table__.insert().values(
                    id=uuid4(),
                    store_id="DS-1",
                    manager_id=manager_id,
                    messages=messages,
                    summary="Summary",
                ),
            )
    chats = evidence_chats("DS-1", "karthik", engine)
    assert len(chats) == 2
    assert sorted((entry["start"], len(entry["messages"])) for entry in chats) == [
        (0, 2),
        (40, 60),
    ]
    long = next(entry for entry in chats if entry["start"])
    assert long["messages"][0]["what"] == "40" and long["messages"][-1]["what"] == "99"


def test_personalization_accept_service_validates_and_keeps_source(engine):
    from service.suggestions import accept_suggestion

    row = proposal()
    source_id = str(uuid4())
    evidence = {
        "conversation_id": source_id,
        "message_index": 0,
        "quote": "I prefer concise answers.",
    }
    row["evidence"] = [evidence]
    assert propose_personalization(row, engine)
    with engine.connect() as connection:
        suggestion_id = connection.execute(select(Suggestion.id)).scalar_one()
    assert (
        accept_suggestion(suggestion_id, "DS-1", "karthik", engine=engine)
        == "Saved. Answer length: Brief."
    )
    saved = read_profile("DS-1", "karthik", engine)["answer_length"]
    assert saved["conversation_id"] == source_id and saved["source"] == "suggestion"
    assert saved["quote"] == row["evidence"][0]["quote"]


def test_invalid_personalization_proposal_is_not_applied(engine):
    from service.suggestions import accept_suggestion

    row = proposal("unknown")
    assert propose_personalization(row, engine)
    with engine.connect() as connection:
        suggestion_id = connection.execute(select(Suggestion.id)).scalar_one()
    with pytest.raises(ValueError, match="Invalid value"):
        accept_suggestion(suggestion_id, "DS-1", "karthik", engine=engine)
    assert read_profile("DS-1", "karthik", engine) == {}
    assert get_suggestion(suggestion_id, "DS-1", "karthik", engine).status == "pending"
