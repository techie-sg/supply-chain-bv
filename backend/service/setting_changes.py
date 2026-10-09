"""Change settings through chat: the model proposes, code validates, the manager confirms.

The model calls `propose_setting_change`. Code merges the request into the
current setting, so fields the manager did not mention keep their values, and
checks the result against the catalogue. A valid result becomes a pending
proposal; nothing is saved until the manager confirms it, and then it goes
through the same `PreferenceService` path as the Settings tab.
"""

import json
from enum import StrEnum
from typing import Any

import structlog

from constants import DEMO_MANAGER_ID
from domain.memory import AlertOptions, PreferenceCode, ValueType, Weekday
from domain.preferences import EffectiveSetting
from domain.setting_changes import SettingChange
from domain.tools import Tool
from service.preferences import (
    PreferenceError,
    PreferenceService,
    default_setting,
    describe,
    manager_preferences,
    validate,
)

logger = structlog.stdlib.get_logger(__name__)

TOOL_NAME = "propose_setting_change"
TOOL_DESCRIPTION = (
    "Propose a change to one of the manager's settings listed in <preferences>, "
    "when the manager's latest message asks to create, change, turn on, turn off "
    "or reset it. Nothing is saved: the manager confirms or cancels the proposal "
    "in the app. Leave out every field the manager did not mention; it keeps its "
    "current value. Call once per setting."
)
TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "enum": [code.value for code in PreferenceCode],
            "description": "The setting's code from <preferences>.",
        },
        "action": {
            "type": "string",
            "enum": ["set", "turn_off", "reset"],
            "description": (
                "set: turn on and apply the given fields. turn_off: switch off "
                "and keep the stored threshold. reset: return to the default."
            ),
        },
        "value": {
            "description": (
                "New value: a number for thresholds and the incentive cap (in the "
                "setting's unit), true or false for on/off rules, or a list of "
                "views for the briefing."
            ),
            "anyOf": [
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"type": "string"}},
            ],
        },
        "days": {
            "type": "array",
            "items": {"type": "string", "enum": [day.value for day in Weekday]},
            "description": "Alerts only: days the alert applies. Weekends: sat, sun.",
        },
        "start": {
            "type": "string",
            "description": "Alerts only: start time, 24-hour HH:MM, IST. 7pm is 19:00.",
        },
        "end": {
            "type": "string",
            "description": "Alerts only: end time, 24-hour HH:MM, IST.",
        },
        "clear_days": {
            "type": "boolean",
            "description": "Alerts only: true makes the alert apply on every day.",
        },
        "clear_times": {
            "type": "boolean",
            "description": "Alerts only: true makes the alert apply at any time of day.",
        },
        "cooldown_min": {
            "type": "integer",
            "description": "Alerts only: minimum minutes between repeats of the alert.",
        },
    },
    "required": ["code", "action"],
}


class Action(StrEnum):
    SET = "set"
    TURN_OFF = "turn_off"
    RESET = "reset"


def _snapshot(setting: EffectiveSetting) -> list[Any]:
    options = (
        setting.options.model_dump(mode="json", exclude_none=True)
        if setting.options
        else None
    )
    return [setting.enabled, setting.value, options or None]


def _state_text(setting: EffectiveSetting) -> str:
    """The setting's description without its leading name."""
    text = describe(setting)
    prefix = f"{setting.definition.name}: "
    return text.removeprefix(prefix)


def _merge(
    current: EffectiveSetting,
    action: Action,
    args: dict[str, Any],
) -> tuple[bool, Any, dict[str, Any] | None]:
    """Apply only the fields present in the request to the current setting."""
    definition = current.definition
    value_type = ValueType(definition.value_type)
    options = (
        current.options.model_dump(mode="json", exclude_none=True)
        if current.options
        else {}
    )
    if args.get("clear_days"):
        options.pop("days", None)
    if args.get("clear_times"):
        options.pop("start", None)
        options.pop("end", None)
    if args.get("days") is not None:
        days = args["days"]
        options["days"] = (
            [str(day).strip().lower()[:3] for day in days]
            if isinstance(days, list)
            else days
        )
    for field in ("start", "end", "cooldown_min"):
        if args.get(field) is not None:
            options[field] = args[field]

    value = current.value if args.get("value") is None else args["value"]
    if value_type == ValueType.NUMBER and isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass

    if action == Action.TURN_OFF:
        if value_type == ValueType.BOOLEAN:
            return False, False, options or None
        if value_type == ValueType.VIEW_LIST:
            raise PreferenceError(
                f"{definition.name} can't be turned off; choose which views to show.",
            )
        return False, value, options or None
    if value_type == ValueType.BOOLEAN and args.get("value") is None:
        value = True
    return True, value, options or None


class SettingChanges:
    """The setting-change tool for one chat turn; collects valid proposals."""

    def __init__(self, preferences: PreferenceService) -> None:
        self.preferences = preferences
        self.proposals: list[SettingChange] = []

    def tool(self) -> Tool:
        return Tool(TOOL_NAME, TOOL_DESCRIPTION, TOOL_PARAMETERS, self.run)

    def run(self, args: dict[str, Any]) -> str:
        """Tool entry point: the result tells the model what to say."""
        try:
            change = self.propose(args)
        except PreferenceError as exc:
            logger.info("Setting change rejected", code=args.get("code"))
            return json.dumps(
                {"status": "rejected", "reason": str(exc), "saved": False},
            )
        # A later proposal for the same setting replaces the earlier one.
        self.proposals = [
            item for item in self.proposals if item.code != change.code
        ] + [change]
        logger.info("Setting change proposed", code=change.code, action=change.action)
        return json.dumps(
            {
                "status": "proposed",
                "setting": change.name,
                "from": change.before,
                "to": change.after,
                "saved": False,
                "next": (
                    "Not saved yet. The manager saves it with Confirm, "
                    "or discards it with Cancel, below the chat."
                ),
            },
            ensure_ascii=False,
        )

    def propose(self, args: dict[str, Any]) -> SettingChange:
        """Merge and validate a request; raises PreferenceError when not allowed."""
        try:
            action = Action(args.get("action") or Action.SET)
        except ValueError as exc:
            raise PreferenceError("The action must be set, turn_off or reset.") from exc
        current = self.preferences.current(str(args.get("code")))
        definition = current.definition
        if definition.locked:
            raise PreferenceError(
                f"{definition.name} is store policy and can't be changed.",
            )
        before = _state_text(current)

        if action == Action.RESET:
            if not current.customized:
                raise PreferenceError(f"{definition.name} is already at its default.")
            default = default_setting(definition)
            return SettingChange(
                code=definition.code,
                name=definition.name,
                action=action,
                enabled=default.enabled,
                value=default.value,
                options=None,
                before=before,
                after=f"{_state_text(default)} (default)",
                proposed_over=_snapshot(current),
                manager_id=self.preferences.manager_id,
            )

        enabled, value, options = _merge(current, action, args)
        enabled, value, options = validate(definition, enabled, value, options)
        if [enabled, value, options] == _snapshot(current):
            raise PreferenceError(
                f"{definition.name} is already set that way: {before}.",
            )
        proposed = EffectiveSetting(
            definition,
            enabled,
            value,
            AlertOptions.model_validate(options) if options else None,
            customized=True,
        )
        return SettingChange(
            code=definition.code,
            name=definition.name,
            action=action,
            enabled=enabled,
            value=value,
            options=options,
            before=before,
            after=_state_text(proposed),
            proposed_over=_snapshot(current),
            manager_id=self.preferences.manager_id,
        )


def _is_tool_output(text: str) -> bool:
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and "status" in data


def tidy_reply(reply: str, proposals: list[SettingChange]) -> str:
    """Replace an empty reply, or one that only echoes the tool's output.

    The model sometimes returns the tool result as its answer; the manager
    should see plain words about the proposal instead.
    """
    if not proposals or (reply.strip() and not _is_tool_output(reply.strip())):
        return reply
    lines = [
        f"- **{change.name}:** {change.before} → {change.after}" for change in proposals
    ]
    heading = "Proposed change" if len(proposals) == 1 else "Proposed changes"
    return (
        f"{heading} (requires your confirmation):\n\n"
        + "\n".join(lines)
        + "\n\nNothing is saved yet. Press **Confirm** below to save, or **Cancel**."
    )


def confirm(
    changes: list[SettingChange],
    preferences: PreferenceService,
) -> list[str]:
    """Save confirmed changes; each is reported on its own."""
    messages = []
    for change in changes:
        try:
            current = preferences.current(change.code)
            if _snapshot(current) != change.proposed_over:
                raise PreferenceError(
                    f"{change.name} changed after this was proposed, so it was not "
                    "saved. Ask again to see a new proposal.",
                )
            if change.action == Action.RESET:
                message = preferences.reset(change.code)
            else:
                message = (
                    preferences.set(
                        change.code,
                        change.enabled,
                        change.value,
                        change.options,
                    )
                    or f"{change.name} is already set that way."
                )
        except PreferenceError as exc:
            logger.info("Confirmed setting change not saved", code=change.code)
            messages.append(f"Not saved: {exc}")
        else:
            logger.info("Setting changed through chat", code=change.code)
            messages.append(message)
    return messages


def confirm_proposals(
    states: list[dict[str, Any]],
    manager_id: str = DEMO_MANAGER_ID,
) -> list[str]:
    """UI entry point: save one manager's confirmed proposals.

    A proposal made for another manager is never saved; the UI discards
    proposals on a manager switch, so this only guards against a stale card.
    """
    changes = [SettingChange.from_state(state) for state in states]
    own = [change for change in changes if change.manager_id == manager_id]
    skipped = [
        f"Not saved: {change.name} was proposed for another manager."
        for change in changes
        if change.manager_id != manager_id
    ]
    return confirm(own, manager_preferences(manager_id)) + skipped
