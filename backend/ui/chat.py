"""Render stored messages, answer turns and confirm proposed setting changes."""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any
from uuid import uuid4

import gradio as gr
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from domain.chat import StoredMessage
from service.conversations import (
    add_note,
    ask_question,
    start_new_conversation,
)
from service.scenarios import (
    TIMEZONE,
)
from service.setting_changes import confirm_proposals
from ui.browser import browser_script

logger = structlog.stdlib.get_logger(__name__)

CHAT_PLACEHOLDER = """
<div class="chat-welcome">
    <div class="welcome-orbit" aria-hidden="true"><span class="welcome-emblem"></span></div>
    <h1>Let’s make <span>the right call.</span></h1>
    <p>Ask a question or talk through a decision.<br>Get clear guidance grounded in your playbook.</p>
</div>
"""


PROCESSING_STATUS = """
<div class="thinking-indicator" role="status" aria-live="polite">
    <span class="thinking-spark" aria-hidden="true">✦</span>
    <span>Thinking…</span>
</div>
"""


SEND_MESSAGE_JS = browser_script("send_message.js")


FINISH_CHAT_JS = browser_script("finish_chat.js")


def message_time(when: datetime, now: datetime) -> str:
    """Time for today's messages; day and time for older ones, in IST."""
    when = when.astimezone(TIMEZONE)
    if when.date() == now.astimezone(TIMEZONE).date():
        return when.strftime("%H:%M")
    return f"{when.day} {when.strftime('%b, %H:%M')}"


def _with_time(text: str, when: datetime, now: datetime) -> str:
    """Message text with its time below it; shown only, never sent to the model."""
    return f'{text}\n\n<span class="message-time">{message_time(when, now)}</span>'


def _tool_line(call: Mapping[str, Any]) -> str:
    """One tool call: name, arguments, the data's as-of time and any problem."""
    arguments = call.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    text = f"{call['tool']}({', '.join(f'{k}={v}' for k, v in arguments.items())})"
    if call.get("as_of"):
        as_of = datetime.fromisoformat(call["as_of"]).astimezone(TIMEZONE)
        text += f" — data as of {as_of.strftime('%H:%M')} IST"
        if call.get("stale"):
            text += " (stale)"
    if call.get("error"):
        text += f" — error {call['error']}"
    return text


def _trace_html(trace: Mapping[str, Any] | None) -> str:
    """A collapsed panel: tool calls, and the manager's own settings in effect."""
    if not trace or not (trace.get("tools") or trace.get("preferences")):
        return ""
    tools = trace.get("tools") or []
    rows = [f"<li>{escape(_tool_line(call))}</li>" for call in tools]
    preferences = [
        f"<li>{escape(item)}</li>" for item in trace.get("preferences") or []
    ]
    count = f"{len(tools)} tool call{'s' if len(tools) != 1 else ''}"
    body = f"<p>Tools</p><ul>{''.join(rows) or '<li>None used</li>'}</ul>"
    if preferences:
        body += f"<p>Your settings applied</p><ul>{''.join(preferences)}</ul>"
    return (
        f'<details class="agent-trace"><summary>Agent trace · {count}</summary>'
        f"{body}</details>"
    )


def to_display(messages: Sequence[StoredMessage]) -> list[dict]:
    """Stored {who, what, when} messages as chat bubbles with their times."""
    now = datetime.now(TIMEZONE)
    return [
        {
            "role": "user" if message["who"] == "manager" else "assistant",
            "content": _with_time(
                message["what"] + _trace_block(message.get("trace")),
                datetime.fromisoformat(message["when"]),
                now,
            ),
        }
        for message in messages
    ]


def _trace_block(trace: Mapping[str, Any] | None) -> str:
    html = _trace_html(trace)
    return f"\n\n{html}" if html else ""


def chat_scope(manager_id: str, conversation_id: str | None) -> dict[str, Any]:
    return {
        "manager_id": manager_id,
        **({"conversation_id": conversation_id} if conversation_id else {}),
    }


def clear_chat(manager_id: str = DEMO_MANAGER_ID) -> tuple[list[dict], str, str, dict]:
    """Start a new stored conversation; the previous one is kept."""
    try:
        conversation_id = start_new_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not start a new conversation")
        raise gr.Error("Could not start a new chat. Please try again.") from exc
    return [], "", conversation_id, gr.update(selected="assistant")


def chat(
    message: str,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], str, Any]:
    """Answer from stored history; keep the draft and display intact on failure.

    The third value is the setting changes the answer proposed, for the
    confirmation card; it is left as it was when the answer proposed none.
    """
    history = history or []
    if not message.strip():
        return history, "", gr.skip()
    request_id = uuid4().hex
    try:
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            answer, proposals, trace = ask_question(
                message,
                **chat_scope(manager_id, conversation_id),
            )
    except (
        requests.RequestException,
        SQLAlchemyError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as exc:
        logger.exception("Assistant request failed", request_id=request_id)
        raise gr.Error(
            "The assistant is unavailable right now. Please try again.",
        ) from exc
    now = datetime.now(TIMEZONE)
    pending = [change.to_state() for change in proposals] if proposals else gr.skip()
    return (
        history
        + [
            {"role": "user", "content": _with_time(message, now, now)},
            {
                "role": "assistant",
                "content": _with_time(answer + _trace_block(trace), now, now),
            },
        ],
        "",
        pending,
    )


def respond_to_pending(
    message: str,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> Iterator[tuple[list[dict], str, Any]]:
    """Answer the message already displayed by the browser without duplicating it."""
    history = history or []
    if not message.strip():
        yield history, "", gr.skip()
        return
    previous = (
        history[:-1] if history and history[-1].get("role") == "user" else history
    )
    try:
        yield chat(message, previous, manager_id, conversation_id)
    except gr.Error:
        yield previous, message, gr.skip()
        raise


def recover_composer() -> tuple[dict, dict, dict]:
    """Unlock the composer after a failed streamed request; retain its draft."""
    return (
        gr.update(visible=False),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def pending_card(pending: list[dict] | None) -> tuple[str, dict]:
    """The proposed setting changes, old to new, waiting for Confirm or Cancel."""
    if not pending:
        return "", gr.update(visible=False)
    items = "".join(
        f"<li><strong>{escape(change['name'])}</strong>"
        f'<span class="change-from">{escape(change["before"])}</span>'
        '<span class="change-arrow" aria-label="changes to">→</span>'
        f'<span class="change-to">{escape(change["after"])}</span></li>'
        for change in pending
    )
    title = (
        "Proposed setting change"
        if len(pending) == 1
        else f"{len(pending)} proposed setting changes"
    )
    card = (
        f'<div class="pending-card"><p class="pending-title">{title}'
        "<span>Not saved until you confirm</span></p>"
        f"<ul>{items}</ul></div>"
    )
    return card, gr.update(visible=True)


def _with_note(
    history: list[dict] | None,
    text: str,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> list[dict]:
    """Store an assistant note in the manager's open chat and show it."""
    now = datetime.now(TIMEZONE)
    try:
        stored = add_note(text, **chat_scope(manager_id, conversation_id))
    except (SQLAlchemyError, RuntimeError, LookupError):
        logger.warning("Could not store the note in the conversation", exc_info=True)
        stored = None
    when = datetime.fromisoformat(stored["when"]) if stored else now
    return (history or []) + [
        {"role": "assistant", "content": _with_time(text, when, now)},
    ]


def confirm_pending(
    pending: list[dict] | None,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], list]:
    """Save the proposed setting changes the manager confirmed."""
    if not pending:
        return history or [], []
    try:
        results = confirm_proposals(pending, manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not save confirmed setting changes")
        raise gr.Error("Could not save the settings. Please try again.") from exc
    return _with_note(history, "\n\n".join(results), manager_id, conversation_id), []


def cancel_pending(
    pending: list[dict] | None,
    history: list[dict] | None,
    manager_id: str = DEMO_MANAGER_ID,
    conversation_id: str | None = None,
) -> tuple[list[dict], list]:
    """Discard the proposed setting changes; nothing is saved."""
    if not pending:
        return history or [], []
    names = ", ".join(change["name"] for change in pending)
    return (
        _with_note(
            history,
            f"Cancelled. Nothing was changed ({names}).",
            manager_id,
            conversation_id,
        ),
        [],
    )


@dataclass
class ChatComponents:
    chatbot: gr.Chatbot
    pending_message: gr.Textbox
    pending_box: gr.Column
    pending_html: gr.HTML
    confirm_changes: gr.Button
    cancel_changes: gr.Button
    processing: gr.HTML
    summary_status: gr.HTML
    summary_text: gr.Markdown
    summarize_button: gr.Button
    message: gr.Textbox
    summary_trigger: gr.Button
    submit: gr.Button
    suggestions: gr.Row
    prompts: list[str]
    prompt_buttons: list[gr.Button]


def build_chat() -> ChatComponents:
    with (
        gr.Tab("Assistant", id="assistant"),
        gr.Column(elem_id="manager-workspace", min_width=0),
        gr.Column(elem_id="assistant-panel", min_width=0),
    ):
        chatbot = gr.Chatbot(
            label="Conversation",
            show_label=False,
            height="auto",
            autoscroll=False,
            layout="bubble",
            group_consecutive_messages=False,
            placeholder=CHAT_PLACEHOLDER,
            buttons=["copy"],
            elem_id="conversation",
        )
        pending_message = gr.Textbox(visible="hidden", interactive=False)
        with gr.Column(elem_id="composer-dock", min_width=0):
            with gr.Column(
                visible=False,
                elem_id="pending-changes",
                min_width=0,
            ) as pending_box:
                pending_html = gr.HTML(apply_default_css=False)
                with gr.Row(elem_id="pending-actions"):
                    confirm_changes = gr.Button(
                        "Confirm",
                        variant="primary",
                        size="sm",
                        scale=0,
                        min_width=96,
                        elem_id="confirm-changes",
                    )
                    cancel_changes = gr.Button(
                        "Cancel",
                        size="sm",
                        scale=0,
                        min_width=96,
                        elem_id="cancel-changes",
                    )
            processing = gr.HTML(
                PROCESSING_STATUS,
                visible=False,
                apply_default_css=False,
                elem_id="chat-processing",
            )
            with gr.Column(elem_id="chat-summary-panel", min_width=0):
                gr.HTML(
                    '<div class="summary-heading">'
                    '<h3 id="chat-summary-title">Conversation summary</h3>'
                    '<button type="button" aria-label="Close summary" '
                    'popovertarget="chat-summary-panel" popovertargetaction="hide">'
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                    'stroke-width="1.5" aria-hidden="true">'
                    '<path d="m6 6 12 12M6 18 18 6"/></svg></button></div>',
                    apply_default_css=False,
                )
                summary_status = gr.HTML(
                    apply_default_css=False,
                    elem_id="chat-summary-status",
                )
                summary_text = gr.Markdown(elem_id="chat-summary-text")
                summarize_button = gr.Button(
                    "Create summary",
                    size="sm",
                    elem_id="summarize-now",
                )
            with gr.Row(elem_id="message-composer"):
                message = gr.Textbox(
                    label="Ask your dispatch assistant",
                    show_label=False,
                    placeholder="What's on your mind?",
                    lines=1,
                    max_lines=6,
                    container=False,
                    elem_id="message-input",
                )
                summary_trigger = gr.Button(
                    "Summary",
                    size="sm",
                    scale=0,
                    min_width=0,
                    visible=False,
                    elem_id="summary-trigger",
                )
                submit = gr.Button(
                    "Send",
                    variant="primary",
                    scale=0,
                    min_width=88,
                    elem_id="send-message",
                )
        with gr.Row(elem_id="starter-prompts") as suggestions:
            prompts = [
                "We're missing the 10-minute promise. Should I ask my riders to jump red lights and speed?",
                "It's pouring and deliveries are late. Can I dock riders' pay for missing the delivery promise?",
                "Packed orders are piling up. Can I batch frozen-item orders with other deliveries?",
            ]
            prompt_buttons = [
                gr.Button(question, size="sm", elem_classes="prompt-button")
                for question in prompts
            ]

    return ChatComponents(
        chatbot,
        pending_message,
        pending_box,
        pending_html,
        confirm_changes,
        cancel_changes,
        processing,
        summary_status,
        summary_text,
        summarize_button,
        message,
        summary_trigger,
        submit,
        suggestions,
        prompts,
        prompt_buttons,
    )
