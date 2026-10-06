"""Tests for service/tools.py — read-only DB tool implementations."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import service.tools as tools_mod
from database.models import HourlyMetric, Order, Rider, Zone
from service.tools import (
    _iso,
    _weighted,
    get_delivery_metrics,
    get_live_dispatch_status,
)

TZ = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(scalars_sequence: list) -> MagicMock:
    """Return a mock Session context manager that yields scalars in order."""
    session = MagicMock()
    session.scalars.side_effect = [iter(s) for s in scalars_sequence]
    cm = MagicMock()
    cm.__enter__ = lambda _: session
    cm.__exit__ = lambda _, *a: False
    return cm


def _order(
    store_id: str = "S-1",
    order_id: str = "O-1",
    status: str = "packed_waiting_rider",
    zone_id: str = "Z-A",
    item_count: int = 3,
    has_frozen_items: bool = False,
    as_of: datetime | None = None,
    placed_at: datetime | None = None,
    scenario_key: str = "normal",
) -> MagicMock:
    now = datetime(2026, 10, 3, 19, 30, tzinfo=TZ)
    obj = MagicMock(spec=Order)
    obj.store_id = store_id
    obj.order_id = order_id
    obj.status = status
    obj.zone_id = zone_id
    obj.item_count = item_count
    obj.has_frozen_items = has_frozen_items
    obj.assigned_rider_id = None
    obj.as_of = as_of or now
    obj.placed_at = placed_at or datetime(2026, 10, 3, 19, 0, tzinfo=TZ)
    obj.scenario_key = scenario_key
    return obj


def _rider(
    store_id: str = "S-1",
    rider_id: str = "R-1",
    status: str = "available",
) -> MagicMock:
    obj = MagicMock(spec=Rider)
    obj.store_id = store_id
    obj.rider_id = rider_id
    obj.name = "Test Rider"
    obj.status = status
    obj.current_zone = "Z-A"
    obj.employment_type = "full_time"
    obj.hours_on_shift = 3.5
    obj.minutes_since_last_break = 45
    obj.deliveries_today = 8
    obj.eta_back_min = None
    obj.as_of = datetime(2026, 10, 3, 19, 30, tzinfo=TZ)
    obj.scenario_key = "normal"
    return obj


def _zone(zone_id: str = "Z-A") -> MagicMock:
    obj = MagicMock(spec=Zone)
    obj.zone_id = zone_id
    obj.zone_name = f"Zone {zone_id}"
    obj.distance_from_store_km = 2.5
    obj.avg_ride_min_dry = 12
    obj.avg_ride_min_rain = 18
    return obj


def _metric(
    store_id: str = "S-1",
    metric_date: date | None = None,
    hour: int = 18,
    orders: int = 10,
    sla_10min_pct: float = 82.0,
    avg_pick_pack_min: float = 4.2,
    avg_rider_wait_min: float = 1.1,
    avg_ride_min: float = 11.5,
    riders_online: int = 3,
    rain_flag: bool = False,
) -> MagicMock:
    obj = MagicMock(spec=HourlyMetric)
    obj.store_id = store_id
    obj.date = metric_date or date(2026, 10, 2)
    obj.hour = hour
    obj.orders = orders
    obj.sla_10min_pct = sla_10min_pct
    obj.avg_pick_pack_min = avg_pick_pack_min
    obj.avg_rider_wait_min = avg_rider_wait_min
    obj.avg_ride_min = avg_ride_min
    obj.riders_online = riders_online
    obj.rain_flag = rain_flag
    return obj


# ---------------------------------------------------------------------------
# _iso helper
# ---------------------------------------------------------------------------


def test_iso_converts_datetime_to_aware_isoformat() -> None:
    dt = datetime(2026, 10, 3, 14, 0, tzinfo=ZoneInfo("UTC"))
    result = _iso(dt)
    assert result is not None
    assert "19:30" in result  # UTC+5:30


def test_iso_returns_none_for_none() -> None:
    assert _iso(None) is None


# ---------------------------------------------------------------------------
# _weighted helper
# ---------------------------------------------------------------------------


def test_weighted_returns_none_when_total_orders_zero() -> None:
    rows = [_metric(orders=0)]
    assert _weighted(rows, "sla_10min_pct") is None


def test_weighted_computes_order_weighted_average() -> None:
    r1 = _metric(orders=10, sla_10min_pct=80.0)
    r2 = _metric(orders=10, sla_10min_pct=90.0)
    assert _weighted([r1, r2], "sla_10min_pct") == 85.0


# ---------------------------------------------------------------------------
# get_live_dispatch_status
# ---------------------------------------------------------------------------


def test_live_status_no_snapshot_when_empty_db() -> None:
    cm = _make_session([[], [], []])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_live_dispatch_status("S-1")
    assert result["error"]["code"] == "NO_SNAPSHOT"


def test_live_status_unknown_store() -> None:
    order = _order(store_id="S-2")
    rider = _rider(store_id="S-2")
    zone = _zone()
    cm = _make_session([[order], [rider], [zone]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_live_dispatch_status("S-1")
    assert result["error"]["code"] == "UNKNOWN_STORE"
    assert "S-2" in result["error"]["details"]["known_store_ids"]


def test_live_status_success_structure() -> None:
    order = _order(store_id="S-1", status="packed_waiting_rider")
    rider = _rider(store_id="S-1", status="available")
    zone = _zone()
    cm = _make_session([[order], [rider], [zone]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_live_dispatch_status("S-1")
    assert "error" not in result
    assert result["store_id"] == "S-1"
    assert "queue" in result and "riders" in result and "zones" in result
    assert result["summary"]["available_riders"] == 1
    assert result["summary"]["pending_per_available_rider"] == 1.0


def test_live_status_orders_truncated_at_50() -> None:
    orders = [_order(store_id="S-1", order_id=f"O-{i}") for i in range(55)]
    rider = _rider(store_id="S-1")
    cm = _make_session([orders, [rider], [_zone()]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_live_dispatch_status("S-1")
    assert len(result["queue"]["orders"]) == 50
    assert result["queue"]["orders_truncated"] is True


def test_live_status_rider_returning_within_10_min() -> None:
    order = _order(store_id="S-1")
    rider = _rider(store_id="S-1", status="on_delivery")
    rider.eta_back_min = 8
    cm = _make_session([[order], [rider], [_zone()]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_live_dispatch_status("S-1")
    assert result["summary"]["riders_returning_within_10_min"] == 1


# ---------------------------------------------------------------------------
# get_delivery_metrics
# ---------------------------------------------------------------------------


def test_delivery_metrics_invalid_date_format() -> None:
    result = get_delivery_metrics("S-1", "not-a-date", 8, 10)
    assert result["error"]["code"] == "INVALID_PERIOD"
    assert result["error"]["details"]["reason"] == "invalid_date"


def test_delivery_metrics_end_hour_not_after_start() -> None:
    result = get_delivery_metrics("S-1", "2026-10-02", 10, 10)
    assert result["error"]["code"] == "INVALID_PERIOD"
    assert result["error"]["details"]["reason"] == "end_hour_not_after_start_hour"


def test_delivery_metrics_unknown_store() -> None:
    # First scalars: store_rows for S-1 → empty; second: known store_ids
    cm = _make_session([[], ["S-2"]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_delivery_metrics("S-1", "2026-10-02", 8, 10)
    assert result["error"]["code"] == "UNKNOWN_STORE"
    assert "S-2" in result["error"]["details"]["known_store_ids"]


def test_delivery_metrics_no_data_for_period() -> None:
    m = _metric(store_id="S-1", metric_date=date(2026, 10, 1), hour=12)
    cm = _make_session([[m]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_delivery_metrics("S-1", "2026-10-02", 18, 20)
    assert result["error"]["code"] == "NO_METRICS_FOR_PERIOD"
    assert "2026-10-01" in result["error"]["details"]["available_dates"]


def test_delivery_metrics_success_structure() -> None:
    m18 = _metric(store_id="S-1", metric_date=date(2026, 10, 2), hour=18, orders=10)
    m19 = _metric(store_id="S-1", metric_date=date(2026, 10, 2), hour=19, orders=20)
    cm = _make_session([[m18, m19]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_delivery_metrics("S-1", "2026-10-02", 18, 20)
    assert "error" not in result
    assert result["store_id"] == "S-1"
    assert len(result["hourly"]) == 2
    assert result["period_summary"]["total_orders"] == 30


def test_delivery_metrics_missing_hours_reported() -> None:
    m = _metric(store_id="S-1", metric_date=date(2026, 10, 2), hour=18)
    cm = _make_session([[m]])
    with patch.object(tools_mod, "Session", return_value=cm):
        result = get_delivery_metrics("S-1", "2026-10-02", 18, 21)
    assert result["period"]["hours_missing"] == [19, 20]
