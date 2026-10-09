"""Shifts the manager starts and ends, and the handover note each one leaves.

The shift and its note are stored apart (`shifts`, `handover_notes`).

A shift never follows the clock: the manager starts it (or asks a question with
none open) and ends it, usually by handing over: that also starts the next
manager's shift and opens a chat for them linked to the note. The note is
editable until the shift ends. A handover chat gives the assistant its own note;
other chats get the store's most recently ended one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import requests
import structlog
from sqlalchemy import Engine

from constants import DEMO_STORE_ID, TIMEZONE
from database.models import Conversation, Manager, Shift
from domain.chat import StoredMessage
from domain.managers import ShiftManager
from domain.memory import SuggestionKind
from queries.conversations import start_conversation
from queries.dreaming import recent_conversations, replace_handover_draft
from queries.shifts import (
    end_shift,
    ended_shifts,
    handover_by_note,
    latest_handover,
    latest_shift,
    note_for,
    open_shift,
    save_note,
    start_shift,
)
from resources import PROMPTS
from service.factory import create_llm_service
from service.managers import store_managers
from service.review_context import chat_evidence, chat_label
from service.summaries import SummaryService, summary_service

logger = structlog.stdlib.get_logger(__name__)

HANDOVER_HISTORY_DAYS = 7
HANDOVER_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class ShiftView:
    id: UUID
    manager_id: str
    manager_name: str
    shift_name: str
    started_at: datetime
    ended_at: datetime | None
    note: str | None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


def _view(
    shift: Shift,
    manager: Manager | ShiftManager,
    note: str | None,
) -> ShiftView:
    return ShiftView(
        shift.id,
        shift.manager_id,
        manager.name,
        manager.shift_name,
        shift.started_at,
        shift.ended_at,
        note,
    )


def when(moment: datetime) -> str:
    """For example "9 Oct, 06:02"."""
    local = moment.astimezone(TIMEZONE)
    return f"{local.day} {local.strftime('%b, %H:%M')}"


def shift_chats(shift: Shift, engine: Engine | None = None) -> list[Conversation]:
    """The manager's chats with messages during this shift, newest first."""
    return [
        conversation
        for conversation in recent_conversations(
            shift.store_id,
            shift.manager_id,
            50,
            engine,
        )
        if shift_messages(conversation, shift)
    ]


def shift_messages(conversation: Conversation, shift: Shift) -> list[StoredMessage]:
    """A reopened chat can span shifts; exclude earlier and later messages."""
    return [
        message
        for message in conversation.messages
        if (moment := datetime.fromisoformat(message["when"])) >= shift.started_at
        and (shift.ended_at is None or moment <= shift.ended_at)
    ]


def note_lines(text: str) -> str | None:
    """The reply's bullet lines, so stray prose never becomes the note.

    Markers become "- "; a bullet indented past the others stays one level in.
    """
    found = [
        (len(line) - len(line.lstrip()), line.strip()[1:].strip())
        for line in text.splitlines()
        if line.strip()[:1] in {"-", "*", "•"} and line.strip()[1:].strip()
    ]
    if not found:
        return None
    # Indentation counts from the least-indented bullet, not the reply's margin.
    margin = min(indent for indent, _ in found)
    return "\n".join(
        f"{'  ' if indent > margin else ''}- {item}" for indent, item in found
    )


def fallback_note(chats: list[Conversation]) -> str:
    """A plain note when the model returns no usable bullets."""
    topics = "; ".join(chat_label(conversation) for conversation in chats)
    return (
        f"- Chats this shift: {topics}.\n"
        "- No issues, decisions or follow-ups were recorded."
    )


class HandoverService:
    def __init__(
        self,
        generate: Callable[[str, str], str],
        engine: Engine | None = None,
        summaries: SummaryService | None = None,
    ):
        self.generate = generate
        self.engine = engine
        self.summaries = summaries

    def _summarize(self, chats: list[Conversation]) -> bool:
        """Bring each chat's summary up to date; True if any changed."""
        if self.summaries is None:
            return False
        changed = False
        for conversation in chats:
            try:
                changed = self.summaries.fold(conversation, keep_recent=0) or changed
            except (requests.RequestException, RuntimeError, ValueError):
                # A chat that can't be summarized is drafted from its messages.
                logger.warning(
                    "Could not summarize before drafting",
                    conversation_id=str(conversation.id),
                    exc_info=True,
                )
        return changed

    def draft(self, shift: Shift) -> tuple[str, list[Conversation]] | None:
        """A note drafted from the shift's chats; None only when it has no chats.

        Summaries are brought up to date first. The note is always bullets:
        a reply without any falls back to a plain list of the shift's chats.
        """
        chats = shift_chats(shift, self.engine)
        if not chats:
            return None
        if self._summarize(chats):
            chats = shift_chats(shift, self.engine)
        sections = []
        decisions: list[str] = []
        for conversation in chats:
            messages = shift_messages(conversation, shift)
            # A full-chat summary may include decisions from a different shift.
            summary = (
                conversation.summary
                if len(messages) == len(conversation.messages)
                else None
            )
            body = summary or "\n".join(
                f"{message['who']}: {message['what'][:300]}"
                for message in messages[-6:]
            )
            sections.append(f"Chat: {chat_label(conversation)}\n{body}")
            decisions.extend(
                f"{message['when']}: {message['what']}"
                for message in messages
                if message["who"] == "assistant"
                and "trace" not in message
                and message["what"].startswith(
                    ("Saved. ", "Cancelled. Nothing was changed"),
                )
            )
        sections.append(
            "Application-recorded setting confirmations/cancellations (authoritative; "
            "these override conflicting descriptions in chat summaries):\n"
            + ("\n".join(decisions) if decisions else "None recorded."),
        )
        reply = self.generate(
            (PROMPTS / "handover_draft.md").read_text(encoding="utf-8"),
            "\n\n".join(sections),
        )
        return note_lines(reply) or fallback_note(chats), chats

    def nightly_draft(self, manager_id: str) -> bool:
        """Fallback: suggest a note for an open shift that has chats and no note."""
        shift = open_shift(manager_id, self.engine)
        if shift is None or (note_for(shift.id, self.engine) or "").strip():
            return False
        drafted = self.draft(shift)
        if drafted is None:
            return False
        note, chats = drafted
        replace_handover_draft(
            {
                "store_id": shift.store_id,
                "manager_id": shift.manager_id,
                "kind": SuggestionKind.HANDOVER_DRAFT,
                "payload": {
                    "shift_id": str(shift.id),
                    "started_at": shift.started_at.isoformat(),
                    "note": note,
                },
                "reason": (
                    f"Handover note for your shift started {when(shift.started_at)}, "
                    f"drafted from {len(chats)} chat{'s' if len(chats) != 1 else ''}."
                ),
                "evidence": [
                    item
                    for conversation in chats
                    for item in chat_evidence(conversation)
                ],
            },
            engine=self.engine,
        )
        return True


# Entry points for the Handover page -------------------------------------------


def _manager(manager_id: str, store_id: str = DEMO_STORE_ID) -> ShiftManager:
    for manager in store_managers(store_id):
        if manager.manager_id == manager_id:
            return manager
    raise LookupError("That manager is not available.")


def current_shift(manager_id: str, engine: Engine | None = None) -> ShiftView | None:
    """The manager's open shift, else their last one; None if they never worked."""
    shift = latest_shift(manager_id, engine)
    if shift is None:
        return None
    return _view(shift, _manager(manager_id), note_for(shift.id, engine))


def begin_shift(
    manager_id: str,
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> ShiftView:
    shift = start_shift(store_id, manager_id, engine)
    logger.info("Shift started", manager_id=manager_id)
    return _view(shift, _manager(manager_id, store_id), note_for(shift.id, engine))


def ensure_shift(
    manager_id: str,
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> None:
    """A question with no open shift starts one."""
    if open_shift(manager_id, engine) is None:
        begin_shift(manager_id, store_id, engine)


def _open(manager_id: str, engine: Engine | None) -> Shift:
    shift = open_shift(manager_id, engine)
    if shift is None:
        raise LookupError("You have no open shift. Start one first.")
    return shift


def generate_note(manager_id: str, engine: Engine | None = None) -> str | None:
    """A draft for the open shift, not saved; None when the shift has no chats."""
    service = HandoverService(
        lambda system, user: create_llm_service().generate(
            system_prompt=system,
            user_message=user,
        ),
        engine,
        summaries=summary_service(),
    )
    drafted = service.draft(_open(manager_id, engine))
    return None if drafted is None else drafted[0]


def save_shift_note(manager_id: str, note: str, engine: Engine | None = None) -> None:
    shift = _open(manager_id, engine)
    if not save_note(shift.id, manager_id, note.strip(), engine):
        raise LookupError("That shift has already ended.")


def finish_shift(
    manager_id: str,
    note: str,
    engine: Engine | None = None,
) -> datetime:
    return _end(manager_id, note, engine)[0]


def _end(manager_id: str, note: str, engine: Engine | None) -> tuple[datetime, UUID]:
    shift = _open(manager_id, engine)
    ended = end_shift(shift.id, manager_id, note.strip(), engine)
    if ended is None:
        raise LookupError("That shift has already ended.")
    logger.info("Shift ended", manager_id=manager_id, with_note=bool(note.strip()))
    return ended


@dataclass(frozen=True)
class HandOver:
    manager_id: str  # who took over
    conversation_id: UUID  # the chat opened for them
    ended_at: datetime


def hand_over(
    manager_id: str,
    note: str,
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> HandOver:
    """End the shift, start the next one, and open their chat on the note.

    A blank note still ends the shift; the new chat then has no note.
    """
    following = next_manager(manager_id, store_id)
    if following is None:
        raise LookupError("There is no next shift to hand over to.")
    ended, note_id = _end(manager_id, note, engine)
    start_shift(store_id, following.manager_id, engine)
    conversation = start_conversation(
        store_id,
        following.manager_id,
        engine,
        handover_note_id=note_id if note.strip() else None,
    )
    logger.info("Handed over", manager_id=manager_id, to=following.manager_id)
    return HandOver(following.manager_id, conversation.id, ended)


def next_manager(
    manager_id: str,
    store_id: str = DEMO_STORE_ID,
) -> ShiftManager | None:
    """Whoever works the store's next shift by start time, wrapping round."""
    managers = store_managers(store_id)
    ids = [manager.manager_id for manager in managers]
    if manager_id not in ids or len(managers) < 2:
        return None
    return managers[(ids.index(manager_id) + 1) % len(managers)]


def past_handovers(
    store_id: str = DEMO_STORE_ID,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> list[ShiftView]:
    """The store's shifts ended in the last week, newest first."""
    since = (now or datetime.now(TIMEZONE)) - timedelta(days=HANDOVER_HISTORY_DAYS)
    return [
        _view(shift, manager, note)
        for shift, manager, note in ended_shifts(
            store_id,
            since,
            HANDOVER_HISTORY_LIMIT,
            engine,
        )
    ]


def handover_block(
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> str | None:
    """The note of the store's last ended shift with one, for the model."""
    found = latest_handover(store_id, engine)
    return None if found is None else _block(*found)


def chat_handover(
    note_id: UUID | None,
    engine: Engine | None = None,
) -> ShiftView | None:
    """The handover a chat was opened with, if any."""
    found = handover_by_note(note_id, engine) if note_id else None
    return None if found is None else _view(*found)


def chat_handover_block(
    note_id: UUID | None,
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> str | None:
    """A handover chat's own note; any other chat gets the store's latest."""
    found = handover_by_note(note_id, engine) if note_id else None
    return handover_block(store_id, engine) if found is None else _block(*found)


def _block(shift: Shift, manager: Manager, note: str) -> str:
    assert shift.ended_at is not None
    return (
        f"Handover from {manager.name} ({manager.shift_name} shift, ended "
        f"{when(shift.ended_at)}):\n{note}"
    )
