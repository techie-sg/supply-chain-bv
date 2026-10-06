from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import HourlyMetric, Order, Rider, Zone
from database.session import build_engine

TZ = ZoneInfo("Asia/Kolkata")
_engine = build_engine()  # one engine for the module; connections are pooled


def _error(code: str, message: str, **details) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(TZ).isoformat() if value is not None else None


def get_live_dispatch_status(store_id: str) -> dict:
    with Session(_engine) as session:
        orders = list(session.scalars(select(Order)))
        riders = list(session.scalars(select(Rider)))
        zones = list(session.scalars(select(Zone)))

    if not orders and not riders:
        return _error(
            "NO_SNAPSHOT",
            "No live dispatch snapshot is loaded, so no live figures are available. "
            "Do not estimate queue or rider numbers.",
        )

    known = sorted({o.store_id for o in orders} | {r.store_id for r in riders})
    if store_id not in known:
        return _error(
            "UNKNOWN_STORE",
            f"No live dispatch data for store '{store_id}'. Known store(s): {known}.",
            requested=store_id,
            known_store_ids=known,
        )

    orders = [o for o in orders if o.store_id == store_id]
    riders = [r for r in riders if r.store_id == store_id]
    as_of = (orders or riders)[0].as_of.astimezone(TZ)

    order_rows = sorted(
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
            for o in orders
        ),
        key=lambda row: row["age_sec"],
        reverse=True,  # oldest first
    )
    rider_rows = [
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
        for r in riders
    ]

    packed_waiting = sum(1 for o in order_rows if o["status"] == "packed_waiting_rider")
    available = sum(1 for r in rider_rows if r["status"] == "available")
    returning = sum(
        1
        for r in rider_rows
        if r["eta_back_min"] is not None and r["eta_back_min"] <= 10
    )

    return {
        "store_id": store_id,
        "scenario_key": (orders or riders)[0].scenario_key,
        "as_of": as_of.isoformat(),
        "data_age_sec": int((datetime.now(TZ) - as_of).total_seconds()),
        "conditions": {"is_raining": None},
        "queue": {
            "open_orders": len(order_rows),
            "packed_waiting": packed_waiting,
            "oldest_order_age_sec": order_rows[0]["age_sec"] if order_rows else None,
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
            for z in zones
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
    if end_hour <= start_hour:
        return _error(
            "INVALID_PERIOD",
            "end_hour must be greater than start_hour. "
            "For 8 to 10pm use start_hour=20, end_hour=22.",
            reason="end_hour_not_after_start_hour",
        )

    # Both queries share one session so the second SELECT runs while the
    # connection is still open (the original had a closed-session bug here).
    with Session(_engine) as session:
        store_rows = list(
            session.scalars(
                select(HourlyMetric).where(HourlyMetric.store_id == store_id),
            ),
        )
        if not store_rows:
            known = sorted(
                session.scalars(select(HourlyMetric.store_id).distinct()),
            )
            return _error(
                "UNKNOWN_STORE",
                f"No delivery metrics for store '{store_id}'.",
                requested=store_id,
                known_store_ids=known,
            )

    rows = sorted(
        (r for r in store_rows if r.date == day and start_hour <= r.hour < end_hour),
        key=lambda r: r.hour,
    )
    if not rows:
        available_dates = sorted({r.date.isoformat() for r in store_rows})
        return _error(
            "NO_METRICS_FOR_PERIOD",
            f"No hourly metrics for store '{store_id}' on {date} in that range. "
            f"Data exists for: {', '.join(available_dates)}.",
            available_dates=available_dates,
            available_hours_on_date=sorted(r.hour for r in store_rows if r.date == day),
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
