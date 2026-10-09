"""Recent conversation navigation and shift-manager selection."""

from dataclasses import dataclass
from html import escape
from typing import Any

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from domain.managers import ShiftManager
from service.conversations import (
    conversation_details,
    current_conversation_id,
    past_conversations,
    resume_past_conversation,
    title_latest_conversation,
)
from service.managers import choose_manager, store_managers
from ui.browser import browser_script
from ui.chat import chat_scope, restore_chat, to_display

logger = structlog.stdlib.get_logger(__name__)


CLOSE_SIDEBAR_ON_PHONE_JS = browser_script("close_sidebar_on_phone.js")


MANAGER_URL_JS = browser_script("manager_url.js")


def _conversation_title(item: dict[str, Any]) -> str:
    """Keep the full title for hover; CSS clips it to one line in the list."""
    return " ".join((item.get("title") or item["first_question"] or "").split())


def conversation_choices(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> dict:
    """Sidebar list of the manager's past chats, highlighting the open one."""
    try:
        items = past_conversations(manager_id=manager_id)
        current = conversation_id or current_conversation_id(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not list past conversations", exc_info=True)
        items, current = [], None
    choices = [(_conversation_title(item), str(item["id"])) for item in items]
    return gr.update(
        choices=choices,
        value=current if current in {value for _, value in choices} else None,
    )


def open_conversation(
    conversation_id: str | None,
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[list[dict], str, str, dict]:
    """Show a past conversation and make it the one new questions continue."""
    if not conversation_id:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()
    try:
        messages = resume_past_conversation(conversation_id, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError) as exc:
        logger.exception("Could not open conversation %s", conversation_id)
        raise gr.Error("Could not open that conversation. Please try again.") from exc
    return to_display(messages), "", conversation_id, gr.update(selected="assistant")


def restore_latest_conversation(manager_id: str) -> tuple[list[dict], str | None]:
    history = restore_chat(manager_id)
    try:
        return history, current_conversation_id(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        return history, None


def restore_conversation(
    manager_id: str,
    request: gr.Request,
) -> tuple[list[dict], str | None]:
    conversation_id = request.query_params.get("chat")
    if conversation_id:
        history, _, _, _ = open_conversation(conversation_id, manager_id)
        return history, conversation_id
    return restore_latest_conversation(manager_id)


def conversation_location(
    manager_id: str,
    conversation_id: str | None,
) -> dict[str, str | None]:
    try:
        item = (
            conversation_details(conversation_id, manager_id=manager_id)
            if conversation_id
            else {}
        )
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError):
        logger.warning("Could not load the chat title", exc_info=True)
        item = {}
    return {"id": conversation_id, "title": item.get("title") or "New chat"}


def title_conversation(
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> dict:
    """Title the open chat after its first answer, then refresh the sidebar."""
    try:
        title_latest_conversation(**chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not title the conversation", exc_info=True)
    return conversation_choices(**chat_scope(manager_id, conversation_id))


def _manager_label(manager: ShiftManager) -> str:
    return (
        f"{manager.name}\n{manager.shift_name} shift · "
        f"{manager.shift_start}–{manager.shift_end}"
    )


def manager_badge(manager: ShiftManager | None) -> str:
    """Who is signed in to the workspace, and their shift."""
    if manager is None:
        return '<div class="manager-badge"><span>No manager available</span></div>'
    return (
        '<div class="manager-badge"><span>Manager</span>'
        f"<strong>{escape(manager.name)}</strong>"
        f'<em data-shift-id="{escape(manager.shift_id)}">'
        f"{escape(manager.shift_name)} shift · {escape(manager.shift_start)}–"
        f"{escape(manager.shift_end)}</em></div>"
    )


def _managers() -> list[ShiftManager]:
    try:
        return store_managers()
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not list managers", exc_info=True)
        return []


def restore_manager(request: gr.Request) -> tuple[str, dict, str]:
    """Pick the manager from the URL (?manager=), else the demo manager."""
    managers = _managers()
    manager = choose_manager(request.query_params.get("manager"), managers)
    manager_id = manager.manager_id if manager else DEMO_MANAGER_ID
    return (
        manager_id,
        gr.update(
            choices=[(_manager_label(item), item.manager_id) for item in managers],
            value=manager.manager_id if manager else None,
        ),
        manager_badge(manager),
    )


def select_manager(requested: str | None) -> tuple[str, str]:
    """Switch the workspace to another manager of the store."""
    managers = _managers()
    manager = choose_manager(requested, managers)
    if manager is None or manager.manager_id != requested:
        raise gr.Error("That manager is not available. Please choose another.")
    logger.info("Manager selected", manager_id=manager.manager_id)
    return manager.manager_id, manager_badge(manager)


@dataclass
class SidebarComponents:
    new_chat: gr.Button
    edit_settings: gr.Button
    demo_navigation: gr.Button
    history_list: gr.Radio
    suggestions_entry: gr.Row
    suggestions_entry_text: gr.HTML
    review_suggestions: gr.Button
    settings_summary: gr.HTML
    context_banner: gr.HTML
    manager_picker: gr.Dropdown
    badge: gr.HTML
    handover_navigation: gr.Button


def build_sidebar() -> SidebarComponents:
    with gr.Sidebar(label="Workspace", width=288, elem_id="chat-sidebar"):
        with gr.Column(elem_id="sidebar-top"):
            gr.HTML(
                '<a href="/?view=assistant" class="sidebar-brand" '
                'aria-label="Reload Assistant">DispatchDesk</a>',
                apply_default_css=False,
                elem_id="sidebar-brand",
            )
            with gr.Column(elem_id="sidebar-navigation"):
                new_chat = gr.Button(
                    "New chat",
                    size="sm",
                    variant="secondary",
                    elem_id="new-chat",
                    elem_classes="sidebar-nav-item",
                )
                edit_settings = gr.Button(
                    "Settings",
                    size="sm",
                    elem_id="edit-settings",
                    elem_classes="sidebar-nav-item",
                )
                handover_navigation = gr.Button(
                    "Handover",
                    size="sm",
                    elem_id="sidebar-handover",
                    elem_classes="sidebar-nav-item",
                )
                demo_navigation = gr.Button(
                    "Demo tools",
                    size="sm",
                    elem_id="sidebar-demo",
                    elem_classes="sidebar-nav-item",
                )
        with gr.Column(elem_id="sidebar-history"):
            gr.HTML(
                '<p class="sidebar-heading">Recent chats</p>',
                apply_default_css=False,
            )
            history_list = gr.Radio(
                choices=[],
                value=None,
                label="Past conversations",
                show_label=False,
                container=False,
                elem_id="history-list",
            )
        with gr.Column(elem_id="settings-summary-block"):
            with gr.Row(
                visible=False,
                elem_id="suggestions-entry",
            ) as suggestions_entry:
                suggestions_entry_text = gr.HTML(
                    '<p class="sidebar-heading">Suggestions</p>',
                    apply_default_css=False,
                )
                review_suggestions = gr.Button(
                    "Review",
                    size="sm",
                    scale=0,
                    min_width=0,
                    elem_id="review-suggestions",
                )
            with gr.Column(elem_id="sidebar-settings"):
                gr.HTML(
                    '<p class="sidebar-heading">Active settings</p>',
                    apply_default_css=False,
                )
                settings_summary = gr.HTML(
                    apply_default_css=False,
                    elem_id="settings-summary",
                )
            context_banner = gr.HTML(
                '<div class="current-scenario"><span>Current scenario</span>'
                "<strong>Checking…</strong></div>",
                apply_default_css=False,
                elem_id="sidebar-scenario",
            )
            with gr.Column(elem_id="manager-profile"):
                manager_picker = gr.Dropdown(
                    choices=[],
                    value=None,
                    label="Shift manager",
                    show_label=False,
                    container=False,
                    filterable=False,
                    interactive=True,
                    elem_id="manager-picker",
                )
                badge = gr.HTML(
                    manager_badge(None),
                    apply_default_css=False,
                    elem_id="assistant-manager",
                )

    return SidebarComponents(
        new_chat,
        edit_settings,
        demo_navigation,
        history_list,
        suggestions_entry,
        suggestions_entry_text,
        review_suggestions,
        settings_summary,
        context_banner,
        manager_picker,
        badge,
        handover_navigation,
    )
