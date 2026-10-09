import gradio as gr
import pytest

from service.alerts import AlertCheck
from ui import alerts as alerts_ui
from ui import chat as chat_ui
from ui import gradio_app

ALERT = {
    "id": "event-1",
    "code": "rider_shortage_alert",
    "name": "Rider shortage",
    "severity": "critical",
    "summary": "6 packed orders waiting for 2 available riders <b>",
    "value": "3 orders per available rider",
    "limit": "above 2 orders per available rider",
    "window": "at all times",
    "as_of": "20:14",
    "triggered_at": "20:30",
    "today": 3,
}
PILING = {
    **ALERT,
    "id": "event-2",
    "code": "orders_piling_up_alert",
    "name": "Orders piling up",
    "severity": "warning",
    "today": 1,
}


def checks(monkeypatch, *alerts, available=True):
    seen = []

    def check(manager_id):
        seen.append(manager_id)
        return AlertCheck(available, list(alerts))

    monkeypatch.setattr(alerts_ui, "check_alerts", check)
    return seen


def test_new_pop_ups_are_queued_once(monkeypatch) -> None:
    managers = checks(monkeypatch, ALERT, PILING)
    queue, seen = alerts_ui.queue_new("imran", [], [])
    assert [alert["id"] for alert in queue] == ["event-1", "event-2"]
    assert seen == ["event-1", "event-2"] and managers == ["imran"]
    # Already shown on this page: nothing changes.
    assert alerts_ui.queue_new("imran", queue, seen) == (gr.skip(), gr.skip())


def test_a_newer_pop_up_replaces_the_queued_one_for_that_alert(monkeypatch) -> None:
    newer = {**ALERT, "id": "event-3", "today": 4}
    checks(monkeypatch, newer)
    queue, seen = alerts_ui.queue_new(
        "karthik",
        [ALERT, PILING],
        ["event-1", "event-2"],
    )
    assert [alert["id"] for alert in queue] == ["event-2", "event-3"]
    assert seen[-1] == "event-3"


def test_failed_checks_leave_the_page_alone(monkeypatch) -> None:
    def unavailable(manager_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(alerts_ui, "check_alerts", unavailable)
    assert alerts_ui.queue_new("karthik", [], []) == (gr.skip(), gr.skip())


def test_card_shows_the_issue_value_limit_and_count() -> None:
    popup, html, details, is_open, label = alerts_ui.card([ALERT, PILING])
    assert popup == gr.update(visible=True)
    assert 'class="alert-card alert-critical"' in html and 'role="alert"' in html
    assert "Critical alert" in html and "1 of 2" in html
    assert "<h3>Rider shortage</h3>" in html
    assert "&lt;b&gt;" in html and "<b>" not in html.split("alert-summary")[1][:80]
    assert "3 orders per available rider" in html
    assert "above 2 orders per available rider" in html
    assert "As of 20:14" in html and "Triggered 3 times today" in html
    assert details == gr.update(visible=False, value="")
    assert is_open is False and label == gr.update(value="Diagnose")


def test_card_for_a_single_warning_and_for_nothing() -> None:
    _, html, *_ = alerts_ui.card([PILING])
    assert "alert-warning" in html and "1 of" not in html
    assert "Triggered once today" in html
    popup, html, *_ = alerts_ui.card([])
    assert popup == gr.update(visible=False) and html == ""


def recorder(closed: list[tuple[str, str]]):
    """A stand-in for dismiss_alert that remembers what was closed."""

    def dismiss_alert(event_id: str, manager_id: str) -> bool:
        closed.append((event_id, manager_id))
        return True

    return dismiss_alert


def test_dismiss_saves_the_close_and_moves_to_the_next_alert(monkeypatch) -> None:
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(alerts_ui, "dismiss_alert", recorder(closed))
    assert alerts_ui.dismiss([ALERT, PILING], "imran") == [PILING]
    assert closed == [("event-1", "imran")]
    assert alerts_ui.dismiss(None) == []
    assert len(closed) == 1


def test_dismiss_still_closes_when_saving_fails(monkeypatch) -> None:
    def unavailable(event_id, manager_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(alerts_ui, "dismiss_alert", unavailable)
    assert alerts_ui.dismiss([ALERT, PILING], "imran") == [PILING]


def test_diagnosis_opens_with_drivers_and_today_then_closes(monkeypatch) -> None:
    calls = []

    def diagnose(code, manager_id):
        calls.append((code, manager_id))
        return {
            "code": code,
            "name": "Rider shortage",
            "description": "Fires when packed orders per available rider rise.",
            "now": "6 packed orders waiting for 2 available riders",
            "value": "3 orders per available rider",
            "limit": "above 2 orders per available rider",
            "breached": True,
            "window": "at all times",
            "as_of": "20:14",
            "items": [
                {
                    "order": "ORD-1",
                    "zone": "B",
                    "items": 4,
                    "frozen": True,
                    "status": "packed_waiting_rider",
                    "waiting_min": 8.0,
                },
                {
                    "rider": "R-1",
                    "name": "Asha <i>",
                    "status": "available",
                    "hours_on_shift": 3.5,
                    "back_in_min": None,
                },
            ],
            "triggers": [
                {"at": "19:10", "value": "2.5"},
                {"at": "20:30", "value": "3"},
            ],
            "allowed": "0.5 to 2",
        }

    monkeypatch.setattr(alerts_ui, "diagnose", diagnose)
    details, is_open, label = alerts_ui.diagnosis([ALERT], False, "imran")
    html = details["value"]
    assert details["visible"] and is_open and label == gr.update(value="Hide details")
    assert calls == [("rider_shortage_alert", "imran")]
    assert "Still above your limit" in html
    assert "ORD-1" in html and "Asha &lt;i&gt;" in html and ">Yes<" in html
    assert "Packed, waiting" in html and ">8 min<" in html and "Available" in html
    assert 'class="alert-table-wrap"' in html
    assert '<span class="alert-badge">2</span>' in html
    assert "19:10" in html and "20:30" in html
    closed = alerts_ui.diagnosis([ALERT], True, "imran")
    assert closed == (
        gr.update(visible=False, value=""),
        False,
        gr.update(value="Diagnose"),
    )


def test_diagnosis_without_details(monkeypatch) -> None:
    monkeypatch.setattr(alerts_ui, "diagnose", lambda code, manager_id: None)
    details, is_open, _ = alerts_ui.diagnosis([ALERT], False)
    assert "not available" in details["value"] and is_open

    def failing(code, manager_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(alerts_ui, "diagnose", failing)
    assert "not available" in alerts_ui.diagnosis([ALERT], False)[0]["value"]
    assert alerts_ui.diagnosis([], False)[1] is False


def test_alert_question_names_the_alert_and_figures() -> None:
    question = alerts_ui.question(ALERT)
    assert question.startswith("Rider shortage alert at 20:14:")
    assert "now 3 orders per available rider" in question
    assert "my limit is above 2 orders per available rider" in question


def test_start_new_chat_from_an_alert(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_ui,
        "start_new_conversation",
        lambda manager_id=None: "new-chat",
    )
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(alerts_ui, "dismiss_alert", recorder(closed))
    history, message, chat_id, tab, queue = alerts_ui.start_alert_chat(
        [ALERT, PILING],
        "karthik",
    )
    assert history == [] and chat_id == "new-chat"
    assert message == alerts_ui.question(ALERT)
    assert tab == gr.update(selected="assistant") and queue == [PILING]
    # Acting on the alert closes it, like the close button.
    assert closed == [("event-1", "karthik")]
    assert alerts_ui.start_alert_chat([], "karthik") == (gr.skip(),) * 5


def block(app, kind, elem_id):
    return next(
        item
        for item in app.blocks.values()
        if isinstance(item, kind) and item.elem_id == elem_id
    )


def callbacks(app, fn):
    return [c for c in app.fns.values() if c.fn is fn]


def test_pop_up_is_hidden_until_an_alert_fires_and_checks_are_wired(ui_app) -> None:
    assert block(ui_app, gr.Column, "alert-popup").visible is False
    timers = [b for b in ui_app.blocks.values() if isinstance(b, gr.Timer)]
    assert [timer.value for timer in timers] == [alerts_ui.CHECK_SECONDS]
    checks_ = callbacks(ui_app, alerts_ui.queue_new)
    # The timer, page load, manager switch, hand over, and a scenario load.
    assert len(checks_) == 5
    manager = next(
        b
        for b in ui_app.blocks.values()
        if isinstance(b, gr.State) and b.value == gradio_app.DEMO_MANAGER_ID
    )
    assert all(callback.inputs[0] is manager for callback in checks_)
    [tick] = [c for c in checks_ if (timers[0]._id, "tick") in c.targets]
    assert tick.concurrency_id == "alerts"


def test_buttons_are_wired_to_the_shown_alert(ui_app) -> None:
    def clicked(elem_id):
        button = block(ui_app, gr.Button, elem_id)
        [callback] = [
            c for c in ui_app.fns.values() if (button._id, "click") in c.targets
        ]
        return callback

    assert clicked("alert-dismiss").fn is alerts_ui.dismiss
    assert clicked("alert-diagnose").fn is alerts_ui.diagnosis
    chat = clicked("alert-chat")
    assert chat.fn is alerts_ui.start_alert_chat
    assert chat.concurrency_id == "workspace"
    sends = [c for c in ui_app.fns.values() if c.js == alerts_ui.SEND_ALERT_QUESTION_JS]
    assert len(sends) == 1
    [render] = callbacks(ui_app, alerts_ui.card)
    assert block(ui_app, gr.Column, "alert-popup") in render.outputs


@pytest.mark.parametrize("severity", ["critical", "warning"])
def test_card_severity_classes(severity) -> None:
    _, html, *_ = alerts_ui.card([{**ALERT, "severity": severity}])
    assert f"alert-{severity}" in html


def test_every_manager_switch_clears_the_previous_managers_alerts(ui_app) -> None:
    from ui import handover as handover_ui
    from ui import sidebar as sidebar_ui

    # Choosing a manager in the sidebar, and the switch after a hand over.
    switches = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn in (sidebar_ui.select_manager, handover_ui.take_over)
    ]
    assert len(switches) == 2

    def descendants(start):
        found, frontier = [], [start._id]
        while frontier:
            current = frontier.pop()
            for callback in ui_app.fns.values():
                if callback.trigger_after == current:
                    found.append(callback)
                    frontier.append(callback._id)
        return found

    for switch in switches:
        resets = [
            callback
            for callback in descendants(switch)
            if callback.fn is not None
            and callback.fn.__name__ == "<lambda>"
            and len(callback.outputs) == 2
            and all(isinstance(item, gr.State) for item in callback.outputs)
            and callback.outputs[0].value == []
            and callback.outputs[1].value == []
        ]
        assert resets, f"{switch.fn.__name__} clears the alert queue and shown alerts"
