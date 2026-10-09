"""Check each manager's alerts against the store's live data, in code.

The model never decides whether an alert fires. `evaluate` computes each alert's
measure from the loaded snapshot and compares it with the manager's threshold,
inside the alert's days and hours. `check_alerts` records a pop-up when an alert
breaches, at most once per cooldown; the UI shows what was recorded.

Measures use the snapshot's `as_of` time, so order ages do not grow while the
page is open. Windows use the current time in Asia/Kolkata. Cooldowns use real
time, so a breach that lasts pops up again once its cooldown has passed.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import structlog
from sqlalchemy import Engine

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID
from database.models import AlertEvent, HourlyMetric, Order, Rider
from domain.memory import AlertOperator, AlertOptions, PreferenceCategory
from queries.alerts import record_trigger, triggers_since
from queries.scenarios import read_scenario_rows
from service.preferences import EffectiveSetting, manager_preferences
from service.scenarios import TIMEZONE

logger = structlog.stdlib.get_logger(__name__)

WAITING = "packed_waiting_rider"
NOT_OUT = ("picking", WAITING)
# Pop-ups recorded this recently are shown to a page that opens or switches
# manager, so an alert already firing is not missed.
RECENT_MINUTES = 15
# Breaching a limit by this share or more is critical rather than a warning.
CRITICAL_MARGIN = 0.5
OPERATOR_TEXT = {
    AlertOperator.GT: "above",
    AlertOperator.GTE: "at or above",
    AlertOperator.LT: "below",
}
UNIT_TEXT = {
    "orders_per_rider": "orders per available rider",
    "orders": "orders",
    "minutes": "min",
    "percent": "%",
}


@dataclass(frozen=True)
class Snapshot:
    """The loaded scenario's rows at one moment."""

    as_of: datetime
    orders: list[Order]
    riders: list[Rider]
    hourly: list[HourlyMetric]


@dataclass(frozen=True)
class Measure:
    """An alert's current value, and the rows that produce it."""

    value: float
    summary: str
    items: list[dict[str, Any]] = field(default_factory=list)
    # True when the value alone cannot express the breach, such as orders
    # waiting with no rider available at all.
    forced: bool = False


@dataclass(frozen=True)
class AlertResult:
    code: str
    name: str
    description: str
    value: float
    threshold: float
    operator: str
    unit: str
    window: str
    cooldown_min: int
    as_of: datetime
    breached: bool
    measure: Measure

    @property
    def severity(self) -> str:
        if self.measure.forced:
            return "critical"
        if self.threshold == 0:
            return "warning"
        gap = abs(self.value - self.threshold) / abs(self.threshold)
        return "critical" if gap >= CRITICAL_MARGIN else "warning"

    def limit_text(self) -> str:
        return f"{OPERATOR_TEXT[AlertOperator(self.operator)]} {_amount(self.threshold, self.unit)}"


def _amount(value: float, unit: str) -> str:
    number = f"{value:.1f}".rstrip("0").rstrip(".")
    if unit == "percent":
        return f"{number}%"
    return f"{number} {UNIT_TEXT.get(unit, unit)}".strip()


def _minutes(as_of: datetime, placed_at: datetime) -> float:
    return max((as_of - placed_at).total_seconds() / 60, 0.0)


def _order_item(order: Order, as_of: datetime) -> dict[str, Any]:
    return {
        "order": order.order_id,
        "zone": order.zone_id,
        "items": order.item_count,
        "frozen": order.has_frozen_items,
        "status": order.status,
        "waiting_min": round(_minutes(as_of, order.placed_at), 1),
    }


def _rider_item(rider: Rider) -> dict[str, Any]:
    return {
        "rider": rider.rider_id,
        "name": rider.name,
        "status": rider.status,
        "hours_on_shift": rider.hours_on_shift,
        "back_in_min": rider.eta_back_min,
    }


def _oldest(orders: list[Order], as_of: datetime, label: str) -> Measure | None:
    if not orders:
        return None
    ordered = sorted(orders, key=lambda order: order.placed_at)
    age = _minutes(as_of, ordered[0].placed_at)
    return Measure(
        round(age, 1),
        f"{label} {ordered[0].order_id} has waited {age:.1f} min for a rider",
        [_order_item(order, as_of) for order in ordered],
    )


def measure(code: str, snapshot: Snapshot) -> Measure | None:
    """The alert's current value; None when the data to compute it is missing."""
    as_of = snapshot.as_of
    waiting = [order for order in snapshot.orders if order.status == WAITING]
    if code == "rider_shortage_alert":
        available = [rider for rider in snapshot.riders if rider.status == "available"]
        riders = [_rider_item(rider) for rider in snapshot.riders]
        orders = [_order_item(order, as_of) for order in waiting]
        if not available:
            return Measure(
                float(len(waiting)),
                f"{len(waiting)} packed orders waiting and no rider available",
                orders + riders,
                forced=bool(waiting),
            )
        ratio = len(waiting) / len(available)
        return Measure(
            round(ratio, 2),
            f"{len(waiting)} packed orders waiting for {len(available)} available "
            f"rider{'s' if len(available) != 1 else ''}",
            orders + riders,
        )
    if code == "orders_piling_up_alert":
        not_out = [order for order in snapshot.orders if order.status in NOT_OUT]
        picking = sum(order.status == "picking" for order in not_out)
        return Measure(
            float(len(not_out)),
            f"{len(not_out)} orders not yet out for delivery "
            f"({picking} picking, {len(not_out) - picking} packed and waiting)",
            [_order_item(order, as_of) for order in not_out],
        )
    if code == "order_waiting_too_long_alert":
        return _oldest(waiting, as_of, "Order")
    if code == "frozen_order_waiting_alert":
        frozen = [order for order in waiting if order.has_frozen_items]
        return _oldest(frozen, as_of, "Frozen order")
    if code == "sla_dip_alert":
        hour_start = as_of.astimezone(TIMEZONE).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        completed = [
            row
            for row in snapshot.hourly
            if datetime.combine(row.date, time(row.hour), TIMEZONE) < hour_start
        ]
        if not completed:
            return None
        last = max(completed, key=lambda row: (row.date, row.hour))
        return Measure(
            float(last.sla_10min_pct),
            f"{last.sla_10min_pct:g}% of orders within 10 minutes in the "
            f"{last.hour:02d}:00 hour of {last.date:%d %b}",
            [
                {
                    "hour": f"{row.date:%d %b} {row.hour:02d}:00",
                    "orders": row.orders,
                    "sla_pct": row.sla_10min_pct,
                    "riders_online": row.riders_online,
                    "rain": row.rain_flag,
                }
                for row in sorted(
                    completed,
                    key=lambda row: (row.date, row.hour),
                )[-4:]
            ],
        )
    return None


def _breached(value: float, operator: str, threshold: float) -> bool:
    match AlertOperator(operator):
        case AlertOperator.GT:
            return value > threshold
        case AlertOperator.GTE:
            return value >= threshold
        case AlertOperator.LT:
            return value < threshold
    return False


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def in_window(options: AlertOptions | None, now: datetime) -> bool:
    """Whether `now` falls in the alert's days and hours (Asia/Kolkata).

    A window that crosses midnight (19:00 to 02:00) belongs to the day it
    starts on, so 01:00 on Sunday is inside a Saturday-evening window.
    """
    if options is None:
        return True
    local = now.astimezone(TIMEZONE)
    start = _clock(options.start) if options.start else time(0, 0)
    end = _clock(options.end) if options.end else None
    clock = local.time()
    day = local
    if end is None or start <= end:
        in_hours = clock >= start and (end is None or clock < end)
    elif clock >= start:
        in_hours = True
    elif clock < end:
        in_hours = True
        day = local - timedelta(days=1)
    else:
        in_hours = False
    if not in_hours:
        return False
    if options.days:
        return day.strftime("%a").lower() in {str(item) for item in options.days}
    return True


def window_text(options: AlertOptions | None) -> str:
    if options is None or not (options.days or options.start or options.end):
        return "at all times"
    parts = []
    if options.days:
        parts.append(", ".join(str(day).capitalize() for day in options.days))
    if options.start or options.end:
        parts.append(f"{options.start or '00:00'}–{options.end or 'end of day'}")
    return " ".join(parts)


def evaluate(
    snapshot: Snapshot,
    settings: Sequence[EffectiveSetting],
    now: datetime,
) -> list[AlertResult]:
    """Every enabled alert inside its window, with its value and whether it breaches."""
    results = []
    for setting in settings:
        definition = setting.definition
        if definition.category != PreferenceCategory.ALERT:
            continue
        if not setting.enabled or setting.value is None:
            continue
        if not in_window(setting.options, now):
            continue
        current = measure(definition.code, snapshot)
        if current is None:
            continue
        threshold = float(setting.value)
        operator = definition.operator or AlertOperator.GT
        cooldown = (
            setting.options.cooldown_min
            if setting.options and setting.options.cooldown_min
            else definition.default_cooldown_min or 15
        )
        results.append(
            AlertResult(
                code=definition.code,
                name=definition.name,
                description=definition.description,
                value=current.value,
                threshold=threshold,
                operator=operator,
                unit=definition.unit or "",
                window=window_text(setting.options),
                cooldown_min=cooldown,
                as_of=snapshot.as_of,
                breached=current.forced
                or _breached(current.value, operator, threshold),
                measure=current,
            ),
        )
    return results


def live_snapshot(engine: Engine | None = None) -> Snapshot | None:
    """The loaded scenario's rows; None when no scenario is loaded."""
    rows = read_scenario_rows(engine)
    orders = [row for row in rows if isinstance(row, Order)]
    riders = [row for row in rows if isinstance(row, Rider)]
    if not orders and not riders:
        return None
    as_of = (orders or riders)[0].as_of
    return Snapshot(
        as_of=as_of,
        orders=orders,
        riders=riders,
        hourly=[row for row in rows if isinstance(row, HourlyMetric)],
    )


def _day_start(now: datetime) -> datetime:
    return now.astimezone(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)


def _details(result: AlertResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "operator": result.operator,
        "unit": result.unit,
        "window": result.window,
        "severity": result.severity,
        "summary": result.measure.summary,
        "items": result.measure.items,
    }


def alert_view(event: AlertEvent, today: int) -> dict[str, Any]:
    """A recorded pop-up, ready for the UI (plain data, safe to keep in state)."""
    details = event.details
    unit = details.get("unit", "")
    return {
        "id": str(event.id),
        "code": event.code,
        "name": details.get("name", event.code),
        "severity": details.get("severity", "warning"),
        "summary": details.get("summary", ""),
        "value": _amount(event.value, unit),
        "limit": f"{OPERATOR_TEXT[AlertOperator(details.get('operator', 'gt'))]} "
        f"{_amount(event.threshold, unit)}",
        "window": details.get("window", "at all times"),
        "as_of": event.snapshot_as_of.astimezone(TIMEZONE).strftime("%H:%M"),
        "triggered_at": event.triggered_at.astimezone(TIMEZONE).strftime("%H:%M"),
        "today": today,
    }


@dataclass(frozen=True)
class AlertCheck:
    """What the page should know after one check."""

    available: bool
    alerts: list[dict[str, Any]]
    # Pop-ups this check recorded; the rest were recorded earlier.
    recorded: int = 0


def check_alerts(
    manager_id: str = DEMO_MANAGER_ID,
    store_id: str = DEMO_STORE_ID,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> AlertCheck:
    """Record pop-ups for breached alerts and return the manager's recent ones.

    Returns pop-ups from the last few minutes, including ones another tab or an
    earlier check recorded; the page shows those it has not shown yet.
    """
    now = now or datetime.now(TIMEZONE)
    snapshot = live_snapshot(engine)
    if snapshot is None:
        return AlertCheck(available=False, alerts=[])
    settings = manager_preferences(manager_id).effective()
    recorded = 0
    for result in evaluate(snapshot, settings, now):
        if not result.breached:
            continue
        event = record_trigger(
            {
                "store_id": store_id,
                "manager_id": manager_id,
                "code": result.code,
                "value": result.value,
                "threshold": result.threshold,
                "snapshot_as_of": result.as_of,
                "details": _details(result),
            },
            result.cooldown_min,
            engine,
        )
        if event is not None:
            recorded += 1
            logger.info(
                "Alert triggered",
                code=result.code,
                manager_id=manager_id,
                value=result.value,
                threshold=result.threshold,
            )
    today = triggers_since(store_id, manager_id, _day_start(now), engine=engine)
    counts: dict[str, int] = {}
    for event in today:
        counts[event.code] = counts.get(event.code, 0) + 1
    recent_from = now - timedelta(minutes=RECENT_MINUTES)
    return AlertCheck(
        available=True,
        alerts=[
            alert_view(event, counts[event.code])
            for event in today
            if event.triggered_at >= recent_from
        ],
        recorded=recorded,
    )


@dataclass(frozen=True)
class StoreCheck:
    """One background check of every manager at a store."""

    available: bool
    recorded: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.recorded.values())


def check_store(
    store_id: str = DEMO_STORE_ID,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> StoreCheck:
    """Check every manager's alerts, so pop-ups are recorded with no page open.

    The cron job runs this; open pages run `check_alerts` for their manager.
    Both record through the same cooldown, so a breach is recorded once.
    """
    from service.managers import store_managers

    now = now or datetime.now(TIMEZONE)
    if live_snapshot(engine) is None:
        return StoreCheck(available=False, recorded={})
    recorded = {}
    for manager in store_managers(store_id, engine):
        check = check_alerts(manager.manager_id, store_id, now, engine)
        recorded[manager.manager_id] = check.recorded
    return StoreCheck(available=True, recorded=recorded)


def diagnose(
    code: str,
    manager_id: str = DEMO_MANAGER_ID,
    store_id: str = DEMO_STORE_ID,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """Everything behind one alert: the measure now, what drives it, and today."""
    now = now or datetime.now(TIMEZONE)
    snapshot = live_snapshot(engine)
    setting = manager_preferences(manager_id).current(code)
    current = measure(code, snapshot) if snapshot else None
    today = triggers_since(store_id, manager_id, _day_start(now), code, engine)
    if current is None and not today:
        return None
    threshold = float(setting.value) if setting.value is not None else None
    operator = setting.definition.operator or AlertOperator.GT
    unit = setting.definition.unit or ""
    return {
        "code": code,
        "name": setting.definition.name,
        "description": setting.definition.description,
        "now": current.summary if current else "No live data to measure right now.",
        "value": _amount(current.value, unit) if current else None,
        "limit": (
            f"{OPERATOR_TEXT[AlertOperator(operator)]} {_amount(threshold, unit)}"
            if threshold is not None
            else "not set"
        ),
        "breached": bool(
            current
            and threshold is not None
            and (current.forced or _breached(current.value, operator, threshold)),
        ),
        "window": window_text(setting.options),
        "as_of": snapshot.as_of.astimezone(TIMEZONE).strftime("%H:%M")
        if snapshot
        else None,
        "items": current.items if current else [],
        "triggers": [
            {
                "at": event.triggered_at.astimezone(TIMEZONE).strftime("%H:%M"),
                "value": _amount(event.value, unit),
            }
            for event in today
        ],
        "allowed": f"{_amount(float(setting.definition.min_value or 0), unit)} to "
        f"{_amount(float(setting.definition.max_value or 0), unit)}",
    }


def alerts_block(
    manager_id: str = DEMO_MANAGER_ID,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> str | None:
    """Breached alerts for the model, as computed data with their as-of time."""
    now = now or datetime.now(TIMEZONE)
    snapshot = live_snapshot(engine)
    if snapshot is None:
        return None
    breached = [
        result
        for result in evaluate(
            snapshot,
            manager_preferences(manager_id).effective(),
            now,
        )
        if result.breached
    ]
    if not breached:
        return None
    as_of = snapshot.as_of.astimezone(TIMEZONE).strftime("%H:%M")
    lines = [
        "<alerts>",
        (
            f"Alerts firing now, computed by DispatchDesk from live data as of "
            f"{as_of}. Quote these figures with that time; do not recompute or "
            "extend them."
        ),
    ]
    for result in breached:
        lines.append(
            f"- {result.name}: {result.measure.summary} "
            f"(limit: {result.limit_text()}, {result.window}).",
        )
    lines.append("</alerts>")
    return "\n".join(lines)
