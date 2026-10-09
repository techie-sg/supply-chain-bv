"""Dreaming: a daily review of chats that proposes, never applies.

A run brings each reviewed chat's summary up to date, then produces:

- answer issues from raw messages after `dreamed_to` (for the admin report),
- a handover draft for the day from the day's chat summaries,
- settings suggestions from recent chat summaries,
- each manager's memory digest from the last week's chat summaries.

Suggestions are saved as pending rows; the digest is used without asking.
Each output fails on its own.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import requests
import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from constants import (
    DEMO_MANAGER_ID,
    DEMO_STORE_ID,
    DIGEST_DAYS,
    DIGEST_MAX_CHATS,
    DIGEST_MAX_ITEMS,
    DREAMING_MIN_CHATS,
    DREAMING_RECENT_CHATS,
)
from database.models import Conversation, MemoryDigest, Suggestion
from domain.memory import (
    AnswerIssue,
    PreferenceCode,
    SuggestionKind,
    SuggestionStatus,
)
from queries.dreaming import (
    add_suggestions,
    advance_dreamed_to,
    all_managers,
    conversations_to_review,
    dismiss_pending_drafts,
    get_digest,
    get_suggestion,
    latest_handover_notes,
    list_suggestions,
    recent_conversations,
    resolve_suggestion,
    save_digest,
    save_handover_note,
)
from service.factory import create_llm_service
from service.preferences import PreferenceError, PreferenceService, validate
from service.rag import NO_GUIDANCE_ANSWER
from service.scenarios import TIMEZONE
from service.summaries import SummaryService, summary_service

logger = structlog.stdlib.get_logger(__name__)

PROMPTS = Path(__file__).resolve().parent / "rag_data" / "prompts"
Generate = Callable[[str, str], str]
PROVIDER_ERRORS = (requests.RequestException, RuntimeError, ValueError, SQLAlchemyError)
ISSUE_REASONS = {
    AnswerIssue.NO_GUIDANCE: "No guidance was found for this question.",
    AnswerIssue.UNANSWERED: "This question got no answer.",
    AnswerIssue.PUSHBACK: "The manager disputed this answer.",
}


@dataclass
class ReviewReport:
    chats: int = 0
    answer_issues: int = 0
    handover_drafts: int = 0
    settings: int = 0
    digests: int = 0
    failures: list[str] = field(default_factory=list)

    def text(self) -> str:
        """Counts cover only what this run found; earlier runs' output is not included."""
        if not self.chats:
            line = "No new messages since the last review."
        else:
            line = (
                f"Reviewed {plural(self.chats, 'chat')} with new messages: found "
                f"{plural(self.answer_issues, 'answer issue')}, drafted "
                f"{plural(self.handover_drafts, 'handover note')}, suggested "
                f"{plural(self.settings, 'setting')}."
            )
        if self.digests:
            line += f" Updated {plural(self.digests, 'memory digest')}."
        if self.failures:
            line += f" Failed: {', '.join(self.failures)}."
        return line


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


def _label(conversation: Conversation) -> str:
    first = conversation.messages[0]["what"] if conversation.messages else ""
    return conversation.title or " ".join(first.split())[:60]


def _day(message: dict[str, str]) -> date:
    return datetime.fromisoformat(message["when"]).astimezone(TIMEZONE).date()


def _short_date(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def bullets(text: str) -> str | None:
    """The reply's bullet lines, at most DIGEST_MAX_ITEMS; None if there are none."""
    items = [
        "- " + line.strip()[1:].strip()
        for line in text.splitlines()
        if line.strip()[:1] in {"-", "*", "•"} and line.strip()[1:].strip()
    ]
    return "\n".join(items[:DIGEST_MAX_ITEMS]) or None


def _evidence(conversation: Conversation, *positions: int) -> list[dict[str, Any]]:
    if not positions:
        return [{"conversation_id": str(conversation.id)}]
    return [
        {"conversation_id": str(conversation.id), "position": position}
        for position in positions
    ]


class DreamingService:
    def __init__(
        self,
        generate: Generate,
        summaries: SummaryService,
        preferences: Callable[[str, str], PreferenceService],
        engine: Engine | None = None,
    ) -> None:
        self.generate = generate
        self.summaries = summaries
        self.preferences = preferences
        self.engine = engine

    def _prompt(self, name: str) -> str:
        return (PROMPTS / name).read_text(encoding="utf-8")

    # Answer issues ---------------------------------------------------------

    def answer_issues(self, conversation: Conversation) -> list[dict[str, Any]]:
        """Issues in the messages after `dreamed_to`, found by text and position."""
        messages = conversation.messages
        start = 0 if conversation.dreamed_to is None else conversation.dreamed_to + 1
        issues: list[tuple[AnswerIssue, int, int | None]] = []
        replies: list[int] = []
        for index in range(start, len(messages)):
            message = messages[index]
            previous = messages[index - 1] if index > 0 else None
            if message["who"] == "assistant":
                if message["what"].strip() == NO_GUIDANCE_ANSWER and previous:
                    issues.append((AnswerIssue.NO_GUIDANCE, index - 1, index))
                continue
            follows = messages[index + 1] if index + 1 < len(messages) else None
            if follows is not None and follows["who"] == "manager":
                issues.append((AnswerIssue.UNANSWERED, index, None))
            if previous is not None and previous["who"] == "assistant":
                replies.append(index)
        for index in self._pushback(messages, replies):
            issues.append((AnswerIssue.PUSHBACK, index, index - 1))
        return [
            self._issue_row(conversation, issue, question, answer)
            for issue, question, answer in issues
        ]

    def _pushback(
        self,
        messages: list[dict[str, str]],
        replies: list[int],
    ) -> list[int]:
        if not replies:
            return []
        items = "\n\n".join(
            f"{number}. Assistant: {messages[index - 1]['what'][:600]}\n"
            f"Manager: {messages[index]['what'][:600]}"
            for number, index in enumerate(replies, start=1)
        )
        picked = json_array(self.generate(self._prompt("answer_pushback.md"), items))
        return [
            replies[number - 1]
            for number in picked
            if isinstance(number, int) and 1 <= number <= len(replies)
        ]

    def _issue_row(
        self,
        conversation: Conversation,
        issue: AnswerIssue,
        question: int,
        answer: int | None,
    ) -> dict[str, Any]:
        messages = conversation.messages
        positions = [question] if answer is None else sorted({question, answer})
        return {
            "store_id": conversation.store_id,
            "manager_id": conversation.manager_id,
            "kind": SuggestionKind.ANSWER_ISSUE,
            "payload": {
                "issue": issue,
                "chat": _label(conversation),
                "question": messages[question]["what"],
                "answer": messages[answer]["what"][:600]
                if answer is not None
                else None,
                "when": messages[question]["when"],
            },
            "reason": ISSUE_REASONS[issue],
            "evidence": _evidence(conversation, *positions),
        }

    # Handover draft --------------------------------------------------------

    def handover_draft(self, store_id: str, manager_id: str, day: date) -> bool:
        """Draft the day's handover note from the summaries of the day's chats."""
        chats = [
            conversation
            for conversation in recent_conversations(
                store_id,
                manager_id,
                50,
                self.engine,
            )
            if any(_day(message) == day for message in conversation.messages)
        ]
        if not chats:
            return False
        sections = []
        for conversation in chats:
            body = conversation.summary or "\n".join(
                f"{message['who']}: {message['what'][:300]}"
                for message in conversation.messages[-6:]
            )
            sections.append(f"Chat: {_label(conversation)}\n{body}")
        note = self.generate(self._prompt("handover_draft.md"), "\n\n".join(sections))
        note = note.strip()
        if not note:
            return False
        dismiss_pending_drafts(store_id, manager_id, day.isoformat(), self.engine)
        add_suggestions(
            [
                {
                    "store_id": store_id,
                    "manager_id": manager_id,
                    "kind": SuggestionKind.HANDOVER_DRAFT,
                    "payload": {"shift": day.isoformat(), "note": note},
                    "reason": (
                        f"Handover note for {day.day} {day.strftime('%b')}, "
                        f"drafted from {len(chats)} chat{'s' if len(chats) != 1 else ''}."
                    ),
                    "evidence": [
                        item
                        for conversation in chats
                        for item in _evidence(conversation)
                    ],
                },
            ],
            self.engine,
        )
        return True

    # Settings suggestions -------------------------------------------------

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
                f"[chat:{conversation.id}] {_label(conversation)}\n{conversation.summary}"
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

    # Memory digest ---------------------------------------------------------

    def memory_digest(self, store_id: str, manager_id: str, now: datetime) -> bool:
        """Rebuild the manager's digest from the last week's chat summaries.

        Returns whether the stored digest changed. When the chats and how far
        their summaries reach equal the stored sources, the model isn't called.
        """
        since = now - timedelta(days=DIGEST_DAYS)
        chats = [
            conversation
            for conversation in recent_conversations(
                store_id,
                manager_id,
                50,
                self.engine,
            )
            if conversation.summary
            and datetime.fromisoformat(conversation.messages[-1]["when"]) >= since
        ][:DIGEST_MAX_CHATS]
        sources = [
            {
                "conversation_id": str(conversation.id),
                "covers_to": conversation.summary_covers_to,
            }
            for conversation in chats
        ]
        current = get_digest(manager_id, self.engine)
        if (current.sources if current else []) == sources:
            return False
        digest = None
        if chats:
            reply = self.generate(
                self._prompt("memory_digest.md"),
                "\n\n".join(
                    f"Chat on {_short_date(_day(conversation.messages[-1]))}: "
                    f"{_label(conversation)}\n{conversation.summary}"
                    for conversation in reversed(chats)
                ),
            )
            digest = bullets(reply)
        save_digest(store_id, manager_id, digest, sources, self.engine)
        logger.info("Memory digest rebuilt", manager_id=manager_id, chats=len(chats))
        return True

    # The run ---------------------------------------------------------------

    def run(self, now: datetime | None = None) -> ReviewReport:
        now = now or datetime.now(TIMEZONE)
        report = ReviewReport()
        chats = conversations_to_review(self.engine)
        report.chats = len(chats)
        for conversation in chats:
            try:
                self.summaries.fold(conversation, keep_recent=0)
            except PROVIDER_ERRORS:
                logger.warning(
                    "Could not refresh summary before review",
                    conversation_id=str(conversation.id),
                    exc_info=True,
                )
            try:
                rows = self.answer_issues(conversation)
                report.answer_issues += add_suggestions(rows, self.engine)
                advance_dreamed_to(
                    conversation.id,
                    len(conversation.messages) - 1,
                    conversation.dreamed_to,
                    self.engine,
                )
            except PROVIDER_ERRORS:
                logger.warning(
                    "Could not review answers",
                    conversation_id=str(conversation.id),
                    exc_info=True,
                )
                report.failures.append("answer issues")
        for store_id, manager_id in sorted(
            {
                (conversation.store_id, conversation.manager_id)
                for conversation in chats
            },
        ):
            try:
                report.handover_drafts += self.handover_draft(
                    store_id,
                    manager_id,
                    now.astimezone(TIMEZONE).date(),
                )
            except PROVIDER_ERRORS:
                logger.warning("Could not draft the handover note", exc_info=True)
                report.failures.append("handover draft")
            try:
                report.settings += self.settings_suggestions(store_id, manager_id)
            except PROVIDER_ERRORS:
                logger.warning("Could not suggest settings", exc_info=True)
                report.failures.append("settings suggestions")
        # After the summaries are up to date; every manager, since old chats age out.
        for manager in all_managers(self.engine):
            try:
                report.digests += self.memory_digest(
                    manager.store_id,
                    manager.manager_id,
                    now,
                )
            except PROVIDER_ERRORS:
                logger.warning(
                    "Could not rebuild the memory digest",
                    manager_id=manager.manager_id,
                    exc_info=True,
                )
                report.failures.append("memory digest")
        logger.info("Daily review finished", report=report.text())
        return report


def _generate(system_prompt: str, user_message: str) -> str:
    return create_llm_service().generate(
        system_prompt=system_prompt,
        user_message=user_message,
    )


def dreaming_service() -> DreamingService:
    return DreamingService(
        generate=_generate,
        summaries=summary_service(),
        preferences=PreferenceService,
    )


def run_review() -> ReviewReport:
    """Admin entry point: run the daily review now."""
    return dreaming_service().run()


# Manager suggestions ---------------------------------------------------------


def pending_suggestions(
    store_id: str = DEMO_STORE_ID,
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> list[Suggestion]:
    """Settings suggestions and handover drafts awaiting the manager, newest first."""
    return list_suggestions(
        store_id,
        manager_id,
        [SuggestionKind.SETTING, SuggestionKind.HANDOVER_DRAFT],
        [SuggestionStatus.PENDING],
        engine=engine,
    )


def accept_suggestion(
    suggestion_id: UUID,
    note: str | None = None,
    engine: Engine | None = None,
) -> str:
    """Apply a pending suggestion through the normal validated path."""
    suggestion = get_suggestion(suggestion_id, engine)
    if suggestion is None or suggestion.status != SuggestionStatus.PENDING:
        raise LookupError("That suggestion is no longer pending.")
    payload = suggestion.payload
    if suggestion.kind == SuggestionKind.SETTING:
        service = PreferenceService(suggestion.store_id, suggestion.manager_id, engine)
        message = service.set(
            payload["code"],
            payload["enabled"],
            payload.get("value"),
            payload.get("options"),
        )
        message = message or "That setting was already in place."
    elif suggestion.kind == SuggestionKind.HANDOVER_DRAFT:
        text = (note if note is not None else payload["note"]).strip()
        if not text:
            raise PreferenceError("The handover note is empty.")
        save_handover_note(
            suggestion.store_id,
            suggestion.manager_id,
            date.fromisoformat(payload["shift"]),
            text,
            engine,
        )
        message = "Saved as the handover note for the next shift."
    else:
        raise LookupError("Answer issues are not accepted, only reviewed.")
    resolve_suggestion(suggestion_id, SuggestionStatus.ACCEPTED, engine)
    logger.info("Suggestion accepted", kind=suggestion.kind)
    return message


def dismiss_suggestion(suggestion_id: UUID, engine: Engine | None = None) -> bool:
    return resolve_suggestion(suggestion_id, SuggestionStatus.DISMISSED, engine)


def open_answer_issues(
    store_id: str = DEMO_STORE_ID,
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> list[Suggestion]:
    return list_suggestions(
        store_id,
        manager_id,
        [SuggestionKind.ANSWER_ISSUE],
        [SuggestionStatus.PENDING],
        limit=100,
        engine=engine,
    )


def handover_block(
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> str | None:
    """The latest shift's handover notes for the model, if any."""
    notes = latest_handover_notes(store_id, engine)
    if not notes:
        return None
    shift = notes[0].shift
    body = "\n\n".join(note.note for note in notes)
    return f"Handover from {shift.day} {shift.strftime('%b')}:\n{body}"


def memory_digest(
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> MemoryDigest | None:
    return get_digest(manager_id, engine)


def memory_block(
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> str | None:
    """The manager's digest for the model, if there is one."""
    digest = get_digest(manager_id, engine)
    if digest is None or not digest.digest:
        return None
    built = digest.built_at.astimezone(TIMEZONE).date()
    return (
        f"From this manager's chats in the {DIGEST_DAYS} days before "
        f"{_short_date(built)}:\n{digest.digest}"
    )
