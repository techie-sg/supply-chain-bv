"""Read-only dispatch tools and per-answer tracing for local tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import replace
from datetime import date as _date
from datetime import datetime, timedelta
from functools import partial
from time import perf_counter
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from constants import TIMEZONE
from domain.chat import ToolCallTrace
from domain.tools import LiveStatusInput, MetricsInput, Tool, ToolResult
from queries.tools import read_delivery_metrics, read_live_dispatch
from service.cache import cache_key, metrics_cache
from service.scenarios import scenario_metadata
from service.tracing import observation

logger = structlog.stdlib.get_logger(__name__)

STALE_AFTER_SEC = 300  # live data older than this is flagged stale
IN_PROGRESS_METRICS_TTL_SEC = (
    60  # a period that includes the current hour can still change
)

# Whether the last metrics lookup on this thread came from the cache; read once
# by _record_call so the flag reaches the trace but never the model's tool result.
_metrics_cache_hit: ContextVar[bool | None] = ContextVar(
    "metrics_cache_hit",
    default=None,
)
_metrics_cache_state: ContextVar[str | None] = ContextVar(
    "metrics_cache_state",
    default=None,
)


LIVE_DESCRIPTION = (
    "Get the live order queue, rider statuses, and zone ride times for one "
    "dark store. Use only when the answer needs actual order or rider facts, "
    "not for general policy questions such as whether riders may jump red lights. "
    "Use this for questions about the current backlog, waiting "
    "orders, available or returning riders, batching candidates, ETAs, or a "
    "rider's hours on shift and time since last break. Figures describe the "
    "snapshot time in as_of; always quote that time with live numbers and "
    "mention when stale is true. For past performance use get_delivery_metrics."
)
METRICS_DESCRIPTION = (
    "Get historical hourly delivery metrics for one dark store, date, and "
    "hour range: order counts, SLA percentage, pick-pack, rider-wait and ride "
    "minutes, riders online, and rain flag, with an order-weighted summary. "
    "Use this for actual past performance or period comparisons, once per period; "
    "not for policy rules or general delivery targets. "
    "Hours are start-inclusive and end-exclusive: 8 to 10pm is start_hour=20, "
    "end_hour=22. Quote the computed summary and mention hours_missing. For "
    "the current queue use get_live_dispatch_status."
)


def _error(code: str, message: str, **details) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(TIMEZONE).isoformat() if value is not None else None


def get_live_dispatch_status(store_id: str) -> dict:
    data = read_live_dispatch(store_id)
    if not data.known_store_ids:
        return _error(
            "NO_SNAPSHOT",
            "No live dispatch snapshot is loaded, so no live figures are available. "
            "Do not estimate queue or rider numbers.",
        )

    if data.as_of is None or data.scenario_key is None:
        return _error(
            "UNKNOWN_STORE",
            f"No live dispatch data for store '{store_id}'. "
            f"Known store(s): {data.known_store_ids}.",
            requested=store_id,
            known_store_ids=data.known_store_ids,
        )

    as_of = data.as_of.astimezone(TIMEZONE)

    order_rows: list[dict[str, Any]] = sorted(
        (
            {
                "order_id": o.order_id,
                "status": o.status,
                "zone_id": o.zone_id,
                "item_count": o.item_count,
                "has_frozen_items": o.has_frozen_items,
                "assigned_rider_id": o.assigned_rider_id,
                "placed_at": _iso(o.placed_at),
                "age_sec": int(
                    (as_of - o.placed_at.astimezone(TIMEZONE)).total_seconds(),
                ),
            }
            for o in data.orders
        ),
        key=lambda row: row["age_sec"],
        reverse=True,  # oldest first
    )
    rider_rows: list[dict[str, Any]] = [
        {
            "rider_id": r.rider_id,
            "name": r.name,
            "status": r.status,
            "current_zone": r.current_zone,
            "employment_type": r.employment_type,
            "hours_on_shift": r.hours_on_shift,
            "minutes_since_last_break": r.minutes_since_last_break,
            "deliveries_today": r.deliveries_today,
            "eta_back_min": r.eta_back_min,
        }
        for r in data.riders
    ]

    packed = [o for o in order_rows if o["status"] == "packed_waiting_rider"]
    packed_waiting = len(packed)
    counts_by_status: dict[str, int] = {}
    for row in order_rows:
        counts_by_status[row["status"]] = counts_by_status.get(row["status"], 0) + 1
    # order_rows is oldest first, so the first match is the oldest.
    oldest_frozen_packed = next((o for o in packed if o["has_frozen_items"]), None)
    available = sum(1 for r in rider_rows if r["status"] == "available")
    returning = sum(
        1
        for r in rider_rows
        if r["eta_back_min"] is not None and r["eta_back_min"] <= 10
    )

    scenario_key = data.scenario_key
    data_age_sec = int((datetime.now(TIMEZONE) - as_of).total_seconds())
    stale = data_age_sec > STALE_AFTER_SEC
    if stale:
        logger.warning(
            "Stale live data",
            store_id=store_id,
            data_age_sec=data_age_sec,
            stale_after_sec=STALE_AFTER_SEC,
        )

    return {
        "store_id": store_id,
        "scenario_key": scenario_key,
        "as_of": as_of.isoformat(),
        "data_age_sec": data_age_sec,
        "stale": stale,
        "stale_after_sec": STALE_AFTER_SEC,
        "fixture_metadata_source": "scenario_yaml",
        "conditions": {"is_raining": scenario_metadata(scenario_key)["is_raining"]},
        "queue": {
            "open_orders": len(order_rows),
            "packed_waiting": packed_waiting,
            "counts_by_status": counts_by_status,
            "oldest_order_age_sec": order_rows[0]["age_sec"] if order_rows else None,
            "oldest_packed_waiting_age_sec": packed[0]["age_sec"] if packed else None,
            "oldest_frozen_packed_age_sec": (
                oldest_frozen_packed["age_sec"] if oldest_frozen_packed else None
            ),
            "orders_truncated": len(order_rows) > 50,
            "orders": order_rows[:50],
        },
        "riders": rider_rows,
        "zones": [
            {
                "zone_id": z.zone_id,
                "zone_name": z.zone_name,
                "distance_from_store_km": z.distance_from_store_km,
                "avg_ride_min_dry": z.avg_ride_min_dry,
                "avg_ride_min_rain": z.avg_ride_min_rain,
            }
            for z in data.zones
        ],
        "summary": {
            "available_riders": available,
            "riders_returning_within_10_min": returning,
            "pending_per_available_rider": (
                round(packed_waiting / available, 2) if available else None
            ),
            "note": None if available else "No riders are currently available.",
        },
    }


def _weighted(rows: list, field: str) -> float | None:
    total = sum(r.orders for r in rows)
    if total == 0:
        return None
    return round(sum(getattr(r, field) * r.orders for r in rows) / total, 1)


def get_delivery_metrics(
    store_id: str,
    date: str,
    start_hour: int,
    end_hour: int,
) -> dict:
    try:
        day = _date.fromisoformat(date)
    except ValueError:
        return _error(
            "INVALID_PERIOD",
            f"'{date}' is not a valid YYYY-MM-DD date.",
            reason="invalid_date",
        )
    if not 0 <= start_hour <= 23 or not 1 <= end_hour <= 24:
        return _error(
            "INVALID_PERIOD",
            "Hours must be between 0 and 24, with start_hour before end_hour.",
            reason="hour_out_of_range",
        )
    if end_hour <= start_hour:
        return _error(
            "INVALID_PERIOD",
            "end_hour must be greater than start_hour. "
            "For 8 to 10pm use start_hour=20, end_hour=22.",
            reason="end_hour_not_after_start_hour",
        )

    key = cache_key(store_id, date, start_hour, end_hour)
    cache = metrics_cache()
    _metrics_cache_state.set("lookup" if cache.enabled else "off")
    if cache.enabled and (hit := cache.get(key)) is not None:
        _metrics_cache_state.set("hit")
        _metrics_cache_hit.set(True)
        return hit

    _metrics_cache_state.set("miss" if cache.enabled else "off")
    data = read_delivery_metrics(store_id, day, start_hour, end_hour)
    rows = data.rows
    if not rows:
        if store_id not in data.known_store_ids:
            return _error(
                "UNKNOWN_STORE",
                f"No delivery metrics for store '{store_id}'.",
                requested=store_id,
                known_store_ids=data.known_store_ids,
            )

        available_dates = [value.isoformat() for value in data.available_dates]
        return _error(
            "NO_METRICS_FOR_PERIOD",
            f"No hourly metrics for store '{store_id}' on {date} in that range. "
            f"Data exists for: {', '.join(available_dates)}.",
            available_dates=available_dates,
            available_hours_on_date=data.available_hours,
        )

    returned = {r.hour for r in rows}
    total_riders = sum(r.riders_online for r in rows)
    total_orders = sum(r.orders for r in rows)

    result = {
        "store_id": store_id,
        "period": {
            "date": date,
            "start_hour": start_hour,
            "end_hour": end_hour,
            "hours_requested": end_hour - start_hour,
            "hours_returned": len(rows),
            "hours_missing": [
                h for h in range(start_hour, end_hour) if h not in returned
            ],
        },
        "hourly": [
            {
                "hour": r.hour,
                "orders": r.orders,
                "sla_10min_pct": r.sla_10min_pct,
                "avg_pick_pack_min": r.avg_pick_pack_min,
                "avg_rider_wait_min": r.avg_rider_wait_min,
                "avg_ride_min": r.avg_ride_min,
                "riders_online": r.riders_online,
                "rain_flag": r.rain_flag,
            }
            for r in rows
        ],
        "period_summary": {
            "total_orders": total_orders,
            "sla_10min_pct": _weighted(rows, "sla_10min_pct"),
            "avg_pick_pack_min": _weighted(rows, "avg_pick_pack_min"),
            "avg_rider_wait_min": _weighted(rows, "avg_rider_wait_min"),
            "avg_ride_min": _weighted(rows, "avg_ride_min"),
            "avg_riders_online": round(total_riders / len(rows), 1),
            "orders_per_rider_online": (
                round(total_orders / total_riders, 2) if total_riders else None
            ),
            "rain_hours": sum(1 for r in rows if r.rain_flag),
        },
        "source": "hourly_metrics (historical aggregates)",
    }
    if cache.enabled:
        _metrics_cache_hit.set(False)
        cache.set(key, result, _metrics_ttl(day, end_hour, cache.ttl))
    return result


def _metrics_ttl(day: _date, end_hour: int, ttl: float) -> float:
    """Full TTL for a finished period; a short one while it still includes now."""
    period_end = datetime.combine(day, datetime.min.time(), TIMEZONE) + timedelta(
        hours=end_hour,
    )
    if period_end <= datetime.now(TIMEZONE):
        return ttl
    return min(ttl, IN_PROGRESS_METRICS_TTL_SEC)


def _invoke(
    call: Callable[..., dict],
    input_model: type[BaseModel],
    arguments: dict[str, Any],
) -> str:
    try:
        inputs = input_model.model_validate(arguments)
    except ValidationError as exc:
        return json.dumps(
            _error(
                "INVALID_INPUT",
                "Correct the tool arguments and try again.",
                errors=exc.errors(include_input=False, include_url=False),
            ),
        )
    try:
        return json.dumps(call(**inputs.model_dump()))
    except Exception:
        logger.exception("Dispatch tool failed", tool=call.__name__)
        return json.dumps(
            _error(
                "DATA_UNAVAILABLE",
                "Dispatch data could not be read. Retry once; if it fails again, "
                "tell the manager the data cannot be reached.",
                retryable=True,
            ),
        )


def dispatch_tools(store_id: str) -> list[Tool]:
    """Expose local service functions through the existing model tool contract."""
    return [
        Tool(
            name="get_live_dispatch_status",
            description=f"{LIVE_DESCRIPTION} The current store_id is {store_id}.",
            parameters=LiveStatusInput.model_json_schema(),
            run=partial(_invoke, get_live_dispatch_status, LiveStatusInput),
        ),
        Tool(
            name="get_delivery_metrics",
            description=f"{METRICS_DESCRIPTION} The current store_id is {store_id}.",
            parameters=MetricsInput.model_json_schema(),
            run=partial(_invoke, get_delivery_metrics, MetricsInput),
        ),
    ]


def _record_call(
    tool: Tool,
    trace: list[ToolCallTrace],
    results: list[ToolResult] | None,
    arguments: dict[str, Any],
) -> str:
    with observation(tool.name, kind="tool", input=arguments) as span:
        started = perf_counter()
        _metrics_cache_hit.set(None)
        _metrics_cache_state.set(None)
        try:
            output = (
                tool.run(arguments)
                if isinstance(arguments, dict)
                else json.dumps(
                    _error("INVALID_INPUT", "Tool arguments must be an object."),
                )
            )
        finally:
            span.update(metadata={"cache": _metrics_cache_state.get()})
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            parsed = None
        data = parsed if isinstance(parsed, dict) else {}
        error = data.get("error")
        duration_ms = round((perf_counter() - started) * 1000, 2)
        cache_hit = _metrics_cache_hit.get()
        span.update(
            output=data if isinstance(parsed, dict) else output,
            metadata={
                "as_of": data.get("as_of"),
                "stale": bool(data.get("stale")),
                "cache_hit": cache_hit,
            },
            **(
                {
                    "level": "WARNING",
                    "status_message": (
                        error.get("code", "TOOL_ERROR")
                        if isinstance(error, dict)
                        else "TOOL_ERROR"
                    ),
                }
                if error
                else {}
            ),
        )
        entry: ToolCallTrace = {
            "tool": tool.name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "error": error.get("code") if isinstance(error, dict) else None,
            "as_of": data.get("as_of"),
            "stale": bool(data.get("stale")),
        }
        if cache_hit is not None:  # only cacheable tools; live-status entries unchanged
            entry["cache_hit"] = cache_hit
            entry["latency_ms"] = duration_ms
        trace.append(entry)
        if results is not None:
            results.append({"tool": tool.name, "result": data})
        logger.info(
            "Tool call",
            tool=tool.name,
            arguments=arguments,
            duration_ms=duration_ms,
            is_error=bool(error),
            cache_hit=cache_hit,
        )
        return output


def traced_tools(
    tools: Sequence[Tool],
    trace: list[ToolCallTrace],
    results: list[ToolResult] | None = None,
) -> list[Tool]:
    """Record actual tool executions without maintaining another model loop.

    `results` also keeps what each call returned, for the guardrail checks;
    it is never stored with the message.
    """
    return [
        replace(tool, run=partial(_record_call, tool, trace, results)) for tool in tools
    ]
