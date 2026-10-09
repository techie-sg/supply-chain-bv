from typing import Literal

from domain.chat import StoredMessage

"""Integration checks against a dedicated TEST_DATABASE_URL."""

import os
from collections.abc import Iterator
from datetime import datetime
from uuid import uuid4

import pytest
from conftest import ensure_managers
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import IntegrityError

from database.models import Conversation
from database.session import Base, build_engine, get_session
from queries.conversations import (
    append_message,
    idle_unsummarized,
    latest_conversation,
    list_conversations,
    resume_conversation,
    save_summary,
    set_title,
    start_conversation,
)


@pytest.fixture
def conversation_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated test database")
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            ensure_managers(connection)
            Base.metadata.tables["app.conversations"].create(
                connection,
                checkfirst=True,
            )
            connection.execute(delete(Conversation))
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Conversation))
        engine.dispose()


def message(who: Literal["manager", "assistant"], what: str) -> StoredMessage:
    return {"who": who, "what": what, "when": "2026-10-06T19:42:10+05:30"}


def test_new_conversation_is_empty_and_latest(conversation_engine: Engine) -> None:
    assert latest_conversation("DS-1", "karthik", conversation_engine) is None
    started = start_conversation("DS-1", "karthik", conversation_engine)
    assert started.messages == [] and started.summary is None
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None and latest.id == started.id


def test_messages_are_appended_in_order(conversation_engine: Engine) -> None:
    started = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(started.id, message("manager", "What first?"), conversation_engine)
    append_message(
        started.id,
        message("assistant", "Oldest order."),
        conversation_engine,
    )
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None
    assert latest.messages == [
        message("manager", "What first?"),
        message("assistant", "Oldest order."),
    ]
    assert latest.updated_at >= started.updated_at


def test_latest_follows_the_most_recent_update(conversation_engine: Engine) -> None:
    older = start_conversation("DS-1", "karthik", conversation_engine)
    newer = start_conversation("DS-1", "karthik", conversation_engine)
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None and latest.id == newer.id
    append_message(older.id, message("manager", "Back here"), conversation_engine)
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None and latest.id == older.id


def test_conversations_are_scoped_to_store_and_manager(
    conversation_engine: Engine,
) -> None:
    start_conversation("DS-1", "karthik", conversation_engine)
    assert latest_conversation("DS-2", "karthik", conversation_engine) is None
    assert latest_conversation("DS-1", "someone-else", conversation_engine) is None


def test_appending_to_a_missing_conversation_fails(conversation_engine: Engine) -> None:
    with pytest.raises(LookupError):
        append_message(uuid4(), message("manager", "Hi"), conversation_engine)


def test_messages_must_be_a_json_list(conversation_engine: Engine) -> None:
    with pytest.raises(IntegrityError), get_session(conversation_engine) as session:
        session.execute(
            text(
                "INSERT INTO app.conversations (store_id, manager_id, messages) "
                """VALUES ('DS-1', 'karthik', '{"who": "manager"}'::jsonb)""",
            ),
        )
    with get_session(conversation_engine) as session:
        assert session.scalars(select(Conversation)).all() == []


def test_listing_skips_empty_chats_and_shows_newest_first(
    conversation_engine: Engine,
) -> None:
    first = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(first.id, message("manager", "Rain plan?"), conversation_engine)
    second = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(second.id, message("manager", "Batching?"), conversation_engine)
    append_message(second.id, message("assistant", "Rules..."), conversation_engine)
    start_conversation("DS-1", "karthik", conversation_engine)
    start_conversation("DS-2", "karthik", conversation_engine)

    items = list_conversations("DS-1", "karthik", engine=conversation_engine)
    assert [
        (item["id"], item["first_question"], item["message_count"]) for item in items
    ] == [
        (second.id, "Batching?", 2),
        (first.id, "Rain plan?", 1),
    ]
    assert len(list_conversations("DS-1", "karthik", 1, conversation_engine)) == 1
    with pytest.raises(ValueError):
        list_conversations("DS-1", "karthik", 0, conversation_engine)


def test_resuming_preserves_message_timestamp_and_recent_order(
    conversation_engine: Engine,
) -> None:
    past = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(past.id, message("manager", "Rain plan?"), conversation_engine)
    newer = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(newer.id, message("manager", "New topic"), conversation_engine)
    before = list_conversations("DS-1", "karthik", engine=conversation_engine)

    resumed = resume_conversation(past.id, "DS-1", "karthik", conversation_engine)
    assert resumed.messages == [message("manager", "Rain plan?")]
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None and latest.id == newer.id
    assert list_conversations("DS-1", "karthik", engine=conversation_engine) == before
    append_message(past.id, message("manager", "Following up"), conversation_engine)
    assert (
        list_conversations("DS-1", "karthik", engine=conversation_engine)[0]["id"]
        == past.id
    )


def test_resuming_another_stores_or_missing_conversation_fails(
    conversation_engine: Engine,
) -> None:
    other = start_conversation("DS-2", "karthik", conversation_engine)
    with pytest.raises(LookupError):
        resume_conversation(other.id, "DS-1", "karthik", conversation_engine)
    with pytest.raises(LookupError):
        resume_conversation(uuid4(), "DS-1", "karthik", conversation_engine)


def test_title_is_set_once_without_changing_recency(
    conversation_engine: Engine,
) -> None:
    older = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(older.id, message("manager", "Rain plan?"), conversation_engine)
    newer = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(newer.id, message("manager", "Batching?"), conversation_engine)

    assert set_title(older.id, "Rain plan", conversation_engine)
    assert not set_title(older.id, "Another title", conversation_engine)
    items = list_conversations("DS-1", "karthik", engine=conversation_engine)
    assert [(item["id"], item["title"]) for item in items] == [
        (newer.id, None),
        (older.id, "Rain plan"),
    ]


def test_summary_is_saved_only_from_the_expected_position(
    conversation_engine: Engine,
) -> None:
    started = start_conversation("DS-1", "karthik", conversation_engine)
    for index in range(4):
        append_message(started.id, message("manager", f"q{index}"), conversation_engine)
    before = latest_conversation("DS-1", "karthik", conversation_engine)
    assert before is not None and before.summarized_at is None

    assert save_summary(started.id, "First", 1, None, conversation_engine)
    assert not save_summary(started.id, "Stale", 3, None, conversation_engine)
    assert save_summary(started.id, "Second", 3, 1, conversation_engine)
    after = latest_conversation("DS-1", "karthik", conversation_engine)
    assert after is not None
    assert (after.summary, after.summary_covers_to) == ("Second", 3)
    assert after.summarized_at is not None
    assert after.updated_at == before.updated_at
    assert [item["what"] for item in after.messages] == ["q0", "q1", "q2", "q3"]


def test_idle_chats_are_found_by_last_message_time_and_coverage(
    conversation_engine: Engine,
) -> None:
    def chat_with(when: str, covers_to: int | None = None):
        started = start_conversation("DS-1", "karthik", conversation_engine)
        for index in range(2):
            append_message(
                started.id,
                {"who": "manager", "what": f"q{index}", "when": when},
                conversation_engine,
            )
        if covers_to is not None:
            save_summary(started.id, "Done", covers_to, None, conversation_engine)
        return started.id

    idle = chat_with("2026-10-07T18:00:00+05:30")
    partly = chat_with("2026-10-07T18:10:00+05:30", covers_to=0)
    chat_with("2026-10-07T18:20:00+05:30", covers_to=1)
    chat_with("2026-10-07T19:50:00+05:30")
    start_conversation("DS-1", "karthik", conversation_engine)

    cutoff = datetime.fromisoformat("2026-10-07T19:30:00+05:30")
    found = idle_unsummarized(cutoff, 10, conversation_engine)
    assert [item.id for item in found] == [idle, partly]
    assert [item.id for item in idle_unsummarized(cutoff, 1, conversation_engine)] == [
        idle,
    ]
