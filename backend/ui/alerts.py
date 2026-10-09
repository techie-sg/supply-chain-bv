"""The alert pop-up: a card in the bottom-right corner when an alert fires.

The page checks every 30 seconds and after scenario loads, manager switches and
settings saves. Code decides what fired (`service.alerts`); this module only
queues new pop-ups for this page, renders the card and the diagnosis.
"""

from dataclasses import dataclass
from html import escape
from typing import Any

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from service.alerts import check_alerts, diagnose, dismiss_alert
from ui import chat as chat_ui

logger = structlog.stdlib.get_logger(__name__)

CHECK_SECONDS = 30
BELL = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
    '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>'
)
STATUS_TEXT = {
    "packed_waiting_rider": "Packed, waiting",
    "picking": "Picking",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "available": "Available",
    "on_delivery": "On delivery",
    "returning": "Returning",
    "on_break": "On break",
    "offline": "Offline",
    "offline_weather": "Offline (weather)",
    "standby_off_shift": "Standby",
}
ITEM_COLUMNS = {
    "order": [
        ("order", "Order"),
        ("zone", "Zone"),
        ("items", "Items"),
        ("frozen", "Frozen"),
        ("status", "Status"),
        ("waiting_min", "Waiting"),
    ],
    "rider": [
        ("name", "Rider"),
        ("status", "Status"),
        ("hours_on_shift", "On shift (h)"),
        ("back_in_min", "Back in"),
    ],
    "hour": [
        ("hour", "Hour"),
        ("orders", "Orders"),
        ("sla_pct", "SLA %"),
        ("riders_online", "Riders online"),
        ("rain", "Rain"),
    ],
}


def _times(count: int) -> str:
    return "once today" if count == 1 else f"{count} times today"


def queue_new(
    manager_id: str = DEMO_MANAGER_ID,
    queue: list[dict[str, Any]] | None = None,
    seen: list[str] | None = None,
) -> tuple[Any, Any]:
    """Check the alerts; queue pop-ups this page has not shown yet.

    A newer pop-up for an alert already queued replaces it, so the queue holds
    one card per alert.
    """
    try:
        check = check_alerts(manager_id)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.warning("Could not check alerts", exc_info=True)
        return gr.skip(), gr.skip()
    seen = list(seen or [])
    new = [alert for alert in check.alerts if alert["id"] not in seen]
    if not new:
        return gr.skip(), gr.skip()
    codes = {alert["code"] for alert in new}
    queue = [alert for alert in queue or [] if alert["code"] not in codes] + new
    return queue, seen + [alert["id"] for alert in new]


def card(queue: list[dict[str, Any]] | None) -> tuple[dict, str, dict, bool, dict]:
    """The pop-up for the first queued alert, with the diagnosis closed."""
    if not queue:
        return (
            gr.update(visible=False),
            "",
            gr.update(visible=False, value=""),
            False,
            gr.update(value="Diagnose"),
        )
    alert = queue[0]
    severity = "critical" if alert["severity"] == "critical" else "warning"
    position = (
        f'<span class="alert-position">1 of {len(queue)}</span>'
        if len(queue) > 1
        else ""
    )
    html = f"""
<div class="alert-card alert-{severity}" role="alert" aria-live="assertive">
  <div class="alert-top">
    <span class="alert-icon">{BELL}</span>
    <div class="alert-heading">
      <p class="alert-kicker">{"Critical alert" if severity == "critical" else "Alert"}{position}</p>
      <h3>{escape(alert["name"])}</h3>
    </div>
  </div>
  <p class="alert-summary">{escape(alert["summary"])}</p>
  <div class="alert-metrics">
    <div><span>Now</span><strong>{escape(alert["value"])}</strong></div>
    <div><span>Your limit</span><strong>{escape(alert["limit"])}</strong></div>
  </div>
  <p class="alert-meta">
    <span>As of {escape(alert["as_of"])}</span>
    <span class="alert-dot" aria-hidden="true">·</span>
    <span class="alert-today">Triggered {_times(alert["today"])}</span>
    <span class="alert-dot" aria-hidden="true">·</span>
    <span>{escape(alert["window"])}</span>
  </p>
</div>"""
    return (
        gr.update(visible=True),
        html,
        gr.update(visible=False, value=""),
        False,
        gr.update(value="Diagnose"),
    )


def _close(alert: dict[str, Any], manager_id: str) -> None:
    """Remember the pop-up was closed, so a refresh or another tab skips it."""
    try:
        dismiss_alert(alert["id"], manager_id)
    except (SQLAlchemyError, RuntimeError):
        # The page still closes it; only other pages may show it again.
        logger.warning("Could not save the alert dismissal", exc_info=True)


def dismiss(
    queue: list[dict[str, Any]] | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> list[dict[str, Any]]:
    """Close the shown alert for good; the next queued one takes its place."""
    queue = list(queue or [])
    if queue:
        _close(queue[0], manager_id)
    return queue[1:]


def _table(items: list[dict[str, Any]], kind: str) -> str:
    rows = [item for item in items if kind in item]
    if not rows:
        return ""
    columns = ITEM_COLUMNS[kind]
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)

    def cell(key: str, value: Any) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "—"
        if value is None:
            return "—"
        if key == "status":
            return escape(STATUS_TEXT.get(str(value), str(value)))
        if key in ("waiting_min", "back_in_min"):
            return f"{value:g} min"
        return escape(str(value))

    body = "".join(
        "<tr>"
        + "".join(f"<td>{cell(key, row.get(key))}</td>" for key, _ in columns)
        + "</tr>"
        for row in rows
    )
    return (
        '<div class="alert-table-wrap"><table class="alert-table">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def diagnosis(
    queue: list[dict[str, Any]] | None,
    is_open: bool,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[dict, bool, dict]:
    """Open or close the diagnosis of the shown alert."""
    if not queue or is_open:
        return gr.update(visible=False, value=""), False, gr.update(value="Diagnose")
    alert = queue[0]
    try:
        found = diagnose(alert["code"], manager_id)
    except (SQLAlchemyError, RuntimeError, ValueError, LookupError):
        logger.warning("Could not diagnose alert %s", alert["code"], exc_info=True)
        found = None
    if found is None:
        html = '<p class="alert-empty">The details for this alert are not available right now.</p>'
    else:
        state = (
            '<span class="alert-state alert-state-on">Still above your limit</span>'
            if found["breached"]
            else '<span class="alert-state">Back within your limit</span>'
        )
        triggers = "".join(
            f"<li><span>{escape(item['at'])}</span><strong>{escape(item['value'])}</strong></li>"
            for item in found["triggers"]
        )
        drivers = "".join(
            _table(found["items"], kind) for kind in ("order", "rider", "hour")
        )
        html = f"""
<div class="alert-diagnosis">
  <section>
    <h4>What's happening {state}</h4>
    <p>{escape(found["now"])}{f" (as of {escape(found['as_of'])})" if found["as_of"] else ""}.</p>
    <p class="alert-compare">Now <strong>{escape(found["value"] or "—")}</strong>, your limit <strong>{escape(found["limit"])}</strong>.</p>
  </section>
  <section>
    <h4>What's driving it</h4>
    {drivers or '<p class="alert-empty">No rows behind this measure right now.</p>'}
  </section>
  <section>
    <h4>Today <span class="alert-badge">{len(found["triggers"])}</span></h4>
    {f'<ol class="alert-timeline">{triggers}</ol>' if triggers else '<p class="alert-empty">Not triggered yet today.</p>'}
  </section>
  <section>
    <h4>About this alert</h4>
    <p>{escape(found["description"])}</p>
    <p class="alert-fine">Applies {escape(found["window"])}. Allowed limit: {escape(found["allowed"])}.</p>
  </section>
</div>"""
    return gr.update(visible=True, value=html), True, gr.update(value="Hide details")


def question(alert: dict[str, Any]) -> str:
    """The first message of a chat started from an alert."""
    return (
        f"{alert['name']} alert at {alert['as_of']}: {alert['summary']} "
        f"(now {alert['value']}, my limit is {alert['limit']}). "
        "What is driving this, and what should I do first?"
    )


def start_alert_chat(
    queue: list[dict[str, Any]] | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[Any, Any, Any, Any, Any]:
    """Start a new chat about the shown alert; its question is sent next.

    Acting on the alert closes it, like the close button.
    """
    if not queue:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
    history, _, conversation_id, tab = chat_ui.clear_chat(manager_id)
    _close(queue[0], manager_id)
    return history, question(queue[0]), conversation_id, tab, queue[1:]


# Sends the alert question through the normal send path, once it is in the box.
SEND_ALERT_QUESTION_JS = """
() => { setTimeout(() => document.querySelector('#send-message')?.click(), 80); }
"""


@dataclass
class AlertComponents:
    popup: gr.Column
    close: gr.Button
    card: gr.HTML
    details: gr.HTML
    diagnose: gr.Button
    chat: gr.Button
    timer: gr.Timer


def build_popup() -> AlertComponents:
    """The bottom-right pop-up, hidden until an alert fires, and its timer."""
    with gr.Column(visible=False, elem_id="alert-popup") as popup:
        close = gr.Button("✕", size="sm", elem_id="alert-dismiss", min_width=0)
        card_html = gr.HTML(apply_default_css=False, elem_id="alert-card")
        details = gr.HTML(
            visible=False,
            apply_default_css=False,
            elem_id="alert-details",
        )
        with gr.Row(elem_id="alert-actions"):
            diagnose_button = gr.Button(
                "Diagnose",
                size="sm",
                elem_id="alert-diagnose",
            )
            chat_button = gr.Button(
                "Start new chat",
                variant="primary",
                size="sm",
                elem_id="alert-chat",
            )
    return AlertComponents(
        popup,
        close,
        card_html,
        details,
        diagnose_button,
        chat_button,
        gr.Timer(CHECK_SECONDS),
    )
