"""Handover page and the handover card at the top of a handover chat.

The page shows the manager's shift and note and the store's past handovers.
Ending a shift hands over: the next manager's chat opens with the note.
"""

from dataclasses import dataclass
from html import escape
from typing import Any

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from service.conversations import handover_card
from service.handover import (
    ShiftView,
    begin_shift,
    current_shift,
    finish_shift,
    generate_note,
    hand_over,
    next_manager,
    past_handovers,
    save_shift_note,
    when,
)
from ui.sidebar import select_manager

logger = structlog.stdlib.get_logger(__name__)

UNAVAILABLE = "Handover is unavailable right now. Check the database connection."
EMPTY_NOTE = "Your note is empty. Click the button again to end without one."


def _span(shift: ShiftView) -> str:
    """ "9 Oct, 06:02 → 13:55", with the end's date only when it differs."""
    start = when(shift.started_at)
    if shift.ended_at is None:
        return f"started {start}"
    end = when(shift.ended_at)
    same_day = end.split(",")[0] == start.split(",")[0]
    return f"{start} → {end.split(', ')[1] if same_day else end}"


def status_html(shift: ShiftView | None) -> str:
    if shift is None:
        return (
            '<div class="shift-status"><p>No shift yet. Start one when you begin '
            "work; asking the assistant a question also starts one.</p></div>"
        )
    state = "Open" if shift.is_open else "Ended"
    return (
        f'<div class="shift-status"><strong>{escape(shift.shift_name)} shift'
        f"</strong> · {escape(_span(shift))} · "
        f'<span class="shift-state shift-{state.lower()}">{state}</span></div>'
    )


def past_html(shifts: list[ShiftView]) -> str:
    if not shifts:
        return '<p class="handover-empty">No handovers in the last 7 days.</p>'
    cards = []
    for shift in shifts:
        note = (shift.note or "").strip()
        body = (
            f'<p class="handover-note">{escape(note)}</p>'
            if note
            else '<p class="handover-empty">No note left.</p>'
        )
        cards.append(
            '<article class="handover-card"><header>'
            f"<strong>{escape(shift.manager_name)}</strong> · "
            f"{escape(shift.shift_name)} · {escape(_span(shift))}</header>"
            f"{body}</article>",
        )
    return f'<div class="handover-list">{"".join(cards)}</div>'


def _end_label(following: Any) -> str:
    if following is None:
        return "End shift"
    return f"End shift and hand over to {following.name} ({following.shift_name}) →"


def page(manager_id: str = DEMO_MANAGER_ID, message: str = "") -> tuple:
    """Every output of the page, in `HandoverPage.outputs()` order.

    The last output, `handed_to`, changes only when a hand over happens.
    """
    try:
        shift = current_shift(manager_id)
        past = past_handovers()
        following = next_manager(manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError):
        logger.warning("Could not load the handover page", exc_info=True)
        return (
            status_html(None),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.skip(),
            UNAVAILABLE,
            past_html([]),
            False,
            gr.skip(),
            gr.skip(),
        )
    is_open = shift is not None and shift.is_open
    return (
        status_html(shift),
        gr.update(
            value=(shift.note or "") if shift else "",
            visible=shift is not None,
            interactive=is_open,
            label=(
                "Handover note (editable until you end your shift)"
                if is_open
                else "Handover note (final)"
            ),
        ),
        gr.update(visible=is_open),
        gr.update(visible=not is_open),
        gr.update(value=_end_label(following)),
        message,
        past_html(past),
        False,
        gr.update(value="Start shift" if shift is None else "Start a new shift"),
        gr.skip(),
    )


def start(manager_id: str) -> tuple:
    try:
        begin_shift(manager_id)
    except (SQLAlchemyError, RuntimeError, LookupError):
        logger.warning("Could not start the shift", exc_info=True)
        return page(manager_id, UNAVAILABLE)
    return page(manager_id, "Shift started.")


def generate(manager_id: str) -> tuple[Any, str]:
    """Fill the box with a draft; it is saved only by Save note or ending the shift."""
    try:
        note = generate_note(manager_id)
    except LookupError as exc:
        return gr.skip(), str(exc)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.warning("Could not draft the handover note", exc_info=True)
        return gr.skip(), "Could not draft a note right now. Please try again."
    if note is None:
        return gr.skip(), "No chats in this shift yet, so there is nothing to draft."
    return note, "Draft ready. Edit it, then save or end your shift."


def save(manager_id: str, note: str | None) -> tuple:
    try:
        save_shift_note(manager_id, note or "")
    except LookupError as exc:
        return page(manager_id, str(exc))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not save the handover note", exc_info=True)
        return page(manager_id, UNAVAILABLE)
    return page(manager_id, "Note saved. Your shift is still open.")


def end(manager_id: str, note: str | None, confirmed_empty: bool) -> tuple:
    """End the shift and hand over; an empty note needs a second click.

    Sets `handed_to` to the next manager and their new chat, which switches the
    workspace to them. Without a next shift, the shift just ends.
    """
    if not (note or "").strip() and not confirmed_empty:
        skipped = [gr.skip()] * 5
        return (*skipped, EMPTY_NOTE, gr.skip(), True, gr.skip(), gr.skip())
    try:
        if next_manager(manager_id) is None:
            ended = finish_shift(manager_id, note or "")
            return page(manager_id, f"Shift ended at {when(ended).split(', ')[1]}.")
        done = hand_over(manager_id, note or "")
    except LookupError as exc:
        return page(manager_id, str(exc))
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not end the shift", exc_info=True)
        return page(manager_id, UNAVAILABLE)
    handed_to = {"manager": done.manager_id, "chat": str(done.conversation_id)}
    message = f"Shift ended at {when(done.ended_at).split(', ')[1]}."
    return (*page(manager_id, message)[:-1], handed_to)


def chat_card(manager_id: str, conversation_id: str | None) -> dict:
    """The note a handover chat was opened with, as a card above the chat."""
    try:
        shift = handover_card(conversation_id, manager_id) if conversation_id else None
    except (SQLAlchemyError, RuntimeError, LookupError, ValueError):
        logger.warning("Could not load the chat's handover", exc_info=True)
        shift = None
    if shift is None or shift.ended_at is None:
        return gr.update(value="", visible=False)
    return gr.update(
        value=(
            '<div class="handover-chat-card"><header>Handover from '
            f"<strong>{escape(shift.manager_name)}</strong> · "
            f"{escape(shift.shift_name)} shift, ended {escape(when(shift.ended_at))}"
            f'</header><p class="handover-note">{escape(shift.note or "")}</p></div>'
        ),
        visible=True,
    )


@dataclass
class HandoverPage:
    status: gr.HTML
    note: gr.Textbox
    open_actions: gr.Row
    closed_actions: gr.Row
    message: gr.Markdown
    past: gr.HTML
    confirmed_empty: gr.State
    handed_to: gr.State
    generate: gr.Button
    save: gr.Button
    end: gr.Button
    start: gr.Button

    def outputs(self) -> list[Any]:
        return [
            self.status,
            self.note,
            self.open_actions,
            self.closed_actions,
            self.end,
            self.message,
            self.past,
            self.confirmed_empty,
            self.start,
            self.handed_to,
        ]


def build(manager: gr.State) -> HandoverPage:
    """Lay out the page and its own events; call inside its gr.Tab."""
    gr.HTML(
        '<div class="settings-heading"><h2>Handover</h2><p>Leave a note for the '
        "next shift, hand over, and read what earlier shifts left.</p></div>",
        apply_default_css=False,
    )
    with gr.Column(elem_classes="setting-card", elem_id="your-shift"):
        gr.HTML('<h3 class="handover-heading">Your shift</h3>', apply_default_css=False)
        status = gr.HTML(status_html(None), apply_default_css=False)
        note = gr.Textbox(
            label="Handover note (editable until you end your shift)",
            lines=7,
            max_lines=20,
            visible=False,
            elem_id="handover-note",
        )
        with gr.Row(visible=False, elem_id="shift-open-actions") as open_actions:
            generate_button = gr.Button("Generate draft", scale=0, min_width=150)
            save_button = gr.Button("Save note", scale=0, min_width=120)
            end_button = gr.Button(
                "End shift",
                variant="primary",
                scale=0,
                min_width=320,
                elem_id="end-shift",
            )
        with gr.Row(elem_id="shift-closed-actions") as closed_actions:
            start_button = gr.Button("Start shift", scale=0, min_width=160)
        message = gr.Markdown(elem_id="handover-message")
    gr.HTML('<h3 class="handover-heading">Past handovers</h3>', apply_default_css=False)
    past = gr.HTML(past_html([]), apply_default_css=False, elem_id="past-handovers")
    confirmed_empty = gr.State(False)
    # Set by a hand over to {"manager", "chat"}; the app switches to them on change.
    handed_to = gr.State(None)
    handover_page = HandoverPage(
        status,
        note,
        open_actions,
        closed_actions,
        message,
        past,
        confirmed_empty,
        handed_to,
        generate_button,
        save_button,
        end_button,
        start_button,
    )
    outputs = handover_page.outputs()
    start_button.click(
        start,
        inputs=manager,
        outputs=outputs,
        concurrency_id="handover",
        concurrency_limit=1,
    )
    generate_button.click(
        generate,
        inputs=manager,
        outputs=[note, message],
        concurrency_id="handover",
        concurrency_limit=1,
    )
    save_button.click(
        save,
        inputs=[manager, note],
        outputs=outputs,
        concurrency_id="handover",
        concurrency_limit=1,
    )
    end_button.click(
        end,
        inputs=[manager, note, confirmed_empty],
        outputs=outputs,
        concurrency_id="handover",
        concurrency_limit=1,
    )
    # Editing after the empty-note warning asks again.
    note.input(lambda: False, outputs=confirmed_empty, queue=False)
    return handover_page


def take_over(handed: dict[str, str] | None) -> tuple:
    """Switch the workspace to whoever took over, in the Assistant tab."""
    if not handed:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()
    manager_id, badge = select_manager(handed["manager"])
    return manager_id, manager_id, badge, gr.update(selected="assistant")
