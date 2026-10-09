import re
from dataclasses import fields

import pytest
from catalogue import DEFINITIONS, definitions

from domain.memory import PreferenceCategory, PreferenceCode
from domain.preferences import SettingDefinition
from service import preferences
from service.preferences import PreferenceError, PreferenceService, validate


@pytest.fixture
def store(preference_store):
    return preference_store


def service() -> PreferenceService:
    return PreferenceService("DS-1", "karthik")


def setting(code: PreferenceCode):
    return next(item for item in service().effective() if item.definition.code == code)


def test_catalogue_seed_matches_the_enums_and_its_own_limits() -> None:
    assert [row["code"] for row in DEFINITIONS] == list(PreferenceCode)
    for definition in definitions():
        assert PreferenceCategory(definition.category)
        if not definition.locked:
            validate(
                SettingDefinition(
                    **{
                        f.name: getattr(definition, f.name)
                        for f in fields(SettingDefinition)
                    },
                ),
                definition.default_enabled,
                definition.default_value,
                None,
            )
    locked = [row["code"] for row in DEFINITIONS if row["locked"]]
    assert locked == [PreferenceCode.COLD_CHAIN_ISOLATION]


def test_without_stored_rows_every_item_uses_its_default(store) -> None:
    settings = service().effective()
    assert [item.definition.code for item in settings] == list(PreferenceCode)
    assert not any(item.customized for item in settings)
    shortage = setting(PreferenceCode.RIDER_SHORTAGE_ALERT)
    assert (shortage.enabled, shortage.value) == (True, 2)


def test_alert_with_a_window_is_saved_exactly_as_given(store) -> None:
    reply = service().set(
        "rider_shortage_alert",
        True,
        1.5,
        {"days": ["sat", "sun"], "start": "19:00"},
    )
    assert reply == (
        "Saved. Rider shortage: on, above 1.5 orders per available rider, "
        "Sat, Sun from 19:00 to end of day, at most every 15 min."
    )
    [row] = store.rows
    assert (row.enabled, row.value) == (True, 1.5)
    assert row.options == {"days": ["sat", "sun"], "start": "19:00"}
    assert setting(PreferenceCode.RIDER_SHORTAGE_ALERT).customized


def test_a_new_value_supersedes_the_old_and_clears_the_window(store) -> None:
    chat = service()
    chat.set("rider_shortage_alert", True, 1.5, {"days": ["sat"]})
    chat.set("rider_shortage_alert", True, 1)
    assert store.statuses("rider_shortage_alert") == ["superseded", "active"]
    current = setting(PreferenceCode.RIDER_SHORTAGE_ALERT)
    assert current.value == 1 and current.options is None


def test_saving_an_unchanged_value_stores_nothing(store) -> None:
    assert service().set("rider_shortage_alert", True, 2) is None
    service().set("sla_dip_alert", True, 85)
    assert service().set("sla_dip_alert", True, 85.0) is None
    assert store.statuses("sla_dip_alert") == ["active"]


@pytest.mark.parametrize(
    ("code", "enabled", "value", "options", "message"),
    [
        (
            "rider_shortage_alert",
            True,
            3,
            None,
            (
                "Rider shortage must be 0.5 orders per available rider to 2 orders "
                "per available rider."
            ),
        ),
        (
            "cold_chain_isolation",
            False,
            False,
            None,
            "Cold-chain isolation is store policy and can't be changed.",
        ),
        (
            "surge_only_batching",
            True,
            True,
            {"start": "19:00"},
            "Only alerts have days, times or a cooldown",
        ),
        (
            "sla_dip_alert",
            True,
            85,
            {"cooldown_min": 1},
            "a cooldown between 5 and 240 minutes",
        ),
        ("sla_dip_alert", True, 85, {"start": "7pm"}, "times as HH:MM"),
        (
            "incentive_cap",
            True,
            None,
            None,
            "Surge incentive cap per shift needs a value (₹0 to ₹500).",
        ),
        (
            "briefing",
            True,
            ["order_queue", "order_queue"],
            None,
            "Greeting briefing must list one or more of",
        ),
        ("briefing", True, ["weather"], None, "Greeting briefing must list"),
        ("speeding_alert", True, 1, None, "is not something that can be configured"),
    ],
)
def test_values_outside_the_catalogue_are_rejected(
    store,
    code,
    enabled,
    value,
    options,
    message,
) -> None:
    with pytest.raises(PreferenceError, match=re.escape(message)):
        service().set(code, enabled, value, options)
    assert store.rows == []


def test_save_reports_each_item_and_keeps_going_after_a_rejection(store) -> None:
    messages = service().save(
        [
            {"code": "rider_shortage_alert", "enabled": True, "value": 1.5},
            {"code": "sla_dip_alert", "enabled": True, "value": 30},
            {"code": "orders_piling_up_alert", "enabled": True, "value": 8},
            {"code": "surge_only_batching", "enabled": True, "value": True},
        ],
    )
    assert messages == [
        (
            "Saved. Rider shortage: on, above 1.5 orders per available rider, at "
            "all times, at most every 15 min."
        ),
        "Not saved: SLA dip must be 50% to 100%.",
        "Saved. Batch only when short of riders: on.",
    ]
    assert {row.code for row in store.rows} == {
        "rider_shortage_alert",
        "surge_only_batching",
    }


def test_turning_an_alert_off_keeps_its_threshold(store) -> None:
    service().set("orders_piling_up_alert", False, 8)
    [row] = store.rows
    assert (row.enabled, row.value) == (False, 8)
    assert "Orders piling up: off" in service().prompt_block()


def test_incentive_cap_and_briefing_values(store) -> None:
    assert service().set("incentive_cap", True, 150) == (
        "Saved. Surge incentive cap per shift: ₹150."
    )
    assert (
        service().set(
            "briefing",
            True,
            ["order_queue", "last_handover_note"],
        )
        == "Saved. Greeting briefing: order_queue, last_handover_note."
    )


def test_reset_returns_to_the_default(store) -> None:
    chat = service()
    chat.set("sla_dip_alert", True, 85)
    assert chat.reset("sla_dip_alert") == "Reset to the default. SLA dip: off."
    assert store.statuses("sla_dip_alert") == ["removed"]
    assert not setting(PreferenceCode.SLA_DIP_ALERT).customized
    assert chat.reset("sla_dip_alert") == "SLA dip is already at its default."
    with pytest.raises(PreferenceError, match="store policy"):
        chat.reset("cold_chain_isolation")


def test_prompt_block_lists_every_item_with_state_and_limits(store) -> None:
    service().set("sla_dip_alert", True, 85)
    block = service().prompt_block()
    assert block.startswith("<preferences>") and block.endswith("</preferences>")
    assert "confirms a change proposed in chat" in block
    for code in PreferenceCode:
        assert f"- {code} (" in block
    assert "- sla_dip_alert (customized): SLA dip: on, below 85%" in block
    assert "Allowed: 50% to 100%" in block
    assert (
        "- cold_chain_isolation (default): Cold-chain isolation: on (store policy)"
        in block
    )
    assert "Surge incentive cap per shift: not set" in block


def test_validate_rejects_a_boolean_where_a_number_is_needed() -> None:
    definition = next(item for item in definitions() if item.code == "sla_dip_alert")
    with pytest.raises(PreferenceError, match="needs a number"):
        validate(
            SettingDefinition(
                **{
                    f.name: getattr(definition, f.name)
                    for f in fields(SettingDefinition)
                },
            ),
            True,
            True,
            None,
        )


def test_manager_preferences_default_to_the_demo_manager() -> None:
    demo = preferences.manager_preferences()
    assert (demo.store_id, demo.manager_id) == ("DS-BLR-014", "karthik")
    other = preferences.manager_preferences("imran")
    assert (other.store_id, other.manager_id) == ("DS-BLR-014", "imran")


def test_managers_settings_are_independent(store) -> None:
    PreferenceService("DS-1", "ananya").set("sla_dip_alert", True, 85)
    mine = PreferenceService("DS-1", "ananya").current("sla_dip_alert")
    theirs = PreferenceService("DS-1", "karthik").current("sla_dip_alert")
    assert (mine.enabled, mine.value, mine.customized) == (True, 85, True)
    assert (theirs.enabled, theirs.value, theirs.customized) == (False, 80, False)
