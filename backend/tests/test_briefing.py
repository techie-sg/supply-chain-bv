import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from constants import TIMEZONE
from domain.chat import AnswerResult
from domain.tools import Tool
from service import briefing as briefing_service
from service import conversations
from service.briefing import briefing, is_greeting

EVENING = datetime(2026, 10, 9, 19, 30, tzinfo=TIMEZONE)
STATUS: dict[str, Any] = {
    "as_of": "2026-10-09T19:28:00+05:30",
    "stale": False,
    "queue": {
        "open_orders": 12,
        "counts_by_status": {
            "out_for_delivery": 2,
            "packed_waiting_rider": 8,
            "picking": 2,
        },
        "oldest_order_age_sec": 960,
        "oldest_packed_waiting_age_sec": 720,
        "oldest_frozen_packed_age_sec": None,
    },
    "riders": [{}] * 9,
    "summary": {
        "available_riders": 2,
        "riders_returning_within_10_min": 1,
        "pending_per_available_rider": 4.0,
    },
}


class FakePreferences:
    """Just the Greeting setting, as PreferenceService.current returns it."""

    store_id = "DS-BLR-014"
    manager_id = "karthik"

    def __init__(self, views, enabled=True) -> None:
        self.setting = SimpleNamespace(value=views, enabled=enabled)

    def current(self, code):
        assert code == "briefing"
        return self.setting


def prefs(views, enabled=True) -> Any:
    return FakePreferences(views, enabled)


@pytest.fixture
def live(monkeypatch):
    """Serve STATUS through the real tool wrapper, so calls are traced."""
    state = {"status": STATUS, "runs": 0}

    def tools(store_id):
        def run(arguments):
            state["runs"] += 1
            return json.dumps(state["status"])

        return [
            Tool("get_live_dispatch_status", "", {}, run),
            Tool("get_delivery_metrics", "", {}, run),
        ]

    monkeypatch.setattr(briefing_service, "dispatch_tools", tools)
    monkeypatch.setattr(
        briefing_service,
        "chat_handover_block",
        lambda note_id, store_id: (
            "Handover from Ananya Rao (Morning shift, ended 9 Oct, 15:43):\n- Rain."
            if note_id
            else None
        ),
    )
    return state


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hi", True),
        ("hello!", True),
        ("Good evening", True),
        ("hey team.", True),
        ("hiii", True),
        ("hi, orders are piling up", False),
        ("history of rain delays", False),
        ("What's the queue?", False),
    ],
)
def test_only_a_bare_greeting_gets_the_briefing(text, expected) -> None:
    assert is_greeting(text) is expected


def test_briefing_shows_the_chosen_views_in_their_order(live) -> None:
    text, calls = briefing(
        prefs(["order_queue", "rider_stats", "oldest_order_age"]),
        "Karthik Reddy",
        now=EVENING,
    )
    assert text == (
        "Good evening, Karthik.\n\n"
        "Your store as of 19:28.\n\n"
        "- **Order queue:** 12 open: 8 packed and waiting, 2 picking, "
        "2 out for delivery.\n"
        "- **Riders:** 2 available of 9, 1 back within 10 min; 4.0 packed orders "
        "per available rider.\n"
        "- **Oldest order:** 16 min (oldest packed 12 min).\n\n"
        "What would you like to look at?"
    )
    assert live["runs"] == 1
    assert [call["tool"] for call in calls] == ["get_live_dispatch_status"]
    assert calls[0]["as_of"] == "2026-10-09T19:28:00+05:30"


def test_handover_only_needs_no_live_call(live) -> None:
    note = uuid4()
    text, calls = briefing(
        prefs(["last_handover_note"]),
        "Ananya Rao",
        handover_note_id=note,
        now=datetime(2026, 10, 9, 8, 0, tzinfo=TIMEZONE),
    )
    assert text.startswith("Good morning, Ananya.")
    assert (
        "**Handover from Ananya Rao (Morning shift, ended 9 Oct, 15:43):**\n- Rain."
        in text
    )
    assert calls == [] and live["runs"] == 0
    text, _ = briefing(prefs(["last_handover_note"]), "Ananya Rao")
    assert "No handover note yet." in text


def test_missing_or_stale_data_is_said_plainly(live) -> None:
    live["status"] = {"error": {"code": "NO_SNAPSHOT", "message": "for the model"}}
    text, calls = briefing(prefs(["rider_stats"]), "Imran Shaikh", now=EVENING)
    assert "no scenario is loaded. Load one in Demo tools." in text
    assert "for the model" not in text and calls[0]["error"] == "NO_SNAPSHOT"
    live["status"] = {**STATUS, "stale": True}
    text, _ = briefing(prefs(["rider_stats"]), "Imran Shaikh", now=EVENING)
    assert "Your store as of 19:28. These figures are stale." in text
    live["status"] = {
        **STATUS,
        "summary": {**STATUS["summary"], "pending_per_available_rider": None},
        "queue": {**STATUS["queue"], "oldest_order_age_sec": None},
    }
    text, _ = briefing(
        prefs(["rider_stats", "oldest_order_age"]),
        "Imran Shaikh",
        now=EVENING,
    )
    assert "no rider is available right now." in text
    assert "**Oldest order:** none open." in text


def test_an_empty_or_disabled_greeting_points_to_settings(live) -> None:
    for preferences in (prefs([]), prefs(["rider_stats"], enabled=False)):
        text, calls = briefing(preferences, "Karthik Reddy", now=EVENING)
        assert "choose what to see in Settings → Greeting" in text
        assert calls == []


def test_a_greeting_is_answered_by_the_briefing_not_the_model(monkeypatch) -> None:
    preferences: Any = SimpleNamespace(
        store_id="DS-BLR-014",
        manager_id="karthik",
        effective=list,
    )
    monkeypatch.setattr(
        conversations,
        "store_managers",
        lambda store_id: [SimpleNamespace(manager_id="karthik", name="Karthik Reddy")],
    )
    seen = []

    def built(prefs, name, note_id):
        seen.append((name, note_id))
        return "Good evening, Karthik.", [{"tool": "get_live_dispatch_status"}]

    monkeypatch.setattr(conversations, "briefing", built)
    note = uuid4()
    assert conversations._briefing(preferences, note) == (
        "Good evening, Karthik.",
        [{"tool": "get_live_dispatch_status"}],
    )
    assert seen == [("Karthik Reddy", note)]

    def down(*args):
        raise OperationalError("select", {}, Exception("database down"))

    monkeypatch.setattr(conversations, "briefing", down)
    assert conversations._briefing(preferences, None) is None


def test_ask_uses_the_briefing_only_for_greetings(monkeypatch) -> None:
    replies = []
    monkeypatch.setattr(conversations, "ensure_shift", lambda manager_id: None)
    monkeypatch.setattr(
        conversations,
        "manager_preferences",
        lambda manager_id: SimpleNamespace(
            store_id="DS-BLR-014",
            manager_id=manager_id,
            effective=list,
        ),
    )
    monkeypatch.setattr(
        conversations,
        "SettingChanges",
        lambda preferences: SimpleNamespace(
            proposals=[],
            tool=lambda: None,
            direct_request=lambda question: None,
        ),
    )
    monkeypatch.setattr(
        conversations,
        "PersonalizationChanges",
        lambda *args: SimpleNamespace(tool=lambda: None, direct_requests=list),
    )
    monkeypatch.setattr(
        conversations,
        "manager_personalization",
        lambda manager_id: None,
    )
    monkeypatch.setattr(
        conversations,
        "_briefing",
        lambda preferences, note_id: ("Briefing", []),
    )
    monkeypatch.setattr(
        conversations,
        "_answer",
        lambda question, **kwargs: AnswerResult("Model answer"),
    )

    class Service:
        conversation_id = None

        def __init__(self, store_id, manager_id, answer, titler, conversation_id):
            self.answer = answer

        def ask(self, question):
            replies.append(self.answer(question))
            return replies[-1]

    monkeypatch.setattr(conversations, "ConversationService", Service)
    text, _, trace = conversations.ask_question("Hi")
    assert text == "Briefing" and trace == {"tools": [], "preferences": []}
    text, _, _ = conversations.ask_question("hi, orders are piling up")
    assert text == "Model answer"
    monkeypatch.setattr(conversations, "_briefing", lambda preferences, note_id: None)
    assert conversations.ask_question("Hello")[0] == "Model answer"
