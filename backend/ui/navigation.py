"""Workspace tabs and browser navigation."""

import gradio as gr
import structlog

from ui import settings
from ui.browser import browser_script

logger = structlog.stdlib.get_logger(__name__)


TAB_URL_JS = browser_script("tab_url.js")


CHAT_URL_JS = browser_script("chat_url.js")


CHAT_NAVIGATION_JS = browser_script("chat_navigation.js")


def open_settings() -> dict:
    return gr.update(selected="settings")


def show_suggestions() -> tuple[dict, ...]:
    """Select the Suggestions category and show its panel.

    Runs after the Settings tab is selected: updates sent before the tab first
    renders would be lost.
    """
    return (gr.update(value="suggestions"), *settings.show_category("suggestions"))


def _restore_tab(request: gr.Request) -> dict:
    view = request.query_params.get("view", "assistant")
    return gr.update(
        selected=view if view in ("demo", "settings", "handover") else "assistant",
    )
