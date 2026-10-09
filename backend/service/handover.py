"""Draft handover suggestions and supply accepted shift notes to chat."""

from collections.abc import Callable
from datetime import date

from sqlalchemy import Engine

from constants import DEMO_STORE_ID
from domain.memory import SuggestionKind
from queries.dreaming import (
    latest_handover_notes,
    recent_conversations,
    replace_handover_draft,
)
from resources import PROMPTS
from service.review_context import chat_evidence, chat_label, message_day


class HandoverService:
    def __init__(
        self,
        generate: Callable[[str, str], str],
        engine: Engine | None = None,
    ):
        self.generate = generate
        self.engine = engine

    def _prompt(self, name: str) -> str:
        return (PROMPTS / name).read_text(encoding="utf-8")

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
            if any(message_day(message) == day for message in conversation.messages)
        ]
        if not chats:
            return False
        sections = []
        for conversation in chats:
            body = conversation.summary or "\n".join(
                f"{message['who']}: {message['what'][:300]}"
                for message in conversation.messages[-6:]
            )
            sections.append(f"Chat: {chat_label(conversation)}\n{body}")
        note = self.generate(self._prompt("handover_draft.md"), "\n\n".join(sections))
        note = note.strip()
        if not note:
            return False
        replace_handover_draft(
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
                    for item in chat_evidence(conversation)
                ],
            },
            engine=self.engine,
        )
        return True


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
