"""Dreaming: a daily review of chats that proposes, never applies.

A run brings each reviewed chat's summary up to date, then produces:

- answer issues from raw messages after `dreamed_to` (for the admin report),
- a handover draft for the day from the day's chat summaries,
- settings suggestions from recent chat summaries,
- each manager's memory digest from the last week's chat summaries.

Suggestions are saved as pending rows; the digest is used without asking.
Each output fails on its own.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests
import structlog
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from constants import NO_GUIDANCE_ANSWER, TIMEZONE
from database.models import Conversation
from domain.chat import StoredMessage
from domain.memory import (
    AnswerIssue,
    SuggestionKind,
)
from queries.dreaming import (
    add_suggestions,
    advance_dreamed_to,
    all_managers,
    conversations_to_review,
)
from resources import PROMPTS
from service.factory import create_llm_service
from service.handover import HandoverService
from service.memory import MemoryService
from service.preferences import PreferenceService
from service.review_context import chat_evidence, chat_label
from service.suggestions import SettingsSuggestionService, json_array, plural
from service.summaries import SummaryService, summary_service

logger = structlog.stdlib.get_logger(__name__)


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
        self.handover = HandoverService(generate, engine)
        self.settings = SettingsSuggestionService(generate, preferences, engine)
        self.memory = MemoryService(generate, engine)

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
        messages: list[StoredMessage],
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
                "chat": chat_label(conversation),
                "question": messages[question]["what"],
                "answer": messages[answer]["what"][:600]
                if answer is not None
                else None,
                "when": messages[question]["when"],
            },
            "reason": ISSUE_REASONS[issue],
            "evidence": chat_evidence(conversation, *positions),
        }

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
                report.handover_drafts += self.handover.handover_draft(
                    store_id,
                    manager_id,
                    now.astimezone(TIMEZONE).date(),
                )
            except PROVIDER_ERRORS:
                logger.warning("Could not draft the handover note", exc_info=True)
                report.failures.append("handover draft")
            try:
                report.settings += self.settings.settings_suggestions(
                    store_id,
                    manager_id,
                )
            except PROVIDER_ERRORS:
                logger.warning("Could not suggest settings", exc_info=True)
                report.failures.append("settings suggestions")
        # After the summaries are up to date; every manager, since old chats age out.
        for manager in all_managers(self.engine):
            try:
                report.digests += self.memory.memory_digest(
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
