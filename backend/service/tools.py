"""Read-only dispatch tools and per-answer tracing for local tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date as _date
from datetime import datetime
from functools import partial
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, ValidationError

from domain.tools import LiveStatusInput, MetricsInput
from queries.tools import read_delivery_metrics, read_live_dispatch
from service.llm_service import Tool
from service.scenarios import _read_scenario

logger = structlog.stdlib.get_logger(__name__)

TZ = ZoneInfo("Asia/Kolkata")
STALE_AFTER_SEC = 300  # live data older than this is flagged stale


LIVE_DESCRIPTION = (
    "Get the live order queue, rider statuses, and zone ride times for one "
    "dark store. Use this for questions about the current backlog, waiting "
    "orders, available or returning riders, batching candidates, ETAs, or a "
    "rider's hours on shift and time since last break. Figures describe the "
    "snapshot time in as_of; always quote that time with live numbers and "
    "mention when stale is true. For past performance use get_delivery_metrics."
)
METRICS_DESCRIPTION = (
    "Get historical hourly delivery metrics for one dark store, date, and "
    "hour range: order counts, SLA percentage, pick-pack, rider-wait and ride "
    "minutes, riders online, and rain flag, with an order-weighted summary. "
    "Use this for past performance or period comparisons, once per period. "
    "Hours are start-inclusive and end-exclusive: 8 to 10pm is start_hour=20, "
    "end_hour=22. Quote the computed summary and mention hours_missing. For "
    "the current queue use get_live_dispatch_status."
)


def _error(code: str, message: str, **details) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(TZ).isoformat() if value is not None else None


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

    as_of = data.as_of.astimezone(TZ)

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
                "age_sec": int((as_of - o.placed_at.astimezone(TZ)).total_seconds()),
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
    data_age_sec = int((datetime.now(TZ) - as_of).total_seconds())
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
        "conditions": {"is_raining": _read_scenario(scenario_key).is_raining},
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

    return {
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
    trace: list[dict[str, Any]],
    arguments: dict[str, Any],
) -> str:
    started = perf_counter()
    output = (
        tool.run(arguments)
        if isinstance(arguments, dict)
        else json.dumps(_error("INVALID_INPUT", "Tool arguments must be an object."))
    )
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = {}
    data = parsed if isinstance(parsed, dict) else {}
    error = data.get("error")
    trace.append(
        {
            "tool": tool.name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "error": error.get("code") if isinstance(error, dict) else None,
            "as_of": data.get("as_of"),
            "stale": data.get("stale"),
        },
    )
    logger.info(
        "Tool call",
        tool=tool.name,
        arguments=arguments,
        duration_ms=round((perf_counter() - started) * 1000, 2),
        is_error=bool(error),
    )
    return output


def traced_tools(
    tools: Sequence[Tool],
    trace: list[dict[str, Any]],
) -> list[Tool]:
    """Record actual tool executions without maintaining another model loop."""
    return [replace(tool, run=partial(_record_call, tool, trace)) for tool in tools]
