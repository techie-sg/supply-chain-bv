from dataclasses import fields
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from catalogue import definitions

from database.models import HourlyMetric, Order, Rider
from domain.memory import AlertOptions
from domain.preferences import SettingDefinition
from service import alerts
from service.alerts import Snapshot, evaluate, in_window, measure
from service.preferences import EffectiveSetting, default_setting
from service.scenarios import TIMEZONE, build_scenario

AS_OF = datetime(2026, 10, 8, 20, 14, tzinfo=TIMEZONE)
# A Thursday evening and a Saturday evening, in IST.
THURSDAY = datetime(2026, 10, 8, 20, 30, tzinfo=TIMEZONE)
SATURDAY = datetime(2026, 10, 10, 20, 30, tzinfo=TIMEZONE)


def snapshot(key: str) -> Snapshot:
    rows, _ = build_scenario(key, AS_OF)
    return Snapshot(
        as_of=AS_OF,
        orders=[row for row in rows if isinstance(row, Order)],
        riders=[row for row in rows if isinstance(row, Rider)],
        hourly=[row for row in rows if isinstance(row, HourlyMetric)],
    )


def catalogue() -> list[SettingDefinition]:
    """The seeded catalogue as the plain definitions the services use."""
    return [
        SettingDefinition(
            **{
                field.name: getattr(row, field.name)
                for field in fields(SettingDefinition)
            },
        )
        for row in definitions()
    ]


def defaults() -> list[EffectiveSetting]:
    return [default_setting(item) for item in catalogue()]


def setting(code: str, value, options: dict | None = None, enabled=True):
    definition = next(item for item in catalogue() if item.code == code)
    return EffectiveSetting(
        definition,
        enabled,
        value,
        AlertOptions.model_validate(options) if options else None,
        customized=True,
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "normal",
            {
                "rider_shortage_alert": 0.5,
                "orders_piling_up_alert": 4,
                "order_waiting_too_long_alert": 4.0,
                "frozen_order_waiting_alert": None,
            },
        ),
        (
            "backlog",
            {
                "rider_shortage_alert": 3.0,
                "orders_piling_up_alert": 10,
                "order_waiting_too_long_alert": 8.0,
                "frozen_order_waiting_alert": 6.0,
            },
        ),
        (
            "rain",
            {
                "rider_shortage_alert": 4.0,
                "orders_piling_up_alert": 10,
                "order_waiting_too_long_alert": 9.0,
                "frozen_order_waiting_alert": 5.5,
            },
        ),
    ],
)
def test_measures_match_each_scenario(key, expected) -> None:
    data = snapshot(key)
    for code, value in expected.items():
        current = measure(code, data)
        assert (current.value if current else None) == value, code


def test_measures_explain_what_drives_them() -> None:
    shortage = measure("rider_shortage_alert", snapshot("backlog"))
    assert shortage is not None
    assert shortage.summary == "6 packed orders waiting for 2 available riders"
    assert {item["order"] for item in shortage.items if "order" in item}
    assert any(item.get("status") == "available" for item in shortage.items)
    oldest = measure("order_waiting_too_long_alert", snapshot("rain"))
    assert oldest is not None and oldest.items[0]["waiting_min"] == 9.0
    piling = measure("orders_piling_up_alert", snapshot("backlog"))
    assert piling is not None
    assert "(4 picking, 6 packed and waiting)" in piling.summary


def test_sla_uses_the_last_completed_hour() -> None:
    sla = measure("sla_dip_alert", snapshot("backlog"))
    assert sla is not None and sla.value == 90
    assert "23:00 hour" in sla.summary
    assert len(sla.items) == 4
    empty = Snapshot(AS_OF, [], [], [])
    assert measure("sla_dip_alert", empty) is None
    assert measure("unknown_alert", empty) is None


def test_no_available_rider_with_orders_waiting_always_breaches() -> None:
    data = snapshot("backlog")
    for rider in data.riders:
        rider.status = "on_delivery"
    shortage = measure("rider_shortage_alert", data)
    assert shortage is not None and shortage.forced
    assert shortage.summary == "6 packed orders waiting and no rider available"
    [result] = [
        item for item in evaluate(data, [setting("rider_shortage_alert", 2)], THURSDAY)
    ]
    assert result.breached and result.severity == "critical"


def test_defaults_fire_on_backlog_and_rain_but_not_normal() -> None:
    def fired(key):
        return {
            result.code
            for result in evaluate(snapshot(key), defaults(), THURSDAY)
            if result.breached
        }

    assert fired("normal") == set()
    assert fired("backlog") == {"rider_shortage_alert", "orders_piling_up_alert"}
    assert fired("rain") == {
        "rider_shortage_alert",
        "orders_piling_up_alert",
        "order_waiting_too_long_alert",
    }


def test_disabled_alerts_and_unset_values_are_skipped() -> None:
    results = evaluate(
        snapshot("backlog"),
        [
            setting("rider_shortage_alert", 2, enabled=False),
            setting("orders_piling_up_alert", None),
            setting("cold_chain_isolation", True),
        ],
        THURSDAY,
    )
    assert results == []


def test_operators_thresholds_and_severity() -> None:
    results = {
        result.code: result
        for result in evaluate(
            snapshot("backlog"),
            [
                setting("orders_piling_up_alert", 10),
                setting("frozen_order_waiting_alert", 4),
                setting("sla_dip_alert", 95),
                setting("rider_shortage_alert", 1.5),
            ],
            THURSDAY,
        )
    }
    # At or above 10 with exactly 10 orders fires; 6 min is above 4; 90% is below 95.
    assert results["orders_piling_up_alert"].breached
    assert results["frozen_order_waiting_alert"].breached
    assert results["sla_dip_alert"].breached
    assert results["sla_dip_alert"].severity == "warning"
    assert results["rider_shortage_alert"].severity == "critical"
    assert results["rider_shortage_alert"].limit_text() == (
        "above 1.5 orders per available rider"
    )
    assert results["frozen_order_waiting_alert"].cooldown_min == 10


@pytest.mark.parametrize(
    ("options", "when", "inside"),
    [
        (None, THURSDAY, True),
        ({"days": ["sat", "sun"], "start": "19:00"}, THURSDAY, False),
        ({"days": ["sat", "sun"], "start": "19:00"}, SATURDAY, True),
        ({"start": "21:00"}, SATURDAY, False),
        ({"start": "08:00", "end": "20:00"}, SATURDAY, False),
        ({"start": "08:00", "end": "21:00"}, SATURDAY, True),
        # Crossing midnight: 01:00 Sunday belongs to Saturday's window.
        (
            {"days": ["sat"], "start": "19:00", "end": "02:00"},
            SATURDAY + timedelta(hours=4, minutes=30),
            True,
        ),
        (
            {"days": ["sun"], "start": "19:00", "end": "02:00"},
            SATURDAY + timedelta(hours=4, minutes=30),
            False,
        ),
        ({"start": "19:00", "end": "02:00"}, SATURDAY + timedelta(hours=8), False),
        ({"days": ["thu"]}, THURSDAY, True),
    ],
)
def test_alert_windows(options, when, inside) -> None:
    parsed = AlertOptions.model_validate(options) if options else None
    assert in_window(parsed, when) is inside


def test_window_text() -> None:
    assert alerts.window_text(None) == "at all times"
    assert (
        alerts.window_text(
            AlertOptions.model_validate({"days": ["sat", "sun"], "start": "19:00"}),
        )
        == "Sat, Sun 19:00–end of day"
    )


def test_out_of_window_alerts_are_not_evaluated() -> None:
    weekend = setting(
        "rider_shortage_alert",
        2,
        {"days": ["sat", "sun"], "start": "19:00", "cooldown_min": 30},
    )
    assert evaluate(snapshot("backlog"), [weekend], THURSDAY) == []
    [result] = evaluate(snapshot("backlog"), [weekend], SATURDAY)
    assert result.breached and result.cooldown_min == 30
    assert result.window == "Sat, Sun 19:00–end of day"


class FakeEvents:
    """Stands in for queries.alerts: a cooldown-aware trigger log."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.now = THURSDAY

    def record(self, values, cooldown_min, engine=None):
        for row in self.rows:
            if (
                row.code == values["code"]
                and row.manager_id == values["manager_id"]
                and row.triggered_at > self.now - timedelta(minutes=cooldown_min)
            ):
                return None
        row = SimpleNamespace(
            id=f"event-{len(self.rows) + 1}",
            triggered_at=self.now,
            **values,
        )
        self.rows.append(row)
        return row

    def since(self, store_id, manager_id, since, code=None, engine=None):
        return [
            row
            for row in self.rows
            if row.manager_id == manager_id
            and row.triggered_at >= since
            and (code is None or row.code == code)
        ]


@pytest.fixture
def events(monkeypatch) -> FakeEvents:
    fake = FakeEvents()
    monkeypatch.setattr(alerts, "record_trigger", fake.record)
    monkeypatch.setattr(alerts, "triggers_since", fake.since)
    monkeypatch.setattr(
        alerts,
        "live_snapshot",
        lambda engine=None: snapshot("backlog"),
    )
    monkeypatch.setattr(
        alerts,
        "manager_preferences",
        lambda manager_id: SimpleNamespace(
            effective=defaults,
            current=lambda code: next(
                item for item in defaults() if item.definition.code == code
            ),
        ),
    )
    return fake


def test_check_records_breaches_once_per_cooldown(events) -> None:
    first = alerts.check_alerts("karthik", now=THURSDAY)
    assert first.available
    assert {alert["code"] for alert in first.alerts} == {
        "rider_shortage_alert",
        "orders_piling_up_alert",
    }
    shortage = next(a for a in first.alerts if a["code"] == "rider_shortage_alert")
    assert shortage["value"] == "3 orders per available rider"
    assert shortage["limit"] == "above 2 orders per available rider"
    assert shortage["as_of"] == "20:14" and shortage["today"] == 1
    assert shortage["severity"] == "critical"
    # Within the 15-minute cooldown, nothing new is recorded.
    events.now = THURSDAY + timedelta(minutes=5)
    alerts.check_alerts("karthik", now=events.now)
    assert len(events.rows) == 2
    # After it, the breach pops up again and today's count grows.
    events.now = THURSDAY + timedelta(minutes=16)
    again = alerts.check_alerts("karthik", now=events.now)
    assert len(events.rows) == 4
    # The first pop-up is now older than the recent window; only the new one shows.
    latest = [a for a in again.alerts if a["code"] == "rider_shortage_alert"]
    assert [alert["today"] for alert in latest] == [2]


def test_check_without_a_scenario_reports_unavailable(events, monkeypatch) -> None:
    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: None)
    check = alerts.check_alerts("karthik", now=THURSDAY)
    assert not check.available and check.alerts == []


def test_only_recent_pop_ups_are_returned(events) -> None:
    alerts.check_alerts("karthik", now=THURSDAY)
    # Inside the cooldown nothing new is recorded, but the recent ones still show,
    # so a page opened now sees the alerts that are firing.
    events.now = THURSDAY + timedelta(minutes=alerts.RECENT_MINUTES - 1)
    assert len(alerts.check_alerts("karthik", now=events.now).alerts) == 2
    assert len(events.rows) == 2


def test_diagnosis_shows_the_measure_drivers_and_today(events) -> None:
    alerts.check_alerts("karthik", now=THURSDAY)
    found = alerts.diagnose("rider_shortage_alert", "karthik", now=THURSDAY)
    assert found is not None
    assert found["breached"] and found["value"] == "3 orders per available rider"
    assert found["limit"] == "above 2 orders per available rider"
    assert found["as_of"] == "20:14"
    assert [item["at"] for item in found["triggers"]] == ["20:30"]
    assert (
        found["allowed"]
        == "0.5 orders per available rider to 2 orders per available rider"
    )
    assert any("order" in item for item in found["items"])


def test_diagnosis_without_data_or_triggers_is_empty(events, monkeypatch) -> None:
    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: None)
    assert alerts.diagnose("rider_shortage_alert", "karthik", now=THURSDAY) is None


def test_alerts_block_lists_only_breaches_with_their_time(events) -> None:
    block = alerts.alerts_block("karthik", now=THURSDAY)
    assert block is not None
    assert block.startswith("<alerts>") and block.endswith("</alerts>")
    assert "as of 20:14" in block
    assert "- Rider shortage: 6 packed orders waiting for 2 available riders" in block
    assert "Order waiting too long" not in block


def test_alerts_block_is_empty_without_breaches(events, monkeypatch) -> None:
    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: snapshot("normal"))
    assert alerts.alerts_block("karthik", now=THURSDAY) is None
    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: None)
    assert alerts.alerts_block("karthik", now=THURSDAY) is None


def test_live_snapshot_reads_the_loaded_rows(monkeypatch) -> None:
    rows, _ = build_scenario("rain", AS_OF)
    monkeypatch.setattr(alerts, "read_scenario_rows", lambda engine=None: rows)
    data = alerts.live_snapshot()
    assert data is not None and data.as_of == AS_OF
    assert len(data.orders) == 12 and data.hourly
    monkeypatch.setattr(alerts, "read_scenario_rows", lambda engine=None: [])
    assert alerts.live_snapshot() is None


def test_store_check_records_for_every_manager(events, monkeypatch) -> None:
    from service import managers

    team = [
        SimpleNamespace(manager_id=manager_id) for manager_id in ("ananya", "karthik")
    ]
    monkeypatch.setattr(managers, "store_managers", lambda store_id, engine=None: team)
    first = alerts.check_store(now=THURSDAY)
    assert first.available
    assert first.recorded == {"ananya": 2, "karthik": 2} and first.total == 4
    # Inside the cooldown, a second run records nothing new.
    events.now = THURSDAY + timedelta(minutes=1)
    again = alerts.check_store(now=events.now)
    assert again.recorded == {"ananya": 0, "karthik": 0}


def test_store_check_without_a_scenario(events, monkeypatch) -> None:
    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: None)
    check = alerts.check_store(now=THURSDAY)
    assert not check.available and check.recorded == {}
