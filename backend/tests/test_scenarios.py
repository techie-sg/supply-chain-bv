from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml

from database.models import HourlyMetric, Order, Rider, Zone
from service import scenarios
from service.scenarios import build_scenario, scenario_names


def test_every_scenario_builds_fresh_state() -> None:
    as_of = datetime(2026, 10, 3, 19, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    for key in (item["key"] for item in scenario_names()):
        rows, context = build_scenario(key, as_of)
        orders = [row for row in rows if isinstance(row, Order)]
        riders = [row for row in rows if isinstance(row, Rider)]
        history = [row for row in rows if isinstance(row, HourlyMetric)]
        zones = [row for row in rows if isinstance(row, Zone)]
        assert len(orders) == context["counts"]["orders"]
        assert len(riders) == 9 and len(history) == 12 and len(zones) == 3
        assert {row.date for row in history} == {
            as_of.date() - timedelta(days=1),
            as_of.date() - timedelta(days=2),
        }
        assert all(row.as_of == as_of for row in orders + riders)
        assert all((as_of - order.placed_at).total_seconds() > 0 for order in orders)
        assert {zone.zone_id for zone in zones} == {"Z-A", "Z-B", "Z-C"}
        assert "prompt" not in context


def test_normal_backlog_and_rain_have_distinct_starting_states() -> None:
    as_of = datetime(2026, 10, 3, 19, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    _, normal = build_scenario("normal", as_of)
    _, backlog = build_scenario("backlog", as_of)
    _, rain = build_scenario("rain", as_of)

    assert normal["counts"]["orders"] == 6
    assert normal["counts"]["available_riders"] == 4
    assert normal["is_raining"] is False
    assert backlog["counts"]["packed_waiting"] == 6
    assert backlog["counts"]["available_riders"] == 2
    assert backlog["is_raining"] is False
    assert rain["counts"]["packed_waiting"] == 8
    assert rain["is_raining"] is True
    assert len(rain["candidate_routes"]) == 8


def test_each_file_can_define_its_own_data(monkeypatch, tmp_path) -> None:
    source = scenarios.SCENARIO_DIR / "normal.yaml"
    data = yaml.safe_load(source.read_text())
    data["store_id"] = "TEST-STORE"
    data["zones"][0]["zone_id"] = "TEST-ZONE"
    for rider in data["riders"]:
        if rider["current_zone"] == "Z-A":
            rider["current_zone"] = "TEST-ZONE"
    for order in data["orders"]:
        if order["zone_id"] == "Z-A":
            order["zone_id"] = "TEST-ZONE"
    data["hourly_metrics"] = data["hourly_metrics"][:1]
    (tmp_path / "custom.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(scenarios, "SCENARIO_DIR", tmp_path)

    rows, context = build_scenario(
        "custom",
        datetime(2026, 10, 3, 19, 30, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert scenario_names() == [
        {"key": "custom", "title": data["title"], "description": data["description"]},
    ]
    assert context["store_id"] == "TEST-STORE"
    assert context["counts"]["hourly_metrics"] == 1
    assert any(isinstance(row, Zone) and row.zone_id == "TEST-ZONE" for row in rows)


def test_invalid_scenario_is_rejected_before_database_work(
    monkeypatch,
    tmp_path,
) -> None:
    def no_database(*args, **kwargs):
        raise AssertionError("invalid scenarios must not access the database")

    monkeypatch.setattr(scenarios, "replace_scenario", no_database)
    data = yaml.safe_load((scenarios.SCENARIO_DIR / "normal.yaml").read_text())
    data["orders"][0]["assigned_rider_id"] = "UNKNOWN"
    (tmp_path / "broken.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(scenarios, "SCENARIO_DIR", tmp_path)

    with pytest.raises(ValueError, match="Invalid scenario file"):
        scenarios.load_scenario("broken")


def test_scenario_details_without_loading_database(monkeypatch) -> None:
    def no_database(*args, **kwargs):
        raise AssertionError("preview must not access the database")

    monkeypatch.setattr(scenarios, "replace_scenario", no_database)
    details = scenarios.scenario_details("normal")
    with pytest.raises(KeyError):
        scenarios.scenario_details("unknown")
    tables = details["tables"]
    assert set(tables) == {"zones", "riders", "orders", "hourly_metrics"}
    assert [
        len(tables[name]["data"])
        for name in ("zones", "riders", "orders", "hourly_metrics")
    ] == [3, 9, 6, 12]
    orders = tables["orders"]
    first = dict(zip(orders["headers"], orders["data"][0], strict=True))
    assert first["order_id"] == "ORD-01-001"
    as_of = datetime.fromisoformat(first["as_of"])
    placed_at = datetime.fromisoformat(first["placed_at"])
    assert as_of.tzinfo is None and placed_at.tzinfo is None
    assert as_of - placed_at == timedelta(seconds=240)


def test_current_scenario_uses_saved_rows_and_original_time(monkeypatch) -> None:
    as_of = datetime(2026, 10, 1, 10, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    rows, _ = build_scenario("rain", as_of)
    order = next(row for row in rows if isinstance(row, Order))
    order.status = "picking"
    monkeypatch.setattr(scenarios, "read_scenario_rows", lambda engine: rows)

    def no_write(*args):
        raise AssertionError("Restoring a snapshot must never replace data")

    monkeypatch.setattr(scenarios, "replace_scenario", no_write)
    monkeypatch.setattr(scenarios, "build_scenario", no_write)
    context = scenarios.current_scenario()
    assert context is not None
    assert context["scenario_key"] == "rain"
    assert context["as_of"] == as_of.isoformat()
    assert context["counts"]["packed_waiting"] == 7
    assert "rain_started_at" not in context
    table = context["tables"]["orders"]
    assert table["data"][0][table["headers"].index("status")] == "picking"


def test_loading_reads_back_the_saved_rows(monkeypatch) -> None:
    events = []
    saved = []

    def replace(rows, engine):
        events.append("replace")
        saved.extend(rows)
        next(row for row in saved if isinstance(row, Order)).status = "delivered"

    def read(engine):
        events.append("read")
        return saved

    monkeypatch.setattr(scenarios, "replace_scenario", replace)
    monkeypatch.setattr(scenarios, "read_scenario_rows", read)
    context = scenarios.load_scenario("normal")
    table = context["tables"]["orders"]
    assert events == ["replace", "read"]
    assert table["data"][0][table["headers"].index("status")] == "delivered"


def test_current_scenario_handles_empty_and_rejects_mixed_snapshots(
    monkeypatch,
) -> None:
    monkeypatch.setattr(scenarios, "read_scenario_rows", lambda engine: [])
    assert scenarios.current_scenario() is None
    as_of = datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
    normal, _ = build_scenario("normal", as_of)
    rain, _ = build_scenario("rain", as_of)
    monkeypatch.setattr(scenarios, "read_scenario_rows", lambda engine: normal + rain)
    with pytest.raises(ValueError, match="single scenario"):
        scenarios.current_scenario()
