from datetime import datetime
from types import SimpleNamespace

import gradio as gr
import pytest

from service import scenarios
from service.managers import ShiftManager
from ui import gradio_app, settings

NOW = datetime(2026, 10, 7, 19, 42, tzinfo=gradio_app.TIMEZONE)


def manager_state() -> gr.State:
    """The app's selected-manager state."""
    return next(
        block
        for block in gradio_app.app.blocks.values()
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

    monkeypatch.setattr(gradio_app, "datetime", FixedDateTime)
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

    def load(key, manager_id=None):
        called.append(key)
        return context

    monkeypatch.setattr(gradio_app, "load_scenario", load)
    monkeypatch.setattr(
        gradio_app,
        "start_new_conversation",
        lambda manager_id=None: called.append("new conversation"),
    )
    current, history, draft, summary, *tables = gradio_app.load_selected_scenario(
        "backlog",
    )
    assert called == ["backlog", "new conversation"]
    assert current == context and len(tables) == 4
    assert context["title"] in summary
    assert history == [] and draft == ""

    def fail(key, manager_id=None):
        raise RuntimeError("internal connection details")

    monkeypatch.setattr(gradio_app, "load_scenario", fail)
    with pytest.raises(gr.Error, match="Scenario could not be loaded") as error:
        gradio_app.load_selected_scenario("rain")
    assert "internal connection details" not in str(error.value)
    assert called == ["backlog", "new conversation"]


def test_loaded_scenario_survives_a_conversation_start_failure(monkeypatch) -> None:
    context = scenarios.scenario_details("backlog")
    monkeypatch.setattr(
        gradio_app,
        "load_scenario",
        lambda key, manager_id=None: context,
    )

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "start_new_conversation", unavailable)
    current, history, *_ = gradio_app.load_selected_scenario("backlog")
    assert current == context and history == []


def test_invalid_preview_has_friendly_error() -> None:
    with pytest.raises(gr.Error, match="Could not prepare this scenario"):
        gradio_app.prepare_scenario("unknown")


def test_new_session_restores_saved_scenario_for_chat_and_tables(monkeypatch) -> None:
    context = scenarios.scenario_details("rain")
    monkeypatch.setattr(gradio_app, "current_scenario", lambda manager_id=None: context)
    current, selection, summary, *tables = gradio_app.restore_workspace()
    assert current == context
    assert context["title"] in summary
    assert selection["value"] == "rain"
    assert "Rain conditions" in summary
    assert len(tables[0]["data"]) == 12


def test_restore_distinguishes_empty_database_from_unavailable_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(gradio_app, "current_scenario", lambda manager_id=None: None)
    current, _, summary, *tables = gradio_app.restore_workspace()
    assert "No saved scenario" in summary
    assert current is None and all(table["data"] == [] for table in tables)

    def unavailable(manager_id=None):
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
    monkeypatch.setattr(gradio_app, "current_scenario", lambda manager_id=None: context)

    def no_yaml(key, manager_id=None):
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
    monkeypatch.setattr(gradio_app, "current_scenario", lambda manager_id=None: context)
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

    def fail(message, manager_id=None):
        raise RuntimeError("internal API details")

    monkeypatch.setattr(gradio_app, "ask_question", fail)
    with pytest.raises(gr.Error, match="assistant is unavailable") as error:
        gradio_app.chat("new question", history)
    assert history == [{"role": "user", "content": "earlier question"}]
    assert "internal API details" not in str(error.value)


def test_reply_uses_pending_message_once_and_keeps_previous_history(
    monkeypatch,
    frozen_now,
) -> None:
    calls = []

    def answer(question, manager_id=None):
        calls.append(question)
        return "reply", []

    monkeypatch.setattr(gradio_app, "ask_question", answer)
    previous = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    pending = previous + [{"role": "user", "content": "Follow-up"}]
    finished, draft, _ = next(gradio_app.respond_to_pending("Follow-up", pending))
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

    monkeypatch.setattr(gradio_app, "ask_question", fail)
    previous = [{"role": "assistant", "content": "Earlier reply"}]
    pending = previous + [{"role": "user", "content": "Try this"}]
    stream = gradio_app.respond_to_pending("Try this", pending)
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
        return "reply", []

    def no_scenario(*args, **kwargs):
        raise AssertionError("Chat must not read scenario data")

    monkeypatch.setattr(gradio_app, "ask_question", answer)
    monkeypatch.setattr(gradio_app, "current_scenario", no_scenario)
    earlier = [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    history, _, _ = gradio_app.chat("What should we do?", earlier)
    assert seen == ["What should we do?"]
    assert history == earlier + [
        {"role": "user", "content": timed("What should we do?")},
        {"role": "assistant", "content": timed("reply")},
    ]


def test_chat_callbacks_receive_no_scenario_state() -> None:
    callbacks = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.respond_to_pending
    ]
    assert len(callbacks) == 2
    # The message, displayed chat, selected manager, and selected chat ID.
    assert all(len(callback.inputs) == 4 for callback in callbacks)
    manager = manager_state()
    assert all(callback.inputs[2] is manager for callback in callbacks)
    assert all(
        not isinstance(component, gr.State)
        for callback in callbacks
        for component in callback.inputs[:2]
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


def test_page_load_restores_the_stored_conversation(monkeypatch, frozen_now) -> None:
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
        gradio_app,
        "conversation_history",
        lambda manager_id=None: stored,
    )
    assert gradio_app.restore_chat() == [
        {
            "role": "user",
            "content": timed("Should riders jump red lights?", "6 Oct, 21:05"),
        },
        {"role": "assistant", "content": timed("No. Safety comes first.", "08:10")},
    ]
    assert any(
        callback.fn is gradio_app.restore_conversation
        and any(isinstance(component, gr.Chatbot) for component in callback.outputs)
        for callback in gradio_app.app.fns.values()
    )


def test_page_load_starts_empty_when_storage_is_unavailable(monkeypatch) -> None:
    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "conversation_history", unavailable)
    assert gradio_app.restore_chat() == []


def test_sidebar_lists_past_chats_and_marks_the_open_one(monkeypatch) -> None:
    from uuid import uuid4

    open_id, other_id = uuid4(), uuid4()
    monkeypatch.setattr(
        gradio_app,
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
        gradio_app,
        "current_conversation_id",
        lambda manager_id=None: str(open_id),
    )
    update = gradio_app.conversation_choices()
    (title, value), other = update["choices"]
    assert value == str(open_id) and update["value"] == str(open_id)
    assert title == (
        "Orders are backing up, what should I do first and who should I call in?"
    )
    assert other == ("Rain plan?", str(other_id))

    # A new, empty chat is current but not listed, so nothing is marked.
    monkeypatch.setattr(
        gradio_app,
        "current_conversation_id",
        lambda manager_id=None: "new-chat",
    )
    assert gradio_app.conversation_choices()["value"] is None

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "past_conversations", unavailable)
    assert gradio_app.conversation_choices()["choices"] == []


def test_opening_a_past_conversation_shows_and_continues_it(
    monkeypatch,
    frozen_now,
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

    monkeypatch.setattr(gradio_app, "resume_past_conversation", resume)
    assert gradio_app.open_conversation("abc") == (
        [{"role": "user", "content": timed("Rain plan?", "19:30")}],
        "",
        "abc",
        gr.update(selected="assistant"),
    )
    assert opened == ["abc"]
    skipped = gradio_app.open_conversation(None)
    assert all(item == gr.skip() for item in skipped)

    def missing(conversation_id, manager_id=None):
        raise LookupError("private detail")

    monkeypatch.setattr(gradio_app, "resume_past_conversation", missing)
    with pytest.raises(gr.Error, match="Could not open that conversation") as error:
        gradio_app.open_conversation("abc")
    assert "private detail" not in str(error.value)
    picker = next(
        item
        for item in gradio_app.app.blocks.values()
        if isinstance(item, gr.Radio) and item.elem_id == "history-list"
    )
    assert any(
        callback.fn is gradio_app.open_conversation
        and (picker._id, "input") in callback.targets
        for callback in gradio_app.app.fns.values()
    )


def test_clear_chat_failure_keeps_the_conversation(monkeypatch) -> None:
    def unavailable(manager_id=None):
        raise RuntimeError("private connection information")

    monkeypatch.setattr(gradio_app, "start_new_conversation", unavailable)
    with pytest.raises(gr.Error, match="Could not start a new chat") as error:
        gradio_app.clear_chat()
    assert "private connection information" not in str(error.value)


def test_linked_chat_is_restored_without_using_the_latest_chat(monkeypatch):
    from types import SimpleNamespace

    opened = []
    monkeypatch.setattr(
        gradio_app,
        "open_conversation",
        lambda chat_id, manager_id=None: (
            opened.append(chat_id) or [{"role": "user", "content": "Rain?"}],
            "",
            chat_id,
            gr.update(selected="assistant"),
        ),
    )
    history, chat_id = gradio_app.restore_conversation(
        gradio_app.DEMO_MANAGER_ID,
        SimpleNamespace(query_params={"chat": "older-chat", "view": "settings"}),
    )
    assert opened == ["older-chat"] and chat_id == "older-chat"
    assert history == [{"role": "user", "content": "Rain?"}]


def test_chat_browser_metadata_uses_selected_chat(monkeypatch):
    seen = []
    monkeypatch.setattr(
        gradio_app,
        "conversation_details",
        lambda chat_id, manager_id=None: (
            seen.append(chat_id) or {"id": chat_id, "title": "Rain response"}
        ),
    )
    assert gradio_app.conversation_location(
        gradio_app.DEMO_MANAGER_ID,
        "older-chat",
    ) == {
        "id": "older-chat",
        "title": "Rain response",
    }
    assert seen == ["older-chat"]
    assert gradio_app.conversation_location(gradio_app.DEMO_MANAGER_ID, None) == {
        "id": None,
        "title": "New chat",
    }


def test_chat_preserves_nonblank_message_whitespace(monkeypatch) -> None:
    seen = []

    def answer(question, manager_id=None):
        seen.append(question)
        return "reply", []

    monkeypatch.setattr(gradio_app, "ask_question", answer)
    message = "  Should riders speed?\n"
    history, draft, _ = gradio_app.chat(message, [])
    assert seen == [message]
    assert history[0]["content"].startswith(message)
    assert draft == ""
    assert gradio_app.chat(" \n\t", history)[:2] == (history, "")
    assert seen == [message]


def test_new_chat_waits_for_outstanding_workspace_callbacks(monkeypatch) -> None:
    started = []

    def start_new(manager_id=None):
        started.append(True)
        return "new-chat-id"

    monkeypatch.setattr(
        gradio_app,
        "start_new_conversation",
        start_new,
    )
    clear = next(
        item
        for item in gradio_app.app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "new-chat"
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
    assert callback.fn is gradio_app.clear_chat
    assert callback.fn() == ([], "", "new-chat-id", gr.update(selected="assistant"))
    assert started == [True]
    assert all(
        chat_callback.queue
        and chat_callback.concurrency_id == callback.concurrency_id
        and chat_callback.concurrency_limit == callback.concurrency_limit
        for chat_callback in gradio_app.app.fns.values()
        if chat_callback.fn is gradio_app.respond_to_pending
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
        sum(
            callback.fn is gradio_app.respond_to_pending
            for callback in app.fns.values()
        )
        == 2
    )


def test_dropdown_lists_all_scenarios_and_handles_empty_inventory(monkeypatch) -> None:
    dropdown = next(
        item
        for item in gradio_app.app.blocks.values()
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
def test_launch_supports_railway_port(monkeypatch, port, expected) -> None:
    if port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port)
    options = {}
    monkeypatch.setattr(
        gradio_app.app,
        "launch",
        lambda **kwargs: options.update(kwargs),
    )
    gradio_app.main()
    assert options["server_name"] == "0.0.0.0"
    assert options["server_port"] == expected
    assert options["share"] is False
    assert options["css_paths"] == gradio_app.CSS_PATH


def test_times_are_shown_in_ist_with_the_day_for_older_messages() -> None:
    from datetime import UTC

    assert gradio_app._time_label(datetime(2026, 10, 7, 14, 12, tzinfo=UTC), NOW) == (
        "19:42"
    )
    assert gradio_app._time_label(datetime(2026, 9, 30, 4, 0, tzinfo=UTC), NOW) == (
        "30 Sep, 09:30"
    )


def test_sidebar_prefers_the_title_over_the_first_question() -> None:
    assert (
        gradio_app._conversation_title(
            {"title": "Rain backlog with two riders", "first_question": "It's pouring"},
        )
        == "Rain backlog with two riders"
    )
    assert (
        gradio_app._conversation_title(
            {"title": None, "first_question": "It's pouring"},
        )
        == "It's pouring"
    )


def test_title_runs_after_the_answer_and_refreshes_the_sidebar(monkeypatch) -> None:
    titled = []
    monkeypatch.setattr(
        gradio_app,
        "title_latest_conversation",
        lambda manager_id=None: titled.append(True),
    )
    monkeypatch.setattr(
        gradio_app,
        "conversation_choices",
        lambda manager_id=None: "choices",
    )
    assert gradio_app.title_conversation() == "choices"
    assert titled == [True]

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "title_latest_conversation", unavailable)
    assert gradio_app.title_conversation() == "choices"
    callbacks = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.title_conversation
    ]
    assert len(callbacks) == 2
    assert all(callback.concurrency_id == "titles" for callback in callbacks)


def test_summary_runs_after_the_answer_on_its_own_queue(monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(
        gradio_app,
        "summarize_latest_conversation",
        lambda manager_id=None: ran.append(True),
    )
    gradio_app.summarize_conversation()
    assert ran == [True]

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "summarize_latest_conversation", unavailable)
    gradio_app.summarize_conversation()
    callbacks = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.summarize_conversation
    ]
    assert len(callbacks) == 2
    assert all(callback.concurrency_id == "summaries" for callback in callbacks)


def test_summary_bar_is_hidden_for_an_empty_chat(monkeypatch) -> None:
    monkeypatch.setattr(
        gradio_app,
        "conversation_summary",
        lambda manager_id=None: None,
    )
    bar, _, text, _ = gradio_app.summary_card()
    assert bar == gr.update(visible=False) and text == ""

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "conversation_summary", unavailable)
    assert gradio_app.summary_card()[0] == gr.update(visible=False)


def test_unsummarized_chat_offers_to_summarize(monkeypatch) -> None:
    monkeypatch.setattr(
        gradio_app,
        "conversation_summary",
        lambda manager_id=None: {
            "summary": None,
            "covered": 0,
            "total": 4,
            "summarized_at": None,
        },
    )
    bar, box, text, button = gradio_app.summary_card()
    assert bar == gr.update(visible=True)
    assert box == gr.update(label="Not summarized yet · 4 messages")
    assert "**Summarize now**" in text
    assert button == gr.update(value="Summarize now")


def test_summary_card_shows_coverage_and_update_time(monkeypatch, frozen_now) -> None:
    monkeypatch.setattr(
        gradio_app,
        "conversation_summary",
        lambda manager_id=None: {
            "summary": "- Standby rider approved.",
            "covered": 12,
            "total": 30,
            "summarized_at": datetime(2026, 10, 7, 19, 30, tzinfo=gradio_app.TIMEZONE),
        },
    )
    _, box, text, button = gradio_app.summary_card()
    assert box == gr.update(
        label="Summary of earlier messages · covers 12 of 30 · updated 19:30",
    )
    assert text.startswith("- Standby rider approved.")
    assert "The full messages are below." in text
    assert button == gr.update(value="Update summary")


def test_summarize_now_updates_and_opens_the_card(monkeypatch, frozen_now) -> None:
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
                    tzinfo=gradio_app.TIMEZONE,
                ),
            },
        ],
    )
    monkeypatch.setattr(
        gradio_app,
        "summarize_open_conversation",
        lambda manager_id=None: True,
    )
    monkeypatch.setattr(
        gradio_app,
        "conversation_summary",
        lambda manager_id=None: next(views),
    )
    _, box, text, _ = gradio_app.summarize_now()
    assert box == gr.update(
        label="Summary of earlier messages · covers 30 of 30 · updated 19:42",
        open=True,
    )
    assert text.startswith("- Now includes the latest exchange.")


def test_summarize_now_reports_when_nothing_is_new(monkeypatch) -> None:
    notices: list[str] = []
    monkeypatch.setattr(gradio_app.gr, "Info", notices.append)
    monkeypatch.setattr(
        gradio_app,
        "summarize_open_conversation",
        lambda manager_id=None: False,
    )
    monkeypatch.setattr(
        gradio_app,
        "conversation_summary",
        lambda manager_id=None: None,
    )
    gradio_app.summarize_now()
    assert notices == ["The summary already covers every message."]

    def failing(manager_id=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(gradio_app, "summarize_open_conversation", failing)
    with pytest.raises(gr.Error, match="Could not summarize this chat"):
        gradio_app.summarize_now()


def test_summary_bar_is_pinned_collapsed_and_refreshed_with_the_chat() -> None:
    def block(kind, elem_id):
        return next(
            item
            for item in gradio_app.app.blocks.values()
            if isinstance(item, kind) and item.elem_id == elem_id
        )

    bar = block(gr.Row, "chat-summary-bar")
    card = block(gr.Accordion, "chat-summary")
    button = block(gr.Button, "summarize-now")
    assert bar.visible is False and card.open is False
    refreshers = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is gradio_app.summary_card
    ]
    # Page load, switching manager, opening a chat, a scenario load, New chat,
    # starting a chat from an alert, and after each answer.
    assert len(refreshers) == 8
    assert all(bar in callback.outputs for callback in refreshers)
    [click] = [
        callback
        for callback in gradio_app.app.fns.values()
        if (button._id, "click") in callback.targets
    ]
    assert click.fn is gradio_app.summarize_now
    assert click.concurrency_id == "summaries"


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
        gradio_app,
        "ask_question",
        lambda q, manager_id=None: ("Proposed.", [change]),
    )
    _, _, pending = gradio_app.chat("Alert me below 85", [])
    assert pending == [CHANGE]
    monkeypatch.setattr(
        gradio_app,
        "ask_question",
        lambda q, manager_id=None: ("No change.", []),
    )
    assert gradio_app.chat("Thanks", [])[2] == gr.skip()


def test_pending_card_shows_old_and_new_values_escaped() -> None:
    html, box = gradio_app.pending_card([CHANGE])
    assert "Proposed setting change" in html and "Not saved until you confirm" in html
    assert "SLA dip" in html and "off" in html
    assert "on, below 85% &lt;b&gt;" in html
    assert box["visible"] is True
    two, _ = gradio_app.pending_card([CHANGE, CHANGE])
    assert "2 proposed setting changes" in two
    assert gradio_app.pending_card([]) == ("", gr.update(visible=False))


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

    monkeypatch.setattr(gradio_app, "confirm_proposals", confirm)
    monkeypatch.setattr(gradio_app, "add_note", note)
    history, pending = gradio_app.confirm_pending([CHANGE], [])
    assert saved == [CHANGE] and pending == []
    assert notes == ["Saved. SLA dip: on, below 85%."]
    assert history == [
        {"role": "assistant", "content": timed("Saved. SLA dip: on, below 85%.")},
    ]
    assert gradio_app.confirm_pending([], history) == (history, [])


def test_confirm_failure_keeps_the_card(monkeypatch) -> None:
    def fail(states, manager_id=None):
        raise RuntimeError("database password in message")

    monkeypatch.setattr(gradio_app, "confirm_proposals", fail)
    with pytest.raises(gr.Error, match="Could not save the settings") as error:
        gradio_app.confirm_pending([CHANGE], [])
    assert "password" not in str(error.value)


def test_cancel_saves_nothing_and_still_shows_a_note_when_storage_fails(
    monkeypatch,
    frozen_now,
) -> None:
    def unavailable(text, manager_id=None):
        raise RuntimeError("no database")

    monkeypatch.setattr(gradio_app, "add_note", unavailable)
    monkeypatch.setattr(
        gradio_app,
        "confirm_proposals",
        lambda states, manager_id=None: pytest.fail("Cancel must not save"),
    )
    history, pending = gradio_app.cancel_pending([CHANGE], [])
    assert pending == []
    assert history == [
        {
            "role": "assistant",
            "content": timed("Cancelled. Nothing was changed (SLA dip)."),
        },
    ]
    assert gradio_app.cancel_pending(None, None) == ([], [])


def test_confirmation_buttons_and_card_are_wired() -> None:
    def callbacks(fn):
        return [c for c in gradio_app.app.fns.values() if c.fn is fn]

    [confirm] = callbacks(gradio_app.confirm_pending)
    [cancel] = callbacks(gradio_app.cancel_pending)
    for callback in (confirm, cancel):
        assert callback.concurrency_id == "workspace"
        assert isinstance(callback.inputs[0], gr.State)
        assert isinstance(callback.outputs[1], gr.State)
    [card] = callbacks(gradio_app.pending_card)
    assert card.inputs == [confirm.inputs[0]]
    replies = callbacks(gradio_app.respond_to_pending)
    assert all(reply.outputs[2] is confirm.inputs[0] for reply in replies)


MANAGERS = [
    ShiftManager("ananya", "Ananya Rao", "SHIFT-MOR", "Morning", "06:00", "14:00"),
    ShiftManager("karthik", "Karthik Reddy", "SHIFT-EVE", "Evening", "14:00", "22:00"),
    ShiftManager("imran", "Imran Shaikh", "SHIFT-NGT", "Night", "22:00", "06:00"),
]


def request(**params):
    return SimpleNamespace(query_params=params)


def test_page_load_picks_the_manager_from_the_url(monkeypatch) -> None:
    monkeypatch.setattr(gradio_app, "store_managers", lambda: MANAGERS)
    manager_id, picker, badge = gradio_app.restore_manager(request(manager="imran"))
    assert manager_id == "imran" and picker["value"] == "imran"
    assert [value for _, value in picker["choices"]] == ["ananya", "karthik", "imran"]
    assert picker["choices"][2][0] == "Imran Shaikh\nNight shift · 22:00–06:00"
    assert "Imran Shaikh" in badge and "SHIFT-NGT" in badge
    # Unknown or missing ids fall back to the demo manager.
    assert gradio_app.restore_manager(request(manager="nobody"))[0] == "karthik"
    assert gradio_app.restore_manager(request())[0] == "karthik"


def test_page_load_without_managers_keeps_working(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("database down")

    monkeypatch.setattr(gradio_app, "store_managers", unavailable)
    manager_id, picker, badge = gradio_app.restore_manager(request(manager="imran"))
    assert manager_id == gradio_app.DEMO_MANAGER_ID
    assert picker["choices"] == [] and "No manager available" in badge


def test_switching_manager_accepts_only_the_store_managers(monkeypatch) -> None:
    monkeypatch.setattr(gradio_app, "store_managers", lambda: MANAGERS)
    manager_id, badge = gradio_app.select_manager("ananya")
    assert manager_id == "ananya" and "Morning shift" in badge
    with pytest.raises(gr.Error, match="not available"):
        gradio_app.select_manager("someone-else")


def test_chat_and_confirmation_use_the_selected_manager(
    monkeypatch,
    frozen_now,
) -> None:
    seen = []

    def answer(question, manager_id=None):
        seen.append(("ask", manager_id))
        return "reply", []

    def confirm(states, manager_id=None):
        seen.append(("confirm", manager_id))
        return ["Saved."]

    def note(text, manager_id=None):
        seen.append(("note", manager_id))

    monkeypatch.setattr(gradio_app, "ask_question", answer)
    monkeypatch.setattr(gradio_app, "confirm_proposals", confirm)
    monkeypatch.setattr(gradio_app, "add_note", note)
    next(gradio_app.respond_to_pending("Hi", [], "imran"))
    gradio_app.confirm_pending([CHANGE], [], "imran")
    gradio_app.cancel_pending([CHANGE], [], "imran")
    assert seen == [
        ("ask", "imran"),
        ("confirm", "imran"),
        ("note", "imran"),
        ("note", "imran"),
    ]


def test_every_manager_scoped_callback_receives_the_selected_manager() -> None:
    manager = manager_state()
    scoped = {
        gradio_app.restore_conversation,
        gradio_app.restore_latest_conversation,
        gradio_app.conversation_choices,
        gradio_app.open_conversation,
        gradio_app.title_conversation,
        gradio_app.summary_card,
        gradio_app.summarize_now,
        gradio_app.summarize_conversation,
        gradio_app.clear_chat,
        gradio_app.respond_to_pending,
        gradio_app.confirm_pending,
        gradio_app.cancel_pending,
        gradio_app.load_selected_scenario,
        settings.load_settings,
        settings.load_summary,
        settings.save_settings,
    }
    seen = set()
    for callback in gradio_app.app.fns.values():
        if callback.fn in scoped:
            assert manager in callback.inputs, callback.fn.__name__
            seen.add(callback.fn)
    assert seen == scoped
    resets = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is not None
        and callback.fn.__name__ == "<lambda>"
        and callback.inputs == [manager]
        and callback.outputs
        and len(callback.outputs) > 10
    ]
    assert len(resets) == len(settings.ALERT_CODES) + 3


def test_switching_manager_clears_the_card_and_reloads_their_workspace() -> None:
    picker = next(
        block
        for block in gradio_app.app.blocks.values()
        if isinstance(block, gr.Dropdown) and block.elem_id == "manager-picker"
    )
    [switch] = [
        callback
        for callback in gradio_app.app.fns.values()
        if (picker._id, "input") in callback.targets
    ]
    assert switch.fn is gradio_app.select_manager
    loaders = [
        callback.fn
        for callback in gradio_app.app.fns.values()
        if callback.fn
        in (
            gradio_app.restore_conversation,
            gradio_app.restore_latest_conversation,
            gradio_app.conversation_choices,
            settings.load_settings,
            settings.load_summary,
        )
        and callback.trigger_after is not None
    ]
    # Page load and switching manager each reload chat, chats and settings.
    assert loaders.count(gradio_app.restore_conversation) == 1
    assert loaders.count(gradio_app.restore_latest_conversation) == 1
    assert loaders.count(settings.load_settings) >= 2
    # The pending-change card is cleared right after the switch.
    [clear] = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.fn is list and callback.trigger_after == switch._id
    ]
    assert isinstance(clear.outputs[0], gr.State)


def test_switching_manager_records_the_choice_in_the_url() -> None:
    picker = next(
        block
        for block in gradio_app.app.blocks.values()
        if isinstance(block, gr.Dropdown) and block.elem_id == "manager-picker"
    )
    [url] = [
        callback
        for callback in gradio_app.app.fns.values()
        if callback.js == gradio_app.MANAGER_URL_JS
    ]
    # State values never reach the browser, so the URL reads the picker.
    assert url.inputs == [picker]
