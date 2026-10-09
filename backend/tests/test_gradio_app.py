from datetime import datetime
from types import SimpleNamespace

import gradio as gr
import pytest
import requests

from service import scenarios
from service.managers import ShiftManager
from ui import chat as chat_ui
from ui import gradio_app, settings
from ui import scenarios as scenarios_ui
from ui import sidebar as sidebar_ui
from ui import summary as summary_ui

NOW = datetime(2026, 10, 7, 19, 42, tzinfo=chat_ui.TIMEZONE)


@pytest.mark.parametrize("arguments", ["null", "[]", "bad-json", None, {}])
def test_invalid_stored_tool_arguments_do_not_break_chat_display(arguments) -> None:
    line = chat_ui._tool_line(
        {
            "tool": "get_live_dispatch_status",
            "arguments": arguments,
            "error": "INVALID_INPUT",
        },
    )
    assert "get_live_dispatch_status()" in line and "INVALID_INPUT" in line


def test_new_tool_trace_arguments_show_the_store_id() -> None:
    line = chat_ui._tool_line(
        {"tool": "get_live_dispatch_status", "arguments": {"store_id": "S-1"}},
    )
    assert line == "get_live_dispatch_status(store_id=S-1)"


def manager_state(ui_app) -> gr.State:
    """The app's selected-manager state."""
    return next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.State) and block.value == gradio_app.DEMO_MANAGER_ID
    )


def timed(text: str, label: str = "19:42") -> str:
    return f'{text}\n\n<span class="message-time">{label}</span>'


@pytest.fixture
def frozen_now(monkeypatch) -> datetime:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(chat_ui, "datetime", FixedDateTime)
    monkeypatch.setattr(summary_ui, "datetime", FixedDateTime)
    return NOW


def test_theme_supports_gradio_launch_analytics_comparison() -> None:
    """Gradio 6.29 compares Font instances when checking for custom themes."""
    from gradio.utils import BUILT_IN_THEMES

    theme = gradio_app.THEME.to_dict()
    assert not any(theme == built_in.to_dict() for built_in in BUILT_IN_THEMES.values())


@pytest.mark.parametrize("key", ["normal", "backlog", "rain"])
def test_situation_heading_shows_the_yaml_description(key) -> None:
    inventory = scenarios.scenario_names()
    description = next(item["description"] for item in inventory if item["key"] == key)
    heading = scenarios_ui._situation_heading(key, inventory)
    assert description and description in heading
    assert "CURRENT SITUATION" in heading
    assert next(item["title"] for item in inventory if item["key"] == key) in heading


@pytest.mark.parametrize("key", ["normal", "backlog", "rain"])
def test_preview_tables_do_not_load_database(key, monkeypatch) -> None:
    def no_database(*args, **kwargs):
        raise AssertionError("Preview must not access the database")

    monkeypatch.setattr(scenarios, "replace_scenario", no_database)
    summary, orders, riders, hourly, zones = scenarios_ui.prepare_scenario(key)
    assert len(zones["data"]) == 3
    assert len(riders["data"]) == 9
    assert len(hourly["data"]) == 12
    assert orders["headers"][:3] == ["Order ID", "Status", "Zone ID"]
    assert ("Rain conditions" if key == "rain" else "Dry conditions") in summary


def test_loading_uses_service_and_clears_chat_only_on_success(monkeypatch) -> None:
    context = scenarios.scenario_details("backlog")
    called = []

    def load(key, manager_id=None):
        called.append(key)
        return context

    monkeypatch.setattr(scenarios_ui, "load_scenario", load)
    monkeypatch.setattr(
        chat_ui,
        "start_new_conversation",
        lambda manager_id=None: called.append("new conversation"),
    )
    monkeypatch.setattr(
        scenarios_ui,
        "start_new_conversation",
        lambda manager_id=None: called.append("new conversation"),
    )
    current, history, draft, summary, *tables = scenarios_ui.load_selected_scenario(
        "backlog",
    )
    assert called == ["backlog", "new conversation"]
    assert current == context and len(tables) == 4
    assert context["title"] in summary
    assert history == [] and draft == ""

    def fail(key, manager_id=None):
        raise RuntimeError("internal connection details")

    monkeypatch.setattr(scenarios_ui, "load_scenario", fail)
    with pytest.raises(gr.Error, match="Scenario could not be loaded") as error:
        scenarios_ui.load_selected_scenario("rain")
    assert "internal connection details" not in str(error.value)
    assert called == ["backlog", "new conversation"]


def test_loaded_scenario_survives_a_conversation_start_failure(monkeypatch) -> None:
    context = scenarios.scenario_details("backlog")
    monkeypatch.setattr(
        scenarios_ui,
        "load_scenario",
        lambda key, manager_id=None: context,
    )

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(chat_ui, "start_new_conversation", unavailable)
    monkeypatch.setattr(scenarios_ui, "start_new_conversation", unavailable)
    current, history, *_ = scenarios_ui.load_selected_scenario("backlog")
    assert current == context and history == []


def test_invalid_preview_has_friendly_error() -> None:
    with pytest.raises(gr.Error, match="Could not prepare this scenario"):
        scenarios_ui.prepare_scenario("unknown")


def test_new_session_restores_saved_scenario_for_chat_and_tables(monkeypatch) -> None:
    context = scenarios.scenario_details("rain")
    monkeypatch.setattr(
        scenarios_ui,
        "current_scenario",
        lambda manager_id=None: context,
    )
    current, selection, summary, *tables = scenarios_ui.restore_workspace()
    assert current == context
    assert context["title"] in summary
    assert selection["value"] == "rain"
    assert "Rain conditions" in summary
    assert len(tables[0]["data"]) == 12


def test_restore_distinguishes_empty_database_from_unavailable_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(scenarios_ui, "current_scenario", lambda manager_id=None: None)
    current, _, summary, *tables = scenarios_ui.restore_workspace()
    assert "No saved scenario" in summary
    assert current is None and all(table["data"] == [] for table in tables)

    def unavailable(manager_id=None):
        raise RuntimeError("private connection information")

    monkeypatch.setattr(scenarios_ui, "current_scenario", unavailable)
    current, _, summary, *tables = scenarios_ui.restore_workspace()
    assert "Saved scenario unavailable" in summary
    assert "private connection information" not in summary
    assert current is None
    assert all(table["data"] == [] for table in tables)


def test_current_scenario_preview_reads_fresh_database_rows(monkeypatch) -> None:
    context = scenarios.scenario_details("normal")
    table = context["tables"]["orders"]
    table["data"][0][table["headers"].index("status")] = "delivered"
    monkeypatch.setattr(
        scenarios_ui,
        "current_scenario",
        lambda manager_id=None: context,
    )

    def no_yaml(key, manager_id=None):
        raise AssertionError("Current data must come from the database")

    monkeypatch.setattr(scenarios_ui, "scenario_details", no_yaml)
    _, orders, *_ = scenarios_ui.prepare_scenario("normal", {"scenario_key": "normal"})
    assert orders["data"][0][orders["headers"].index("Status")] == "delivered"


def test_refresh_updates_current_data_without_touching_chat(
    monkeypatch,
    ui_app,
) -> None:
    refresh = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "refresh-scenario"
    )
    callback = next(
        callback
        for callback in ui_app.fns.values()
        if (refresh._id, "click") in callback.targets
    )
    context = scenarios.scenario_details("backlog")
    monkeypatch.setattr(
        scenarios_ui,
        "current_scenario",
        lambda manager_id=None: context,
    )
    assert callback.fn is scenarios_ui.restore_workspace
    assert callback.inputs == []
    assert not any(
        isinstance(component, (gr.Chatbot, gr.Textbox))
        for component in callback.outputs
    )
    current, selection, summary, *tables = callback.fn()
    assert current == context and context["title"] in summary
    assert selection["value"] == "backlog"
    assert len(tables[0]["data"]) == 12


def test_chat_failure_preserves_existing_history(monkeypatch) -> None:
    history = [{"role": "user", "content": "earlier question"}]

    def fail(message, manager_id=None):
        raise RuntimeError("internal API details")

    monkeypatch.setattr(chat_ui, "ask_question", fail)
    with pytest.raises(gr.Error, match="assistant is unavailable") as error:
        chat_ui.chat("new question", history)
    assert history == [{"role": "user", "content": "earlier question"}]
    assert "internal API details" not in str(error.value)


def test_rate_limit_restores_draft_and_explains_the_failure(monkeypatch) -> None:
    calls = []

    def fail(question, manager_id=None):
        calls.append(question)
        response = requests.Response()
        response.status_code = 429
        raise requests.HTTPError("private provider details", response=response)

    monkeypatch.setattr(chat_ui, "ask_question", fail)
    previous = [{"role": "assistant", "content": "Earlier reply"}]
    stream = chat_ui.respond_to_pending(
        "Try this",
        previous + [{"role": "user", "content": "Try this"}],
    )
    assert next(stream)[:2] == (previous, "Try this")
    with pytest.raises(gr.Error, match="rate-limiting") as error:
        next(stream)
    assert calls == ["Try this"]  # Never retries the provider or replays tools.
    assert "private provider details" not in str(error.value)


def test_reply_uses_pending_message_once_and_keeps_previous_history(
    monkeypatch,
    frozen_now,
) -> None:
    calls = []

    def answer(question, manager_id=None):
        calls.append(question)
        return "reply", [], None

    monkeypatch.setattr(chat_ui, "ask_question", answer)
    previous = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    pending = previous + [{"role": "user", "content": "Follow-up"}]
    finished, draft, _ = next(chat_ui.respond_to_pending("Follow-up", pending))
    assert calls == ["Follow-up"]
    assert finished == previous + [
        {"role": "user", "content": timed("Follow-up")},
        {"role": "assistant", "content": timed("reply")},
    ]
    assert draft == ""
    assert len(previous) == 2


def test_failed_generation_restores_draft_without_duplicate_user_message(
    monkeypatch,
) -> None:
    def fail(question, manager_id=None):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chat_ui, "ask_question", fail)
    previous = [{"role": "assistant", "content": "Earlier reply"}]
    pending = previous + [{"role": "user", "content": "Try this"}]
    stream = chat_ui.respond_to_pending("Try this", pending)
    assert next(stream)[:2] == (previous, "Try this")
    with pytest.raises(gr.Error, match="assistant is unavailable"):
        next(stream)


def test_chat_passes_only_the_question_without_reading_scenario_data(
    monkeypatch,
    frozen_now,
) -> None:
    seen = []

    def answer(question, manager_id=None):
        seen.append(question)
        return "reply", [], None

    def no_scenario(*args, **kwargs):
        raise AssertionError("Chat must not read scenario data")

    monkeypatch.setattr(chat_ui, "ask_question", answer)
    monkeypatch.setattr(scenarios_ui, "current_scenario", no_scenario)
    earlier = [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    history, _, _ = chat_ui.chat("What should we do?", earlier)
    assert seen == ["What should we do?"]
    assert history == earlier + [
        {"role": "user", "content": timed("What should we do?")},
        {"role": "assistant", "content": timed("reply")},
    ]


def test_chat_callbacks_receive_no_scenario_state(ui_app) -> None:
    callbacks = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is chat_ui.respond_to_pending
    ]
    assert len(callbacks) == 2
    # The message, displayed chat, selected manager, and selected chat ID.
    assert all(len(callback.inputs) == 4 for callback in callbacks)
    manager = manager_state(ui_app)
    assert all(callback.inputs[2] is manager for callback in callbacks)
    assert all(
        not isinstance(component, gr.State)
        for callback in callbacks
        for component in callback.inputs[:2]
    )


def test_processing_indicator_clears_after_success_or_failure(ui_app) -> None:
    callbacks = list(ui_app.fns.values())
    chat_callbacks = [
        callback for callback in callbacks if callback.fn is chat_ui.respond_to_pending
    ]
    finish_callbacks = [
        callback for callback in callbacks if callback.js == chat_ui.FINISH_CHAT_JS
    ]
    send_callbacks = [
        callback for callback in callbacks if callback.js == chat_ui.SEND_MESSAGE_JS
    ]
    assert len(send_callbacks) == 2
    assert all(
        callback.fn is None and not callback.queue for callback in send_callbacks
    )
    assert len(finish_callbacks) == len(chat_callbacks) == 2
    assert all(callback.show_progress == "hidden" for callback in chat_callbacks)
    for callback in finish_callbacks:
        assert callback.trigger_after in [item._id for item in chat_callbacks]
        assert not callback.trigger_only_on_success
        assert not callback.trigger_only_on_failure


def test_page_load_restores_the_stored_conversation(
    monkeypatch,
    frozen_now,
    ui_app,
) -> None:
    stored = [
        {
            "who": "manager",
            "what": "Should riders jump red lights?",
            "when": "2026-10-06T21:05:00+05:30",
        },
        {
            "who": "assistant",
            "what": "No. Safety comes first.",
            "when": "2026-10-07T08:10:00+05:30",
        },
    ]
    monkeypatch.setattr(
        sidebar_ui,
        "latest_conversation_state",
        lambda manager_id=None: (stored, "chat-id"),
    )
    history, conversation_id = sidebar_ui.restore_latest_conversation(
        gradio_app.DEMO_MANAGER_ID,
    )
    assert conversation_id == "chat-id"
    assert history == [
        {
            "role": "user",
            "content": timed("Should riders jump red lights?", "6 Oct, 21:05"),
        },
        {"role": "assistant", "content": timed("No. Safety comes first.", "08:10")},
    ]
    assert any(
        callback.fn is sidebar_ui.restore_conversation
        and any(isinstance(component, gr.Chatbot) for component in callback.outputs)
        for callback in ui_app.fns.values()
    )


def test_page_load_starts_empty_when_storage_is_unavailable(monkeypatch) -> None:
    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(sidebar_ui, "latest_conversation_state", unavailable)
    assert sidebar_ui.restore_latest_conversation(gradio_app.DEMO_MANAGER_ID) == (
        [],
        None,
    )


def test_sidebar_lists_past_chats_and_marks_the_open_one(monkeypatch) -> None:
    from uuid import uuid4

    open_id, other_id = uuid4(), uuid4()
    monkeypatch.setattr(
        sidebar_ui,
        "past_conversations",
        lambda manager_id=None: [
            {
                "id": open_id,
                "first_question": "  Orders are backing up,\nwhat should I do "
                "first and who should I call in?",
            },
            {"id": other_id, "first_question": "Rain plan?"},
        ],
    )
    monkeypatch.setattr(
        sidebar_ui,
        "current_conversation_id",
        lambda manager_id=None: str(open_id),
    )
    update = sidebar_ui.conversation_choices()
    (title, value), other = update["choices"]
    assert value == str(open_id) and update["value"] == str(open_id)
    assert title == (
        "Orders are backing up, what should I do first and who should I call in?"
    )
    assert other == ("Rain plan?", str(other_id))

    # A new, empty chat is current but not listed, so nothing is marked.
    monkeypatch.setattr(
        sidebar_ui,
        "current_conversation_id",
        lambda manager_id=None: "new-chat",
    )
    assert sidebar_ui.conversation_choices()["value"] is None

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(sidebar_ui, "past_conversations", unavailable)
    assert sidebar_ui.conversation_choices()["choices"] == []


def test_opening_a_past_conversation_shows_and_continues_it(
    monkeypatch,
    frozen_now,
    ui_app,
) -> None:
    opened = []

    def resume(conversation_id, manager_id=None):
        opened.append(conversation_id)
        return [
            {
                "who": "manager",
                "what": "Rain plan?",
                "when": "2026-10-07T19:30:00+05:30",
            },
        ]

    monkeypatch.setattr(sidebar_ui, "resume_past_conversation", resume)
    assert sidebar_ui.open_conversation("abc") == (
        [{"role": "user", "content": timed("Rain plan?", "19:30")}],
        "",
        "abc",
        gr.update(selected="assistant"),
    )
    assert opened == ["abc"]
    skipped = sidebar_ui.open_conversation(None)
    assert all(item == gr.skip() for item in skipped)

    def missing(conversation_id, manager_id=None):
        raise LookupError("private detail")

    monkeypatch.setattr(sidebar_ui, "resume_past_conversation", missing)
    with pytest.raises(gr.Error, match="Could not open that conversation") as error:
        sidebar_ui.open_conversation("abc")
    assert "private detail" not in str(error.value)
    picker = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Radio) and item.elem_id == "history-list"
    )
    assert any(
        callback.fn is sidebar_ui.open_conversation
        and (picker._id, "input") in callback.targets
        for callback in ui_app.fns.values()
    )


def test_clear_chat_failure_keeps_the_conversation(monkeypatch) -> None:
    def unavailable(manager_id=None):
        raise RuntimeError("private connection information")

    monkeypatch.setattr(chat_ui, "start_new_conversation", unavailable)
    monkeypatch.setattr(scenarios_ui, "start_new_conversation", unavailable)
    with pytest.raises(gr.Error, match="Could not start a new chat") as error:
        chat_ui.clear_chat()
    assert "private connection information" not in str(error.value)


def test_linked_chat_is_restored_without_using_the_latest_chat(monkeypatch):
    from types import SimpleNamespace

    opened = []
    monkeypatch.setattr(
        sidebar_ui,
        "open_conversation",
        lambda chat_id, manager_id=None: (
            opened.append(chat_id) or [{"role": "user", "content": "Rain?"}],
            "",
            chat_id,
            gr.update(selected="assistant"),
        ),
    )
    history, chat_id = sidebar_ui.restore_conversation(
        gradio_app.DEMO_MANAGER_ID,
        SimpleNamespace(query_params={"chat": "older-chat", "view": "settings"}),
    )
    assert opened == ["older-chat"] and chat_id == "older-chat"
    assert history == [{"role": "user", "content": "Rain?"}]


def test_chat_browser_metadata_uses_selected_chat(monkeypatch):
    seen = []
    monkeypatch.setattr(
        sidebar_ui,
        "conversation_details",
        lambda chat_id, manager_id=None: (
            seen.append(chat_id) or {"id": chat_id, "title": "Rain response"}
        ),
    )
    assert sidebar_ui.conversation_location(
        gradio_app.DEMO_MANAGER_ID,
        "older-chat",
    ) == {
        "id": "older-chat",
        "title": "Rain response",
    }
    assert seen == ["older-chat"]
    assert sidebar_ui.conversation_location(gradio_app.DEMO_MANAGER_ID, None) == {
        "id": None,
        "title": "New chat",
    }


def test_chat_preserves_nonblank_message_whitespace(monkeypatch) -> None:
    seen = []

    def answer(question, manager_id=None):
        seen.append(question)
        return "reply", [], None

    monkeypatch.setattr(chat_ui, "ask_question", answer)
    message = "  Should riders speed?\n"
    history, draft, _ = chat_ui.chat(message, [])
    assert seen == [message]
    assert history[0]["content"].startswith(message)
    assert draft == ""
    assert chat_ui.chat(" \n\t", history)[:2] == (history, "")
    assert seen == [message]


def test_new_chat_waits_for_outstanding_workspace_callbacks(
    monkeypatch,
    ui_app,
) -> None:
    started = []

    def start_new(manager_id=None):
        started.append(True)
        return "new-chat-id"

    monkeypatch.setattr(
        chat_ui,
        "start_new_conversation",
        start_new,
    )
    monkeypatch.setattr(
        scenarios_ui,
        "start_new_conversation",
        start_new,
    )
    clear = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "new-chat"
    )
    callback = next(
        callback
        for callback in ui_app.fns.values()
        if (clear._id, "click") in callback.targets
    )
    assert callback.queue is True
    assert callback.concurrency_id == "workspace"
    assert callback.concurrency_limit == 1
    assert callback.fn is not None
    assert callback.fn is chat_ui.clear_chat
    assert callback.fn() == ([], "", "new-chat-id", gr.update(selected="assistant"))
    assert started == [True]
    assert all(
        chat_callback.queue
        and chat_callback.concurrency_id == callback.concurrency_id
        and chat_callback.concurrency_limit == callback.concurrency_limit
        for chat_callback in ui_app.fns.values()
        if chat_callback.fn is chat_ui.respond_to_pending
    )


def test_read_only_screens_do_not_wait_for_another_sessions_answer(ui_app):
    readers = {
        sidebar_ui.restore_conversation,
        scenarios_ui.restore_workspace,
        scenarios_ui.prepare_scenario,
    }
    callbacks = [callback for callback in ui_app.fns.values() if callback.fn in readers]
    assert {callback.fn for callback in callbacks} == readers
    assert all(callback.concurrency_id != "workspace" for callback in callbacks)
    switches = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is sidebar_ui.restore_latest_conversation
    ]
    assert switches and all(
        callback.concurrency_id == "workspace" for callback in switches
    )


def test_sidebar_reload_refreshes_current_scenario_without_touching_chat(
    monkeypatch,
    ui_app,
):
    context = scenarios.scenario_details("rain")
    monkeypatch.setattr(scenarios_ui, "reload_current_scenario", lambda: context)
    monkeypatch.setattr(
        scenarios_ui,
        "start_new_conversation",
        lambda *args, **kwargs: pytest.fail("Reload must preserve chat"),
    )
    result = scenarios_ui.reload_workspace()
    assert result[0] == context and result[1]["value"] == "rain"
    button = next(
        component
        for component in ui_app.blocks.values()
        if isinstance(component, gr.Button)
        and component.elem_id == "reload-current-scenario"
    )
    callback = next(
        callback
        for callback in ui_app.fns.values()
        if (button._id, "click") in callback.targets
    )
    assert callback.fn is scenarios_ui.reload_workspace
    assert (
        callback.inputs == []
    )  # resolves the actual database scenario, not the preview
    assert callback.concurrency_id == "workspace" and callback.concurrency_limit == 1
    assert all(
        component.elem_id not in {"conversation", "message-input"}
        for component in callback.outputs
    )


def test_sidebar_reload_errors_leave_the_workspace_unchanged(monkeypatch):
    def missing():
        raise LookupError("No scenario is loaded")

    monkeypatch.setattr(scenarios_ui, "reload_current_scenario", missing)
    with pytest.raises(gr.Error, match="No scenario is loaded"):
        scenarios_ui.reload_workspace()


def test_failed_stream_unlocks_the_composer_without_discarding_its_draft(ui_app):
    recoveries = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is chat_ui.recover_composer
    ]
    assert len(recoveries) == 2
    for callback in recoveries:
        assert callback.trigger_only_on_failure
        assert ui_app.fns[callback.trigger_after].fn is chat_ui.respond_to_pending
        assert [output.elem_id for output in callback.outputs] == [
            "chat-processing",
            "send-message",
            "message-input",
        ]
    assert chat_ui.recover_composer() == (
        gr.update(visible=False),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def test_active_chat_change_does_not_duplicate_sidebar_and_summary_reads(ui_app):
    active_chat = next(
        callback.outputs[2]
        for callback in ui_app.fns.values()
        if callback.fn is chat_ui.clear_chat
    )
    changes = [
        callback
        for callback in ui_app.fns.values()
        if (active_chat._id, "change") in callback.targets
    ]
    assert all(
        callback.fn not in {sidebar_ui.conversation_choices, summary_ui.summary_card}
        for callback in changes
    )


@pytest.mark.parametrize("error", [ValueError("Invalid YAML"), OSError("Unreadable")])
def test_unavailable_scenarios_do_not_prevent_assistant_startup(
    monkeypatch,
    error,
) -> None:
    def unavailable(manager_id=None):
        raise error

    monkeypatch.setattr(gradio_app, "scenario_names", unavailable)
    app = gradio_app.build_app()
    dropdown = next(
        item
        for item in app.blocks.values()
        if isinstance(item, gr.Dropdown) and item.elem_id == "scenario-picker"
    )
    load = next(
        item
        for item in app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "load-scenario"
    )
    overview = next(
        item
        for item in app.blocks.values()
        if isinstance(item, gr.HTML) and item.elem_id == "scenario-overview"
    )
    assert dropdown.value is None and not dropdown.interactive
    assert not load.interactive
    assert "Scenarios unavailable" in overview.value
    assert "Assistant chat is still available" in overview.value
    assert (
        sum(callback.fn is chat_ui.respond_to_pending for callback in app.fns.values())
        == 2
    )


def test_dropdown_lists_all_scenarios_and_handles_empty_inventory(
    monkeypatch,
    ui_app,
) -> None:
    dropdown = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Dropdown) and item.elem_id == "scenario-picker"
    )
    assert {key for _, key in dropdown.choices} == {
        item["key"] for item in scenarios.scenario_names()
    }
    assert dropdown.value == "normal"
    monkeypatch.setattr(gradio_app, "scenario_names", list)
    empty_app = gradio_app.build_app()
    empty_dropdown = next(
        item
        for item in empty_app.blocks.values()
        if isinstance(item, gr.Dropdown) and item.elem_id == "scenario-picker"
    )
    assert empty_dropdown.value is None and not empty_dropdown.interactive


@pytest.mark.parametrize("port, expected", [("8080", 8080), (None, 7860)])
def test_launch_supports_railway_port(monkeypatch, port, expected, ui_app) -> None:
    if port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port)
    options = {}
    monkeypatch.setattr(
        ui_app,
        "launch",
        lambda **kwargs: options.update(kwargs),
    )
    monkeypatch.setattr(gradio_app, "build_app", lambda: ui_app)
    gradio_app.main()
    assert options["server_name"] == "0.0.0.0"
    assert options["server_port"] == expected
    assert options["share"] is False
    assert options["css_paths"] == gradio_app.CSS_PATH


def test_times_are_shown_in_ist_with_the_day_for_older_messages() -> None:
    from datetime import UTC

    assert chat_ui.message_time(datetime(2026, 10, 7, 14, 12, tzinfo=UTC), NOW) == (
        "19:42"
    )
    assert chat_ui.message_time(datetime(2026, 9, 30, 4, 0, tzinfo=UTC), NOW) == (
        "30 Sep, 09:30"
    )


def test_sidebar_prefers_the_title_over_the_first_question() -> None:
    assert (
        sidebar_ui._conversation_title(
            {"title": "Rain backlog with two riders", "first_question": "It's pouring"},
        )
        == "Rain backlog with two riders"
    )
    assert (
        sidebar_ui._conversation_title(
            {"title": None, "first_question": "It's pouring"},
        )
        == "It's pouring"
    )


def test_title_runs_after_the_answer_and_refreshes_the_sidebar(
    monkeypatch,
    ui_app,
) -> None:
    titled = []
    monkeypatch.setattr(
        sidebar_ui,
        "title_latest_conversation",
        lambda manager_id=None: titled.append(True),
    )
    monkeypatch.setattr(
        sidebar_ui,
        "conversation_choices",
        lambda manager_id=None: "choices",
    )
    assert sidebar_ui.title_conversation() == "choices"
    assert titled == [True]

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(sidebar_ui, "title_latest_conversation", unavailable)
    assert sidebar_ui.title_conversation() == "choices"
    callbacks = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is sidebar_ui.title_conversation
    ]
    assert len(callbacks) == 2
    assert all(callback.concurrency_id == "titles" for callback in callbacks)


def test_summary_runs_after_the_answer_on_its_own_queue(monkeypatch, ui_app) -> None:
    ran = []
    monkeypatch.setattr(
        summary_ui,
        "summarize_latest_conversation",
        lambda manager_id=None: ran.append(True),
    )
    summary_ui.summarize_conversation()
    assert ran == [True]

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(summary_ui, "summarize_latest_conversation", unavailable)
    summary_ui.summarize_conversation()
    callbacks = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is summary_ui.summarize_conversation
    ]
    assert len(callbacks) == 2
    assert all(callback.concurrency_id == "summaries" for callback in callbacks)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_demo_summary_job_uses_the_forced_shared_job(monkeypatch, count):
    called = []

    def run(*, force):
        called.append(force)
        return count, 0

    monkeypatch.setattr(summary_ui, "run_summary_job", run)
    status = summary_ui.run_summary_job_now()
    assert called == [True]
    assert f"Updated {count} conversation" in status
    assert "Verified personalization preferences are saved automatically." in status


def test_demo_summary_job_reports_partial_failures(monkeypatch):
    monkeypatch.setattr(summary_ui, "run_summary_job", lambda **kwargs: (1, 2))
    status = summary_ui.run_summary_job_now()
    assert "Updated 1 conversation summary." in status
    assert "2 conversations could not finish; run again to retry." in status


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_demo_summary_job_reports_failure_without_exposing_details(monkeypatch, error):
    def unavailable(**kwargs):
        raise error("private connection details")

    monkeypatch.setattr(summary_ui, "run_summary_job", unavailable)
    with pytest.raises(gr.Error, match="Could not run summary and personalization"):
        summary_ui.run_summary_job_now()


def test_demo_summary_job_refreshes_the_selected_chat_and_preferences(ui_app):
    from ui import personalization as personalization_ui
    from ui import suggestions as suggestions_ui

    button = next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.Button) and block.elem_id == "run-summary-job"
    )
    [click] = [
        callback
        for callback in ui_app.fns.values()
        if (button._id, "click") in callback.targets
    ]
    assert click.fn is summary_ui.run_summary_job_now
    assert click.concurrency_id == "summaries" and click.concurrency_limit == 1
    assert click.trigger_mode == "once"
    assert click.outputs[0].elem_id == "summary-job-status"
    parent = click
    for function in (
        summary_ui.summary_card,
        suggestions_ui.refresh_for,
        personalization_ui.load,
    ):
        [child] = [
            callback
            for callback in ui_app.fns.values()
            if callback.fn is function and callback.trigger_after == parent._id
        ]
        assert manager_state(ui_app) in child.inputs
        parent = child


def test_summary_trigger_is_hidden_for_an_empty_chat(monkeypatch) -> None:
    monkeypatch.setattr(
        summary_ui,
        "conversation_summary",
        lambda manager_id=None: None,
    )
    trigger, status, text, _ = summary_ui.summary_card()
    assert trigger == gr.update(visible=False) and status == text == ""

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(summary_ui, "conversation_summary", unavailable)
    assert summary_ui.summary_card()[0] == gr.update(visible=False)


def test_unsummarized_chat_offers_to_summarize(monkeypatch) -> None:
    monkeypatch.setattr(
        summary_ui,
        "conversation_summary",
        lambda manager_id=None: {
            "summary": None,
            "covered": 0,
            "total": 4,
            "summarized_at": None,
        },
    )
    trigger, status, text, button = summary_ui.summary_card()
    assert trigger == gr.update(visible=True)
    assert status == "4 messages · No summary yet"
    assert "key decisions and details" in text
    assert button == gr.update(value="Create summary")


def test_summary_card_shows_coverage_and_update_time(monkeypatch, frozen_now) -> None:
    monkeypatch.setattr(
        summary_ui,
        "conversation_summary",
        lambda manager_id=None: {
            "summary": "- Standby rider approved.",
            "covered": 12,
            "total": 30,
            "summarized_at": datetime(2026, 10, 7, 19, 30, tzinfo=chat_ui.TIMEZONE),
        },
    )
    _, status, text, button = summary_ui.summary_card()
    assert status == "12 of 30 messages · Updated 19:30"
    assert text.startswith("- Standby rider approved.")
    assert "Your full conversation stays in the chat." in text
    assert button == gr.update(value="Update summary")


def test_summarize_now_updates_the_popover_content(monkeypatch, frozen_now) -> None:
    views = iter(
        [
            {
                "summary": "- Now includes the latest exchange.",
                "covered": 30,
                "total": 30,
                "summarized_at": datetime(
                    2026,
                    10,
                    7,
                    19,
                    42,
                    tzinfo=chat_ui.TIMEZONE,
                ),
            },
        ],
    )
    monkeypatch.setattr(
        summary_ui,
        "summarize_open_conversation",
        lambda manager_id=None: True,
    )
    monkeypatch.setattr(
        summary_ui,
        "conversation_summary",
        lambda manager_id=None: next(views),
    )
    _, status, text, _ = summary_ui.summarize_now()
    assert status == "30 of 30 messages · Updated 19:42"
    assert text.startswith("- Now includes the latest exchange.")


def test_summarize_now_reports_when_nothing_is_new(monkeypatch) -> None:
    notices: list[str] = []
    monkeypatch.setattr(gradio_app.gr, "Info", notices.append)
    monkeypatch.setattr(
        summary_ui,
        "summarize_open_conversation",
        lambda manager_id=None: False,
    )
    monkeypatch.setattr(
        summary_ui,
        "conversation_summary",
        lambda manager_id=None: None,
    )
    summary_ui.summarize_now()
    assert notices == ["The summary already covers every message."]

    def failing(manager_id=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(summary_ui, "summarize_open_conversation", failing)
    with pytest.raises(gr.Error, match="Could not summarize this chat"):
        summary_ui.summarize_now()


def test_summary_popover_is_refreshed_with_the_chat(ui_app) -> None:
    def block(kind, elem_id):
        return next(
            item
            for item in ui_app.blocks.values()
            if isinstance(item, kind) and item.elem_id == elem_id
        )

    trigger = block(gr.Button, "summary-trigger")
    button = block(gr.Button, "summarize-now")
    assert trigger.visible is False
    refreshers = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is summary_ui.summary_card
    ]
    # Page load, switching manager (picker or hand over), opening a chat, a
    # scenario load, New chat, and before title generation and after any summary
    # folding for each answer, starting a chat from an alert, and the demo job.
    assert len(refreshers) == 12
    assert all(trigger in callback.outputs for callback in refreshers)
    [click] = [
        callback
        for callback in ui_app.fns.values()
        if (button._id, "click") in callback.targets
    ]
    assert click.fn is summary_ui.summarize_now
    assert click.concurrency_id == "workspace"


def test_answer_summary_is_available_before_title_generation(ui_app):
    for title in ui_app.fns.values():
        if title.fn is sidebar_ui.title_conversation:
            preceding = ui_app.fns[title.trigger_after]
            assert preceding.fn is summary_ui.summary_card


CHANGE = {
    "code": "sla_dip_alert",
    "name": "SLA dip",
    "action": "set",
    "enabled": True,
    "value": 85,
    "options": None,
    "before": "off",
    "after": "on, below 85% <b>",
    "proposed_over": [False, 80, None],
    "manager_id": "karthik",
}


def test_chat_hands_proposed_changes_to_the_confirmation_card(
    monkeypatch,
    frozen_now,
) -> None:
    from service.setting_changes import SettingChange

    change = SettingChange.from_state(CHANGE)
    monkeypatch.setattr(
        chat_ui,
        "ask_question",
        lambda q, manager_id=None: ("Proposed.", [change], None),
    )
    _, _, pending = chat_ui.chat("Alert me below 85", [])
    assert pending == [CHANGE]
    monkeypatch.setattr(
        chat_ui,
        "ask_question",
        lambda q, manager_id=None: ("No change.", [], None),
    )
    assert chat_ui.chat("Thanks", [])[2] == gr.skip()


def test_pending_card_shows_old_and_new_values_escaped() -> None:
    html, box = chat_ui.pending_card([CHANGE])
    assert "Proposed setting change" in html and "Not saved until you confirm" in html
    assert "SLA dip" in html and "off" in html
    assert "on, below 85% &lt;b&gt;" in html
    assert box["visible"] is True
    two, _ = chat_ui.pending_card([CHANGE, CHANGE])
    assert "2 proposed setting changes" in two
    assert chat_ui.pending_card([]) == ("", gr.update(visible=False))


def test_confirm_saves_records_a_note_and_clears_the_card(
    monkeypatch,
    frozen_now,
) -> None:
    saved: list[dict] = []
    notes: list[str] = []

    def confirm(states, manager_id=None):
        saved.extend(states)
        return ["Saved. SLA dip: on, below 85%."]

    def note(text, manager_id=None):
        notes.append(text)
        return {"when": NOW.isoformat()}

    monkeypatch.setattr(chat_ui, "confirm_proposals", confirm)
    monkeypatch.setattr(chat_ui, "add_note", note)
    history, pending = chat_ui.confirm_pending([CHANGE], [])
    assert saved == [CHANGE] and pending == []
    assert notes == ["Saved. SLA dip: on, below 85%."]
    assert history == [
        {"role": "assistant", "content": timed("Saved. SLA dip: on, below 85%.")},
    ]
    assert chat_ui.confirm_pending([], history) == (history, [])


def test_confirm_failure_keeps_the_card(monkeypatch) -> None:
    def fail(states, manager_id=None):
        raise RuntimeError("database password in message")

    monkeypatch.setattr(chat_ui, "confirm_proposals", fail)
    with pytest.raises(gr.Error, match="Could not save the settings") as error:
        chat_ui.confirm_pending([CHANGE], [])
    assert "password" not in str(error.value)


def test_cancel_saves_nothing_and_still_shows_a_note_when_storage_fails(
    monkeypatch,
    frozen_now,
) -> None:
    def unavailable(text, manager_id=None):
        raise RuntimeError("no database")

    monkeypatch.setattr(chat_ui, "add_note", unavailable)
    monkeypatch.setattr(
        chat_ui,
        "confirm_proposals",
        lambda states, manager_id=None: pytest.fail("Cancel must not save"),
    )
    history, pending = chat_ui.cancel_pending([CHANGE], [])
    assert pending == []
    assert history == [
        {
            "role": "assistant",
            "content": timed("Cancelled. Nothing was changed (SLA dip)."),
        },
    ]
    assert chat_ui.cancel_pending(None, None) == ([], [])


def test_confirmation_buttons_and_card_are_wired(ui_app) -> None:
    def callbacks(fn):
        return [c for c in ui_app.fns.values() if c.fn is fn]

    [confirm] = callbacks(chat_ui.confirm_pending)
    [cancel] = callbacks(chat_ui.cancel_pending)
    for callback in (confirm, cancel):
        assert callback.concurrency_id == "workspace"
        assert isinstance(callback.inputs[0], gr.State)
        assert isinstance(callback.outputs[1], gr.State)
    [card] = callbacks(chat_ui.pending_card)
    assert card.inputs == [confirm.inputs[0]]
    replies = callbacks(chat_ui.respond_to_pending)
    assert all(reply.outputs[2] is confirm.inputs[0] for reply in replies)


MANAGERS = [
    ShiftManager("ananya", "Ananya Rao", "SHIFT-MOR", "Morning", "06:00", "14:00"),
    ShiftManager("karthik", "Karthik Reddy", "SHIFT-EVE", "Evening", "14:00", "22:00"),
    ShiftManager("imran", "Imran Shaikh", "SHIFT-NGT", "Night", "22:00", "06:00"),
]


def request(**params):
    return SimpleNamespace(query_params=params)


def test_page_load_picks_the_manager_from_the_url(monkeypatch) -> None:
    monkeypatch.setattr(sidebar_ui, "store_managers", lambda: MANAGERS)
    manager_id, picker, badge = sidebar_ui.restore_manager(request(manager="imran"))
    assert manager_id == "imran" and picker["value"] == "imran"
    assert [value for _, value in picker["choices"]] == ["ananya", "karthik", "imran"]
    assert picker["choices"][2][0] == "Imran Shaikh\nNight shift · 22:00–06:00"
    assert "Imran Shaikh" in badge and "SHIFT-NGT" in badge
    # Unknown or missing ids fall back to the demo manager.
    assert sidebar_ui.restore_manager(request(manager="nobody"))[0] == "karthik"
    assert sidebar_ui.restore_manager(request())[0] == "karthik"


def test_page_load_without_managers_keeps_working(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("database down")

    monkeypatch.setattr(sidebar_ui, "store_managers", unavailable)
    manager_id, picker, badge = sidebar_ui.restore_manager(request(manager="imran"))
    assert manager_id == gradio_app.DEMO_MANAGER_ID
    assert picker["choices"] == [] and "No manager available" in badge


def test_switching_manager_accepts_only_the_store_managers(monkeypatch) -> None:
    monkeypatch.setattr(sidebar_ui, "store_managers", lambda: MANAGERS)
    manager_id, badge = sidebar_ui.select_manager("ananya")
    assert manager_id == "ananya" and "Morning shift" in badge
    with pytest.raises(gr.Error, match="not available"):
        sidebar_ui.select_manager("someone-else")


def test_chat_and_confirmation_use_the_selected_manager(
    monkeypatch,
    frozen_now,
) -> None:
    seen = []

    def answer(question, manager_id=None):
        seen.append(("ask", manager_id))
        return "reply", [], None

    def confirm(states, manager_id=None):
        seen.append(("confirm", manager_id))
        return ["Saved."]

    def note(text, manager_id=None):
        seen.append(("note", manager_id))

    monkeypatch.setattr(chat_ui, "ask_question", answer)
    monkeypatch.setattr(chat_ui, "confirm_proposals", confirm)
    monkeypatch.setattr(chat_ui, "add_note", note)
    next(chat_ui.respond_to_pending("Hi", [], "imran"))
    chat_ui.confirm_pending([CHANGE], [], "imran")
    chat_ui.cancel_pending([CHANGE], [], "imran")
    assert seen == [
        ("ask", "imran"),
        ("confirm", "imran"),
        ("note", "imran"),
        ("note", "imran"),
    ]


def test_every_manager_scoped_callback_receives_the_selected_manager(ui_app) -> None:
    manager = manager_state(ui_app)
    scoped = {
        sidebar_ui.restore_conversation,
        sidebar_ui.restore_latest_conversation,
        sidebar_ui.conversation_choices,
        sidebar_ui.open_conversation,
        sidebar_ui.title_conversation,
        summary_ui.summary_card,
        summary_ui.summarize_now,
        summary_ui.summarize_conversation,
        chat_ui.clear_chat,
        chat_ui.respond_to_pending,
        chat_ui.confirm_pending,
        chat_ui.cancel_pending,
        scenarios_ui.load_selected_scenario,
        settings.load_settings,
        settings.load_summary,
        settings.save_settings,
    }
    seen = set()
    for callback in ui_app.fns.values():
        if callback.fn in scoped:
            assert manager in callback.inputs, callback.fn.__name__
            seen.add(callback.fn)
    assert seen == scoped
    resets = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is not None
        and callback.fn.__name__ == "<lambda>"
        and callback.inputs == [manager]
        and callback.outputs
        and len(callback.outputs) > 10
    ]
    assert len(resets) == len(settings.ALERT_CODES) + 3


def test_switching_manager_clears_the_card_and_reloads_their_workspace(ui_app) -> None:
    picker = next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.Dropdown) and block.elem_id == "manager-picker"
    )
    [switch] = [
        callback
        for callback in ui_app.fns.values()
        if (picker._id, "input") in callback.targets
    ]
    assert switch.fn is sidebar_ui.select_manager
    loaders = [
        callback.fn
        for callback in ui_app.fns.values()
        if callback.fn
        in (
            sidebar_ui.restore_conversation,
            sidebar_ui.restore_latest_conversation,
            sidebar_ui.conversation_choices,
            settings.load_settings,
            settings.load_summary,
        )
        and callback.trigger_after is not None
    ]
    # Page load, switching manager and handing over each reload chat, chats and
    # settings.
    assert loaders.count(sidebar_ui.restore_conversation) == 1
    assert loaders.count(sidebar_ui.restore_latest_conversation) == 2
    assert loaders.count(settings.load_settings) >= 3
    # The pending-change card is cleared right after the switch.
    [clear] = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is list and callback.trigger_after == switch._id
    ]
    assert isinstance(clear.outputs[0], gr.State)


def test_switching_manager_records_the_choice_in_the_url(ui_app) -> None:
    picker = next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.Dropdown) and block.elem_id == "manager-picker"
    )
    urls = [
        callback
        for callback in ui_app.fns.values()
        if callback.js == sidebar_ui.MANAGER_URL_JS
    ]
    # Picking a manager and handing over; state values never reach the
    # browser, so the URL reads the picker.
    assert len(urls) == 2
    assert all(url.inputs == [picker] for url in urls)
