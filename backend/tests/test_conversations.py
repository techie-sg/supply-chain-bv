from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from database.models import Conversation
from domain.chat import AnswerResult, AnswerTrace
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

    def selected(self, store_id, manager_id, conversation_id=None, engine=None):
        if conversation_id is not None:
            return self.resume(conversation_id, store_id, manager_id, engine)
        return self.latest(store_id, manager_id, engine)

    def selected_id(self, *args):
        row = self.selected(*args)
        return row.id if row else None

    def summary(self, *args):
        row = self.selected(*args)
        if row is None:
            return None
        return {
            "summary": row.summary,
            "summary_covers_to": row.summary_covers_to,
            "summarized_at": row.summarized_at,
            "total": len(row.messages),
        }

    def details(self, *args):
        row = self.selected(*args)
        return {
            "id": row.id,
            "title": row.title
            or next(
                (item["what"] for item in row.messages if item["who"] == "manager"),
                None,
            ),
        }

    def title_exchange(self, *args):
        row = self.selected(*args)
        if row is None:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "question": next(
                (item["what"] for item in row.messages if item["who"] == "manager"),
                None,
            ),
            "answer": next(
                (item["what"] for item in row.messages if item["who"] == "assistant"),
                None,
            ),
        }

    def answer_context(
        self,
        store_id,
        manager_id,
        recent_messages,
        conversation_id,
        engine,
    ):
        row = self.selected(store_id, manager_id, conversation_id, engine)
        if row is None:
            return None
        start = min(
            0 if row.summary_covers_to is None else row.summary_covers_to + 1,
            max(len(row.messages) - recent_messages, 0),
        )
        return {"id": row.id, "summary": row.summary, "messages": row.messages[start:]}


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
    monkeypatch.setattr(conversations, "selected_conversation_id", fake.selected_id)
    monkeypatch.setattr(conversations, "read_summary", fake.summary)
    monkeypatch.setattr(conversations, "read_details", fake.details)
    monkeypatch.setattr(conversations, "read_title_exchange", fake.title_exchange)
    monkeypatch.setattr(conversations, "read_answer_context", fake.answer_context)
    return fake


def service(answer) -> conversations.ConversationService:
    def result(question, **kwargs):
        value = answer(question, **kwargs)
        return value if isinstance(value, AnswerResult) else AnswerResult(value)

    return conversations.ConversationService("DS-1", "karthik", answer=result)


def test_first_question_starts_a_conversation_and_stores_both_messages(store) -> None:
    seen = []

    def answer(question, *, history):
        seen.append((question, history))
        return "Check the oldest order."

    assert service(answer).ask("What first?").text == "Check the oldest order."
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
def no_saved_context(monkeypatch, request) -> None:
    """Default to no handover, memory or customized settings."""
    monkeypatch.setattr(conversations, "handover_block", lambda store_id: None)
    monkeypatch.setattr(conversations, "memory_block", lambda manager_id: None)
    if "preference_store" not in request.fixturenames:
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
        alerts=None,
    ):
        seen.append(preferences)
        assert [tool.name for tool in tools] == [
            "propose_setting_change",
            "get_live_dispatch_status",
            "get_delivery_metrics",
        ]
        return "reply"

    monkeypatch.setattr(conversations, "answer_question", answer)
    assert conversations.ask_question("Hello")[:2] == ("reply", [])
    assert conversations.latest_conversation_state()[0][0]["what"] == "Hello"
    conversations.start_new_conversation()
    assert conversations.latest_conversation_state()[0] == []
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
        answer=lambda question, **kwargs: AnswerResult("Rain follow-up"),
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
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(), alerts=None: (
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
        answer=lambda question, *, history: AnswerResult("Call in the standby rider."),
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
        answer=lambda question, *, history: AnswerResult("reply"),
        titler=failing,
    )
    chat.ask("Rain plan?")
    monkeypatch.setattr(conversations, "set_title", lambda *args, **kwargs: True)
    assert chat.title_latest() is None
    blank = conversations.ConversationService(
        "DS-1",
        "karthik",
        answer=lambda question, *, history: AnswerResult("reply"),
        titler=lambda question, answer: "  \n",
    )
    assert blank.title_latest() is None
    assert (
        conversations.ConversationService(
            "DS-1",
            "karthik",
            answer=lambda question, *, history: AnswerResult("reply"),
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
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(), alerts=None: (
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


def test_dispatch_tools_share_the_rag_path_and_record_their_results(
    monkeypatch,
) -> None:
    import json

    import service.tools as tools_service

    seen = {}
    prefs = SimpleNamespace(effective=list, manager_id="m", store_id="S-9")

    def answer(question, **kwargs):
        seen.update(kwargs)
        tool = next(t for t in kwargs["tools"] if t.name == "get_live_dispatch_status")
        result = json.loads(tool.run({"store_id": "S-9"}))
        assert result["as_of"] == "2026-10-09T20:14:00+05:30"
        return "from tools"

    monkeypatch.setattr(
        tools_service,
        "get_live_dispatch_status",
        lambda store_id: {"as_of": "2026-10-09T20:14:00+05:30", "stale": False},
    )
    monkeypatch.setattr(conversations, "answer_question", answer)
    monkeypatch.setattr(conversations, "manager_preferences", lambda *a: prefs)
    result = conversations._answer("Why late?", history=[], summary="earlier")
    reply, trace = result.text, result.trace
    assert reply == "from tools"
    assert trace is not None
    assert trace["tools"][0]["tool"] == "get_live_dispatch_status"
    assert trace["tools"][0]["as_of"] == "2026-10-09T20:14:00+05:30"
    assert seen["summary"] == "earlier" and seen["preferences"] is prefs


def test_policy_questions_do_not_read_dispatch_rows(monkeypatch) -> None:
    import service.tools as tools_service

    monkeypatch.setattr(
        tools_service,
        "read_live_dispatch",
        lambda *a: (_ for _ in ()).throw(AssertionError("unexpected dispatch read")),
    )
    monkeypatch.setattr(conversations, "answer_question", lambda *a, **k: "policy")
    result = conversations._answer("Can riders jump red lights?", history=[])
    reply, trace = result.text, result.trace
    assert reply == "policy" and trace == {"tools": [], "preferences": []}


def test_setting_proposals_are_traced_without_a_scenario(monkeypatch) -> None:
    from domain.tools import Tool

    local = Tool(
        "propose_setting_change",
        "Propose a setting change.",
        {"type": "object"},
        lambda args: '{"status":"proposed","saved":false}',
    )

    def answer(question, **kwargs):
        kwargs["tools"][0].run({"code": "incentive_cap", "value": 300})
        return "Press Confirm."

    monkeypatch.setattr(conversations, "answer_question", answer)
    result = conversations._answer("Set cap to 300", history=[], tools=[local])
    reply, trace = result.text, result.trace
    assert reply == "Press Confirm."
    assert trace is not None
    assert trace["tools"][0]["tool"] == "propose_setting_change"
    assert trace is not None
    assert trace["tools"][0]["arguments"]["value"] == 300


def test_the_reply_is_stored_with_its_trace_and_returned_to_the_ui(store) -> None:
    trace: AnswerTrace = {
        "tools": [
            {
                "tool": "get_live_dispatch_status",
                "arguments": {},
                "error": None,
                "as_of": None,
                "stale": False,
            },
        ],
        "preferences": [],
    }
    chat = service(lambda question, *, history: AnswerResult("tool reply", trace))
    assert chat.ask("Queue?") == AnswerResult("tool reply", trace)
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
        lambda question, *, history, preferences, summary=None, handover=None, memory=None, tools=(), alerts=None: (
            "reply"
        ),
    )
    conversations.ask_question("Rain plan?")
    view = conversations.conversation_summary()
    assert view is not None and view["summary"] is None and view["total"] == 2
    store.rows[-1].summary, store.rows[-1].summary_covers_to = "- Rain plan.", 1
    view = conversations.conversation_summary()
    assert view is not None and view["covered"] == 2


def test_summary_and_id_do_not_load_a_full_conversation(store, monkeypatch) -> None:
    chat = service(lambda question, history: "reply")
    chat.ask("Rain plan?")

    def no_transcript():
        raise AssertionError("Summary and id reads must not load messages")

    monkeypatch.setattr(chat, "selected", no_transcript)
    assert chat.current_id() == store.rows[-1].id
    view = chat.summary_view()
    assert view is not None and view["total"] == 2


def test_restoring_messages_and_id_uses_one_read(store, monkeypatch) -> None:
    from unittest.mock import Mock

    row = store.start(conversations.DEMO_STORE_ID, conversations.DEMO_MANAGER_ID)
    store.append(row.id, conversations.new_message("manager", "Rain plan?"))
    read = Mock(wraps=store.latest)
    monkeypatch.setattr(conversations, "latest_conversation", read)
    messages, conversation_id = conversations.latest_conversation_state()
    assert messages == row.messages and conversation_id == str(row.id)
    read.assert_called_once()


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
        alerts=None,
    ):
        tool = next(t for t in tools if t.name == "propose_setting_change")
        tool.run({"code": "sla_dip_alert", "action": "set", "value": 85})
        return "Proposed: SLA dip below 85%. Press Confirm to save it."

    monkeypatch.setattr(conversations, "answer_question", answer)
    reply, [change], _ = conversations.ask_question("Alert me if SLA drops below 85")
    assert reply.startswith("Proposed")
    assert (change.code, change.value) == ("sla_dip_alert", 85)
    # Nothing is saved until the manager confirms.
    assert preference_store.rows == []
    conversations.add_note("Saved.")
    assert conversations.latest_conversation_state()[0][-1]["what"] == "Saved."


def test_selected_chat_cannot_be_read_or_written_by_another_manager(store):
    owner = service(lambda question, **kwargs: "Answer")
    owner.ask("Rain plan?")
    conversation_id = str(owner.current_id())
    other = conversations.ConversationService(
        "DS-1",
        "other-manager",
        answer=lambda question, **kwargs: AnswerResult("Wrong answer"),
        conversation_id=conversation_id,
    )
    with pytest.raises(LookupError):
        other.history()
    with pytest.raises(LookupError):
        other.ask("Follow-up")
    assert len(owner.history()) == 2
