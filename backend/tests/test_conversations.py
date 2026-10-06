from datetime import datetime
from uuid import uuid4

import pytest

from database.models import Conversation
from service import conversations


class FakeStore:
    """In-memory stand-in for queries.conversations."""

    def __init__(self) -> None:
        self.rows: list[Conversation] = []

    def latest(self, store_id, manager_id, engine=None):
        matches = [
            row
            for row in self.rows
            if row.store_id == store_id and row.manager_id == manager_id
        ]
        return matches[-1] if matches else None

    def start(self, store_id, manager_id, engine=None):
        row = Conversation(
            id=uuid4(),
            store_id=store_id,
            manager_id=manager_id,
            messages=[],
        )
        self.rows.append(row)
        return row

    def append(self, conversation_id, message, engine=None):
        row = next(row for row in self.rows if row.id == conversation_id)
        row.messages = [*row.messages, message]
        self.touch(row)

    def touch(self, row):
        """Keep `rows` ordered by recency, like ordering by updated_at."""
        self.rows.remove(row)
        self.rows.append(row)

    def list(self, store_id, manager_id, limit=20, engine=None):
        return [
            {"id": row.id, "first_question": row.messages[0]["what"]}
            for row in reversed(self.rows)
            if row.messages
        ][:limit]

    def resume(self, conversation_id, store_id, manager_id, engine=None):
        row = next((row for row in self.rows if row.id == conversation_id), None)
        if row is None:
            raise LookupError(conversation_id)
        self.touch(row)
        return row


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(conversations, "latest_conversation", fake.latest)
    monkeypatch.setattr(conversations, "start_conversation", fake.start)
    monkeypatch.setattr(conversations, "append_message", fake.append)
    monkeypatch.setattr(conversations, "list_conversations", fake.list)
    monkeypatch.setattr(conversations, "resume_conversation", fake.resume)
    return fake


def service(answer) -> conversations.ConversationService:
    return conversations.ConversationService("DS-1", "karthik", answer=answer)


def test_first_question_starts_a_conversation_and_stores_both_messages(store) -> None:
    seen = []

    def answer(question, *, history):
        seen.append((question, history))
        return "Check the oldest order."

    assert service(answer).ask("What first?") == "Check the oldest order."
    assert seen == [("What first?", [])]
    [row] = store.rows
    assert [(m["who"], m["what"]) for m in row.messages] == [
        ("manager", "What first?"),
        ("assistant", "Check the oldest order."),
    ]


def test_follow_up_uses_stored_history_in_order(store) -> None:
    seen = []

    def answer(question, *, history):
        seen.append(history)
        return f"reply to {question}"

    chat = service(answer)
    chat.ask("First")
    chat.ask("Second")
    assert seen[1] == [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "reply to First"},
    ]
    assert len(store.rows) == 1


def test_failed_answer_keeps_the_question_and_appends_no_reply(store) -> None:
    def fail(question, *, history):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        service(fail).ask("Can I batch these?")
    [row] = store.rows
    assert [m["who"] for m in row.messages] == ["manager"]
    assert row.messages[0]["what"] == "Can I batch these?"


def test_new_conversation_starts_with_empty_history(store) -> None:
    seen = []

    def answer(question, *, history):
        seen.append(history)
        return "reply"

    chat = service(answer)
    chat.ask("Before clearing")
    chat.start_new()
    chat.ask("After clearing")
    assert seen[-1] == []
    assert len(store.rows) == 2
    assert chat.history() == [
        {"role": "user", "content": "After clearing"},
        {"role": "assistant", "content": "reply"},
    ]


def test_history_does_not_create_a_conversation(store) -> None:
    chat = service(lambda question, *, history: "unused")
    assert chat.history() == [] and chat.current_id() is None
    assert store.rows == []


def test_messages_record_who_what_and_an_ist_timestamp() -> None:
    message = conversations.new_message("manager", "Hi")
    assert message["who"] == "manager" and message["what"] == "Hi"
    offset = datetime.fromisoformat(message["when"]).utcoffset()
    assert offset is not None and offset.total_seconds() == 5.5 * 3600


def test_ui_entry_points_use_the_demo_store_and_manager(store, monkeypatch) -> None:
    monkeypatch.setattr(
        conversations,
        "answer_question",
        lambda question, *, history: "reply",
    )
    assert conversations.ask_question("Hello") == "reply"
    assert conversations.conversation_history()[0]["content"] == "Hello"
    conversations.start_new_conversation()
    assert conversations.conversation_history() == []
    assert {(row.store_id, row.manager_id) for row in store.rows} == {
        (conversations.DEMO_STORE_ID, conversations.DEMO_MANAGER_ID),
    }


def test_resumed_conversation_is_continued_by_the_next_question(store) -> None:
    seen = []

    def answer(question, *, history):
        seen.append(history)
        return f"reply to {question}"

    chat = service(answer)
    chat.ask("Rain plan?")
    first = store.rows[-1].id
    chat.start_new()
    chat.ask("Batching?")
    assert [item["first_question"] for item in chat.past()] == [
        "Batching?",
        "Rain plan?",
    ]

    assert chat.resume(first) == [
        {"role": "user", "content": "Rain plan?"},
        {"role": "assistant", "content": "reply to Rain plan?"},
    ]
    chat.ask("And now?")
    assert seen[-1][0]["content"] == "Rain plan?"
    assert [m["what"] for m in store.rows[-1].messages][-1] == "reply to And now?"
    assert chat.past()[0]["id"] == first


def test_resuming_an_unknown_conversation_fails(store) -> None:
    with pytest.raises(LookupError):
        service(lambda question, *, history: "unused").resume(uuid4())


def test_ui_browse_entry_points(store, monkeypatch) -> None:
    monkeypatch.setattr(
        conversations,
        "answer_question",
        lambda question, *, history: "reply",
    )
    conversations.ask_question("Earlier")
    earlier_id = conversations.current_conversation_id()
    conversations.start_new_conversation()
    assert conversations.current_conversation_id() not in (None, earlier_id)
    [item] = conversations.past_conversations()
    assert str(item["id"]) == earlier_id
    assert item["first_question"] == "Earlier"
    assert conversations.resume_past_conversation(str(item["id"]))[0] == {
        "role": "user",
        "content": "Earlier",
    }
