from types import SimpleNamespace

import gradio as gr
import pytest

from domain.memory import PreferenceCode
from ui import navigation as navigation_ui
from ui import settings
from ui import sidebar as sidebar_ui

ALERTS = len(settings.ALERT_CODES)


def form() -> settings.SettingsForm:
    """Build the tab outside the app so its fields can be inspected."""
    with gr.Blocks():
        return settings.build(gr.State("karthik"))


def defaults(store) -> tuple:
    """Form input values for the catalogue defaults, as the browser would send."""
    values = settings.form_values(settings.manager_preferences().effective())
    inputs = []
    for index in range(ALERTS):
        enabled, threshold, cooldown, days, start, end, _ = values[
            index * 7 : index * 7 + 7
        ]
        inputs += [
            enabled["value"],
            threshold["value"],
            cooldown,
            days,
            start,
            end,
        ]
    surge = values[ALERTS * 7 + 2]["value"]
    incentive_amount = values[ALERTS * 7 + 4]["value"]
    briefing = values[ALERTS * 7 + 6]
    return (*inputs, surge, incentive_amount, briefing)


def test_settings_tab_is_part_of_the_workspace(ui_app) -> None:
    tabs = [
        item.id
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Tab) and item.id is not None
    ]
    assert {"assistant", "settings", "demo"} <= set(tabs)
    assert any(
        callback.fn is settings.load_settings for callback in ui_app.fns.values()
    )


def test_form_fields_line_up_with_the_values() -> None:
    built = form()
    assert len(built.inputs()) == ALERTS * 6 + 3
    assert len(built.outputs()) == ALERTS * 7 + 9
    assert not built.cold_chain.interactive


def test_form_shows_defaults_with_limits(preference_store) -> None:
    values = settings.load_settings()
    assert len(values) == ALERTS * 7 + 9
    enabled, threshold, cooldown, days, start, end, info = values[:7]
    assert enabled == gr.update(value=True, label="Rider shortage")
    assert threshold == gr.update(value=2, minimum=0.5, maximum=2)
    assert (cooldown, days, start, end) == (15, [], "", "")
    assert "Allowed: 0.5 orders per available rider to 2" in info
    assert "Default." in info
    assert values[ALERTS * 7]["value"] is True
    assert values[-3] == ["rider_stats", "order_queue"]
    assert values[-1] == ""


def test_saving_unchanged_defaults_stores_nothing(preference_store) -> None:
    result = settings.save_settings("karthik", *defaults(preference_store))
    assert result[-1] == '<p class="settings-status">No changes to save.</p>'
    assert preference_store.rows == []


def test_saving_changes_stores_exactly_what_was_entered(preference_store) -> None:
    values = list(defaults(preference_store))
    values[1:6] = [1.5, 20, ["sat", "sun"], "19:00", ""]
    values[ALERTS * 6] = True
    values[ALERTS * 6 + 1] = 150
    result = settings.save_settings("karthik", *values)
    assert (
        "Saved. Rider shortage: on, above 1.5 orders per available rider, Sat, "
        in (result[-1])
    )
    assert "Saved. Batch only when short of riders: on." in result[-1]
    assert "Saved. Surge incentive cap per shift: ₹150." in result[-1]
    saved = {row.code: row for row in preference_store.rows}
    assert saved["rider_shortage_alert"].options == {
        "days": ["sat", "sun"],
        "start": "19:00",
        "cooldown_min": 20,
    }
    assert "Your setting." in result[6]


def test_invalid_entries_are_reported_and_not_saved(preference_store) -> None:
    values = list(defaults(preference_store))
    values[1] = 3
    values[4] = "7pm"
    result = settings.save_settings("karthik", *values)
    assert "Not saved: Rider shortage" in result[-1]
    assert preference_store.rows == []


def test_unticking_every_view_turns_the_briefing_off(preference_store) -> None:
    values = list(defaults(preference_store))
    values[-1] = []
    settings.save_settings("karthik", *values)
    [row] = preference_store.rows
    assert row.code == PreferenceCode.BRIEFING
    assert (row.enabled, row.value) == (False, ["rider_stats", "order_queue"])


def test_reset_returns_an_item_to_its_default(preference_store) -> None:
    values = list(defaults(preference_store))
    values[1] = 1
    settings.save_settings("karthik", *values)
    result = settings.reset_setting(PreferenceCode.RIDER_SHORTAGE_ALERT)
    assert "Reset to the default. Rider shortage" in result[-1]
    assert result[1] == gr.update(value=2, minimum=0.5, maximum=2)
    locked = settings.reset_setting(PreferenceCode.COLD_CHAIN_ISOLATION)
    assert "Not reset: Cold-chain isolation is store policy" in locked[-1]


def test_unavailable_storage_is_reported(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("database down")

    class Broken:
        def __init__(self, *args) -> None:
            pass

        def effective(self):
            unavailable()

        def reset(self, code):
            unavailable()

    monkeypatch.setattr(settings, "manager_preferences", Broken)
    values = settings.load_settings()
    assert len(values) == ALERTS * 7 + 9
    assert "Settings are unavailable" in values[-1]
    with pytest.raises(gr.Error, match="Settings are unavailable"):
        settings.save_settings("karthik")
    with pytest.raises(gr.Error, match="Settings are unavailable"):
        settings.reset_setting("sla_dip_alert")


@pytest.mark.parametrize(
    ("view", "selected"),
    [("settings", "settings"), ("x", "assistant")],
)
def test_settings_tab_can_be_opened_from_the_url(view, selected) -> None:
    request = SimpleNamespace(query_params={"view": view})
    assert navigation_ui._restore_tab(request)["selected"] == selected  # type: ignore[arg-type]


def test_categories_switch_panels_in_the_browser(ui_app) -> None:
    nav = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Radio) and item.elem_id == "settings-nav"
    )
    assert [value for _, value in nav.choices] == [
        "alerts",
        "batching",
        "incentive",
        "greeting",
        "suggestions",
        "memory",
    ]
    assert nav.value == "alerts"
    [callback] = [
        callback
        for callback in ui_app.fns.values()
        if callback.js == settings.SHOW_CATEGORY_JS
    ]
    assert callback.fn is None and not callback.queue
    *panels, save_row = callback.outputs
    assert len(panels) == 6
    assert [panel.visible for panel in panels] == [True] + [False] * 5
    assert save_row.elem_id == "settings-actions"
    assert "!['suggestions', 'memory'].includes(category)" in settings.SHOW_CATEGORY_JS
    for key, _, _ in settings.CATEGORIES:
        assert f"category === '{key}'" in settings.SHOW_CATEGORY_JS


def test_summary_lists_what_is_on_and_marks_custom_values(preference_store) -> None:
    service = settings.manager_preferences()
    service.set(
        "rider_shortage_alert",
        True,
        1.5,
        {"days": ["sat", "sun"], "start": "19:00", "end": "23:00"},
    )
    service.set("incentive_cap", True, 150)
    service.set("surge_only_batching", True, True)
    service.set("orders_piling_up_alert", False, 8)
    html = settings.load_summary()
    assert "Rider shortage &gt; 1.5 per rider" in html
    assert "Sat, Sun from 19:00 to 23:00" in html
    assert "Order waiting &gt; 8 min" in html
    assert "Cold-chain isolation (policy)" in html
    assert "Batch only when short" in html
    assert "Incentive cap ₹150" in html
    assert html.count('class="summary-yours"') == 3
    assert "Off: orders piling up, frozen order waiting, sla dip" in html
    assert "briefing" not in html.lower()


def test_summary_with_defaults_lists_off_items_once(preference_store) -> None:
    html = settings.load_summary()
    assert 'class="summary-yours"' not in html
    assert html.endswith(
        '<p class="summary-off">Off: frozen order waiting, sla dip, batch only '
        "when short, incentive cap</p>",
    )


def test_summary_reports_unavailable_storage(monkeypatch) -> None:
    class Broken:
        def __init__(self, *args) -> None:
            pass

        def effective(self):
            raise RuntimeError("database down")

    monkeypatch.setattr(settings, "manager_preferences", Broken)
    assert settings.load_summary() == '<p class="summary-off">Settings unavailable.</p>'


def test_summary_is_shown_in_the_sidebar_and_refreshed_after_changes(ui_app) -> None:
    summary = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.HTML) and item.elem_id == "settings-summary"
    )
    refreshers = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is settings.load_summary
    ]
    assert all(callback.outputs == [summary] for callback in refreshers)
    # Page load, switching manager, Save settings, one reset per configurable
    # item, accepting a suggestion, and confirming a change proposed in chat.
    assert len(refreshers) == 1 + 1 + 1 + len(settings.ALERT_CODES) + 3 + 1 + 1
    edit = next(
        item
        for item in ui_app.blocks.values()
        if isinstance(item, gr.Button) and item.elem_id == "edit-settings"
    )
    [callback] = [
        callback
        for callback in ui_app.fns.values()
        if (edit._id, "click") in callback.targets
    ]
    assert callback.fn is navigation_ui.open_settings
    assert navigation_ui.open_settings()["selected"] == "settings"
    assert callback.js == sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS


def test_entering_an_amount_turns_the_incentive_cap_on(preference_store) -> None:
    values = list(defaults(preference_store))
    values[ALERTS * 6 + 1] = 200
    result = settings.save_settings("karthik", *values)
    assert "Saved. Surge incentive cap per shift: ₹200." in result[-1]
    assert result[ALERTS * 7 + 4]["value"] == 200
    assert "Incentive cap ₹200" in settings.load_summary()
    values[ALERTS * 6 + 1] = None
    result = settings.save_settings("karthik", *values)
    assert "Surge incentive cap per shift: not set" in result[-1]
    assert (
        "Off: frozen order waiting, sla dip, batch only when short, incentive cap"
        in (settings.load_summary())
    )


def test_each_manager_saves_and_sees_only_their_own_settings(preference_store) -> None:
    values = list(defaults(preference_store))
    # The first alert's threshold, for the morning manager only.
    values[1] = 1.5
    settings.save_settings("ananya", *values)
    ananya = settings.load_settings("ananya")
    karthik = settings.load_settings("karthik")
    assert ananya[1]["value"] == 1.5
    assert karthik[1]["value"] == 2
    assert "yours" in settings.load_summary("ananya")
    assert "yours" not in settings.load_summary("karthik")
    assert {row.manager_id for row in preference_store.rows} == {"ananya"}
    settings.reset_setting(PreferenceCode.RIDER_SHORTAGE_ALERT, "karthik")
    assert preference_store.statuses(PreferenceCode.RIDER_SHORTAGE_ALERT) == ["active"]


def test_memory_view_shows_the_digest_read_only(monkeypatch) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    from service.scenarios import TIMEZONE

    built = datetime(2026, 10, 8, 23, 30, tzinfo=TIMEZONE)
    digest = SimpleNamespace(
        digest="- Radius shrink deferred on 7 Oct.",
        sources=[{"conversation_id": "a"}, {"conversation_id": "b"}],
        built_at=built,
    )
    monkeypatch.setattr(settings, "memory_digest", lambda manager_id: digest)
    text = settings.load_memory("karthik")
    assert text.startswith("- Radius shrink deferred on 7 Oct.")
    assert "From 2 chats in the last 7 days · built 8 Oct, 23:30." in text
    monkeypatch.setattr(settings, "memory_digest", lambda manager_id: None)
    assert settings.load_memory().startswith("Nothing remembered yet.")

    def unavailable(manager_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(settings, "memory_digest", unavailable)
    assert "unavailable" in settings.load_memory()
    *panels, save_row = settings.show_category("memory")
    assert save_row["visible"] is False and panels[-1]["visible"] is True
