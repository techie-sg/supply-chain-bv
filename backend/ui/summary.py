"""Present and refresh the open conversation summary."""

from datetime import datetime

import gradio as gr
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from service.conversations import (
    conversation_summary,
)
from service.scenarios import (
    TIMEZONE,
)
from service.summaries import (
    run_summary_job,
    summarize_latest_conversation,
    summarize_open_conversation,
)
from ui.browser import browser_script
from ui.chat import chat_scope, message_time

logger = structlog.stdlib.get_logger(__name__)

SUMMARY_NOTE = (
    '\n\n<p class="summary-note">Your full conversation stays in the chat.</p>'
)


SUMMARY_POPOVER_JS = browser_script("summary_popover.js")


CLOSE_SUMMARY_JS = browser_script("close_summary.js")


def summary_card(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[dict, str, str, dict]:
    """Show the summary trigger for a saved chat and refresh its popover content."""
    hidden = (gr.update(visible=False), "", "", gr.skip())
    try:
        view = conversation_summary(**chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load the conversation summary", exc_info=True)
        return hidden
    if view is None:
        return hidden
    if view["summary"] is None:
        return (
            gr.update(visible=True),
            f"{view['total']} messages · No summary yet",
            "Bring the key decisions and details from this conversation into one place.",
            gr.update(value="Create summary"),
        )
    status = f"{view['covered']} of {view['total']} messages"
    if view["summarized_at"] is not None:
        status += (
            f" · Updated {message_time(view['summarized_at'], datetime.now(TIMEZONE))}"
        )
    return (
        gr.update(visible=True),
        status,
        view["summary"] + SUMMARY_NOTE,
        gr.update(value="Update summary"),
    )


def summarize_now(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[dict, str, str, dict]:
    """Summarize the open chat and refresh the content inside its open popover."""
    try:
        updated = summarize_open_conversation(
            **chat_scope(manager_id, conversation_id),
        )
    except (
        SQLAlchemyError,
        RuntimeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        logger.exception("Could not summarize the conversation on request")
        raise gr.Error("Could not summarize this chat. Please try again.") from exc
    if not updated:
        gr.Info("The summary already covers every message.")
    return summary_card(**chat_scope(manager_id, conversation_id))


def summarize_conversation(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> None:
    """After an answer, fold older messages into the summary if over a limit."""
    try:
        summarize_latest_conversation(**chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError, ValueError, requests.RequestException):
        logger.warning("Could not summarize the conversation", exc_info=True)


def run_summary_job_now() -> str:
    """Demo action: run the cron batch without waiting for chats to become idle."""
    try:
        count, failures = run_summary_job(force=True)
    except (
        SQLAlchemyError,
        RuntimeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        logger.exception("Could not run the summary and personalization job")
        raise gr.Error(
            "Could not run summary and personalization. Please try again.",
        ) from exc
    status = (
        f"Run finished at {datetime.now(TIMEZONE):%H:%M}. "
        f"Updated {count} conversation {'summary' if count == 1 else 'summaries'}. "
    )
    if failures:
        status += f"{failures} conversations could not finish; run again to retry. "
    return status + "Verified personalization preferences are saved automatically."
