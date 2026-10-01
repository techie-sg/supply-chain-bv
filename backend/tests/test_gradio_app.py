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
    status, current, history, draft, summary, *tables = (
        gradio_app.load_selected_scenario("backlog")
    )
    assert called == ["backlog"]
    assert context["title"] in status and context["store_id"] in status
    assert "IST" in status
    assert "CURRENT SCENARIO" in status
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
    status, current, selection, summary, *tables = gradio_app.restore_workspace(
        "normal"
    )
    assert current == context
    assert context["title"] in status
    assert selection["value"] == "rain"
    assert "Rain conditions" in summary
    assert len(tables[0]["data"]) == 12


def test_restore_distinguishes_empty_database_from_unavailable_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(gradio_app, "current_scenario", lambda: None)
    status, current, _, _, *tables = gradio_app.restore_workspace("normal")
    assert "No saved scenario" in status
    assert current is None and len(tables[0]["data"]) == 6

    def unavailable():
        raise RuntimeError("private connection information")

    monkeypatch.setattr(gradio_app, "current_scenario", unavailable)
    status, current, *_ = gradio_app.restore_workspace("normal")
    assert "Saved scenario unavailable" in status
    assert "private connection information" not in status
    assert current is None


def test_chat_failure_preserves_existing_history(monkeypatch) -> None:
    history = [{"role": "user", "content": "earlier question"}]

    def fail(message):
        raise RuntimeError("internal API details")

    monkeypatch.setattr(gradio_app, "answer_question", fail)
    with pytest.raises(gr.Error, match="assistant is unavailable") as error:
        gradio_app.chat("new question", history)
    assert history == [{"role": "user", "content": "earlier question"}]
    assert "internal API details" not in str(error.value)


def test_chat_passes_only_the_question_and_never_reads_scenario_data(
    monkeypatch,
) -> None:
    seen = []

    def answer(question):
        seen.append(question)
        return "reply"

    def no_scenario(*args, **kwargs):
        raise AssertionError("Chat must not read scenario data")

    monkeypatch.setattr(gradio_app, "answer_question", answer)
    monkeypatch.setattr(gradio_app, "current_scenario", no_scenario)
    history, _ = gradio_app.chat("What should we do?", [])
    assert seen == ["What should we do?"]
    assert history[-1]["content"] == "reply"


def test_chat_callbacks_receive_no_scenario_state() -> None:
    callbacks = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.chat
    ]
    assert len(callbacks) == 2
    assert all(len(callback.inputs) == 2 for callback in callbacks)
    assert all(
        not isinstance(component, gr.State)
        for callback in callbacks
        for component in callback.inputs
    )


def test_chat_preserves_nonblank_message_whitespace(monkeypatch) -> None:
    seen = []

    def answer(question):
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
        if chat_callback.fn is gradio_app.chat
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
    status = next(
        item
        for item in app.blocks.values()
        if isinstance(item, gr.HTML) and item.elem_id == "load-status"
    )
    assert dropdown.value is None and not dropdown.interactive
    assert not load.interactive
    assert "Scenarios unavailable" in status.value
    assert "Assistant chat is still available" in status.value
    assert sum(callback.fn is gradio_app.chat for callback in app.fns.values()) == 2
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
