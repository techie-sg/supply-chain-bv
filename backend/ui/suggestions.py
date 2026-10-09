"""Suggestions from the daily review: the manager's panel and the admin report.

Nothing here changes memory without a click: accepting a settings suggestion
saves it through the Settings path; accepting a handover draft saves a note.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any
from uuid import UUID

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID, TIMEZONE
from domain.memory import AlertOptions, SuggestionKind
from domain.preferences import EffectiveSetting
from domain.suggestions import SuggestionView
from service.dreaming import run_review
from service.preferences import (
    PreferenceError,
    describe,
    manager_preferences,
)
from service.suggestions import (
    accept_suggestion,
    dismiss_suggestion,
    mark_answer_issues_reviewed,
    open_answer_issues,
    pending_suggestions,
    plural,
)

logger = structlog.stdlib.get_logger(__name__)

UNAVAILABLE = "Suggestions are unavailable right now. Check the database connection."
ISSUE_NAMES = {
    "no_guidance": "No guidance found",
    "unanswered": "No answer",
    "pushback": "Pushback",
}


@dataclass
class SuggestionComponents:
    items: gr.Radio
    detail: gr.Markdown
    note: gr.Textbox
    actions: gr.Row
    accept: gr.Button
    dismiss: gr.Button
    status: gr.Markdown

    def outputs(self, entry: gr.Row, entry_text: gr.HTML) -> list[Any]:
        return [
            entry,
            entry_text,
            self.items,
            self.detail,
            self.note,
            self.actions,
            self.status,
        ]


def _setting_text(payload: dict[str, Any]) -> str:
    definitions = {
        setting.definition.code: setting.definition
        for setting in manager_preferences().effective()
    }
    definition = definitions.get(payload["code"])
    if definition is None:
        return payload["code"]
    options = payload.get("options")
    return describe(
        EffectiveSetting(
            definition,
            payload["enabled"],
            payload.get("value"),
            AlertOptions.model_validate(options) if options else None,
            customized=True,
        ),
    )


def _label(suggestion: SuggestionView) -> str:
    if suggestion.kind == SuggestionKind.HANDOVER_DRAFT:
        day = datetime.fromisoformat(suggestion.payload["shift"])
        return f"Handover note for {day.day} {day.strftime('%b')}"
    return f"Setting: {_setting_text(suggestion.payload)}"


def _details(suggestion: SuggestionView | None) -> tuple[str, dict]:
    if suggestion is None:
        return "No suggestions right now.", gr.update(visible=False, value="")
    chats = len({item["conversation_id"] for item in suggestion.evidence})
    detail = (
        f"**{escape(_label(suggestion))}**\n\n{escape(suggestion.reason)}\n\n"
        f'<span class="suggestion-evidence">Based on {chats} '
        f"chat{'s' if chats != 1 else ''}.</span>"
    )
    if suggestion.kind == SuggestionKind.HANDOVER_DRAFT:
        return detail, gr.update(visible=True, value=suggestion.payload["note"])
    return detail, gr.update(visible=False, value="")


def refresh(
    status: str = "",
    selected: str | None = None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple:
    """Sidebar entry and panel for the manager's pending suggestions."""
    try:
        items = pending_suggestions(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load suggestions", exc_info=True)
        return (
            gr.update(visible=False),
            "",
            gr.update(choices=[], value=None),
            UNAVAILABLE,
            gr.update(visible=False),
            gr.update(visible=False),
            status,
        )
    ids = [str(item.id) for item in items]
    current = selected if selected in ids else (ids[0] if ids else None)
    chosen = next((item for item in items if str(item.id) == current), None)
    detail, note = _details(chosen)
    count = len(items)
    return (
        gr.update(visible=bool(items)),
        f'<p class="sidebar-heading">Suggestions · {count}</p>',
        gr.update(
            choices=[(_label(item), str(item.id)) for item in items],
            value=current,
        ),
        detail,
        note,
        gr.update(visible=bool(items)),
        status,
    )


def refresh_for(manager_id: str = DEMO_MANAGER_ID) -> tuple:
    """The panel for one manager; the entry point for events that pass only them."""
    return refresh(manager_id=manager_id)


def select(
    suggestion_id: str | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[str, dict]:
    try:
        items = {
            str(item.id): item for item in pending_suggestions(manager_id=manager_id)
        }
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load suggestions", exc_info=True)
        return UNAVAILABLE, gr.update(visible=False)
    return _details(items.get(suggestion_id or ""))


def accept(
    suggestion_id: str | None,
    note: str | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple:
    if not suggestion_id:
        return refresh("Choose a suggestion first.", manager_id=manager_id)
    try:
        message = accept_suggestion(
            UUID(suggestion_id),
            DEMO_STORE_ID,
            manager_id,
            note,
        )
    except (PreferenceError, LookupError) as exc:
        return refresh(f"Not applied: {exc}", suggestion_id, manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not accept suggestion")
        raise gr.Error(UNAVAILABLE) from exc
    return refresh(message, manager_id=manager_id)


def dismiss(suggestion_id: str | None, manager_id: str = DEMO_MANAGER_ID) -> tuple:
    if not suggestion_id:
        return refresh("Choose a suggestion first.", manager_id=manager_id)
    try:
        if not dismiss_suggestion(UUID(suggestion_id), DEMO_STORE_ID, manager_id):
            return refresh(
                "Not dismissed: that suggestion is no longer pending or is not yours.",
                manager_id=manager_id,
            )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not dismiss suggestion")
        raise gr.Error(UNAVAILABLE) from exc
    return refresh("Dismissed.", manager_id=manager_id)


def build_panel() -> SuggestionComponents:
    """Lay out the Suggestions category inside the Settings tab."""
    items = gr.Radio(
        choices=[],
        value=None,
        label="Pending suggestions",
        show_label=False,
        container=False,
        elem_id="suggestion-list",
    )
    with gr.Column(elem_classes="setting-card"):
        detail = gr.Markdown("No suggestions right now.", elem_id="suggestion-detail")
        note = gr.Textbox(
            label="Handover note (edit before accepting)",
            lines=6,
            visible=False,
            elem_id="suggestion-note",
        )
    with gr.Row(visible=False, elem_id="suggestion-actions") as actions:
        accept_button = gr.Button("Accept", variant="primary", scale=0, min_width=120)
        dismiss_button = gr.Button("Dismiss", scale=0, min_width=120)
    # Outside the actions row, so the outcome stays visible once nothing is pending.
    status = gr.Markdown(elem_id="suggestion-status")
    return SuggestionComponents(
        items,
        detail,
        note,
        actions,
        accept_button,
        dismiss_button,
        status,
    )


# Admin: the answer-issue report ----------------------------------------------


def issues_table(manager_id: str = DEMO_MANAGER_ID) -> dict[str, Any]:
    try:
        issues = open_answer_issues(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load answer issues", exc_info=True)
        issues = []
    rows = []
    for issue in issues:
        payload = issue.payload
        when = datetime.fromisoformat(payload["when"]).astimezone(TIMEZONE)
        rows.append(
            [
                when.strftime("%d %b, %H:%M"),
                ISSUE_NAMES.get(payload["issue"], payload["issue"]),
                payload.get("chat") or "",
                payload["question"],
                payload.get("answer") or "",
            ],
        )
    return {"headers": ["When", "Issue", "Chat", "Question", "Answer"], "data": rows}


def run_review_now(manager_id: str = DEMO_MANAGER_ID) -> tuple[str, dict[str, Any]]:
    """Review every manager's chats; then show the selected manager's issues."""
    try:
        report = run_review()
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Daily review failed")
        raise gr.Error(
            "The review could not run. Check the database connection.",
        ) from exc
    status = " ".join(part for part in (report.text(), _waiting(manager_id)) if part)
    return status, issues_table(manager_id)


def _waiting(manager_id: str) -> str:
    """What is still open after the run, including output from earlier runs."""
    try:
        pending = len(pending_suggestions(manager_id=manager_id))
        issues = len(open_answer_issues(manager_id=manager_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not count open review items", exc_info=True)
        return ""
    return (
        f"Waiting: {plural(pending, 'suggestion')} for the manager, "
        f"{plural(issues, 'answer issue')} to look at."
    )


def mark_issues_reviewed(
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[str, dict[str, Any]]:
    try:
        count = mark_answer_issues_reviewed(DEMO_STORE_ID, manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not mark issues reviewed")
        raise gr.Error(UNAVAILABLE) from exc
    return f"Marked {count} issues as reviewed.", issues_table(manager_id)
