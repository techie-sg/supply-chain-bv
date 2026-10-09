"""Generate preference suggestions and scope manager actions before atomic persistence."""

import json
import re
from collections.abc import Callable
from dataclasses import fields
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine

from constants import (
    DEMO_MANAGER_ID,
    DEMO_STORE_ID,
    DREAMING_MIN_CHATS,
    DREAMING_RECENT_CHATS,
)
from domain.memory import AlertOptions, PreferenceCode, SuggestionKind, SuggestionStatus
from domain.personalization import FIELDS, validate_value
from domain.personalization import describe as describe_personalization
from domain.preferences import EffectiveSetting
from domain.suggestions import SuggestionView
from queries.dreaming import (
    add_suggestions,
    apply_suggestion,
    get_suggestion,
    list_suggestions,
    recent_conversations,
    resolve_suggestion,
    review_answer_issues,
)
from resources import PROMPTS
from service.personalization import item as personalization_item
from service.preferences import PreferenceError, PreferenceService, describe, validate
from service.review_context import chat_label

logger = structlog.stdlib.get_logger(__name__)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class SettingCandidate(BaseModel):
    code: PreferenceCode
    enabled: bool
    value: Any = None
    options: dict[str, Any] | None = None
    reason: str
    chats: list[str]


def json_array(text: str) -> list[Any]:
    """The first JSON array in a model reply; empty if there is none."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match is None:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


class SettingsSuggestionService:
    def __init__(
        self,
        generate: Callable[[str, str], str],
        preferences: Callable[[str, str], PreferenceService],
        engine: Engine | None = None,
    ):
        self.generate = generate
        self.preferences = preferences
        self.engine = engine

    def _prompt(self, name: str) -> str:
        return (PROMPTS / name).read_text(encoding="utf-8")

    def settings_suggestions(self, store_id: str, manager_id: str) -> int:
        """Propose catalogue changes the recent chats show a repeated need for."""
        chats = [
            conversation
            for conversation in recent_conversations(
                store_id,
                manager_id,
                DREAMING_RECENT_CHATS,
                self.engine,
            )
            if conversation.summary
        ]
        if len(chats) < DREAMING_MIN_CHATS:
            return 0
        preferences = self.preferences(store_id, manager_id)
        known = {str(conversation.id) for conversation in chats}
        reply = self.generate(
            self._prompt("settings_suggestions.md"),
            preferences.prompt_block()
            + "\n\n"
            + "\n\n".join(
                f"[chat:{conversation.id}] {chat_label(conversation)}\n{conversation.summary}"
                for conversation in chats
            ),
        )
        current = {
            setting.definition.code: setting for setting in preferences.effective()
        }
        seen = [
            suggestion.payload
            for suggestion in list_suggestions(
                store_id,
                manager_id,
                [SuggestionKind.SETTING],
                [SuggestionStatus.PENDING, SuggestionStatus.DISMISSED],
                engine=self.engine,
            )
        ]
        rows = []
        for item in json_array(reply):
            row = self._setting_row(store_id, manager_id, item, known, current, seen)
            if row is not None:
                rows.append(row)
                seen.append(row["payload"])
        return add_suggestions(rows, self.engine)

    def _setting_row(
        self,
        store_id: str,
        manager_id: str,
        item: Any,
        known: set[str],
        current: dict[str, Any],
        seen: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        try:
            candidate = SettingCandidate.model_validate(item)
        except ValidationError:
            return None
        chats = sorted(set(candidate.chats) & known)
        setting = current.get(candidate.code)
        if len(chats) < DREAMING_MIN_CHATS or setting is None:
            return None
        try:
            enabled, value, options = validate(
                setting.definition,
                candidate.enabled,
                candidate.value,
                candidate.options,
            )
        except PreferenceError:
            return None
        current_options = (
            setting.options.model_dump(mode="json", exclude_none=True)
            if setting.options
            else None
        )
        if (enabled, value, options) == (
            setting.enabled,
            setting.value,
            current_options,
        ):
            return None
        payload = {
            "code": str(candidate.code),
            "enabled": enabled,
            "value": value,
            "options": options,
        }
        if payload in seen:
            return None
        return {
            "store_id": store_id,
            "manager_id": manager_id,
            "kind": SuggestionKind.SETTING,
            "payload": payload,
            "reason": candidate.reason.strip(),
            "evidence": [{"conversation_id": chat} for chat in chats],
        }


def _view(row) -> SuggestionView:
    return SuggestionView(
        **{field.name: getattr(row, field.name) for field in fields(SuggestionView)},
    )


def pending_suggestions(
    store_id: str = DEMO_STORE_ID,
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> list[SuggestionView]:
    """Settings suggestions and handover drafts awaiting the manager, newest first."""
    return [
        _view(row)
        for row in list_suggestions(
            store_id,
            manager_id,
            [
                SuggestionKind.SETTING,
                SuggestionKind.HANDOVER_DRAFT,
                SuggestionKind.PERSONALIZATION,
            ],
            [SuggestionStatus.PENDING],
            engine=engine,
        )
    ]


def open_answer_issues(
    store_id: str = DEMO_STORE_ID,
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> list[SuggestionView]:
    return [
        _view(row)
        for row in list_suggestions(
            store_id,
            manager_id,
            [SuggestionKind.ANSWER_ISSUE],
            [SuggestionStatus.PENDING],
            limit=100,
            engine=engine,
        )
    ]


def accept_suggestion(
    suggestion_id: UUID,
    store_id: str,
    manager_id: str,
    note: str | None = None,
    engine: Engine | None = None,
) -> str:
    """Validate for the acting manager, then apply and resolve in one transaction."""
    suggestion = get_suggestion(suggestion_id, store_id, manager_id, engine)
    if suggestion is None or suggestion.status != SuggestionStatus.PENDING:
        raise LookupError("That suggestion is no longer pending or is not yours.")
    payload = suggestion.payload
    preference = None
    handover = None
    personalization = None
    if suggestion.kind == SuggestionKind.PERSONALIZATION:
        code = payload["code"]
        if code not in FIELDS:
            raise PreferenceError("Unknown personalization preference.")
        try:
            value = validate_value(code, payload["value"])
        except ValueError as exc:
            raise PreferenceError(str(exc)) from exc
        evidence = suggestion.evidence[0] if suggestion.evidence else {}
        personalization = {
            code: personalization_item(
                value,
                "suggestion",
                conversation_id=evidence.get("conversation_id"),
                quote=evidence.get("quote", ""),
            ),
        }
        message = f"Saved. {describe_personalization(code, value)}."
    elif suggestion.kind == SuggestionKind.SETTING:
        current = PreferenceService(store_id, manager_id, engine).current(
            payload["code"],
        )
        enabled, value, options = validate(
            current.definition,
            payload["enabled"],
            payload.get("value"),
            payload.get("options"),
        )
        preference = {
            "code": payload["code"],
            "enabled": enabled,
            "value": value,
            "options": options,
        }
        saved = EffectiveSetting(
            current.definition,
            enabled,
            value,
            AlertOptions.model_validate(options) if options else None,
            customized=True,
        )
        current_options = (
            current.options.model_dump(mode="json", exclude_none=True)
            if current.options
            else None
        )
        if (enabled, value, options) == (
            current.enabled,
            current.value,
            current_options,
        ):
            preference = None
            message = "That setting was already in place."
        else:
            message = f"Saved. {describe(saved)}."
    elif suggestion.kind == SuggestionKind.HANDOVER_DRAFT:
        text = (note if note is not None else payload["note"]).strip()
        if not text:
            raise PreferenceError("The handover note is empty.")
        handover = (UUID(payload["shift_id"]), text)
        message = (
            "Saved as your shift's handover note. End your shift on the "
            "Handover page when you are done."
        )
    else:
        raise LookupError("Answer issues are not accepted, only reviewed.")
    apply_suggestion(
        suggestion_id,
        store_id,
        manager_id,
        payload,
        preference=preference,
        handover=handover,
        personalization=personalization,
        engine=engine,
    )
    logger.info("Suggestion accepted", kind=suggestion.kind)
    return message


def dismiss_suggestion(
    suggestion_id: UUID,
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> bool:
    return resolve_suggestion(
        suggestion_id,
        SuggestionStatus.DISMISSED,
        store_id,
        manager_id,
        engine,
    )


def mark_answer_issues_reviewed(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> int:
    return review_answer_issues(store_id, manager_id, engine)
