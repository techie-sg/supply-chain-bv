"""The greeting briefing: what the manager chose to see when they say hi.

Built in code, never by the model, so it always follows the Greeting setting
exactly. Live views come from one call to the assistant's own
`get_live_dispatch_status` tool, recorded in the answer's trace like any other.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import TIMEZONE
from domain.chat import ToolCallTrace
from domain.memory import BriefingView, PreferenceCode
from service.handover import chat_handover_block
from service.preferences import PreferenceService
from service.tools import dispatch_tools, traced_tools

logger = structlog.stdlib.get_logger(__name__)

# Only a bare greeting gets the briefing; anything more goes to the assistant.
GREETING = re.compile(
    r"^(hi+|hello+|hey+|hiya|namaste|gm|good (morning|afternoon|evening|day))"
    r"( there| team| all| dispatchdesk)?[\s!.,]*$",
    re.IGNORECASE,
)
UNAVAILABLE = {
    "NO_SNAPSHOT": "No live figures yet: no scenario is loaded. Load one in Demo tools.",
    "*": "Live figures for this store are unavailable right now.",
}
ORDER_STATUSES = {
    "packed_waiting_rider": "packed and waiting",
    "picking": "picking",
    "out_for_delivery": "out for delivery",
}


def is_greeting(text: str) -> bool:
    return bool(GREETING.match(text.strip()))


def _minutes(seconds: int | None) -> str | None:
    return None if seconds is None else f"{max(seconds, 0) // 60} min"


def _salutation(name: str, now: datetime) -> str:
    hour = now.astimezone(TIMEZONE).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part}, {name.split()[0]}."


def rider_stats(status: dict[str, Any]) -> str:
    summary = status["summary"]
    riders = status["riders"]
    line = (
        f"**Riders:** {summary['available_riders']} available of {len(riders)}, "
        f"{summary['riders_returning_within_10_min']} back within 10 min"
    )
    load = summary["pending_per_available_rider"]
    line += (
        f"; {load} packed orders per available rider."
        if load is not None
        else "; no rider is available right now."
    )
    return line


def order_queue(status: dict[str, Any]) -> str:
    queue = status["queue"]
    counts = queue["counts_by_status"]
    parts = [
        f"{counts[key]} {label}"
        for key, label in ORDER_STATUSES.items()
        if counts.get(key)
    ]
    parts += [
        f"{count} {key.replace('_', ' ')}"
        for key, count in counts.items()
        if key not in ORDER_STATUSES
    ]
    detail = f": {', '.join(parts)}" if parts else ""
    return f"**Order queue:** {queue['open_orders']} open{detail}."


def oldest_order_age(status: dict[str, Any]) -> str:
    queue = status["queue"]
    oldest = _minutes(queue["oldest_order_age_sec"])
    if oldest is None:
        return "**Oldest order:** none open."
    extra = [
        f"oldest {label} {value}"
        for label, value in (
            ("packed", _minutes(queue["oldest_packed_waiting_age_sec"])),
            ("frozen packed", _minutes(queue["oldest_frozen_packed_age_sec"])),
        )
        if value is not None
    ]
    detail = f" ({'; '.join(extra)})" if extra else ""
    return f"**Oldest order:** {oldest}{detail}."


LIVE_VIEWS: dict[str, Callable[[dict[str, Any]], str]] = {
    BriefingView.RIDER_STATS: rider_stats,
    BriefingView.ORDER_QUEUE: order_queue,
    BriefingView.OLDEST_ORDER_AGE: oldest_order_age,
}


def briefing(
    preferences: PreferenceService,
    manager_name: str,
    handover_note_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[str, list[ToolCallTrace]]:
    """The greeting reply, with the manager's chosen views in their order.

    Also returns the tool calls it made, for the answer's trace.
    """
    calls: list[ToolCallTrace] = []
    now = now or datetime.now(TIMEZONE)
    setting = preferences.current(PreferenceCode.BRIEFING)
    views = [str(view) for view in (setting.value or [])] if setting.enabled else []
    lines = [_salutation(manager_name, now)]
    live_views = [view for view in views if view in LIVE_VIEWS]
    if live_views:
        status = _live_status(preferences.store_id, calls)
        if "error" in status:
            lines.append(UNAVAILABLE.get(status["error"]["code"], UNAVAILABLE["*"]))
        else:
            as_of = datetime.fromisoformat(status["as_of"]).strftime("%H:%M")
            stale = " These figures are stale." if status["stale"] else ""
            lines.append(f"Your store as of {as_of}.{stale}")
            lines.append(
                "\n".join(f"- {LIVE_VIEWS[view](status)}" for view in live_views),
            )
    if BriefingView.LAST_HANDOVER_NOTE in views:
        try:
            note = chat_handover_block(handover_note_id, preferences.store_id)
        except (SQLAlchemyError, RuntimeError):
            logger.warning(
                "Could not load the handover for the briefing",
                exc_info=True,
            )
            note = None
        if note:
            header, _, body = note.partition("\n")
            lines.append(f"**{header}**\n{body}")
        else:
            lines.append("No handover note yet.")
    if len(lines) == 1:
        lines.append(
            "Your greeting shows nothing; choose what to see in Settings → Greeting.",
        )
    lines.append("What would you like to look at?")
    return "\n\n".join(lines), calls


def _live_status(store_id: str, calls: list[ToolCallTrace]) -> dict[str, Any]:
    """Run the live status tool the way the assistant does, recording the call."""
    tool = next(
        tool
        for tool in traced_tools(dispatch_tools(store_id), calls)
        if tool.name == "get_live_dispatch_status"
    )
    status = json.loads(tool.run({"store_id": store_id}))
    return status if isinstance(status, dict) else {"error": {"code": "*"}}
