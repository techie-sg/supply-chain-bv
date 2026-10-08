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
from logging_config import configure_logging
from service.conversations import (
    add_note,
    ask_question,
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
    idle_summary_job,
    summarize_latest_conversation,
    summarize_open_conversation,
)
from ui import settings

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


def _icon(name: str) -> str:
    """Small, local line icons; no external font or image dependency."""
    paths = {
        "box": '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Z"/><path d="m3 8 9 5 9-5M12 13v8M7.5 5.5l9 5"/>',
        "riders": '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5.7"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "zones": '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6ZM9 3v15M15 6v15"/>',
        "spark": '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>',
    }
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{paths[name]}</svg>'
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
    """The chat's title, or its first question until titled; one short line."""
    label = " ".join((item.get("title") or item["first_question"] or "").split())
    return label if len(label) <= 40 else label[:39] + "…"


def conversation_choices(manager_id: str = DEMO_MANAGER_ID) -> dict:
    """Sidebar list of the manager's past chats, highlighting the open one."""
    try:
        items = past_conversations(manager_id=manager_id)
        current = current_conversation_id(manager_id=manager_id)
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
) -> tuple[list[dict], str]:
    """Show a past conversation and make it the one new questions continue."""
    if not conversation_id:
        return gr.skip(), gr.skip()
    try:
        messages = resume_past_conversation(conversation_id, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError) as exc:
        logger.exception("Could not open conversation %s", conversation_id)
        raise gr.Error("Could not open that conversation. Please try again.") from exc
    return to_display(messages), ""


def title_conversation(manager_id: str = DEMO_MANAGER_ID) -> dict:
    """Title the open chat after its first answer, then refresh the sidebar."""
    try:
        title_latest_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not title the conversation", exc_info=True)
    return conversation_choices(manager_id=manager_id)


SUMMARY_NOTE = (
    '\n\n<p class="summary-note">Written by the assistant for its own context. '
    "The full messages are below.</p>"
)


def summary_card(manager_id: str = DEMO_MANAGER_ID) -> tuple[dict, dict, str, dict]:
    """The pinned summary row: hidden for an empty chat, otherwise its state."""
    hidden = (gr.update(visible=False), gr.skip(), "", gr.skip())
    try:
        view = conversation_summary(manager_id=manager_id)
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


def summarize_now(manager_id: str = DEMO_MANAGER_ID) -> tuple[dict, dict, str, dict]:
    """Fold every message of the open chat into its summary, then show it open."""
    try:
        updated = summarize_open_conversation(manager_id=manager_id)
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
    bar, box, text, button = summary_card(manager_id=manager_id)
    if updated:
        box = gr.update(label=box["label"], open=True)
    return bar, box, text, button


def summarize_conversation(manager_id: str = DEMO_MANAGER_ID) -> None:
    """After an answer, fold older messages into the summary if over a limit."""
    try:
        summarize_latest_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError, ValueError, requests.RequestException):
        logger.warning("Could not summarize the conversation", exc_info=True)


def clear_chat(manager_id: str = DEMO_MANAGER_ID) -> tuple[list[dict], str]:
    """Start a new stored conversation; the previous one is kept."""
    try:
        start_new_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not start a new conversation")
        raise gr.Error("Could not start a new chat. Please try again.") from exc
    return [], ""


def chat(
    message: str,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
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
            answer, proposals = ask_question(message, manager_id=manager_id)
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
        yield chat(message, previous, manager_id)
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
) -> list[dict]:
    """Store an assistant note in the manager's open chat and show it."""
    now = datetime.now(TIMEZONE)
    try:
        stored = add_note(text, manager_id=manager_id)
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
) -> tuple[list[dict], list]:
    """Save the proposed setting changes the manager confirmed."""
    if not pending:
        return history or [], []
    try:
        results = confirm_proposals(pending, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not save confirmed setting changes")
        raise gr.Error("Could not save the settings. Please try again.") from exc
    return _with_note(history, "\n\n".join(results), manager_id), []


def cancel_pending(
    pending: list[dict] | None,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[list[dict], list]:
    """Discard the proposed setting changes; nothing is saved."""
    if not pending:
        return history or [], []
    names = ", ".join(change["name"] for change in pending)
    return (
        _with_note(history, f"Cancelled. Nothing was changed ({names}).", manager_id),
        [],
    )


def _manager_label(manager: ShiftManager) -> str:
    return f"{manager.name} · {manager.shift_name} {manager.shift_start}–{manager.shift_end}"


def manager_badge(manager: ShiftManager | None) -> str:
    """Who is signed in to the workspace, and their shift."""
    if manager is None:
        return '<div class="manager-badge"><span>No manager available</span></div>'
    return (
        '<div class="manager-badge"><span>Manager</span>'
        f"<strong>{escape(manager.name)}</strong>"
        f"<em>{escape(manager.shift_name)} shift · {escape(manager.shift_start)}–"
        f"{escape(manager.shift_end)} · {escape(manager.shift_id)}</em></div>"
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
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    window.history.replaceState(null, '', url);
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
        gr.HTML(
            '<header class="desk-header"><a href="/?view=assistant" class="brand" aria-label="Reload Assistant"><span class="brand-mark">'
            f'{_icon("box")}</span><span>Dispatch<span class="brand-light">Desk</span></span>'
            "</a></header>",
            apply_default_css=False,
            elem_id="brand-home",
        )
        current = gr.State(None)
        # The selected manager's id; chats, settings and proposals follow it.
        manager = gr.State(DEMO_MANAGER_ID)
        # Setting changes proposed in chat, waiting for Confirm or Cancel.
        pending_changes = gr.State([])
        with gr.Sidebar(label="Chats", width=272, elem_id="chat-sidebar"):
            gr.HTML(
                '<p class="sidebar-heading">Shift manager</p>',
                apply_default_css=False,
            )
            manager_picker = gr.Radio(
                choices=[],
                value=None,
                label="Shift manager",
                show_label=False,
                container=False,
                elem_id="manager-picker",
            )
            new_chat = gr.Button(
                "New chat",
                size="sm",
                variant="secondary",
                elem_id="new-chat",
            )
            gr.HTML(
                '<p class="sidebar-heading">Recent</p>',
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
                with gr.Row(elem_id="settings-summary-heading"):
                    gr.HTML(
                        '<p class="sidebar-heading">Active settings</p>',
                        apply_default_css=False,
                    )
                    edit_settings = gr.Button(
                        "Edit",
                        size="sm",
                        scale=0,
                        min_width=0,
                        elem_id="edit-settings",
                    )
                settings_summary = gr.HTML(
                    apply_default_css=False,
                    elem_id="settings-summary",
                )
        with gr.Tabs(selected="assistant", elem_id="workspace-tabs") as workspace:
            with (
                gr.Tab("Assistant", id="assistant"),
                gr.Column(elem_id="manager-workspace", min_width=0),
                gr.Column(elem_id="assistant-panel", min_width=0),
            ):
                with gr.Row(elem_id="assistant-heading"):
                    badge = gr.HTML(
                        manager_badge(None),
                        apply_default_css=False,
                        elem_id="assistant-manager",
                        scale=0,
                        min_width=0,
                    )
                    context_banner = gr.HTML(
                        '<div class="current-scenario"><span>Checking scenario…</span></div>'
                        if choices
                        else _assistant_context(None),
                        apply_default_css=False,
                        elem_id="assistant-context",
                        scale=0,
                        min_width=0,
                    )
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
                gr.Tab("Demo tools", id="demo"),
                gr.Column(elem_id="demo-workspace", min_width=0),
            ):
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
        app.load(_restore_tab, outputs=workspace, queue=False)
        # Selecting the tab from the server works even when narrow screens fold
        # the tab into the "More tabs" menu.
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
            inputs=manager,
            outputs=summary_outputs,
            concurrency_id="summaries",
            concurrency_limit=1,
        )

        def show_manager(event):
            """After the manager is set: their chat, chats, summary and settings."""
            return (
                event.then(
                    restore_chat,
                    inputs=manager,
                    outputs=chatbot,
                    concurrency_id="workspace",
                    concurrency_limit=1,
                )
                .then(conversation_choices, inputs=manager, outputs=history_list)
                .then(summary_card, inputs=manager, outputs=summary_outputs)
                .then(
                    settings.load_settings,
                    inputs=manager,
                    outputs=settings_form.outputs(),
                    concurrency_id="settings",
                    concurrency_limit=1,
                )
                .then(settings.load_summary, inputs=manager, outputs=settings_summary)
            )

        show_manager(
            app.load(
                restore_manager,
                outputs=[manager, manager_picker, badge],
                queue=False,
            ),
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
        history_list.input(
            open_conversation,
            inputs=[history_list, manager],
            outputs=[chatbot, message],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(conversation_choices, inputs=manager, outputs=history_list).then(
            summary_card,
            inputs=manager,
            outputs=summary_outputs,
        ).then(
            fn=None,
            js=CLOSE_SIDEBAR_ON_PHONE_JS,
        )
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
            conversation_choices,
            inputs=manager,
            outputs=history_list,
        ).then(summary_card, inputs=manager, outputs=summary_outputs)
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
                inputs=[pending_message, chatbot, manager],
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
            ).then(conversation_choices, inputs=manager, outputs=history_list).then(
                title_conversation,
                inputs=manager,
                outputs=history_list,
                concurrency_id="titles",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                summarize_conversation,
                inputs=manager,
                concurrency_id="summaries",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                summary_card,
                inputs=manager,
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
                const dock = document.querySelector('#composer-dock').getBoundingClientRect();
                const bottom = dock.top - 24;
                // Leave room for the pinned header above the message.
                const header = document.querySelector('#brand-home')?.getBoundingClientRect().height || 0;
                const top = header + 24;
                const target = bounds.height > bottom - top
                    ? scrollY + bounds.top - top
                    : scrollY + bounds.bottom - bottom;
                window.scrollTo({top: Math.max(0, target), behavior:
                    matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
            }""",
        )
        new_chat.click(
            clear_chat,
            inputs=manager,
            outputs=[chatbot, message],
            queue=True,
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(conversation_choices, inputs=manager, outputs=history_list).then(
            summary_card,
            inputs=manager,
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
            inputs=[pending_changes, chatbot, manager],
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
            inputs=[pending_changes, chatbot, manager],
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
    idle_summary_job().start()
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
