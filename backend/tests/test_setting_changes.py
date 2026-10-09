import json

import pytest

from domain.memory import PreferenceCode
from service import setting_changes
from service.preferences import PreferenceError, PreferenceService
from service.setting_changes import (
    TOOL_NAME,
    SettingChange,
    SettingChanges,
    confirm,
    confirm_proposals,
)


@pytest.fixture
def store(preference_store):
    return preference_store


def prefs() -> PreferenceService:
    return PreferenceService("DS-1", "karthik")


def propose(**args) -> SettingChange:
    return SettingChanges(prefs()).propose(args)


def current(code: PreferenceCode):
    return prefs().current(code)


def test_tool_is_offered_with_every_catalogue_code(store) -> None:
    tool = SettingChanges(prefs()).tool()
    assert tool.name == TOOL_NAME
    schema = tool.schema()["function"]
    assert schema["name"] == TOOL_NAME
    assert schema["parameters"]["properties"]["code"]["enum"] == list(PreferenceCode)
    assert schema["parameters"]["required"] == ["code", "action"]


def test_sample_query_five_becomes_a_weekend_evening_alert(store) -> None:
    change = propose(
        code="rider_shortage_alert",
        action="set",
        value=2,
        days=["sat", "sun"],
        start="19:00",
    )
    assert (change.enabled, change.value) == (True, 2)
    assert change.options == {"days": ["sat", "sun"], "start": "19:00"}
    assert (
        change.before
        == "on, above 2 orders per available rider, at all times, at most every 15 min"
    )
    assert change.after == (
        "on, above 2 orders per available rider, Sat, Sun from 19:00 to end of day, "
        "at most every 15 min"
    )
    # Proposing saves nothing.
    assert store.rows == []


def test_update_keeps_the_fields_the_manager_did_not_mention(store) -> None:
    prefs().set(
        "rider_shortage_alert",
        True,
        2,
        {"days": ["sat", "sun"], "start": "19:00"},
    )
    change = propose(code="rider_shortage_alert", action="set", value=1.5)
    assert change.value == 1.5
    assert change.options == {"days": ["sat", "sun"], "start": "19:00"}


def test_clearing_days_or_times_widens_the_window(store) -> None:
    prefs().set(
        "rider_shortage_alert",
        True,
        2,
        {"days": ["sat", "sun"], "start": "19:00", "cooldown_min": 30},
    )
    every_day = propose(code="rider_shortage_alert", action="set", clear_days=True)
    assert every_day.options == {"start": "19:00", "cooldown_min": 30}
    any_time = propose(
        code="rider_shortage_alert",
        action="set",
        clear_days=True,
        clear_times=True,
    )
    assert any_time.options == {"cooldown_min": 30}


def test_day_names_and_numeric_strings_are_normalized(store) -> None:
    change = propose(
        code="orders_piling_up_alert",
        action="set",
        value="12",
        days=["Saturday", "SUN"],
    )
    assert change.value == 12
    assert change.options == {"days": ["sat", "sun"]}


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (
            {"code": "rider_shortage_alert", "value": 3},
            "0.5 to 2 orders per available rider",
        ),
        (
            {"code": "order_waiting_too_long_alert", "value": 12},
            "1 to 8 minutes",
        ),
        ({"code": "sla_dip_alert", "value": 40}, "50% to 100%"),
        ({"code": "incentive_cap", "value": 900}, "₹0 to ₹500"),
        (
            {"code": "rider_shortage_alert", "cooldown_min": 2},
            "cooldown between 5 and 240",
        ),
        ({"code": "rider_shortage_alert", "start": "7pm"}, "times as HH:MM"),
        ({"code": "incentive_cap"}, "needs a value"),
        ({"code": "incentive_cap", "days": ["sat"]}, "Only alerts have days"),
        ({"code": "cold_chain_isolation", "value": False}, "store policy"),
        ({"code": "rain_alert", "value": 1}, "not something that can be configured"),
        ({"code": "rider_shortage_alert", "value": 2}, "already set that way"),
    ],
)
def test_values_outside_the_catalogue_are_rejected(store, args, reason) -> None:
    with pytest.raises(PreferenceError, match=reason):
        propose(action="set", **args)


def test_unknown_action_is_rejected(store) -> None:
    with pytest.raises(PreferenceError, match="set, turn_off or reset"):
        propose(code="sla_dip_alert", action="delete")


def test_turn_off_keeps_the_threshold_and_switches_rules_off(store) -> None:
    prefs().set("sla_dip_alert", True, 85)
    off = propose(code="sla_dip_alert", action="turn_off")
    assert (off.enabled, off.value) == (False, 85)
    assert off.after == "off"
    prefs().set("surge_only_batching", True, True)
    rule = propose(code="surge_only_batching", action="turn_off")
    assert (rule.enabled, rule.value) == (False, False)
    with pytest.raises(PreferenceError, match="can't be turned off"):
        propose(code="briefing", action="turn_off")
    with pytest.raises(PreferenceError, match="already set that way"):
        propose(code="frozen_order_waiting_alert", action="turn_off")


def test_set_without_a_value_turns_an_item_on(store) -> None:
    frozen = propose(code="frozen_order_waiting_alert", action="set")
    assert (frozen.enabled, frozen.value) == (True, 5)
    rule = propose(code="surge_only_batching", action="set")
    assert (rule.enabled, rule.value) == (True, True)


def test_reset_needs_a_customized_setting(store) -> None:
    with pytest.raises(PreferenceError, match="already at its default"):
        propose(code="sla_dip_alert", action="reset")
    prefs().set("sla_dip_alert", True, 85)
    change = propose(code="sla_dip_alert", action="reset")
    assert change.action == "reset"
    assert change.after == "off (default)"


def test_tool_result_tells_the_model_nothing_was_saved(store) -> None:
    changes = SettingChanges(prefs())
    proposed = json.loads(
        changes.run({"code": "sla_dip_alert", "action": "set", "value": 85}),
    )
    assert proposed["status"] == "proposed" and proposed["saved"] is False
    assert (
        proposed["from"] == "off"
        and proposed["to"] == "on, below 85%, at all times, at most every 60 min"
    )
    assert "Confirm" in proposed["next"]
    rejected = json.loads(
        changes.run({"code": "sla_dip_alert", "action": "set", "value": 10}),
    )
    assert rejected == {
        "status": "rejected",
        "reason": "SLA dip must be 50% to 100%.",
        "saved": False,
    }
    assert [item.code for item in changes.proposals] == ["sla_dip_alert"]
    assert store.rows == []


def test_a_later_proposal_for_the_same_setting_replaces_the_earlier(store) -> None:
    changes = SettingChanges(prefs())
    changes.run({"code": "sla_dip_alert", "action": "set", "value": 85})
    changes.run({"code": "orders_piling_up_alert", "action": "set", "value": 10})
    changes.run({"code": "sla_dip_alert", "action": "set", "value": 90})
    assert [(item.code, item.value) for item in changes.proposals] == [
        ("orders_piling_up_alert", 10),
        ("sla_dip_alert", 90),
    ]


def test_confirm_saves_through_the_settings_path(store) -> None:
    change = propose(
        code="rider_shortage_alert",
        action="set",
        value=1.5,
        days=["sat", "sun"],
        start="19:00",
    )
    [message] = confirm([change], prefs())
    assert message.startswith("Saved. Rider shortage: on, above 1.5")
    saved = current(PreferenceCode.RIDER_SHORTAGE_ALERT)
    assert saved.customized and saved.value == 1.5
    assert saved.options.days == ["sat", "sun"]


def test_confirmed_reset_returns_to_the_default(store) -> None:
    prefs().set("sla_dip_alert", True, 85)
    change = propose(code="sla_dip_alert", action="reset")
    [message] = confirm([change], prefs())
    assert message.startswith("Reset to the default.")
    assert store.statuses("sla_dip_alert") == ["removed"]


def test_a_setting_changed_since_the_proposal_is_not_saved(store) -> None:
    change = propose(code="sla_dip_alert", action="set", value=85)
    other = propose(code="orders_piling_up_alert", action="set", value=10)
    prefs().set("sla_dip_alert", True, 90)
    stale, saved = confirm([change, other], prefs())
    assert stale.startswith("Not saved: SLA dip changed after this was proposed")
    assert saved.startswith("Saved. Orders piling up")
    assert current(PreferenceCode.SLA_DIP_ALERT).value == 90


def test_proposals_survive_the_browser_state_round_trip(store, monkeypatch) -> None:
    change = propose(code="sla_dip_alert", action="set", value=85)
    assert SettingChange.from_state(change.to_state()) == change
    monkeypatch.setattr(
        setting_changes,
        "manager_preferences",
        lambda manager_id: prefs(),
    )
    assert confirm_proposals([change.to_state()])[0].startswith("Saved. SLA dip")


def test_a_proposal_is_saved_only_for_the_manager_it_was_made_for(
    store,
    monkeypatch,
) -> None:
    change = SettingChanges(PreferenceService("DS-1", "ananya")).propose(
        {"code": "sla_dip_alert", "action": "set", "value": 85},
    )
    assert change.manager_id == "ananya"
    monkeypatch.setattr(
        setting_changes,
        "manager_preferences",
        lambda manager_id: PreferenceService("DS-1", manager_id),
    )
    [skipped] = confirm_proposals([change.to_state()], "karthik")
    assert skipped == "Not saved: SLA dip was proposed for another manager."
    assert store.rows == []
    [saved] = confirm_proposals([change.to_state()], "ananya")
    assert saved.startswith("Saved. SLA dip")
    assert {row.manager_id for row in store.rows} == {"ananya"}


def test_an_echoed_tool_result_or_empty_reply_is_replaced(store) -> None:
    from service.setting_changes import tidy_reply

    change = propose(code="sla_dip_alert", action="set", value=85)
    echoed = SettingChanges(prefs()).run(
        {"code": "sla_dip_alert", "action": "set", "value": 85},
    )
    for reply in (echoed, "", "  "):
        tidied = tidy_reply(reply, [change])
        assert tidied.startswith("Proposed change (requires your confirmation)")
        assert "**SLA dip:** off → on, below 85%" in tidied
        assert "Nothing is saved yet" in tidied
    assert (
        tidy_reply("Proposed: press Confirm.", [change]) == "Proposed: press Confirm."
    )
    assert tidy_reply("", []) == ""
    assert tidy_reply('{"note": "plain JSON answer"}', [change]).startswith("{")
    two = tidy_reply("", [change, change])
    assert two.startswith("Proposed changes")
