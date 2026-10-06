"""Integration checks against a dedicated TEST_DATABASE_URL."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import IntegrityError

from database.models import Conversation
from database.session import Base, build_engine, get_session
from queries.conversations import (
    append_message,
    latest_conversation,
    list_conversations,
    resume_conversation,
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


def message(who: str, what: str) -> dict[str, str]:
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


def test_resuming_makes_a_past_conversation_the_latest(
    conversation_engine: Engine,
) -> None:
    past = start_conversation("DS-1", "karthik", conversation_engine)
    append_message(past.id, message("manager", "Rain plan?"), conversation_engine)
    start_conversation("DS-1", "karthik", conversation_engine)

    resumed = resume_conversation(past.id, "DS-1", "karthik", conversation_engine)
    assert resumed.messages == [message("manager", "Rain plan?")]
    latest = latest_conversation("DS-1", "karthik", conversation_engine)
    assert latest is not None and latest.id == past.id


def test_resuming_another_stores_or_missing_conversation_fails(
    conversation_engine: Engine,
) -> None:
    other = start_conversation("DS-2", "karthik", conversation_engine)
    with pytest.raises(LookupError):
        resume_conversation(other.id, "DS-1", "karthik", conversation_engine)
    with pytest.raises(LookupError):
        resume_conversation(uuid4(), "DS-1", "karthik", conversation_engine)
