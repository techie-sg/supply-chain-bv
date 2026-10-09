"""Manager isolation, unchanged writes, proposal races and bounded raw evidence."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import ensure_managers
from sqlalchemy import delete, select, text

from database.models import Conversation, ManagerPersonalization, Suggestion
from database.session import Base, build_engine, get_session
from domain.memory import SuggestionStatus
from queries.dreaming import apply_suggestion, get_suggestion, resolve_suggestion
from queries.personalization import (
    evidence_chats,
    finish_review,
    pending_reviews,
    read_profile,
    save_profile,
)
from service.personalization import item


def summarized_chat(engine, count=4):
    row = Conversation(
        store_id="DS-1",
        manager_id="karthik",
        messages=[
            {
                "who": "manager",
                "what": "I prefer concise answers.",
                "when": "2026-10-09T19:00:00+05:30",
            }
            for _ in range(count)
        ],
        summary="Summary is already saved.",
        summary_covers_to=count - 1,
    )
    with get_session(engine) as session:
        session.add(row)
    return row


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


def proposal(value="brief", chat=None, index=0):
    return {
        "store_id": "DS-1",
        "manager_id": "karthik",
        "kind": "personalization",
        "payload": {"code": "answer_length", "value": value},
        "reason": "Explicit preference",
        "evidence": [
            {
                "conversation_id": str(chat.id if chat else uuid4()),
                "message_index": index,
                "quote": "I prefer concise answers.",
            },
        ],
    }


def queue_proposal(row, engine):
    """Seed a legacy suggestion, created before automatic dreaming saves."""
    with get_session(engine) as session:
        session.add(Suggestion(**row))
    return True


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
    chat = summarized_chat(engine)
    assert (
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            3,
            [proposal(chat=chat)],
            engine,
        )
        == 0
    )
    with pytest.raises(LookupError):
        save_profile("OTHER", "karthik", {"answer_length": original}, engine=engine)


def test_blank_explicit_removal_blocks_old_proposals_without_repeated_writes(engine):
    assert queue_proposal(proposal(), engine)
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
    chat = summarized_chat(engine)
    assert (
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            3,
            [proposal(chat=chat)],
            engine,
        )
        == 0
    )
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
    assert queue_proposal(row, engine)
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
    assert queue_proposal(other, engine)
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
    assert get_suggestion(other_id, "DS-1", "karthik", engine).status == "dismissed"


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
    assert queue_proposal(row, engine)
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
    assert queue_proposal(row, engine)
    with engine.connect() as connection:
        suggestion_id = connection.execute(select(Suggestion.id)).scalar_one()
    with pytest.raises(ValueError, match="Invalid value"):
        accept_suggestion(suggestion_id, "DS-1", "karthik", engine=engine)
    assert read_profile("DS-1", "karthik", engine) == {}
    assert get_suggestion(suggestion_id, "DS-1", "karthik", engine).status == "pending"


def test_progress_and_preferences_commit_together_without_changing_chat_recency(engine):
    chat = summarized_chat(engine)
    assert (
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            1,
            [proposal(chat=chat)],
            engine,
        )
        == 1
    )
    saved = read_profile("DS-1", "karthik", engine)["answer_length"]
    assert saved["value"] == "brief" and saved["source"] == "dreaming"
    assert saved["conversation_id"] == str(chat.id)
    assert saved["evidence"][0]["quote"] == "I prefer concise answers."
    with get_session(engine) as session:
        row = session.get(Conversation, chat.id)
        assert row.personalization_covers_to == 1 and row.personalized_at is not None
        assert row.updated_at == chat.updated_at
        assert row.summary_covers_to == chat.summary_covers_to == 3
        assert row.summarized_at == chat.summarized_at
        reviewed_at = row.personalized_at
    assert [row.id for row in pending_reviews(10, engine=engine)] == [chat.id]
    assert pending_reviews(10, exclude=[chat.id], engine=engine) == []
    assert (
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            3,
            [proposal("detailed")],
            engine,
        )
        is None
    )
    with get_session(engine) as session:
        row = session.get(Conversation, chat.id)
        assert row.personalization_covers_to == 1 and row.personalized_at == reviewed_at
        assert list(session.scalars(select(Suggestion))) == []
    assert finish_review(chat.id, "DS-1", "karthik", 1, 3, [], engine) == 0
    assert pending_reviews(10, engine=engine) == []


def test_failed_preference_transaction_rolls_back_profile_and_progress(engine):
    chat = summarized_chat(engine)
    invalid = {
        **proposal(chat=chat),
        "payload": {"code": "comparisons", "value": "invalid"},
    }
    with pytest.raises(ValueError):
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            3,
            [proposal(chat=chat), invalid],
            engine,
        )
    with get_session(engine) as session:
        row = session.get(Conversation, chat.id)
        assert row.personalization_covers_to is None and row.personalized_at is None
        assert list(session.scalars(select(Suggestion))) == []
    assert [row.id for row in pending_reviews(10, engine=engine)] == [chat.id]
    assert read_profile("DS-1", "karthik", engine) == {}


def test_concurrent_personalization_workers_complete_a_batch_once(engine):
    chat = summarized_chat(engine)

    def finish():
        return finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            3,
            [proposal(chat=chat)],
            engine,
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: finish(), range(2)))
    assert sorted(value for value in results if value is not None) == [1]
    assert results.count(None) == 1
    with get_session(engine) as session:
        assert session.get(Conversation, chat.id).personalization_covers_to == 3
        assert list(session.scalars(select(Suggestion))) == []
    assert read_profile("DS-1", "karthik", engine)["answer_length"]["value"] == "brief"


def test_review_progress_is_scoped_and_cannot_pass_saved_summary_coverage(engine):
    chat = summarized_chat(engine)
    assert finish_review(chat.id, "DS-1", "ananya", None, 3, [], engine) is None
    assert finish_review(chat.id, "DS-1", "karthik", None, 4, [], engine) is None
    with pytest.raises(ValueError, match="forward"):
        finish_review(chat.id, "DS-1", "karthik", 1, 1, [], engine)
    with pytest.raises(ValueError, match="another manager"):
        finish_review(
            chat.id,
            "DS-1",
            "karthik",
            None,
            1,
            [{**proposal(), "manager_id": "ananya"}],
            engine,
        )
    with get_session(engine) as session:
        assert session.get(Conversation, chat.id).personalization_covers_to is None
