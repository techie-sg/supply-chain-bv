import gradio as gr
import pytest

from service import scenarios
from ui import gradio_app


def test_theme_supports_gradio_launch_analytics_comparison() -> None:
    """Gradio 6.29 compares Font instances when checking for custom themes."""
    from gradio.utils import BUILT_IN_THEMES

    theme = gradio_app.THEME.to_dict()
    assert not any(theme == built_in.to_dict() for built_in in BUILT_IN_THEMES.values())


@pytest.mark.parametrize("key", ["normal", "backlog", "rain"])
def test_situation_heading_shows_the_yaml_description(key) -> None:
    inventory = scenarios.scenario_names()
    description = next(item["description"] for item in inventory if item["key"] == key)
    heading = gradio_app._situation_heading(key, inventory)
    assert description and description in heading
    assert "CURRENT SITUATION" in heading
    assert next(item["title"] for item in inventory if item["key"] == key) in heading


@pytest.mark.parametrize("key", ["normal", "backlog", "rain"])
def test_preview_tables_do_not_load_database(key, monkeypatch) -> None:
    def no_database(*args, **kwargs):
        raise AssertionError("Preview must not access the database")

    monkeypatch.setattr(scenarios, "replace_scenario", no_database)
    summary, orders, riders, hourly, zones = gradio_app.prepare_scenario(key)
    assert len(zones["data"]) == 3
    assert len(riders["data"]) == 9
    assert len(hourly["data"]) == 12
    assert orders["headers"][:3] == ["Order ID", "Status", "Zone ID"]
    assert ("Rain conditions" if key == "rain" else "Dry conditions") in summary


def test_loading_uses_service_and_clears_chat_only_on_success(monkeypatch) -> None:
    context = scenarios.scenario_details("backlog")
    called = []

    def load(key):
        called.append(key)
        return context

    monkeypatch.setattr(gradio_app, "load_scenario", load)
    current, history, draft, summary, *tables = gradio_app.load_selected_scenario(
        "backlog"
    )
    assert called == ["backlog"]
    assert current == context and len(tables) == 4
    assert context["title"] in summary
    assert history == [] and draft == ""

    def fail(key):
        raise RuntimeError("internal connection details")

    monkeypatch.setattr(gradio_app, "load_scenario", fail)
    with pytest.raises(gr.Error, match="Scenario could not be loaded") as error:
        gradio_app.load_selected_scenario("rain")
    assert "internal connection details" not in str(error.value)


def test_invalid_preview_has_friendly_error() -> None:
    with pytest.raises(gr.Error, match="Could not prepare this scenario"):
        gradio_app.prepare_scenario("unknown")


def test_new_session_restores_saved_scenario_for_chat_and_tables(monkeypatch) -> None:
    context = scenarios.scenario_details("rain")
    monkeypatch.setattr(gradio_app, "current_scenario", lambda: context)
    current, selection, summary, *tables = gradio_app.restore_workspace()
    assert current == context
    assert context["title"] in summary
    assert selection["value"] == "rain"
    assert "Rain conditions" in summary
    assert len(tables[0]["data"]) == 12


def test_restore_distinguishes_empty_database_from_unavailable_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(gradio_app, "current_scenario", lambda: None)
    current, _, summary, *tables = gradio_app.restore_workspace()
    assert "No saved scenario" in summary
    assert current is None and all(table["data"] == [] for table in tables)

    def unavailable():
        raise RuntimeError("private connection information")

    monkeypatch.setattr(gradio_app, "current_scenario", unavailable)
    current, _, summary, *tables = gradio_app.restore_workspace()
    assert "Saved scenario unavailable" in summary
    assert "private connection information" not in summary
    assert current is None
    assert all(table["data"] == [] for table in tables)


def test_current_scenario_preview_reads_fresh_database_rows(monkeypatch) -> None:
    context = scenarios.scenario_details("normal")
    table = context["tables"]["orders"]
    table["data"][0][table["headers"].index("status")] = "delivered"
    monkeypatch.setattr(gradio_app, "current_scenario", lambda: context)

    def no_yaml(key):
        raise AssertionError("Current data must come from the database")

    monkeypatch.setattr(gradio_app, "scenario_details", no_yaml)
    _, orders, *_ = gradio_app.prepare_scenario("normal", {"scenario_key": "normal"})
    assert orders["data"][0][orders["headers"].index("Status")] == "delivered"


def test_refresh_updates_current_data_without_touching_chat(monkeypatch) -> None:
    refresh = next(
        item
        for item in gradio_app.app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "refresh-scenario"
    )
    callback = next(
        callback
        for callback in gradio_app.app.fns.values()
        if (refresh._id, "click") in callback.targets
    )
    context = scenarios.scenario_details("backlog")
    monkeypatch.setattr(gradio_app, "current_scenario", lambda: context)
    assert callback.fn is gradio_app.restore_workspace
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

    def fail(message, *, history):
        raise RuntimeError("internal API details")

    monkeypatch.setattr(gradio_app, "answer_question", fail)
    with pytest.raises(gr.Error, match="assistant is unavailable") as error:
        gradio_app.chat("new question", history)
    assert history == [{"role": "user", "content": "earlier question"}]
    assert "internal API details" not in str(error.value)


def test_reply_uses_pending_message_once_and_keeps_previous_history(
    monkeypatch,
) -> None:
    calls = []

    def answer(question, *, history):
        calls.append((question, history))
        return "reply"

    monkeypatch.setattr(gradio_app, "answer_question", answer)
    previous = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    pending = previous + [{"role": "user", "content": "Follow-up"}]
    finished, draft = next(gradio_app.respond_to_pending("Follow-up", pending))
    assert calls == [("Follow-up", previous)]
    assert finished == pending + [{"role": "assistant", "content": "reply"}]
    assert draft == ""
    assert len(previous) == 2


def test_failed_generation_restores_draft_without_duplicate_user_message(
    monkeypatch,
) -> None:
    def fail(question, *, history):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(gradio_app, "answer_question", fail)
    previous = [{"role": "assistant", "content": "Earlier reply"}]
    pending = previous + [{"role": "user", "content": "Try this"}]
    stream = gradio_app.respond_to_pending("Try this", pending)
    assert next(stream) == (previous, "Try this")
    with pytest.raises(gr.Error, match="assistant is unavailable"):
        next(stream)


def test_chat_passes_question_and_history_without_reading_scenario_data(
    monkeypatch,
) -> None:
    seen = []

    def answer(question, *, history):
        seen.append((question, history))
        return "reply"

    def no_scenario(*args, **kwargs):
        raise AssertionError("Chat must not read scenario data")

    monkeypatch.setattr(gradio_app, "answer_question", answer)
    monkeypatch.setattr(gradio_app, "current_scenario", no_scenario)
    history, _ = gradio_app.chat("What should we do?", [])
    assert seen == [("What should we do?", [])]
    assert history[-1]["content"] == "reply"


def test_chat_callbacks_receive_no_scenario_state() -> None:
    callbacks = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.respond_to_pending
    ]
    assert len(callbacks) == 2
    assert all(len(callback.inputs) == 2 for callback in callbacks)
    assert all(
        not isinstance(component, gr.State)
        for callback in callbacks
        for component in callback.inputs
    )


def test_processing_indicator_clears_after_success_or_failure() -> None:
    callbacks = list(gradio_app.app.fns.values())
    chat_callbacks = [
        callback
        for callback in callbacks
        if callback.fn is gradio_app.respond_to_pending
    ]
    finish_callbacks = [
        callback for callback in callbacks if callback.js == gradio_app.FINISH_CHAT_JS
    ]
    send_callbacks = [
        callback for callback in callbacks if callback.js == gradio_app.SEND_MESSAGE_JS
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


def test_follow_up_uses_gradio_history_and_new_session_starts_empty(
    monkeypatch,
) -> None:
    seen = []

    def answer(question, *, history):
        seen.append((question, history))
        return "No. Safety comes first."

    monkeypatch.setattr(gradio_app, "answer_question", answer)
    first_history, _ = gradio_app.chat("Should riders jump red lights?", [])
    chatbot = next(
        item for item in gradio_app.app.blocks.values() if isinstance(item, gr.Chatbot)
    )
    payload = chatbot.postprocess(
        [
            gr.ChatMessage(role=message["role"], content=message["content"])
            for message in first_history
        ]
    )
    browser_history = [dict(message) for message in chatbot.preprocess(payload)]
    follow_up_history, _ = gradio_app.chat("Why?", browser_history)
    assert seen[1] == (
        "Why?",
        [
            {"role": "user", "content": "Should riders jump red lights?"},
            {"role": "assistant", "content": "No. Safety comes first."},
        ],
    )
    assert len(follow_up_history) == 4
    gradio_app.chat("A new conversation", [])
    assert seen[-1] == ("A new conversation", [])


def test_chat_preserves_nonblank_message_whitespace(monkeypatch) -> None:
    seen = []

    def answer(question, *, history):
        seen.append(question)
        return "reply"

    monkeypatch.setattr(gradio_app, "answer_question", answer)
    message = "  Should riders speed?\n"
    history, draft = gradio_app.chat(message, [])
    assert seen == [message]
    assert history[0]["content"] == message
    assert draft == ""
    assert gradio_app.chat(" \n\t", history) == (history, "")
    assert seen == [message]


def test_clear_chat_waits_for_outstanding_workspace_callbacks() -> None:
    clear = next(
        item
        for item in gradio_app.app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "clear-chat"
    )
    callback = next(
        callback
        for callback in gradio_app.app.fns.values()
        if (clear._id, "click") in callback.targets
    )
    assert callback.queue is True
    assert callback.concurrency_id == "workspace"
    assert callback.concurrency_limit == 1
    assert callback.fn is not None
    assert callback.fn() == ([], "")
    assert all(
        chat_callback.queue
        and chat_callback.concurrency_id == callback.concurrency_id
        and chat_callback.concurrency_limit == callback.concurrency_limit
        for chat_callback in gradio_app.app.fns.values()
        if chat_callback.fn is gradio_app.respond_to_pending
    )


@pytest.mark.parametrize("error", [ValueError("Invalid YAML"), OSError("Unreadable")])
def test_unavailable_scenarios_do_not_prevent_assistant_startup(
    monkeypatch, caplog, error
) -> None:
    def unavailable():
        raise error

    monkeypatch.setattr(gradio_app, "scenario_names", unavailable)
    app = gradio_app.build_app()
    dropdown = next(
        item for item in app.blocks.values() if isinstance(item, gr.Dropdown)
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
        sum(
            callback.fn is gradio_app.respond_to_pending
            for callback in app.fns.values()
        )
        == 2
    )
    assert "Could not list scenarios" in caplog.text


def test_dropdown_lists_all_scenarios_and_handles_empty_inventory(monkeypatch) -> None:
    dropdown = next(
        item for item in gradio_app.app.blocks.values() if isinstance(item, gr.Dropdown)
    )
    assert {key for _, key in dropdown.choices} == {
        item["key"] for item in scenarios.scenario_names()
    }
    assert dropdown.value == "normal"
    monkeypatch.setattr(gradio_app, "scenario_names", list)
    empty_app = gradio_app.build_app()
    empty_dropdown = next(
        item for item in empty_app.blocks.values() if isinstance(item, gr.Dropdown)
    )
    assert empty_dropdown.value is None and not empty_dropdown.interactive


@pytest.mark.parametrize("port, expected", [("8080", 8080), (None, 7860)])
def test_launch_supports_railway_port(monkeypatch, port, expected) -> None:
    if port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port)
    options = {}
    monkeypatch.setattr(
        gradio_app.app, "launch", lambda **kwargs: options.update(kwargs)
    )
    gradio_app.main()
    assert options["server_name"] == "0.0.0.0"
    assert options["server_port"] == expected
    assert options["share"] is False
    assert options["css_paths"] == gradio_app.CSS_PATH
