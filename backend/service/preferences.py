"""Validate, store and describe each manager's settings for the preference catalogue."""

from dataclasses import fields
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import Engine

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID
from domain.memory import (
    AlertOperator,
    AlertOptions,
    PreferenceCategory,
    PreferenceCode,
    Unit,
    ValueType,
)
from domain.preferences import EffectiveSetting, SettingDefinition
from queries.preferences import (
    active_preferences,
    list_definitions,
    remove_preference,
    save_preference,
)

logger = structlog.stdlib.get_logger(__name__)

CATALOGUE_ORDER = {code.value: index for index, code in enumerate(PreferenceCode)}
UNIT_TEXT = {
    Unit.ORDERS_PER_RIDER: "orders per available rider",
    Unit.ORDERS: "orders",
    Unit.MINUTES: "minutes",
}
OPERATOR_TEXT = {
    AlertOperator.GT: "above",
    AlertOperator.GTE: "at or above",
    AlertOperator.LT: "below",
}


class PreferenceError(ValueError):
    """A value the catalogue does not allow; the message is shown to the manager."""


def _number(value: float) -> str:
    return f"{value:g}"


def _amount(definition: SettingDefinition, value: float) -> str:
    if definition.unit == Unit.INR:
        return f"₹{_number(value)}"
    if definition.unit == Unit.PERCENT:
        return f"{_number(value)}%"
    unit = UNIT_TEXT.get(Unit(definition.unit), "") if definition.unit else ""
    return f"{_number(value)} {unit}".strip()


def _window(options: AlertOptions | None) -> str:
    if options is None or not (options.days or options.start or options.end):
        return "at all times"
    parts = []
    if options.days:
        parts.append(", ".join(day.capitalize() for day in options.days))
    if options.start or options.end:
        parts.append(
            f"from {options.start or '00:00'} to {options.end or 'end of day'}",
        )
    return " ".join(parts)


def describe(setting: EffectiveSetting) -> str:
    """One plain-language line, used in replies and in the model's context."""
    definition = setting.definition
    if definition.value_type == ValueType.BOOLEAN:
        state = "on" if setting.value else "off"
        return f"{definition.name}: {state}" + (
            " (store policy)" if definition.locked else ""
        )
    if not setting.enabled or setting.value is None:
        return f"{definition.name}: {'off' if definition.category != PreferenceCategory.INCENTIVE else 'not set'}"
    if definition.category == PreferenceCategory.ALERT:
        cooldown = (
            setting.options.cooldown_min
            if setting.options and setting.options.cooldown_min
            else definition.default_cooldown_min
        )
        operator = OPERATOR_TEXT.get(
            AlertOperator(definition.operator or "gt"),
            "above",
        )
        return (
            f"{definition.name}: on, {operator} "
            f"{_amount(definition, setting.value)}, {_window(setting.options)}, "
            f"at most every {cooldown} min"
        )
    if definition.category == PreferenceCategory.INCENTIVE:
        return f"{definition.name}: {_amount(definition, setting.value)}"
    if definition.value_type == ValueType.VIEW_LIST:
        return f"{definition.name}: {', '.join(setting.value)}"
    return f"{definition.name}: {setting.value}"


def limits(definition: SettingDefinition) -> str:
    if definition.locked:
        return "fixed by store policy"
    if definition.value_type == ValueType.NUMBER:
        low, high = definition.min_value or 0, definition.max_value or 0
        if definition.unit in (Unit.INR, Unit.PERCENT):
            return f"{_amount(definition, low)} to {_amount(definition, high)}"
        # "1 to 50 orders", not "1 orders to 50 orders".
        return f"{_number(low)} to {_amount(definition, high)}"
    if definition.value_type == ValueType.BOOLEAN:
        return "on or off"
    return "one or more of " + ", ".join(definition.allowed_values or [])


def validate(
    definition: SettingDefinition,
    enabled: bool,
    value: Any,
    options: dict[str, Any] | None,
) -> tuple[bool, Any, dict[str, Any] | None]:
    """Check a value against its catalogue item; return what should be stored."""
    if definition.locked:
        raise PreferenceError(
            f"{definition.name} is store policy and can't be changed.",
        )
    stored_options = None
    if options:
        if definition.category != PreferenceCategory.ALERT:
            raise PreferenceError(
                f"Only alerts have days, times or a cooldown; {definition.name} doesn't.",
            )
        try:
            parsed = AlertOptions.model_validate(options)
        except ValidationError as exc:
            raise PreferenceError(
                f"{definition.name} needs days such as sat or sun, times as HH:MM, "
                "and a cooldown between 5 and 240 minutes.",
            ) from exc
        stored_options = parsed.model_dump(mode="json", exclude_none=True) or None

    match ValueType(definition.value_type):
        case ValueType.NUMBER:
            if value is None:
                if enabled:
                    raise PreferenceError(
                        f"{definition.name} needs a value ({limits(definition)}).",
                    )
            elif isinstance(value, bool) or not isinstance(value, int | float):
                raise PreferenceError(f"{definition.name} needs a number.")
            elif (
                not (definition.min_value or 0) <= value <= (definition.max_value or 0)
            ):
                raise PreferenceError(
                    f"{definition.name} must be {limits(definition)}.",
                )
            else:
                value = int(value) if float(value).is_integer() else float(value)
        case ValueType.BOOLEAN:
            if not isinstance(value, bool):
                raise PreferenceError(f"{definition.name} can only be on or off.")
            enabled = value
        case ValueType.CHOICE:
            if value not in (definition.allowed_values or []):
                raise PreferenceError(
                    f"{definition.name} must be {limits(definition)}.",
                )
        case ValueType.VIEW_LIST:
            allowed = definition.allowed_values or []
            if (
                not isinstance(value, list)
                or not value
                or any(view not in allowed for view in value)
                or len(value) != len(set(value))
            ):
                raise PreferenceError(
                    f"{definition.name} must list {limits(definition)}, each once.",
                )
            value = [str(view) for view in value]
    return enabled, value, stored_options


class PreferenceService:
    def __init__(
        self,
        store_id: str,
        manager_id: str,
        engine: Engine | None = None,
    ) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.engine = engine
        self._catalogue: dict[str, SettingDefinition] | None = None

    def _definitions(self) -> dict[str, SettingDefinition]:
        if self._catalogue is None:
            definitions = sorted(
                list_definitions(self.engine),
                key=lambda item: CATALOGUE_ORDER.get(item.code, len(CATALOGUE_ORDER)),
            )
            self._catalogue = {
                item.code: SettingDefinition(
                    **{
                        field.name: getattr(item, field.name)
                        for field in fields(SettingDefinition)
                    },
                )
                for item in definitions
            }
        return self._catalogue

    def effective(self) -> list[EffectiveSetting]:
        """Every catalogue item with the manager's value or its default."""
        rows = {
            row.code: row
            for row in active_preferences(self.store_id, self.manager_id, self.engine)
        }
        settings = []
        for code, definition in self._definitions().items():
            row = None if definition.locked else rows.get(code)
            if row is None:
                settings.append(default_setting(definition))
            else:
                settings.append(
                    EffectiveSetting(
                        definition,
                        row.enabled,
                        row.value,
                        AlertOptions.model_validate(row.options)
                        if row.options
                        else None,
                        customized=True,
                    ),
                )
        return settings

    def current(self, code: str) -> EffectiveSetting:
        """The setting that applies now for one catalogue item."""
        for setting in self.effective():
            if setting.definition.code == code:
                return setting
        raise PreferenceError(f"{code} is not something that can be configured.")

    def set(
        self,
        code: str,
        enabled: bool,
        value: Any,
        options: dict[str, Any] | None = None,
    ) -> str | None:
        """Validate and store a value exactly as given; None if nothing changed."""
        saved = self._set(self.current(code), enabled, value, options)
        return f"Saved. {describe(saved)}." if saved else None

    def _set(
        self,
        current: EffectiveSetting,
        enabled: bool,
        value: Any,
        options: dict[str, Any] | None,
    ) -> EffectiveSetting | None:
        definition = current.definition
        enabled, value, stored_options = validate(definition, enabled, value, options)
        current_options = (
            current.options.model_dump(mode="json", exclude_none=True)
            if current.options
            else None
        )
        if (enabled, value, stored_options) == (
            current.enabled,
            current.value,
            current_options,
        ):
            return None
        save_preference(
            self.store_id,
            self.manager_id,
            definition.code,
            enabled,
            value,
            stored_options,
            self.engine,
        )
        logger.info("Preference saved", code=definition.code, enabled=enabled)
        return EffectiveSetting(
            definition,
            enabled,
            value,
            AlertOptions.model_validate(stored_options) if stored_options else None,
            customized=True,
        )

    def save(self, entries: list[dict[str, Any]]) -> list[str]:
        """Save several items; each is checked on its own and reported."""
        if not entries:
            return []
        # Read once for this save, not once per field. This snapshot is local to
        # the call: later reads still fetch fresh settings from the database.
        current = {item.definition.code: item for item in self.effective()}
        messages = []
        for entry in entries:
            try:
                setting = current.get(entry["code"])
                if setting is None:
                    raise PreferenceError(
                        f"{entry['code']} is not something that can be configured.",
                    )
                saved = self._set(
                    setting,
                    entry["enabled"],
                    entry.get("value"),
                    entry.get("options"),
                )
            except PreferenceError as exc:
                logger.info("Preference rejected", code=entry["code"])
                messages.append(f"Not saved: {exc}")
            else:
                if saved:
                    current[entry["code"]] = saved
                    messages.append(f"Saved. {describe(saved)}.")
        return messages

    def reset(self, code: str) -> str:
        """Return an item to its default by marking the manager's value removed."""
        definition = self.current(code).definition
        if definition.locked:
            raise PreferenceError(
                f"{definition.name} is store policy and can't be changed.",
            )
        default = default_setting(definition)
        if not remove_preference(
            self.store_id,
            self.manager_id,
            definition.code,
            self.engine,
        ):
            return f"{definition.name} is already at its default."
        logger.info("Preference reset", code=definition.code)
        return f"Reset to the default. {describe(default)}."

    def prompt_block(self) -> str:
        """The manager's settings for the model, with the limits of each item."""
        lines = [
            "<preferences>",
            (
                "The manager's current settings. They change only in the Settings "
                "tab or when the manager confirms a change proposed in chat. "
                "'customized' means the manager set it; otherwise it is the default."
            ),
        ]
        for setting in self.effective():
            definition = setting.definition
            state = "customized" if setting.customized else "default"
            lines.append(
                f"- {definition.code} ({state}): {describe(setting)}. "
                f"Allowed: {limits(definition)}. {definition.description}",
            )
        lines.append("</preferences>")
        return "\n".join(lines)


def default_setting(definition: SettingDefinition) -> EffectiveSetting:
    return EffectiveSetting(
        definition,
        definition.default_enabled,
        definition.default_value,
        None,
        customized=False,
    )


def manager_preferences(manager_id: str = DEMO_MANAGER_ID) -> PreferenceService:
    """One manager's settings at the demo store, used by the UI and the assistant."""
    return PreferenceService(DEMO_STORE_ID, manager_id)
