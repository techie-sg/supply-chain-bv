"""Dispatch calculations, local input validation, and tool traces."""

import json
from datetime import date, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from database.models import HourlyMetric, Order, Rider, Zone
from queries.tools import DispatchRows, MetricRows
from service import tools
from service.llm_service import Tool

TZ = ZoneInfo("Asia/Kolkata")
AS_OF = datetime(2026, 10, 3, 19, 30, tzinfo=TZ)


def order(identifier="O-1", *, age=1800, frozen=False, status="packed_waiting_rider"):
    return Order(
        order_id=identifier,
        store_id="S-1",
        scenario_key="normal",
        as_of=AS_OF,
        placed_at=AS_OF - timedelta(seconds=age),
        status=status,
        zone_id="Z-A",
        item_count=3,
        has_frozen_items=frozen,
        assigned_rider_id=None,
    )


def metric(hour=18, *, orders=10, sla=80, riders=3, rain=False):
    return HourlyMetric(
        store_id="S-1",
        date=date(2026, 10, 2),
        hour=hour,
        orders=orders,
        sla_10min_pct=sla,
        avg_pick_pack_min=4,
        avg_rider_wait_min=1,
        avg_ride_min=8,
        riders_online=riders,
        rain_flag=rain,
    )


@pytest.fixture
def dispatch(monkeypatch):
    rows = DispatchRows(
        known_store_ids=["S-1"],
        scenario_key="normal",
        as_of=AS_OF,
        orders=[order()],
        riders=[
            Rider(
                rider_id="R-1",
                store_id="S-1",
                scenario_key="normal",
                as_of=AS_OF,
                name="Test Rider",
                status="available",
                current_zone="Z-A",
                employment_type="full_time",
                hours_on_shift=3.5,
                minutes_since_last_break=45,
                deliveries_today=8,
                eta_back_min=None,
            ),
        ],
        zones=[
            Zone(
                zone_id="Z-A",
                zone_name="Zone A",
                distance_from_store_km=2.5,
                avg_ride_min_dry=12,
                avg_ride_min_rain=18,
            ),
        ],
    )
    monkeypatch.setattr(tools, "read_live_dispatch", lambda store_id: rows)
    return rows


@pytest.fixture
def historical(monkeypatch):
    rows = MetricRows(rows=[metric(), metric(19, orders=20, sla=95, rain=True)])
    monkeypatch.setattr(tools, "read_delivery_metrics", lambda *args: rows)
    return rows


def test_live_status_returns_snapshot_counts_and_rider_facts(dispatch):
    result = tools.get_live_dispatch_status("S-1")
    assert result["as_of"] == AS_OF.isoformat()
    assert result["queue"]["open_orders"] == 1
    assert result["queue"]["counts_by_status"] == {"packed_waiting_rider": 1}
    assert result["summary"]["pending_per_available_rider"] == 1
    assert result["riders"][0]["minutes_since_last_break"] == 45
    assert result["conditions"]["is_raining"] is False


def test_oldest_packed_and_frozen_ages_use_the_snapshot_time(dispatch):
    dispatch.orders.extend([order("O-2", age=600, frozen=True), order("O-3", age=300)])
    result = tools.get_live_dispatch_status("S-1")
    assert result["queue"]["oldest_packed_waiting_age_sec"] == 1800
    assert result["queue"]["oldest_frozen_packed_age_sec"] == 600
    assert result["stale"] is True


def test_live_status_keeps_counts_when_the_order_list_is_truncated(dispatch):
    dispatch.orders[:] = [order(f"O-{i}", age=i) for i in range(55)]
    result = tools.get_live_dispatch_status("S-1")
    assert result["queue"]["open_orders"] == 55
    assert len(result["queue"]["orders"]) == 50
    assert result["queue"]["orders_truncated"] is True
    assert result["queue"]["orders"][0]["order_id"] == "O-54"


def test_no_available_riders_does_not_divide_by_zero(dispatch):
    dispatch.riders[0].status = "on_delivery"
    dispatch.riders[0].eta_back_min = 8
    result = tools.get_live_dispatch_status("S-1")
    assert result["summary"]["pending_per_available_rider"] is None
    assert result["summary"]["riders_returning_within_10_min"] == 1


@pytest.mark.parametrize(
    "known, code",
    [([], "NO_SNAPSHOT"), (["S-2"], "UNKNOWN_STORE")],
)
def test_missing_snapshot_and_unknown_store_are_distinct(monkeypatch, known, code):
    monkeypatch.setattr(tools, "read_live_dispatch", lambda _: DispatchRows(known))
    assert tools.get_live_dispatch_status("S-1")["error"]["code"] == code


def test_metrics_are_order_weighted_and_missing_hours_are_explicit(historical):
    result = tools.get_delivery_metrics("S-1", "2026-10-02", 18, 21)
    summary = result["period_summary"]
    assert summary["total_orders"] == 30
    assert summary["sla_10min_pct"] == 90
    assert summary["orders_per_rider_online"] == 5
    assert summary["rain_hours"] == 1
    assert result["period"]["hours_missing"] == [20]


def test_metrics_with_no_orders_have_no_weighted_average(historical):
    historical.rows[:] = [metric(orders=0, riders=0)]
    summary = tools.get_delivery_metrics("S-1", "2026-10-02", 18, 19)["period_summary"]
    assert summary["sla_10min_pct"] is None
    assert summary["orders_per_rider_online"] is None


@pytest.mark.parametrize(
    "day, start, end",
    [
        ("bad-date", 8, 10),
        ("2026-02-30", 8, 10),
        ("2026-10-02", 10, 10),
        ("2026-10-02", -1, 25),
    ],
)
def test_invalid_periods_are_rejected_before_database_access(
    monkeypatch,
    day,
    start,
    end,
):
    read = Mock()
    monkeypatch.setattr(tools, "read_delivery_metrics", read)
    assert (
        tools.get_delivery_metrics("S-1", day, start, end)["error"]["code"]
        == "INVALID_PERIOD"
    )
    read.assert_not_called()


def test_missing_history_lists_available_dates_and_hours(monkeypatch):
    monkeypatch.setattr(
        tools,
        "read_delivery_metrics",
        lambda *args: MetricRows([], ["S-1"], [date(2026, 10, 2)], [12]),
    )
    error = tools.get_delivery_metrics("S-1", "2026-10-02", 18, 20)["error"]
    assert error["code"] == "NO_METRICS_FOR_PERIOD"
    assert error["details"]["available_dates"] == ["2026-10-02"]
    assert error["details"]["available_hours_on_date"] == [12]


def test_metrics_unknown_store(monkeypatch):
    monkeypatch.setattr(
        tools,
        "read_delivery_metrics",
        lambda *a: MetricRows([], ["S-2"]),
    )
    assert (
        tools.get_delivery_metrics("S-1", "2026-10-02", 18, 20)["error"]["code"]
        == "UNKNOWN_STORE"
    )


@pytest.mark.parametrize(
    "arguments",
    [None, [], {}, {"store_id": "S-1", "extra": True}],
)
def test_tool_inputs_are_validated_before_queries(monkeypatch, arguments):
    read = Mock()
    monkeypatch.setattr(tools, "read_live_dispatch", read)
    tool = tools.dispatch_tools("S-1")[0]
    assert json.loads(tool.run(arguments))["error"]["code"] == "INVALID_INPUT"
    assert tool.parameters["additionalProperties"] is False
    read.assert_not_called()


def test_tool_failures_are_returned_without_internal_database_details(monkeypatch):
    def unavailable(store_id):
        raise RuntimeError("private connection detail")

    monkeypatch.setattr(tools, "read_live_dispatch", unavailable)
    result = tools.dispatch_tools("S-1")[0].run({"store_id": "S-1"})
    assert json.loads(result)["error"]["code"] == "DATA_UNAVAILABLE"
    assert "private connection detail" not in result


def test_live_results_reach_the_model_and_the_trace(dispatch):
    trace = []
    wrapped = tools.traced_tools(tools.dispatch_tools("S-1"), trace)
    output = json.loads(wrapped[0].run({"store_id": "S-1"}))
    assert output["queue"]["open_orders"] == 1
    assert trace == [
        {
            "tool": "get_live_dispatch_status",
            "arguments": {"store_id": "S-1"},
            "as_of": AS_OF.isoformat(),
            "stale": True,
            "error": None,
        },
    ]


def test_plain_text_local_results_are_also_traced():
    trace = []
    local = Tool("local", "A local tool.", {"type": "object"}, lambda args: "proposal")
    assert tools.traced_tools([local], trace)[0].run({}) == "proposal"
    assert trace[0]["tool"] == "local" and trace[0]["error"] is None
