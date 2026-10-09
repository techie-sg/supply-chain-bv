"""Settings tab: the manager sets alerts, batching rules, incentive cap and greeting.

It also shows review suggestions and editable response personalization.

Each manager has their own settings. Values are saved exactly as entered in the
form; the assistant only proposes changes, which the manager confirms.
"""

from dataclasses import dataclass
from html import escape
from typing import Any

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from domain.memory import BriefingView, PreferenceCode, Weekday
from domain.preferences import EffectiveSetting, SettingDefinition
from service.preferences import (
    PreferenceError,
    limits,
    manager_preferences,
)
from ui import personalization as personalization_ui
from ui import suggestions as suggestions_ui

logger = structlog.stdlib.get_logger(__name__)

ALERT_CODES = [
    PreferenceCode.RIDER_SHORTAGE_ALERT,
    PreferenceCode.ORDERS_PILING_UP_ALERT,
    PreferenceCode.ORDER_WAITING_TOO_LONG_ALERT,
    PreferenceCode.FROZEN_ORDER_WAITING_ALERT,
    PreferenceCode.SLA_DIP_ALERT,
]
DAY_CHOICES = [(day.value.capitalize(), day.value) for day in Weekday]
VIEW_CHOICES = [
    ("Rider stats", BriefingView.RIDER_STATS.value),
    ("Order queue", BriefingView.ORDER_QUEUE.value),
    ("Oldest order age", BriefingView.OLDEST_ORDER_AGE.value),
    ("Last handover note", BriefingView.LAST_HANDOVER_NOTE.value),
]
UNAVAILABLE = "Settings are unavailable right now. Check the database connection."


ALERT_FIELDS = ("enabled", "threshold", "cooldown", "days", "start", "end")
FORM_INPUTS = [(code, name) for code in ALERT_CODES for name in ALERT_FIELDS] + [
    (PreferenceCode.SURGE_ONLY_BATCHING, "value"),
    (PreferenceCode.INCENTIVE_CAP, "value"),
    (PreferenceCode.BRIEFING, "value"),
]
FORM_OUTPUTS = (
    [(code, name) for code in ALERT_CODES for name in (*ALERT_FIELDS, "info")]
    + [
        (code, name)
        for code in (
            PreferenceCode.COLD_CHAIN_ISOLATION,
            PreferenceCode.SURGE_ONLY_BATCHING,
            PreferenceCode.INCENTIVE_CAP,
            PreferenceCode.BRIEFING,
        )
        for name in ("value", "info")
    ]
    + [("", "status")]
)


@dataclass
class AlertFields:
    enabled: gr.Checkbox
    threshold: gr.Number
    cooldown: gr.Number
    days: gr.CheckboxGroup
    start: gr.Textbox
    end: gr.Textbox
    info: gr.Markdown


@dataclass
class SettingsForm:
    alerts: dict[str, AlertFields]
    cold_chain: gr.Checkbox
    cold_chain_info: gr.Markdown
    surge: gr.Checkbox
    surge_info: gr.Markdown
    incentive_amount: gr.Number
    incentive_info: gr.Markdown
    briefing: gr.CheckboxGroup
    briefing_info: gr.Markdown
    status: gr.Markdown
    nav: gr.Radio | None = None
    suggestions: suggestions_ui.SuggestionComponents | None = None
    personalization: personalization_ui.PersonalizationComponents | None = None
    category_outputs: tuple[Any, ...] = ()  # each panel, then the Save row

    def _component(self, code: str, name: str) -> Any:
        if code in self.alerts:
            return getattr(self.alerts[code], name)
        controls: dict[str, tuple[Any, Any]] = {
            PreferenceCode.COLD_CHAIN_ISOLATION: (
                self.cold_chain,
                self.cold_chain_info,
            ),
            PreferenceCode.SURGE_ONLY_BATCHING: (self.surge, self.surge_info),
            PreferenceCode.INCENTIVE_CAP: (self.incentive_amount, self.incentive_info),
            PreferenceCode.BRIEFING: (self.briefing, self.briefing_info),
        }
        return (
            self.status
            if name == "status"
            else controls[code][0 if name == "value" else 1]
        )

    def inputs(self) -> list[Any]:
        return [self._component(code, name) for code, name in FORM_INPUTS]

    def outputs(self) -> list[Any]:
        return [self._component(code, name) for code, name in FORM_OUTPUTS]


def _info(setting: EffectiveSetting) -> str:
    definition = setting.definition
    state = "Your setting" if setting.customized else "Default"
    return (
        f'<p class="setting-info">{escape(definition.description)} '
        f"Allowed: {escape(limits(definition))}. <strong>{state}.</strong></p>"
    )


def form_values(settings: list[EffectiveSetting], status: str = "") -> list[Any]:
    """Field updates for the current settings, in `SettingsForm.outputs` order."""
    by_code = {setting.definition.code: setting for setting in settings}
    updates: dict[tuple[str, str], Any] = {}
    for code, setting in by_code.items():
        definition = setting.definition
        updates[(code, "info")] = _info(setting)
        if code in ALERT_CODES:
            options = setting.options
            values = {
                "enabled": gr.update(value=setting.enabled, label=definition.name),
                "threshold": gr.update(
                    value=setting.value,
                    minimum=definition.min_value,
                    maximum=definition.max_value,
                ),
                "cooldown": (options.cooldown_min if options else None)
                or definition.default_cooldown_min,
                "days": [day.value for day in options.days]
                if options and options.days
                else [],
                "start": options.start or "" if options else "",
                "end": options.end or "" if options else "",
            }
            updates.update({(code, name): value for name, value in values.items()})
        elif code == PreferenceCode.INCENTIVE_CAP:
            updates[(code, "value")] = gr.update(
                value=setting.value if setting.enabled else None,
                minimum=definition.min_value,
                maximum=definition.max_value,
            )
        elif code == PreferenceCode.BRIEFING:
            updates[(code, "value")] = (
                list(setting.value) if setting.enabled and setting.value else []
            )
        else:
            updates[(code, "value")] = gr.update(
                value=bool(setting.value),
                label=definition.name,
            )
    updates[("", "status")] = status
    return [updates[field] for field in FORM_OUTPUTS]


def entries(
    values: tuple[Any, ...],
    definitions: dict[str, SettingDefinition],
) -> list[dict[str, Any]]:
    """Turn form values into one entry per catalogue item, exactly as entered."""
    fields = dict(zip(FORM_INPUTS, values, strict=True))
    items: list[dict[str, Any]] = []
    for code in ALERT_CODES:
        enabled, threshold, cooldown, days, start, end = (
            fields[(code, name)] for name in ALERT_FIELDS
        )
        options = {
            "days": list(days) or None,
            "start": (start or "").strip() or None,
            "end": (end or "").strip() or None,
            "cooldown_min": None
            if cooldown is None or cooldown == definitions[code].default_cooldown_min
            else int(cooldown),
        }
        items.append(
            {
                "code": code,
                "enabled": bool(enabled),
                "value": threshold,
                "options": {key: item for key, item in options.items() if item} or None,
            },
        )
    surge = fields[(PreferenceCode.SURGE_ONLY_BATCHING, "value")]
    incentive_amount = fields[(PreferenceCode.INCENTIVE_CAP, "value")]
    views = fields[(PreferenceCode.BRIEFING, "value")]
    briefing = definitions[PreferenceCode.BRIEFING]
    items += [
        {
            "code": PreferenceCode.SURGE_ONLY_BATCHING,
            "enabled": bool(surge),
            "value": bool(surge),
        },
        {
            "code": PreferenceCode.INCENTIVE_CAP,
            # The cap is on exactly when an amount is entered.
            "enabled": incentive_amount is not None,
            "value": incentive_amount,
        },
        {
            "code": PreferenceCode.BRIEFING,
            "enabled": bool(views),
            "value": list(views) if views else briefing.default_value,
        },
    ]
    return items


def _status(messages: list[str]) -> str:
    if not messages:
        return '<p class="settings-status">No changes to save.</p>'
    items = "".join(f"<li>{escape(message)}</li>" for message in messages)
    return f'<ul class="settings-status">{items}</ul>'


def load_settings(manager_id: str = DEMO_MANAGER_ID) -> list[Any]:
    try:
        return form_values(manager_preferences(manager_id).effective())
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load settings", exc_info=True)
        return [gr.skip()] * (len(FORM_OUTPUTS) - 1) + [
            f'<p class="settings-status">{UNAVAILABLE}</p>',
        ]


def save_settings(manager_id: str, *values: Any) -> list[Any]:
    """Save what changed for one manager; report each saved or rejected item."""
    service = manager_preferences(manager_id)
    try:
        definitions = {
            setting.definition.code: setting.definition
            for setting in service.effective()
        }
        messages = service.save(entries(values, definitions))
        return form_values(service.effective(), _status(messages))
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not save settings")
        raise gr.Error(UNAVAILABLE) from exc


def reset_setting(code: str, manager_id: str = DEMO_MANAGER_ID) -> list[Any]:
    service = manager_preferences(manager_id)
    try:
        message = service.reset(code)
        return form_values(service.effective(), _status([message]))
    except PreferenceError as exc:
        return form_values(service.effective(), _status([f"Not reset: {exc}"]))
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not reset setting %s", code)
        raise gr.Error(UNAVAILABLE) from exc


SUMMARY_SYMBOL = {"gt": ">", "gte": "≥", "lt": "<"}
SUMMARY_UNIT = {
    "orders_per_rider": " per rider",
    "orders": "",
    "minutes": " min",
    "percent": "%",
}
SUMMARY_ICONS = {
    "alert": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
    '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "batching": '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "cold": '<path d="M12 2v20M4.9 6l14.2 12M19.1 6 4.9 18"/>',
    "incentive": '<path d="M7 5h11M7 9h11M14 19 8 13h2a4 4 0 0 0 0-8"/>',
}
SUMMARY_SHORT_NAMES = {
    PreferenceCode.RIDER_SHORTAGE_ALERT: "Rider shortage",
    PreferenceCode.ORDERS_PILING_UP_ALERT: "Orders piling up",
    PreferenceCode.ORDER_WAITING_TOO_LONG_ALERT: "Order waiting",
    PreferenceCode.FROZEN_ORDER_WAITING_ALERT: "Frozen order waiting",
    PreferenceCode.SLA_DIP_ALERT: "SLA dip",
    PreferenceCode.SURGE_ONLY_BATCHING: "Batch only when short",
    PreferenceCode.INCENTIVE_CAP: "Incentive cap",
}


def _summary_icon(name: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{SUMMARY_ICONS[name]}</svg>"
    )


def _summary_item(icon: str, text: str, detail: str = "", yours: bool = False) -> str:
    detail_html = (
        f'<span class="summary-detail">{escape(detail)}</span>' if detail else ""
    )
    tag = '<span class="summary-yours">yours</span>' if yours else ""
    return (
        f'<li>{_summary_icon(icon)}<span class="summary-text">{escape(text)}'
        f"{detail_html}</span>{tag}</li>"
    )


def _number(value: float) -> str:
    return f"{value:g}"


def summary_html(settings: list[EffectiveSetting]) -> str:
    """Compact list of what is on, for the sidebar; off items share one line."""
    items, off = [], []
    for setting in settings:
        definition = setting.definition
        code = definition.code
        if code == PreferenceCode.BRIEFING:
            continue
        name = SUMMARY_SHORT_NAMES.get(PreferenceCode(code), definition.name)
        if code == PreferenceCode.COLD_CHAIN_ISOLATION:
            items.append(_summary_item("cold", "Cold-chain isolation (policy)"))
            continue
        is_on = setting.value if definition.value_type == "boolean" else setting.enabled
        if not is_on or setting.value is None:
            off.append(name.lower())
            continue
        if definition.category == "alert":
            symbol = SUMMARY_SYMBOL.get(definition.operator or "", "")
            unit = SUMMARY_UNIT.get(definition.unit or "", "")
            options = setting.options
            window = []
            if options and options.days:
                window.append(", ".join(day.capitalize() for day in options.days))
            if options and (options.start or options.end):
                window.append(
                    f"from {options.start or '00:00'}"
                    + (f" to {options.end}" if options.end else ""),
                )
            items.append(
                _summary_item(
                    "alert",
                    f"{name} {symbol} {_number(setting.value)}{unit}",
                    " ".join(window),
                    setting.customized,
                ),
            )
        elif definition.category == "incentive":
            items.append(
                _summary_item(
                    "incentive",
                    f"{name} ₹{_number(setting.value)}",
                    yours=setting.customized,
                ),
            )
        else:
            items.append(_summary_item("batching", name, yours=setting.customized))
    off_line = (
        f'<p class="summary-off">Off: {escape(", ".join(off))}</p>' if off else ""
    )
    return f'<ul class="settings-summary">{"".join(items)}</ul>{off_line}'


def load_summary(manager_id: str = DEMO_MANAGER_ID) -> str:
    try:
        return summary_html(manager_preferences(manager_id).effective())
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load the settings summary", exc_info=True)
        return '<p class="summary-off">Settings unavailable.</p>'


CATEGORIES = [
    ("alerts", "Alerts", "When the assistant should warn you about the store."),
    ("batching", "Batching", "Rules for putting more than one order on a trip."),
    ("incentive", "Incentive", "The most you will spend on surge incentives."),
    ("greeting", "Greeting", "What to show when you say hi."),
    (
        "suggestions",
        "Suggestions",
        (
            "Proposals from the daily review of your chats. Nothing changes until "
            "you accept one."
        ),
    ),
    (
        "personalization",
        "Personalization",
        ("Tell DispatchDesk how you like to work and receive advice."),
    ),
]
# Categories without the Save row.
READ_ONLY = ("suggestions", "personalization")

# Show only the chosen category's panel; runs in the browser, no server call.
# The last output is the Save row, which does not apply to read-only categories.
SHOW_CATEGORY_JS = (
    "(category) => ["
    + ", ".join(
        f"{{__type__: 'update', visible: category === '{key}'}}"
        for key, _, _ in CATEGORIES
    )
    + f", {{__type__: 'update', visible: !{list(READ_ONLY)}.includes(category)}}]"
)


def show_category(key: str) -> list[dict]:
    """Server-side twin of SHOW_CATEGORY_JS, for opening a category from elsewhere."""
    return [
        *(gr.update(visible=key == category) for category, _, _ in CATEGORIES),
        gr.update(visible=key not in READ_ONLY),
    ]


def _reset_button(code: str, resets: list[tuple[gr.Button, str]]) -> None:
    button = gr.Button(
        "Reset to default",
        size="sm",
        scale=0,
        min_width=132,
        elem_classes="reset-setting",
    )
    resets.append((button, code))


def _panel_heading(title: str, description: str) -> None:
    gr.HTML(
        f'<div class="settings-panel-heading"><h3>{escape(title)}</h3>'
        f"<p>{escape(description)}</p></div>",
        apply_default_css=False,
    )


def build(manager: gr.State, summary: gr.HTML | None = None) -> SettingsForm:
    """Lay out the tab and its events; call inside its gr.Tab.

    `manager` holds the selected manager's id; every save and reset applies to
    that manager. `summary`, when given, is the sidebar quick view refreshed
    after each change.
    """
    resets: list[tuple[gr.Button, str]] = []
    gr.HTML(
        '<div class="settings-heading"><h2>Settings</h2><p>The assistant applies '
        "these; only you change them, here.</p></div>",
        apply_default_css=False,
    )
    with gr.Row(elem_id="settings-layout"):
        nav = gr.Radio(
            choices=[(title, key) for key, title, _ in CATEGORIES],
            value="alerts",
            label="Settings categories",
            show_label=False,
            container=False,
            scale=0,
            min_width=180,
            elem_id="settings-nav",
        )
        with gr.Column(scale=1, min_width=0, elem_id="settings-panels"):
            panels = []
            headings = {key: (title, text) for key, title, text in CATEGORIES}

            with gr.Column(visible=True, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["alerts"])
                alerts: dict[str, AlertFields] = {}
                for code in ALERT_CODES:
                    with gr.Column(elem_classes="setting-card"):
                        with gr.Row():
                            enabled = gr.Checkbox(label=code)
                            _reset_button(code, resets)
                        info = gr.Markdown(elem_classes="setting-info-block")
                        with gr.Row():
                            threshold = gr.Number(label="Threshold", min_width=140)
                            cooldown = gr.Number(
                                label="Repeat at most every (minutes)",
                                precision=0,
                                minimum=5,
                                maximum=240,
                                min_width=140,
                            )
                        with gr.Row():
                            days = gr.CheckboxGroup(
                                choices=DAY_CHOICES,
                                label="Days (none ticked means every day)",
                                scale=2,
                            )
                            start = gr.Textbox(
                                label="From (HH:MM)",
                                placeholder="19:00",
                                min_width=110,
                            )
                            end = gr.Textbox(
                                label="Until (HH:MM)",
                                placeholder="end of day",
                                min_width=110,
                            )
                        alerts[code] = AlertFields(
                            enabled,
                            threshold,
                            cooldown,
                            days,
                            start,
                            end,
                            info,
                        )

            with gr.Column(visible=False, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["batching"])
                with gr.Column(elem_classes="setting-card"):
                    cold_chain = gr.Checkbox(
                        label="Cold-chain isolation",
                        interactive=False,
                    )
                    cold_chain_info = gr.Markdown(elem_classes="setting-info-block")
                with gr.Column(elem_classes="setting-card"):
                    with gr.Row():
                        surge = gr.Checkbox(
                            label=PreferenceCode.SURGE_ONLY_BATCHING.value,
                        )
                        _reset_button(PreferenceCode.SURGE_ONLY_BATCHING, resets)
                    surge_info = gr.Markdown(elem_classes="setting-info-block")

            with gr.Column(visible=False, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["incentive"])
                with gr.Column(elem_classes="setting-card"):
                    with gr.Row():
                        incentive_amount = gr.Number(
                            label="Cap per shift (₹); leave empty for no cap",
                            min_width=140,
                        )
                        _reset_button(PreferenceCode.INCENTIVE_CAP, resets)
                    incentive_info = gr.Markdown(elem_classes="setting-info-block")

            with gr.Column(visible=False, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["greeting"])
                with gr.Column(elem_classes="setting-card"):
                    with gr.Row():
                        briefing = gr.CheckboxGroup(
                            choices=VIEW_CHOICES,
                            label="Show when I say hi",
                        )
                        _reset_button(PreferenceCode.BRIEFING, resets)
                    briefing_info = gr.Markdown(elem_classes="setting-info-block")

            with gr.Column(visible=False, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["suggestions"])
                suggestion_parts = suggestions_ui.build_panel()

            with gr.Column(visible=False, elem_classes="settings-panel") as panel:
                panels.append(panel)
                _panel_heading(*headings["personalization"])
                personalization = personalization_ui.build(manager)

            with gr.Row(elem_id="settings-actions") as actions:
                save = gr.Button(
                    "Save settings",
                    variant="primary",
                    scale=0,
                    min_width=160,
                )
                status = gr.Markdown(elem_id="settings-status")

    nav.change(
        fn=None,
        js=SHOW_CATEGORY_JS,
        inputs=nav,
        outputs=[*panels, actions],
        queue=False,
        show_progress="hidden",
    )
    form = SettingsForm(
        alerts,
        cold_chain,
        cold_chain_info,
        surge,
        surge_info,
        incentive_amount,
        incentive_info,
        briefing,
        briefing_info,
        status,
        nav=nav,
        suggestions=suggestion_parts,
        personalization=personalization,
        category_outputs=(*panels, actions),
    )
    events = [
        save.click(
            save_settings,
            inputs=[manager, *form.inputs()],
            outputs=form.outputs(),
            concurrency_id="settings",
            concurrency_limit=1,
        ),
    ]
    for button, reset_code in resets:
        events.append(
            button.click(
                lambda manager_id, reset_code=reset_code: reset_setting(
                    reset_code,
                    manager_id,
                ),
                inputs=manager,
                outputs=form.outputs(),
                concurrency_id="settings",
                concurrency_limit=1,
            ),
        )
    if summary is not None:
        for event in events:
            event.success(load_summary, inputs=manager, outputs=summary)
    return form
