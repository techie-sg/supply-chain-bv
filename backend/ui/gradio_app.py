"""Local DispatchDesk workspace for scenario data and dispatch guidance."""

import os
from collections.abc import Iterator, Sequence
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import gradio as gr
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from logging_config import configure_logging, configure_uvicorn_logging
from service.conversations import (
    add_note,
    ask_question,
    conversation_details,
    conversation_history,
    conversation_summary,
    current_conversation_id,
    past_conversations,
    resume_past_conversation,
    start_new_conversation,
    title_latest_conversation,
)
from service.managers import ShiftManager, choose_manager, store_managers
from service.scenarios import (
    TIMEZONE,
    current_scenario,
    load_scenario,
    scenario_details,
    scenario_names,
)
from service.setting_changes import confirm_proposals
from service.summaries import (
    summarize_latest_conversation,
    summarize_open_conversation,
)
from ui import settings
from ui import suggestions as suggestions_ui

logger = structlog.stdlib.get_logger(__name__)
CSS_PATH = Path(__file__).with_name("gradio_app.css")
THEME = gr.themes.Base(
    primary_hue="indigo",
    secondary_hue="violet",
    neutral_hue="slate",
    # Gradio 6.29 compares the first font with built-in Font objects at launch.
    font=[gr.themes.GoogleFont("DM Sans", weights=[400, 500, 600, 700]), "sans-serif"],
).set(
    body_background_fill="#18181a",
    body_background_fill_dark="#18181a",
    body_text_color="#eeedf0",
    body_text_color_dark="#eeedf0",
    body_text_color_subdued="#c0bec7",
    body_text_color_subdued_dark="#c0bec7",
    block_background_fill="#232325",
    block_background_fill_dark="#232325",
    block_border_color="#363539",
    block_border_color_dark="#363539",
    block_radius="12px",
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    block_label_text_color="#c0bec7",
    block_label_text_color_dark="#c0bec7",
    input_background_fill="#232325",
    input_background_fill_dark="#232325",
    input_border_color="#3e3c43",
    input_border_color_dark="#3e3c43",
    button_primary_background_fill="#b4a3de",
    button_primary_background_fill_dark="#b4a3de",
    button_primary_background_fill_hover="#c5b6e9",
    button_primary_background_fill_hover_dark="#c5b6e9",
    button_primary_text_color="#211c2c",
    button_primary_text_color_dark="#211c2c",
    button_primary_border_color="#b4a3de",
    button_primary_border_color_dark="#b4a3de",
    button_secondary_background_fill="#232325",
    button_secondary_background_fill_dark="#232325",
    button_secondary_background_fill_hover="#302c38",
    button_secondary_background_fill_hover_dark="#302c38",
    button_secondary_text_color="#ded9e7",
    button_secondary_text_color_dark="#ded9e7",
    button_border_width="1px",
    button_large_radius="10px",
)


CHAT_PLACEHOLDER = """
<div class="chat-welcome">
    <div class="welcome-orbit" aria-hidden="true"><span class="welcome-emblem"></span></div>
    <h1>Let’s make <span>the right call.</span></h1>
    <p>Ask a question or talk through a decision.<br>Get clear guidance grounded in your playbook.</p>
</div>
"""


PROCESSING_STATUS = """
<div class="thinking-indicator" role="status" aria-live="polite">
    <span class="thinking-spark" aria-hidden="true">✦</span>
    <span>Thinking…</span>
</div>
"""


SEND_MESSAGE_JS = """
(message, history) => {
    const busy = Boolean(message.trim());
    const update = (props) => ({__type__: 'update', ...props});
    return [
        busy ? message : '',
        busy ? [...(history || []), {role: 'user', content: [{type: 'text', text: message}]}] : (history || []),
        update({value: '', interactive: !busy}),
        update({interactive: !busy}),
        update({visible: busy})
    ];
}
"""

# On phones the sidebar covers the chat, so close it on load and after a choice.
CLOSE_SIDEBAR_ON_PHONE_JS = """
() => {
    if (!matchMedia('(max-width: 768px)').matches) return;
    if (document.querySelector('#chat-sidebar.open')) {
        document.querySelector('#chat-sidebar .toggle-button')?.click();
    }
}
"""

FINISH_CHAT_JS = """
() => [
    {__type__: 'update', visible: false},
    {__type__: 'update', interactive: true},
    {__type__: 'update', interactive: true}
]
"""


def _scenario_summary(context: dict[str, Any]) -> str:
    weather_pill = ""
    if "is_raining" in context:
        raining = context["is_raining"]
        weather = "Rain conditions" if raining else "Dry conditions"
        weather_class = "rain" if raining else "dry"
        weather_pill = (
            f'<span class="weather-pill {weather_class}"><span aria-hidden="true">'
            f"{'☂' if raining else '☀'}</span> {weather}</span>"
        )
    source = "SCENARIO PREVIEW" if "is_raining" in context else "CURRENT SCENARIO"
    return (
        '<section class="scenario-preview" aria-label="Scenario data">'
        f'<div class="preview-heading"><div><span class="section-kicker">{source}</span>'
        f"<h2>{escape(context['title'])}</h2>"
        f'<span class="store-badge">Store <strong>{escape(context["store_id"])}</strong></span></div>'
        f"{weather_pill}</div>"
        "</section>"
    )


def _table_views(context: dict[str, Any] | None) -> tuple[dict, dict, dict, dict]:
    if context is None:
        return (
            {"headers": [], "data": []},
            {"headers": [], "data": []},
            {"headers": [], "data": []},
            {"headers": [], "data": []},
        )
    priority = {
        "orders": [
            "order_id",
            "status",
            "zone_id",
            "item_count",
            "has_frozen_items",
            "assigned_rider_id",
            "placed_at",
        ],
        "riders": [
            "rider_id",
            "name",
            "status",
            "current_zone",
            "employment_type",
            "hours_on_shift",
            "minutes_since_last_break",
            "deliveries_today",
            "eta_back_min",
        ],
        "hourly_metrics": [
            "date",
            "hour",
            "orders",
            "avg_pick_pack_min",
            "avg_rider_wait_min",
            "avg_ride_min",
            "sla_10min_pct",
            "riders_online",
            "rain_flag",
        ],
        "zones": [
            "zone_id",
            "zone_name",
            "distance_from_store_km",
            "avg_ride_min_dry",
            "avg_ride_min_rain",
        ],
    }
    labels = {
        "has_frozen_items": "Frozen items",
        "assigned_rider_id": "Assigned rider",
        "item_count": "Items",
        "minutes_since_last_break": "Break ago (min)",
        "current_zone": "Zone",
        "employment_type": "Employment",
        "hours_on_shift": "Shift (h)",
        "deliveries_today": "Deliveries",
        "eta_back_min": "Return (min)",
        "scenario_key": "Scenario",
        "avg_pick_pack_min": "Pick / pack (min)",
        "avg_rider_wait_min": "Rider wait (min)",
        "avg_ride_min": "Ride (min)",
        "riders_online": "Riders online",
        "rain_flag": "Rain",
        "distance_from_store_km": "Distance (km)",
        "avg_ride_min_dry": "Dry ride (min)",
        "avg_ride_min_rain": "Rain ride (min)",
        "sla_10min_pct": "10-min SLA (%)",
    }
    views = []
    for name in ("orders", "riders", "hourly_metrics", "zones"):
        table = context["tables"][name]
        columns = priority[name] + [
            header
            for header in table["headers"]
            if header not in priority[name] and header not in {"as_of", "store_id"}
        ]
        indices = [table["headers"].index(column) for column in columns]
        views.append(
            {
                "headers": [
                    labels.get(
                        header,
                        header.replace("_", " ").title().replace(" Id", " ID"),
                    )
                    for header in columns
                ],
                "data": [[row[index] for index in indices] for row in table["data"]],
            },
        )
    return views[0], views[1], views[2], views[3]


def prepare_scenario(
    key: str,
    current: dict[str, Any] | None = None,
) -> tuple[str, dict, dict, dict, dict]:
    """Read current rows from the database; preview other starting scenarios."""
    try:
        if current and key == current["scenario_key"]:
            context = current_scenario()
            if context is None:
                raise RuntimeError("The saved scenario is no longer available")
        else:
            context = scenario_details(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not prepare scenario %s", key)
        raise gr.Error(
            "Could not prepare this scenario. Check its configuration and database connection.",
        ) from exc
    return _scenario_summary(context), *_table_views(context)


def load_selected_scenario(
    key: str,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[dict[str, Any], list[dict], str, str, dict, dict, dict, dict]:
    """Load through the existing service, starting a new conversation on success."""
    try:
        context = load_scenario(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load scenario %s", key)
        raise gr.Error(
            "Scenario could not be loaded. Check the database connection and migrations.",
        ) from exc
    try:
        start_new_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        # The scenario is loaded; the next question continues the previous chat.
        logger.exception("Could not start a conversation after loading %s", key)
    return (
        context,
        [],
        "",
        _scenario_summary(context),
        *_table_views(context),
    )


def restore_workspace() -> tuple:
    """Refresh saved data without replacing rows or changing the conversation."""
    try:
        context = current_scenario()
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError):
        logger.warning("Could not restore the saved scenario", exc_info=True)
        empty_message = "Saved scenario unavailable. Check the database connection and refresh again."
        context = None
    else:
        empty_message = "No saved scenario. Choose and load a scenario to get started."
    if context:
        return (
            context,
            gr.update(value=context["scenario_key"]),
            _scenario_summary(context),
            *_table_views(context),
        )
    return (
        None,
        gr.skip(),
        f'<p class="muted">{empty_message}</p>',
        *_table_views(None),
    )


def _time_label(when: datetime, now: datetime) -> str:
    """Time for today's messages; day and time for older ones, in IST."""
    when = when.astimezone(TIMEZONE)
    if when.date() == now.astimezone(TIMEZONE).date():
        return when.strftime("%H:%M")
    return f"{when.day} {when.strftime('%b, %H:%M')}"


def _with_time(text: str, when: datetime, now: datetime) -> str:
    """Message text with its time below it; shown only, never sent to the model."""
    return f'{text}\n\n<span class="message-time">{_time_label(when, now)}</span>'


def to_display(messages: Sequence[dict[str, str]]) -> list[dict]:
    """Stored {who, what, when} messages as chat bubbles with their times."""
    now = datetime.now(TIMEZONE)
    return [
        {
            "role": "user" if message["who"] == "manager" else "assistant",
            "content": _with_time(
                message["what"],
                datetime.fromisoformat(message["when"]),
                now,
            ),
        }
        for message in messages
    ]


def restore_chat(manager_id: str = DEMO_MANAGER_ID) -> list[dict]:
    """Show the manager's latest stored conversation."""
    try:
        return to_display(conversation_history(manager_id=manager_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not restore the conversation", exc_info=True)
        return []


def _conversation_title(item: dict[str, Any]) -> str:
    """Keep the full title for hover; CSS clips it to one line in the list."""
    return " ".join((item.get("title") or item["first_question"] or "").split())


def _chat_scope(manager_id: str, conversation_id: str | None) -> dict[str, Any]:
    return {
        "manager_id": manager_id,
        **({"conversation_id": conversation_id} if conversation_id else {}),
    }


def conversation_choices(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> dict:
    """Sidebar list of the manager's past chats, highlighting the open one."""
    try:
        items = past_conversations(manager_id=manager_id)
        current = conversation_id or current_conversation_id(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not list past conversations", exc_info=True)
        items, current = [], None
    choices = [(_conversation_title(item), str(item["id"])) for item in items]
    return gr.update(
        choices=choices,
        value=current if current in {value for _, value in choices} else None,
    )


def open_conversation(
    conversation_id: str | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[list[dict], str, str, dict]:
    """Show a past conversation and make it the one new questions continue."""
    if not conversation_id:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()
    try:
        messages = resume_past_conversation(conversation_id, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError) as exc:
        logger.exception("Could not open conversation %s", conversation_id)
        raise gr.Error("Could not open that conversation. Please try again.") from exc
    return to_display(messages), "", conversation_id, gr.update(selected="assistant")


def restore_latest_conversation(manager_id: str) -> tuple[list[dict], str | None]:
    history = restore_chat(manager_id)
    try:
        return history, current_conversation_id(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        return history, None


def restore_conversation(
    manager_id: str,
    request: gr.Request,
) -> tuple[list[dict], str | None]:
    conversation_id = request.query_params.get("chat")
    if conversation_id:
        history, _, _, _ = open_conversation(conversation_id, manager_id)
        return history, conversation_id
    return restore_latest_conversation(manager_id)


def conversation_location(
    manager_id: str,
    conversation_id: str | None,
) -> dict[str, str | None]:
    try:
        item = (
            conversation_details(conversation_id, manager_id=manager_id)
            if conversation_id
            else {}
        )
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError):
        logger.warning("Could not load the chat title", exc_info=True)
        item = {}
    return {"id": conversation_id, "title": item.get("title") or "New chat"}


def title_conversation(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> dict:
    """Title the open chat after its first answer, then refresh the sidebar."""
    try:
        title_latest_conversation(**_chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not title the conversation", exc_info=True)
    return conversation_choices(**_chat_scope(manager_id, conversation_id))


SUMMARY_NOTE = (
    '\n\n<p class="summary-note">Written by the assistant for its own context. '
    "The full messages are below.</p>"
)


def summary_card(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[dict, dict, str, dict]:
    """The pinned summary row: hidden for an empty chat, otherwise its state."""
    hidden = (gr.update(visible=False), gr.skip(), "", gr.skip())
    try:
        view = conversation_summary(**_chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load the conversation summary", exc_info=True)
        return hidden
    if view is None:
        return hidden
    if view["summary"] is None:
        return (
            gr.update(visible=True),
            gr.update(label=f"Not summarized yet · {view['total']} messages"),
            "No summary yet. **Summarize now** folds this chat's messages into one.",
            gr.update(value="Summarize now"),
        )
    label = f"Summary of earlier messages · covers {view['covered']} of {view['total']}"
    if view["summarized_at"] is not None:
        label += (
            f" · updated {_time_label(view['summarized_at'], datetime.now(TIMEZONE))}"
        )
    return (
        gr.update(visible=True),
        gr.update(label=label),
        view["summary"] + SUMMARY_NOTE,
        gr.update(value="Update summary"),
    )


def summarize_now(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[dict, dict, str, dict]:
    """Fold every message of the open chat into its summary, then show it open."""
    try:
        updated = summarize_open_conversation(
            **_chat_scope(manager_id, conversation_id),
        )
    except (
        SQLAlchemyError,
        RuntimeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        logger.exception("Could not summarize the conversation on request")
        raise gr.Error("Could not summarize this chat. Please try again.") from exc
    if not updated:
        gr.Info("The summary already covers every message.")
    bar, box, text, button = summary_card(**_chat_scope(manager_id, conversation_id))
    if updated:
        box = gr.update(label=box["label"], open=True)
    return bar, box, text, button


def summarize_conversation(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> None:
    """After an answer, fold older messages into the summary if over a limit."""
    try:
        summarize_latest_conversation(**_chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError, ValueError, requests.RequestException):
        logger.warning("Could not summarize the conversation", exc_info=True)


def clear_chat(manager_id: str = DEMO_MANAGER_ID) -> tuple[list[dict], str, str, dict]:
    """Start a new stored conversation; the previous one is kept."""
    try:
        conversation_id = start_new_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not start a new conversation")
        raise gr.Error("Could not start a new chat. Please try again.") from exc
    return [], "", conversation_id, gr.update(selected="assistant")


def chat(
    message: str,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], str, Any]:
    """Answer from stored history; keep the draft and display intact on failure.

    The third value is the setting changes the answer proposed, for the
    confirmation card; it is left as it was when the answer proposed none.
    """
    history = history or []
    if not message.strip():
        return history, "", gr.skip()
    request_id = uuid4().hex
    try:
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            answer, proposals = ask_question(
                message,
                **_chat_scope(manager_id, conversation_id),
            )
    except (
        requests.RequestException,
        SQLAlchemyError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as exc:
        logger.exception("Assistant request failed", request_id=request_id)
        raise gr.Error(
            "The assistant is unavailable right now. Please try again.",
        ) from exc
    now = datetime.now(TIMEZONE)
    pending = [change.to_state() for change in proposals] if proposals else gr.skip()
    return (
        history
        + [
            {"role": "user", "content": _with_time(message, now, now)},
            {"role": "assistant", "content": _with_time(answer, now, now)},
        ],
        "",
        pending,
    )


def respond_to_pending(
    message: str,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> Iterator[tuple[list[dict], str, Any]]:
    """Answer the message already displayed by the browser without duplicating it."""
    history = history or []
    if not message.strip():
        yield history, "", gr.skip()
        return
    previous = (
        history[:-1] if history and history[-1].get("role") == "user" else history
    )
    try:
        yield chat(message, previous, manager_id, conversation_id)
    except gr.Error:
        yield previous, message, gr.skip()
        raise


def pending_card(pending: list[dict] | None) -> tuple[str, dict]:
    """The proposed setting changes, old to new, waiting for Confirm or Cancel."""
    if not pending:
        return "", gr.update(visible=False)
    items = "".join(
        f"<li><strong>{escape(change['name'])}</strong>"
        f'<span class="change-from">{escape(change["before"])}</span>'
        '<span class="change-arrow" aria-label="changes to">→</span>'
        f'<span class="change-to">{escape(change["after"])}</span></li>'
        for change in pending
    )
    title = (
        "Proposed setting change"
        if len(pending) == 1
        else f"{len(pending)} proposed setting changes"
    )
    card = (
        f'<div class="pending-card"><p class="pending-title">{title}'
        "<span>Not saved until you confirm</span></p>"
        f"<ul>{items}</ul></div>"
    )
    return card, gr.update(visible=True)


def _with_note(
    history: list[dict] | None,
    text: str,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> list[dict]:
    """Store an assistant note in the manager's open chat and show it."""
    now = datetime.now(TIMEZONE)
    try:
        stored = add_note(text, **_chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError, LookupError):
        logger.warning("Could not store the note in the conversation", exc_info=True)
        stored = None
    when = datetime.fromisoformat(stored["when"]) if stored else now
    return (history or []) + [
        {"role": "assistant", "content": _with_time(text, when, now)},
    ]


def confirm_pending(
    pending: list[dict] | None,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], list]:
    """Save the proposed setting changes the manager confirmed."""
    if not pending:
        return history or [], []
    try:
        results = confirm_proposals(pending, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not save confirmed setting changes")
        raise gr.Error("Could not save the settings. Please try again.") from exc
    return _with_note(history, "\n\n".join(results), manager_id, conversation_id), []


def cancel_pending(
    pending: list[dict] | None,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], list]:
    """Discard the proposed setting changes; nothing is saved."""
    if not pending:
        return history or [], []
    names = ", ".join(change["name"] for change in pending)
    return (
        _with_note(
            history,
            f"Cancelled. Nothing was changed ({names}).",
            manager_id,
            conversation_id,
        ),
        [],
    )


def _manager_label(manager: ShiftManager) -> str:
    return (
        f"{manager.name}\n{manager.shift_name} shift · "
        f"{manager.shift_start}–{manager.shift_end}"
    )


def manager_badge(manager: ShiftManager | None) -> str:
    """Who is signed in to the workspace, and their shift."""
    if manager is None:
        return '<div class="manager-badge"><span>No manager available</span></div>'
    return (
        '<div class="manager-badge"><span>Manager</span>'
        f"<strong>{escape(manager.name)}</strong>"
        f'<em data-shift-id="{escape(manager.shift_id)}">'
        f"{escape(manager.shift_name)} shift · {escape(manager.shift_start)}–"
        f"{escape(manager.shift_end)}</em></div>"
    )


def _managers() -> list[ShiftManager]:
    try:
        return store_managers()
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not list managers", exc_info=True)
        return []


def restore_manager(request: gr.Request) -> tuple[str, dict, str]:
    """Pick the manager from the URL (?manager=), else the demo manager."""
    managers = _managers()
    manager = choose_manager(request.query_params.get("manager"), managers)
    manager_id = manager.manager_id if manager else DEMO_MANAGER_ID
    return (
        manager_id,
        gr.update(
            choices=[(_manager_label(item), item.manager_id) for item in managers],
            value=manager.manager_id if manager else None,
        ),
        manager_badge(manager),
    )


def select_manager(requested: str | None) -> tuple[str, str]:
    """Switch the workspace to another manager of the store."""
    managers = _managers()
    manager = choose_manager(requested, managers)
    if manager is None or manager.manager_id != requested:
        raise gr.Error("That manager is not available. Please choose another.")
    logger.info("Manager selected", manager_id=manager.manager_id)
    return manager.manager_id, manager_badge(manager)


# Reads the picker, not the manager state: state values never reach the browser.
MANAGER_URL_JS = """
(manager) => {
    if (!manager) return;
    const url = new URL(window.location.href);
    url.searchParams.set('manager', manager);
    window.history.replaceState(null, '', url);
}
"""


def _assistant_context(context: dict[str, Any] | None) -> str:
    """Identify the loaded scenario, independently of the demo preview."""
    if not context:
        return '<div class="current-scenario"><span>Scenario unavailable</span></div>'
    return (
        '<div class="current-scenario"><span>Current scenario</span>'
        f"<strong>{escape(context['title'])}</strong></div>"
    )


def open_settings() -> dict:
    return gr.update(selected="settings")


def show_suggestions() -> tuple[dict, ...]:
    """Select the Suggestions category and show its panel.

    Runs after the Settings tab is selected: updates sent before the tab first
    renders would be lost.
    """
    return (gr.update(value="suggestions"), *settings.show_category("suggestions"))


def _restore_tab(request: gr.Request) -> dict:
    view = request.query_params.get("view", "assistant")
    return gr.update(selected=view if view in ("demo", "settings") else "assistant")


TAB_URL_JS = """
async () => {
    await new Promise(requestAnimationFrame);
    const tab = document.querySelector(
        '#workspace-tabs > .tab-wrapper [role="tab"][aria-selected="true"]'
    );
    const view = tab?.dataset.tabId;
    if (!['assistant', 'settings', 'demo'].includes(view)) return;
    const navigation = {assistant: 'new-chat', settings: 'edit-settings', demo: 'sidebar-demo'};
    for (const [name, id] of Object.entries(navigation)) {
        const button = document.getElementById(id);
        if (name === view) button?.setAttribute('aria-current', 'page');
        else button?.removeAttribute('aria-current');
    }
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    window.history.replaceState(null, '', url);
    document.title = view === 'assistant'
        ? (document.documentElement.dataset.chatTitle || 'DispatchDesk')
        : `${view === 'settings' ? 'Settings' : 'Scenarios'} | DispatchDesk`;
}
"""


CHAT_URL_JS = """
(chat) => {
    if (!chat) return;
    const url = new URL(window.location.href);
    if (chat.id) url.searchParams.set('chat', chat.id);
    else url.searchParams.delete('chat');
    window.history.replaceState(null, '', url);
    const title = chat.title ? `${chat.title} | DispatchDesk` : 'DispatchDesk';
    document.documentElement.dataset.chatTitle = title;
    if (!url.searchParams.has('view') || url.searchParams.get('view') === 'assistant') {
        document.title = title;
    }
}
"""


CHAT_NAVIGATION_JS = """
() => {
    const list = document.querySelector('#history-list');
    if (!list || list.dataset.navigationReady) return;
    list.dataset.navigationReady = 'true';
    const showTitle = (event) => {
        const label = event.target.closest('label');
        if (label) label.title = label.querySelector('span')?.textContent.trim() || '';
    };
    list.addEventListener('pointerover', showTitle);
    list.addEventListener('focusin', showTitle);
    list.addEventListener('click', (event) => {
        if (!event.target.closest('label')) return;
        // Radio input doesn't fire again when the already selected chat is clicked.
        document.querySelector(
            '#workspace-tabs [role="tab"][data-tab-id="assistant"]'
        )?.click();
    });
}
"""


def _situation_heading(key: str | None, scenarios: list[dict[str, str]]) -> str:
    scenario = next(
        (scenario for scenario in scenarios if scenario["key"] == key),
        {},
    )
    title = scenario.get(
        "title",
        key.replace("_", " ").replace("-", " ").title()
        if key
        else "No scenario loaded",
    )
    description = scenario.get(
        "description",
        "Choose and load a scenario to get started.",
    )
    return (
        '<div class="page-heading"><span class="section-kicker">CURRENT SITUATION</span>'
        f"<h1>{escape(title)}</h1>"
        f"<p>{escape(description)}</p></div>"
    )


def build_app() -> gr.Blocks:
    scenarios_unavailable = False
    try:
        scenarios = scenario_names()
    except (ValueError, OSError):
        logger.exception("Could not list scenarios; demo controls are unavailable")
        scenarios = []
        scenarios_unavailable = True
    choices = [(scenario["title"], scenario["key"]) for scenario in scenarios]
    default = next(
        (key for _, key in choices if key == "normal"),
        choices[0][1] if choices else None,
    )
    with gr.Blocks(title="DispatchDesk", delete_cache=(3600, 86400)) as app:
        current = gr.State(None)
        active_chat = gr.State(None)
        chat_location = gr.JSON(visible=False)
        # The selected manager's id; chats, settings and proposals follow it.
        manager = gr.State(DEMO_MANAGER_ID)
        # Setting changes proposed in chat, waiting for Confirm or Cancel.
        pending_changes = gr.State([])
        with gr.Sidebar(label="Workspace", width=288, elem_id="chat-sidebar"):
            with gr.Column(elem_id="sidebar-top"):
                gr.HTML(
                    '<a href="/?view=assistant" class="sidebar-brand" '
                    'aria-label="Reload Assistant">DispatchDesk</a>',
                    apply_default_css=False,
                    elem_id="sidebar-brand",
                )
                with gr.Column(elem_id="sidebar-navigation"):
                    new_chat = gr.Button(
                        "New chat",
                        size="sm",
                        variant="secondary",
                        elem_id="new-chat",
                        elem_classes="sidebar-nav-item",
                    )
                    edit_settings = gr.Button(
                        "Settings",
                        size="sm",
                        elem_id="edit-settings",
                        elem_classes="sidebar-nav-item",
                    )
                    demo_navigation = gr.Button(
                        "Demo tools",
                        size="sm",
                        elem_id="sidebar-demo",
                        elem_classes="sidebar-nav-item",
                    )
            with gr.Column(elem_id="sidebar-history"):
                gr.HTML(
                    '<p class="sidebar-heading">Recent chats</p>',
                    apply_default_css=False,
                )
                history_list = gr.Radio(
                    choices=[],
                    value=None,
                    label="Past conversations",
                    show_label=False,
                    container=False,
                    elem_id="history-list",
                )
            with gr.Column(elem_id="settings-summary-block"):
                with gr.Row(
                    visible=False,
                    elem_id="suggestions-entry",
                ) as suggestions_entry:
                    suggestions_entry_text = gr.HTML(
                        '<p class="sidebar-heading">Suggestions</p>',
                        apply_default_css=False,
                    )
                    review_suggestions = gr.Button(
                        "Review",
                        size="sm",
                        scale=0,
                        min_width=0,
                        elem_id="review-suggestions",
                    )
                with gr.Column(elem_id="sidebar-settings"):
                    gr.HTML(
                        '<p class="sidebar-heading">Active settings</p>',
                        apply_default_css=False,
                    )
                    settings_summary = gr.HTML(
                        apply_default_css=False,
                        elem_id="settings-summary",
                    )
                with gr.Column(elem_id="manager-profile"):
                    manager_picker = gr.Dropdown(
                        choices=[],
                        value=None,
                        label="Shift manager",
                        show_label=False,
                        container=False,
                        filterable=False,
                        interactive=True,
                        elem_id="manager-picker",
                    )
                    badge = gr.HTML(
                        manager_badge(None),
                        apply_default_css=False,
                        elem_id="assistant-manager",
                    )
        with gr.Tabs(selected="assistant", elem_id="workspace-tabs") as workspace:
            with (
                gr.Tab("Assistant", id="assistant"),
                gr.Column(elem_id="manager-workspace", min_width=0),
                gr.Column(elem_id="assistant-panel", min_width=0),
            ):
                with gr.Row(
                    visible=False,
                    elem_id="chat-summary-bar",
                ) as summary_bar:
                    with gr.Accordion(
                        "Summary of earlier messages",
                        open=False,
                        elem_id="chat-summary",
                    ) as summary_box:
                        summary_text = gr.Markdown(elem_id="chat-summary-text")
                    summarize_button = gr.Button(
                        "Summarize now",
                        size="sm",
                        scale=0,
                        min_width=140,
                        elem_id="summarize-now",
                    )
                chatbot = gr.Chatbot(
                    label="Conversation",
                    show_label=False,
                    height="auto",
                    autoscroll=False,
                    layout="bubble",
                    group_consecutive_messages=False,
                    placeholder=CHAT_PLACEHOLDER,
                    buttons=["copy"],
                    elem_id="conversation",
                )
                pending_message = gr.Textbox(visible="hidden", interactive=False)
                with gr.Column(elem_id="composer-dock", min_width=0):
                    with gr.Column(
                        visible=False,
                        elem_id="pending-changes",
                        min_width=0,
                    ) as pending_box:
                        pending_html = gr.HTML(apply_default_css=False)
                        with gr.Row(elem_id="pending-actions"):
                            confirm_changes = gr.Button(
                                "Confirm",
                                variant="primary",
                                size="sm",
                                scale=0,
                                min_width=96,
                                elem_id="confirm-changes",
                            )
                            cancel_changes = gr.Button(
                                "Cancel",
                                size="sm",
                                scale=0,
                                min_width=96,
                                elem_id="cancel-changes",
                            )
                    processing = gr.HTML(
                        PROCESSING_STATUS,
                        visible=False,
                        apply_default_css=False,
                        elem_id="chat-processing",
                    )
                    with gr.Row(elem_id="message-composer"):
                        message = gr.Textbox(
                            label="Ask your dispatch assistant",
                            show_label=False,
                            placeholder="What's on your mind?",
                            lines=1,
                            max_lines=6,
                            container=False,
                            elem_id="message-input",
                        )
                        submit = gr.Button(
                            "Send",
                            variant="primary",
                            scale=0,
                            min_width=88,
                            elem_id="send-message",
                        )
                with gr.Row(elem_id="starter-prompts") as suggestions:
                    prompts = [
                        "We're missing the 10-minute promise. Should I ask my riders to jump red lights and speed?",
                        "It's pouring and deliveries are late. Can I dock riders' pay for missing the delivery promise?",
                        "Packed orders are piling up. Can I batch frozen-item orders with other deliveries?",
                    ]
                    prompt_buttons = [
                        gr.Button(question, size="sm", elem_classes="prompt-button")
                        for question in prompts
                    ]
            with (
                gr.Tab("Settings", id="settings"),
                gr.Column(elem_id="settings-workspace", min_width=0),
            ):
                settings_form = settings.build(manager, summary=settings_summary)
            with (
                gr.Tab("Demo tools", id="demo", render_children=True),
                gr.Column(elem_id="demo-workspace", min_width=0),
            ):
                with gr.Row(elem_id="demo-context"):
                    context_banner = gr.HTML(
                        '<div class="current-scenario"><span>Checking scenario…</span></div>'
                        if choices
                        else _assistant_context(None),
                        apply_default_css=False,
                        elem_id="assistant-context",
                        scale=0,
                        min_width=0,
                    )
                with gr.Row(elem_id="demo-heading"):
                    situation = gr.HTML(
                        _situation_heading(None, scenarios),
                        apply_default_css=False,
                        elem_id="scenario-situation",
                    )
                with gr.Column(elem_id="demo-layout", min_width=0):
                    with (
                        gr.Column(elem_id="scenario-toolbar", min_width=0),
                        gr.Row(elem_id="scenario-controls"),
                    ):
                        gr.HTML(
                            '<div class="loader-heading"><h2>Scenario loader</h2>'
                            "<p>Choose a situation to inspect or load.</p></div>",
                            apply_default_css=False,
                            scale=1,
                            min_width=230,
                        )
                        scenario = gr.Dropdown(
                            choices=choices,
                            value=default,
                            label="Choose a scenario",
                            show_label=False,
                            interactive=bool(choices),
                            filterable=False,
                            scale=0,
                            min_width=300,
                            elem_id="scenario-picker",
                        )
                        load = gr.Button(
                            "Load scenario",
                            variant="primary",
                            interactive=bool(choices),
                            scale=0,
                            min_width=168,
                            elem_id="load-scenario",
                        )
                    with gr.Column(scale=1, min_width=0, elem_id="data-panel"):
                        with gr.Row(elem_id="data-titlebar"):
                            gr.Markdown(
                                "### Inspect the data",
                                elem_id="data-heading",
                                scale=1,
                            )
                            preview = gr.HTML(
                                '<p class="muted">Scenarios unavailable. Check the scenario configuration. Assistant chat is still available.</p>'
                                if scenarios_unavailable
                                else '<p class="muted">Choose a scenario to inspect its data.</p>',
                                apply_default_css=False,
                                elem_id="scenario-overview",
                                scale=2,
                            )
                            refresh = gr.Button(
                                "↻ Refresh",
                                size="sm",
                                scale=0,
                                min_width=96,
                                elem_id="refresh-scenario",
                            )
                        tables = []
                        with gr.Tabs(elem_id="data-tabs"):
                            for title in (
                                "Orders",
                                "Riders",
                                "Hourly metrics",
                                "Zones",
                            ):
                                with gr.Tab(title):
                                    tables.append(
                                        gr.Dataframe(
                                            value={"headers": [], "data": []},
                                            label=title,
                                            show_label=False,
                                            interactive=False,
                                            type="array",
                                            datatype="auto",
                                            wrap=False,
                                            show_search="filter",
                                            show_row_numbers=True,
                                            pinned_columns=1,
                                            max_height="calc(100dvh - 390px)",
                                            buttons=["fullscreen", "copy"],
                                            elem_classes="scenario-table",
                                        ),
                                    )
                        gr.Markdown(
                            "Synthetic starting snapshots · Refresh to see saved changes",
                            elem_classes="panel-note",
                        )
                with gr.Column(elem_id="review-admin", min_width=0):
                    gr.HTML(
                        '<div class="loader-heading"><h2>Daily review (admin)</h2>'
                        "<p>Runs every day at 23:30 IST. Drafts the handover note, "
                        "proposes settings, and lists answers that need a look.</p></div>",
                        apply_default_css=False,
                    )
                    with gr.Row(elem_id="review-actions"):
                        run_review_button = gr.Button(
                            "Run review now",
                            variant="primary",
                            scale=0,
                            min_width=160,
                        )
                        mark_reviewed_button = gr.Button(
                            "Mark all reviewed",
                            scale=0,
                            min_width=160,
                        )
                        review_status = gr.Markdown(elem_id="review-status")
                    answer_issues = gr.Dataframe(
                        value={"headers": [], "data": []},
                        label="Answer issues",
                        interactive=False,
                        type="array",
                        wrap=True,
                        show_search="filter",
                        elem_id="answer-issues",
                    )
        app.load(_restore_tab, outputs=workspace, queue=False)
        (
            suggestion_items,
            suggestion_detail,
            suggestion_note,
            suggestion_actions,
            accept_suggestion,
            dismiss_suggestion,
            suggestion_status,
        ) = settings_form.suggestions
        suggestion_outputs = [
            suggestions_entry,
            suggestions_entry_text,
            suggestion_items,
            suggestion_detail,
            suggestion_note,
            suggestion_actions,
            suggestion_status,
        ]
        assert settings_form.nav is not None
        review_suggestions.click(
            open_settings,
            outputs=workspace,
            js=CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        ).then(
            show_suggestions,
            outputs=[settings_form.nav, *settings_form.category_outputs],
            queue=False,
            show_progress="hidden",
        )
        suggestion_items.input(
            suggestions_ui.select,
            inputs=[suggestion_items, manager],
            outputs=[suggestion_detail, suggestion_note],
        )
        accept_suggestion.click(
            suggestions_ui.accept,
            inputs=[suggestion_items, suggestion_note, manager],
            outputs=suggestion_outputs,
            concurrency_id="settings",
            concurrency_limit=1,
        ).then(
            settings.load_settings,
            inputs=manager,
            outputs=settings_form.outputs(),
        ).then(settings.load_summary, inputs=manager, outputs=settings_summary)
        dismiss_suggestion.click(
            suggestions_ui.dismiss,
            inputs=[suggestion_items, manager],
            outputs=suggestion_outputs,
            concurrency_id="settings",
            concurrency_limit=1,
        )
        run_review_button.click(
            suggestions_ui.run_review_now,
            inputs=manager,
            outputs=[review_status, answer_issues],
            concurrency_id="review",
            concurrency_limit=1,
        ).then(
            suggestions_ui.refresh_for,
            inputs=manager,
            outputs=suggestion_outputs,
        )
        mark_reviewed_button.click(
            suggestions_ui.mark_issues_reviewed,
            inputs=manager,
            outputs=[review_status, answer_issues],
            concurrency_id="review",
            concurrency_limit=1,
        )
        # Sidebar navigation selects the same workspace panels and URL state.
        demo_navigation.click(
            lambda: gr.update(selected="demo"),
            outputs=workspace,
            js=CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        )
        edit_settings.click(
            open_settings,
            outputs=workspace,
            js=CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        )
        summary_outputs = [summary_bar, summary_box, summary_text, summarize_button]
        summarize_button.click(
            summarize_now,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
            concurrency_id="summaries",
            concurrency_limit=1,
        )

        def show_manager(event, linked=False):
            """After the manager is set: their chat, chats, summary and settings."""
            return (
                event.then(
                    restore_conversation if linked else restore_latest_conversation,
                    inputs=manager,
                    outputs=[chatbot, active_chat],
                    concurrency_id="workspace",
                    concurrency_limit=1,
                )
                .then(
                    conversation_choices,
                    inputs=[manager, active_chat],
                    outputs=history_list,
                )
                .then(
                    summary_card,
                    inputs=[manager, active_chat],
                    outputs=summary_outputs,
                )
                .then(
                    settings.load_settings,
                    inputs=manager,
                    outputs=settings_form.outputs(),
                    concurrency_id="settings",
                    concurrency_limit=1,
                )
                .then(settings.load_summary, inputs=manager, outputs=settings_summary)
                .then(
                    suggestions_ui.refresh_for,
                    inputs=manager,
                    outputs=suggestion_outputs,
                )
                .then(
                    suggestions_ui.issues_table,
                    inputs=manager,
                    outputs=answer_issues,
                )
            )

        show_manager(
            app.load(
                restore_manager,
                outputs=[manager, manager_picker, badge],
                queue=False,
            ),
            linked=True,
        )
        # A proposal belongs to the manager it was made for.
        show_manager(
            manager_picker.input(
                select_manager,
                inputs=manager_picker,
                outputs=[manager, badge],
                queue=False,
            )
            .then(list, outputs=pending_changes, queue=False)
            .then(fn=None, js=MANAGER_URL_JS, inputs=manager_picker),
        ).then(fn=None, js=CLOSE_SIDEBAR_ON_PHONE_JS)
        app.load(fn=None, js=CLOSE_SIDEBAR_ON_PHONE_JS)
        app.load(fn=None, js=CHAT_NAVIGATION_JS)
        history_list.input(
            open_conversation,
            inputs=[history_list, manager],
            outputs=[chatbot, message, active_chat, workspace],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(fn=None, js=CLOSE_SIDEBAR_ON_PHONE_JS)
        active_chat.change(
            conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(
            summary_card,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
        ).then(
            conversation_location,
            inputs=[manager, active_chat],
            outputs=chat_location,
        )
        chat_location.change(fn=None, js=CHAT_URL_JS, inputs=chat_location)
        workspace.change(fn=None, js=TAB_URL_JS)
        for button, question in zip(prompt_buttons, prompts, strict=True):
            button.click(lambda q=question: q, outputs=message, queue=False).then(
                fn=None,
                js="() => document.querySelector('#message-input textarea')?.focus()",
            )
        for event in (app.load, refresh.click):
            event(
                restore_workspace,
                outputs=[current, scenario, preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(_assistant_context, inputs=current, outputs=context_banner)
        current.change(
            lambda context: _situation_heading(
                context["scenario_key"] if context else None,
                scenarios,
            ),
            inputs=current,
            outputs=situation,
            queue=False,
            show_progress="hidden",
        )
        if choices:
            scenario.input(
                prepare_scenario,
                inputs=[scenario, current],
                outputs=[preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            )
        load.click(
            load_selected_scenario,
            inputs=[scenario, manager],
            outputs=[current, chatbot, message, preview, *tables],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).success(_assistant_context, inputs=current, outputs=context_banner).then(
            current_conversation_id,
            inputs=manager,
            outputs=active_chat,
        ).then(
            conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(summary_card, inputs=[manager, active_chat], outputs=summary_outputs)
        for event in (submit.click, message.submit):
            event(
                fn=None,
                js=SEND_MESSAGE_JS,
                inputs=[message, chatbot],
                outputs=[pending_message, chatbot, message, submit, processing],
                queue=False,
                show_progress="hidden",
            ).then(
                respond_to_pending,
                inputs=[pending_message, chatbot, manager, active_chat],
                outputs=[chatbot, message, pending_changes],
                show_progress="hidden",
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(
                fn=None,
                js=FINISH_CHAT_JS,
                outputs=[processing, submit, message],
                queue=False,
                show_progress="hidden",
            ).then(
                lambda manager_id, selected: (
                    selected or current_conversation_id(manager_id=manager_id)
                ),
                inputs=[manager, active_chat],
                outputs=active_chat,
            ).then(
                conversation_choices,
                inputs=[manager, active_chat],
                outputs=history_list,
            ).then(
                title_conversation,
                inputs=[manager, active_chat],
                outputs=history_list,
                concurrency_id="titles",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                conversation_location,
                inputs=[manager, active_chat],
                outputs=chat_location,
            ).then(
                summarize_conversation,
                inputs=[manager, active_chat],
                concurrency_id="summaries",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                summary_card,
                inputs=[manager, active_chat],
                outputs=summary_outputs,
                show_progress="hidden",
            )
        chatbot.change(
            fn=None,
            js="""(history) => [
                {__type__: 'update', visible: !(history && history.length)},
                {__type__: 'update', placeholder: history && history.length
                    ? 'Ask a follow-up…' : "What's on your mind?"}
            ]""",
            inputs=chatbot,
            outputs=[suggestions, message],
            queue=False,
            show_progress="hidden",
        ).then(
            fn=None,
            js="""async () => {
                await new Promise(requestAnimationFrame);
                await new Promise(requestAnimationFrame);
                const messages = document.querySelectorAll('#conversation .message.user, #conversation .message.bot');
                const latest = messages[messages.length - 1];
                if (!latest) return;
                const bounds = latest.getBoundingClientRect();
                const actions = latest.closest('.message-row')?.nextElementSibling;
                const messageBottom = actions?.classList.contains('message-buttons')
                    ? actions.getBoundingClientRect().bottom : bounds.bottom;
                const dock = document.querySelector('#composer-dock').getBoundingClientRect();
                const bottom = dock.top - 24;
                const summary = document.querySelector('#chat-summary-bar');
                const top = (summary?.getBoundingClientRect().height || 0) + 24;
                const target = messageBottom - bounds.top > bottom - top
                    ? scrollY + bounds.top - top
                    : scrollY + messageBottom - bottom;
                window.scrollTo({top: Math.max(0, target), behavior:
                    matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
            }""",
        )
        new_chat.click(
            clear_chat,
            inputs=manager,
            outputs=[chatbot, message, active_chat, workspace],
            queue=True,
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(
            conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(
            summary_card,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
        ).then(
            fn=None,
            js=CLOSE_SIDEBAR_ON_PHONE_JS,
        )
        pending_changes.change(
            pending_card,
            inputs=pending_changes,
            outputs=[pending_html, pending_box],
            queue=False,
            show_progress="hidden",
        )
        confirm_changes.click(
            confirm_pending,
            inputs=[pending_changes, chatbot, manager, active_chat],
            outputs=[chatbot, pending_changes],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(
            settings.load_settings,
            inputs=manager,
            outputs=settings_form.outputs(),
            concurrency_id="settings",
            concurrency_limit=1,
        ).then(settings.load_summary, inputs=manager, outputs=settings_summary)
        cancel_changes.click(
            cancel_pending,
            inputs=[pending_changes, chatbot, manager, active_chat],
            outputs=[chatbot, pending_changes],
            concurrency_id="workspace",
            concurrency_limit=1,
        )
        # A proposal belongs to the chat it was made in.
        for event in (new_chat.click, history_list.input, load.click):
            event(list, outputs=pending_changes, queue=False)
    return app


if __name__ == "__main__":
    configure_logging()

app = build_app()


def main() -> None:
    configure_logging()
    configure_uvicorn_logging()
    port = int(os.environ.get("PORT", "7860"))
    logger.info("DispatchDesk starting", host="0.0.0.0", port=port)
    app.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=False,
        theme=THEME,
        css_paths=CSS_PATH,
        favicon_path=Path(__file__).with_name("favicon.svg"),
        footer_links=[],
    )


if __name__ == "__main__":
    main()
