from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from database.models import Conversation
from service import conversations
from service.preferences import PreferenceService


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
            and row.store_id == store_id
            and row.manager_id == manager_id
        ][:limit]

    def resume(self, conversation_id, store_id, manager_id, engine=None):
        row = next(
            (
                row
                for row in self.rows
                if row.id == conversation_id
                and row.store_id == store_id
                and row.manager_id == manager_id
            ),
            None,
        )
        if row is None:
            raise LookupError(conversation_id)
        return row


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(conversations, "latest_conversation", fake.latest)
    monkeypatch.setattr(conversations, "start_conversation", fake.start)
    monkeypatch.setattr(conversations, "append_message", fake.append)
    # Keep tests off any real database: no handover notes.
    monkeypatch.setattr(conversations, "handover_block", lambda store_id: None)
    monkeypatch.setattr(conversations, "memory_block", lambda manager_id: None)
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
    assert [(item["who"], item["what"]) for item in chat.history()] == [
        ("manager", "After clearing"),
        ("assistant", "reply"),
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


@pytest.fixture(autouse=True)
def no_scenario_loaded(monkeypatch) -> None:
    """Default to the playbook-only path with no stored settings or scenario."""
    monkeypatch.setattr(conversations, "current_scenario", lambda: None)
    monkeypatch.setattr(PreferenceService, "effective", lambda self: [])


def test_ui_entry_points_use_the_demo_store_and_manager(store, monkeypatch) -> None:
    seen = []

    def answer(
        question,
        *,
        history,
        preferences,
        summary=None,
        handover=None,
        memory=None,
        tools=(),
    ):
        seen.append(preferences)
        assert [tool.name for tool in tools] == ["propose_setting_change"]
        return "reply"

    monkeypatch.setattr(conversations, "answer_question", answer)
    assert conversations.ask_question("Hello") == ("reply", [])
    assert conversations.conversation_history()[0]["what"] == "Hello"
    conversations.start_new_conversation()
    assert conversations.conversation_history() == []
    assert {(row.store_id, row.manager_id) for row in store.rows} == {
        (conversations.DEMO_STORE_ID, conversations.DEMO_MANAGER_ID),
    }
    assert {(item.store_id, item.manager_id) for item in seen} == {
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

    assert [(item["who"], item["what"]) for item in chat.resume(first)] == [
        ("manager", "Rain plan?"),
        ("assistant", "reply to Rain plan?"),
    ]
    assert [item["first_question"] for item in chat.past()] == [
        "Batching?",
        "Rain plan?",
    ]
    chat.ask("And now?")
    assert seen[-1][0]["content"] == "Rain plan?"
    assert [m["what"] for m in store.rows[-1].messages][-1] == "reply to And now?"
    assert chat.past()[0]["id"] == first


def test_resuming_an_unknown_conversation_fails(store) -> None:
    with pytest.raises(LookupError):
        service(lambda question, *, history: "unused").resume(uuid4())


def test_explicit_chat_selection_survives_newer_activity_in_another_chat(store):
    first = service(lambda question, **kwargs: "First answer")
    first.ask("Rain plan?")
    first_id = first.current_id()
    second = service(lambda question, **kwargs: "Second answer")
    second.start_new()
    second.ask("Backlog plan?")
    second_id = second.current_id()
    selected = conversations.ConversationService(
        "DS-1",
        "karthik",
        answer=lambda question, **kwargs: "Rain follow-up",
        conversation_id=str(first_id),
    )
    assert selected.history()[0]["what"] == "Rain plan?"
    assert selected.past()[0]["id"] == second_id
    assert selected.current_id() == first_id
    selected.ask("What next?")
    assert selected.past()[0]["id"] == first_id
    assert second.history()[0]["what"] == "Backlog plan?"
    assert len(second.history()) == 2


def test_ui_browse_entry_points(store, monkeypatch) -> None:
    monkeypatch.setattr(
        conversations,
        "answer_question",
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(): (
            "reply"
        ),
    )
    conversations.ask_question("Earlier")
    earlier_id = conversations.current_conversation_id()
    conversations.start_new_conversation()
    assert conversations.current_conversation_id() not in (None, earlier_id)
    [item] = conversations.past_conversations()
    assert str(item["id"]) == earlier_id
    assert item["first_question"] == "Earlier"
    first = conversations.resume_past_conversation(str(item["id"]))[0]
    assert (first["who"], first["what"]) == ("manager", "Earlier")


def test_first_answer_gets_a_clean_title_once(store, monkeypatch) -> None:
    calls, saved = [], []

    def titler(question, answer):
        calls.append((question, answer))
        return '"Title: Rain backlog with two riders."\nmore text'

    def set_title(conversation_id, title, engine=None):
        saved.append(title)
        store.rows[-1].title = title
        return True

    monkeypatch.setattr(conversations, "set_title", set_title)
    chat = conversations.ConversationService(
        "DS-1",
        "karthik",
        answer=lambda question, *, history: "Call in the standby rider.",
        titler=titler,
    )
    assert chat.title_latest() is None
    chat.ask("It's pouring and riders are short")
    assert chat.title_latest() == "Rain backlog with two riders"
    assert chat.title_latest() is None
    assert calls == [
        ("It's pouring and riders are short", "Call in the standby rider."),
    ]
    assert saved == ["Rain backlog with two riders"]


def test_title_failures_leave_the_chat_untitled(store, monkeypatch) -> None:
    def failing(question, answer):
        raise RuntimeError("provider down")

    chat = conversations.ConversationService(
        "DS-1",
        "karthik",
        answer=lambda question, *, history: "reply",
        titler=failing,
    )
    chat.ask("Rain plan?")
    monkeypatch.setattr(conversations, "set_title", lambda *args, **kwargs: True)
    assert chat.title_latest() is None
    blank = conversations.ConversationService(
        "DS-1",
        "karthik",
        answer=lambda question, *, history: "reply",
        titler=lambda question, answer: "  \n",
    )
    assert blank.title_latest() is None
    assert (
        conversations.ConversationService(
            "DS-1",
            "karthik",
            answer=lambda question, *, history: "reply",
        ).title_latest()
        is None
    )


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Batching frozen orders", "Batching frozen orders"),
        ("# Rider break limits!", "Rider break limits"),
        ("**SLA dip after rain**", "SLA dip after rain"),
        ("", None),
        ("word " * 30, ("word " * 12).strip()),
    ],
)
def test_clean_title(raw, title) -> None:
    assert conversations.clean_title(raw) == title


def test_ui_title_entry_point_uses_the_configured_model(store, monkeypatch) -> None:
    seen = []

    class Model:
        def generate(self, system_prompt, user_message):
            seen.append((system_prompt, user_message))
            return "Rain plan"

    monkeypatch.setattr(conversations, "create_llm_service", lambda: Model())
    monkeypatch.setattr(
        conversations,
        "answer_question",
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(): (
            "Use the standby rider."
        ),
    )
    monkeypatch.setattr(conversations, "set_title", lambda *args, **kwargs: True)
    conversations.ask_question("Rain plan?")
    assert conversations.title_latest_conversation() == "Rain plan"
    system_prompt, user_message = seen[0]
    assert "2 to 6 words" in system_prompt
    assert user_message == "Manager: Rain plan?\n\nAssistant: Use the standby rider."


def test_a_summarized_chat_sends_the_summary_and_only_recent_messages(store) -> None:
    seen = []

    def answer(question, *, history, summary=None):
        seen.append((history, summary))
        return "reply"

    chat = service(answer)
    for index in range(10):
        chat.ask(f"q{index}")
    row = store.rows[-1]
    row.summary, row.summary_covers_to = "Earlier: rain backlog.", 19
    chat.ask("And now?")
    history, summary = seen[-1]
    assert summary == "Earlier: rain backlog."
    assert [item["content"] for item in history] == [
        "q7",
        "reply",
        "q8",
        "reply",
        "q9",
        "reply",
    ]
    assert seen[0][1] is None


def test_with_a_scenario_loaded_the_agent_gets_guidance_settings_and_summary(
    monkeypatch,
) -> None:
    seen = {}
    prefs = SimpleNamespace(effective=list)

    def prepare(question, **kwargs):
        seen["prepare"] = (question, kwargs)
        return "PREPARED"

    def agent(question, **kwargs):
        seen["agent"] = (question, kwargs)
        return {"answer": "from tools", "trace": [{"tool": "get_live_dispatch_status"}]}

    monkeypatch.setattr(conversations, "current_scenario", lambda: {"store_id": "S-9"})
    monkeypatch.setattr(conversations, "prepare_message", prepare)
    monkeypatch.setattr(conversations, "run_agent", agent)
    monkeypatch.setattr(conversations, "demo_preferences", lambda: prefs)

    reply, trace = conversations._answer("Why late?", history=[], summary="earlier")

    assert reply == "from tools"
    assert trace["tools"] == [{"tool": "get_live_dispatch_status"}]
    assert seen["prepare"][1]["summary"] == "earlier"
    assert seen["prepare"][1]["preferences"] is prefs
    assert seen["agent"][1]["store_id"] == "S-9"
    assert seen["agent"][1]["user_message"] == "PREPARED"


def test_with_a_scenario_but_no_guidance_the_agent_is_not_started(monkeypatch) -> None:
    monkeypatch.setattr(conversations, "current_scenario", lambda: {"store_id": "S-9"})
    monkeypatch.setattr(conversations, "prepare_message", lambda *a, **k: None)
    monkeypatch.setattr(
        conversations,
        "demo_preferences",
        lambda: SimpleNamespace(effective=list),
    )
    monkeypatch.setattr(
        conversations,
        "run_agent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent should not run")),
    )
    reply, trace = conversations._answer("Why late?", history=[])
    assert "could not find relevant guidance" in reply
    assert trace == {"tools": [], "preferences": []}


def test_the_reply_is_stored_with_its_trace_and_returned_to_the_ui(store) -> None:
    trace = {"tools": [{"tool": "get_live_dispatch_status"}], "preferences": []}
    chat = service(lambda question, *, history: ("tool reply", trace))
    assert chat.ask_traced("Queue?") == ("tool reply", trace)
    assert chat.history()[-1]["trace"] == trace
    assert "trace" not in chat.history()[0]


def test_summary_view_reports_coverage(store) -> None:
    chat = service(lambda question, *, history, summary=None: "reply")
    assert chat.summary_view() is None
    chat.start_new()
    assert chat.summary_view() is None
    for index in range(5):
        chat.ask(f"q{index}")
    assert chat.summary_view() == {
        "summary": None,
        "covered": 0,
        "total": 10,
        "summarized_at": None,
    }
    row = store.rows[-1]
    when = datetime(2026, 10, 7, 19, 42, tzinfo=conversations.TIMEZONE)
    row.summary, row.summary_covers_to, row.summarized_at = "- Rain plan.", 5, when
    assert chat.summary_view() == {
        "summary": "- Rain plan.",
        "covered": 6,
        "total": 10,
        "summarized_at": when,
    }


def test_ui_summary_entry_point_uses_the_open_chat(store, monkeypatch) -> None:
    monkeypatch.setattr(
        conversations,
        "answer_question",
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(): (
            "reply"
        ),
    )
    conversations.ask_question("Rain plan?")
    view = conversations.conversation_summary()
    assert view is not None and view["summary"] is None and view["total"] == 2
    store.rows[-1].summary, store.rows[-1].summary_covers_to = "- Rain plan.", 1
    view = conversations.conversation_summary()
    assert view is not None and view["covered"] == 2


def test_note_is_stored_as_an_assistant_message_in_the_open_chat(store) -> None:
    chat = service(lambda question, history: "reply")
    assert chat.note("Saved.") is None
    chat.ask("Hello")
    note = chat.note("Saved. SLA dip: on, below 85%.")
    assert note is not None and note["who"] == "assistant"
    assert [message["what"] for message in chat.history()] == [
        "Hello",
        "reply",
        "Saved. SLA dip: on, below 85%.",
    ]


def test_ui_ask_returns_the_changes_the_assistant_proposed(
    store,
    preference_store,
    monkeypatch,
) -> None:
    def answer(
        question,
        *,
        history,
        preferences,
        summary=None,
        handover=None,
        memory=None,
        tools=(),
    ):
        [tool] = tools
        tool.run({"code": "sla_dip_alert", "action": "set", "value": 85})
        return "Proposed: SLA dip below 85%. Press Confirm to save it."

    monkeypatch.setattr(conversations, "answer_question", answer)
    reply, [change] = conversations.ask_question("Alert me if SLA drops below 85")
    assert reply.startswith("Proposed")
    assert (change.code, change.value) == ("sla_dip_alert", 85)
    # Nothing is saved until the manager confirms.
    assert preference_store.rows == []
    conversations.add_note("Saved.")
    assert conversations.conversation_history()[-1]["what"] == "Saved."


def test_selected_chat_cannot_be_read_or_written_by_another_manager(store):
    owner = service(lambda question, **kwargs: "Answer")
    owner.ask("Rain plan?")
    conversation_id = str(owner.current_id())
    other = conversations.ConversationService(
        "DS-1",
        "other-manager",
        answer=lambda question, **kwargs: "Wrong answer",
        conversation_id=conversation_id,
    )
    with pytest.raises(LookupError):
        other.history()
    with pytest.raises(LookupError):
        other.ask("Follow-up")
    assert len(owner.history()) == 2
