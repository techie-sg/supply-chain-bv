from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import gradio as gr
import pytest
from sqlalchemy.exc import OperationalError

from constants import TIMEZONE
from database.models import Conversation
from domain.managers import ShiftManager
from service import handover
from ui import handover as handover_ui

MORNING = datetime(2026, 10, 9, 6, 2, tzinfo=TIMEZONE)
ENDED = datetime(2026, 10, 9, 13, 55, tzinfo=TIMEZONE)
MANAGERS = [
    ShiftManager("ananya", "Ananya Rao", "SHIFT-MOR", "Morning", "06:00", "14:00"),
    ShiftManager("karthik", "Karthik Reddy", "SHIFT-EVE", "Evening", "14:00", "22:00"),
    ShiftManager("imran", "Imran Shaikh", "SHIFT-NGT", "Night", "22:00", "06:00"),
]


class FakeShifts:
    """In-memory stand-in for queries.shifts."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.chats: list[Conversation] = []
        self.opened: list[tuple[str, object]] = []
        self.posted: list[dict] = []
        self.titles: list[str] = []
        self.summaries: Any = None

    def open(self, manager_id, engine=None):
        return next(
            (
                row
                for row in self.rows
                if row.manager_id == manager_id and row.ended_at is None
            ),
            None,
        )

    def latest(self, manager_id, engine=None):
        mine = [row for row in self.rows if row.manager_id == manager_id]
        return self.open(manager_id) or (mine[-1] if mine else None)

    def start(self, store_id, manager_id, engine=None):
        row = self.open(manager_id)
        if row is None:
            row = SimpleNamespace(
                id=uuid4(),
                store_id=store_id,
                manager_id=manager_id,
                started_at=MORNING,
                ended_at=None,
                handover_note=None,
            )
            self.rows.append(row)
        return row

    def save(self, shift_id, manager_id, note, engine=None):
        row = self.open(manager_id)
        if row is None or row.id != shift_id:
            return False
        row.handover_note = note
        return True

    def end(self, shift_id, manager_id, note, engine=None):
        if not self.save(shift_id, manager_id, note):
            return None
        row = self.open(manager_id)
        row.ended_at = ENDED
        return ENDED, row.id  # the fake uses the shift's id as its note's id

    def by_note(self, note_id, engine=None):
        managers = {manager.manager_id: manager for manager in MANAGERS}
        row = next((row for row in self.rows if row.id == note_id), None)
        if row is None or row.handover_note is None:
            return None
        return row, managers[row.manager_id], row.handover_note

    def start_conversation(
        self,
        store_id,
        manager_id,
        engine=None,
        handover_note_id=None,
    ):
        self.opened.append((manager_id, handover_note_id))
        return SimpleNamespace(id=uuid4())

    def append_message(self, conversation_id, message, engine=None):
        self.posted.append(message)

    def set_title(self, conversation_id, title, engine=None):
        self.titles.append(title)
        return True

    def note_for(self, shift_id, engine=None):
        row = next((row for row in self.rows if row.id == shift_id), None)
        return None if row is None else row.handover_note

    def ended(self, store_id, since, limit, engine=None):
        managers = {manager.manager_id: manager for manager in MANAGERS}
        return [
            (row, managers[row.manager_id], row.handover_note)
            for row in reversed(self.rows)
            if row.ended_at is not None and row.ended_at >= since
        ][:limit]

    def latest_handover(self, store_id, engine=None):
        rows = [
            found
            for found in self.ended(store_id, datetime.min.replace(tzinfo=TIMEZONE), 50)
            if (found[2] or "").strip()
        ]
        return rows[0] if rows else None

    def recent(self, store_id, manager_id, limit, engine=None):
        return [chat for chat in self.chats if chat.manager_id == manager_id]


class FakeSummaries:
    """Summarizes a chat by naming its first question."""

    def __init__(self) -> None:
        self.folded: list = []

    def fold(self, conversation, keep_recent):
        self.folded.append(conversation.id)
        if conversation.summary:
            return False
        conversation.summary = f"- Asked: {conversation.messages[0]['what']}"
        return True


@pytest.fixture
def shifts(monkeypatch) -> FakeShifts:
    fake = FakeShifts()
    fake.summaries = FakeSummaries()
    monkeypatch.setattr(handover, "summary_service", lambda: fake.summaries)
    for name, method in {
        "open_shift": fake.open,
        "latest_shift": fake.latest,
        "start_shift": fake.start,
        "save_note": fake.save,
        "end_shift": fake.end,
        "ended_shifts": fake.ended,
        "latest_handover": fake.latest_handover,
        "note_for": fake.note_for,
        "handover_by_note": fake.by_note,
        "start_conversation": fake.start_conversation,
        "append_message": fake.append_message,
        "set_title": fake.set_title,
        "recent_conversations": fake.recent,
    }.items():
        monkeypatch.setattr(handover, name, method)
    monkeypatch.setattr(handover, "store_managers", lambda store_id=None: MANAGERS)
    return fake


def chat(when: str, summary: str | None = None) -> Conversation:
    return Conversation(
        id=uuid4(),
        store_id="DS-BLR-014",
        manager_id="ananya",
        messages=[{"who": "manager", "what": "Rain backlog?", "when": when}],
        summary=summary,
        title="Rain backlog",
    )


def model(reply: str, seen: list | None = None):
    class Model:
        def generate(self, system_prompt, user_message):
            if seen is not None:
                seen.append(user_message)
            return reply

    return lambda: Model()


def test_a_shift_runs_from_start_to_handover(shifts, monkeypatch) -> None:
    assert handover.current_shift("ananya") is None
    with pytest.raises(LookupError, match="Start one first"):
        handover.save_shift_note("ananya", "Early")
    handover.ensure_shift("ananya")
    handover.ensure_shift("ananya")
    assert len(shifts.rows) == 1
    shifts.chats = [
        chat("2026-10-09T10:30:00+05:30", "- Standby rider approved."),
        chat("2026-10-08T19:00:00+05:30", "- Yesterday's chat."),
    ]
    seen: list[str] = []
    monkeypatch.setattr(handover, "create_llm_service", model(" - Rain. ", seen))
    assert handover.generate_note("ananya") == "- Rain."
    assert "Standby rider approved" in seen[0] and "Yesterday" not in seen[0]
    handover.save_shift_note("ananya", "  Draft  ")
    current = handover.current_shift("ananya")
    assert current is not None and current.is_open and current.note == "Draft"
    assert handover.finish_shift("ananya", "Final note") == ENDED
    with pytest.raises(LookupError):
        handover.finish_shift("ananya", "Again")
    assert handover.handover_block() == (
        "Handover from Ananya Rao (Morning shift, ended 9 Oct, 13:55):\nFinal note"
    )
    [past] = handover.past_handovers(now=ENDED)
    assert (past.manager_name, past.shift_name, past.note) == (
        "Ananya Rao",
        "Morning",
        "Final note",
    )
    assert handover.past_handovers(now=datetime(2026, 10, 20, tzinfo=TIMEZONE)) == []


def test_drafting_needs_chats_in_the_shift(shifts, monkeypatch) -> None:
    handover.begin_shift("ananya")
    monkeypatch.setattr(handover, "create_llm_service", model("- Note"))
    assert handover.generate_note("ananya") is None
    shifts.chats = [chat("2026-10-09T10:30:00+05:30")]
    seen: list[str] = []
    monkeypatch.setattr(
        handover,
        "create_llm_service",
        model("I don't have the summaries, could you provide them?", seen),
    )
    # The chat is summarized first, and a reply without bullets still gives a note.
    assert handover.generate_note("ananya") == (
        "- Chats this shift: Rain backlog.\n"
        "- No issues, decisions or follow-ups were recorded."
    )
    assert shifts.summaries.folded == [shifts.chats[0].id]
    assert "- Asked: Rain backlog?" in seen[0]


def test_draft_preserves_application_confirmations_separately_from_model_claims(shifts):
    handover.ensure_shift("ananya")
    conversation = chat("2026-10-09T10:30:00+05:30", "No changes approved.")
    conversation.messages.extend(
        [
            {
                "who": "assistant",
                "what": "Saved. Surge incentive cap per shift: ₹300.",
                "when": "2026-10-09T10:31:00+05:30",
            },
            {
                "who": "assistant",
                "what": "Saved. Surge incentive cap per shift: ₹400.",
                "when": "2026-10-09T10:32:00+05:30",
                "trace": {"tools": [], "preferences": []},
            },
        ],
    )
    shifts.chats = [conversation]
    seen = []
    service = handover.HandoverService(
        lambda prompt, message: seen.append(message) or "- Cap confirmed at ₹300.",
    )
    service.draft(shifts.rows[0])
    recorded = seen[0].split("Application-recorded", 1)[1]
    assert "₹300" in recorded and "₹400" not in recorded


def test_a_failed_summary_drafts_from_the_messages(shifts, monkeypatch) -> None:
    handover.begin_shift("ananya")
    shifts.chats = [chat("2026-10-09T10:30:00+05:30")]

    def fail(conversation, keep_recent):
        raise RuntimeError("provider down")

    shifts.summaries.fold = fail
    seen: list[str] = []
    monkeypatch.setattr(
        handover,
        "create_llm_service",
        model("Note:\n* Rain backlog.\n  • Standby rider approved.", seen),
    )
    assert handover.generate_note("ananya") == (
        "- Rain backlog.\n  - Standby rider approved."
    )
    assert "manager: Rain backlog?" in seen[0]


def test_reopened_chat_draft_uses_only_messages_from_the_shift(shifts) -> None:
    handover.begin_shift("ananya")
    shift = shifts.rows[0]
    shift.ended_at = ENDED
    conversation = chat("2026-10-08T10:00:00+05:30", "Prior shift cap ₹100.")
    conversation.messages.extend(
        [
            {
                "who": "assistant",
                "what": "Saved. Cap ₹300.",
                "when": "2026-10-09T10:00:00+05:30",
            },
            {
                "who": "assistant",
                "what": "Saved. Next shift cap ₹400.",
                "when": "2026-10-09T15:00:00+05:30",
            },
        ],
    )
    future_chat = chat("2026-10-09T16:00:00+05:30", "Future-only chat.")
    shifts.chats = [conversation, future_chat]
    seen: list[str] = []

    def generate(prompt: str, message: str) -> str:
        seen.append(message)
        return "- Cap ₹300."

    service = handover.HandoverService(generate)
    result = service.draft(shift)
    assert result is not None and result[1] == [conversation]
    assert "₹300" in seen[0]
    assert all(text not in seen[0] for text in ("₹100", "₹400", "Future-only"))
    assert len(conversation.messages) == 3  # Stored chat history is unchanged.


def test_next_manager_follows_shift_order_and_wraps(shifts) -> None:
    following = [handover.next_manager(item) for item in ("ananya", "imran")]
    assert [item.manager_id if item else None for item in following] == [
        "karthik",
        "ananya",
    ]
    assert handover.next_manager("nobody") is None
    assert handover.handover_block() is None
    with pytest.raises(LookupError):
        handover.begin_shift("nobody")


def test_page_shows_each_state_of_the_shift(shifts, monkeypatch) -> None:
    status, note, open_row, closed_row, end, message, past, confirmed, start, handed = (
        handover_ui.page("ananya")
    )
    assert start["value"] == "Start shift" and handed == gr.skip()
    assert "No shift yet" in status
    assert note["visible"] is False and open_row["visible"] is False
    assert closed_row["visible"] is True
    assert end["value"] == "End shift and hand over to Karthik Reddy (Evening) →"
    assert "No handovers" in past and confirmed is False

    status, note, open_row, *_, message, _, _, _, _ = handover_ui.start("ananya")
    assert "Morning shift</strong> · started 9 Oct, 06:02" in status
    assert ">Open<" in status and message == "Shift started."
    assert note["interactive"] is True and open_row["visible"] is True

    assert handover_ui.generate("ananya")[1].startswith("No chats in this shift")
    shifts.chats = [chat("2026-10-09T10:30:00+05:30", "- Rain.")]
    monkeypatch.setattr(handover, "create_llm_service", model("- Rain backlog."))
    assert handover_ui.generate("ananya") == (
        "- Rain backlog.",
        "Draft ready. Edit it, then save or end your shift.",
    )
    saved = handover_ui.save("ananya", "- Rain backlog.")
    assert saved[1]["value"] == "- Rain backlog."
    assert saved[5] == "Note saved. Your shift is still open."

    ended = handover_ui.end("ananya", "- Rain backlog, standby rider approved.", False)
    status, note, open_row, closed_row, _, message, past, _, start, handed = ended
    assert start["value"] == "Start a new shift"
    assert "9 Oct, 06:02 → 13:55" in status and ">Ended<" in status
    assert note["interactive"] is False and note["label"] == "Handover note (final)"
    assert closed_row["visible"] is True and open_row["visible"] is False
    assert message == "Shift ended at 13:55."
    assert "Ananya Rao" in past and "standby rider approved" in past
    # Karthik's shift starts, and a chat opens for him on Ananya's note.
    ananya = shifts.rows[0]
    assert handed["manager"] == "karthik"
    assert shifts.open("karthik") is not None
    assert shifts.opened == [("karthik", ananya.id)]
    view = handover.chat_handover(ananya.id)
    assert view is not None and view.manager_name == "Ananya Rao"
    assert handover.chat_handover(None) is None
    block = handover.chat_handover_block(ananya.id)
    assert block is not None
    assert block.startswith("Handover from Ananya Rao (Morning shift")


def test_take_over_switches_to_the_next_manager(monkeypatch) -> None:
    monkeypatch.setattr(
        handover_ui,
        "select_manager",
        lambda manager_id: (manager_id, f"<badge {manager_id}>"),
    )
    assert handover_ui.take_over({"manager": "karthik", "chat": "c1"}) == (
        "karthik",
        "karthik",
        "<badge karthik>",
        gr.update(selected="assistant"),
    )
    assert handover_ui.take_over(None) == (gr.skip(),) * 4


def test_chat_card_shows_the_note_the_chat_opened_with(monkeypatch) -> None:
    view = handover.ShiftView(
        uuid4(),
        "ananya",
        "Ananya Rao",
        "Morning",
        MORNING,
        ENDED,
        "- Z3 <slow>",
    )
    monkeypatch.setattr(
        handover_ui,
        "handover_card",
        lambda conversation_id, manager_id: view,
    )
    card = handover_ui.chat_card("karthik", "c1")
    assert card["visible"] is True
    assert "Handover from <strong>Ananya Rao</strong> · Morning shift" in card["value"]
    assert "- Z3 &lt;slow&gt;" in card["value"]
    assert handover_ui.chat_card("karthik", None)["visible"] is False

    def down(conversation_id, manager_id):
        raise OperationalError("select", {}, Exception("database down"))

    monkeypatch.setattr(handover_ui, "handover_card", down)
    assert handover_ui.chat_card("karthik", "c1")["visible"] is False


def test_a_store_with_one_manager_just_ends_the_shift(shifts, monkeypatch) -> None:
    monkeypatch.setattr(handover, "store_managers", lambda store_id=None: MANAGERS[:1])
    handover.begin_shift("ananya")
    result = handover_ui.end("ananya", "Note", False)
    assert result[4]["value"] == "End shift" and result[9] == gr.skip()
    assert shifts.opened == []
    with pytest.raises(LookupError):
        handover.hand_over("ananya", "Note")


def test_ending_with_an_empty_note_asks_again(shifts) -> None:
    handover.begin_shift("karthik")
    first = handover_ui.end("karthik", "  ", False)
    assert first[5] == handover_ui.EMPTY_NOTE and first[7] is True
    assert shifts.rows[0].ended_at is None
    second = handover_ui.end("karthik", "", True)
    assert second[5] == "Shift ended at 13:55."
    assert "No note left." in second[6]
    # The next manager's chat opens without a note.
    assert second[9]["manager"] == "imran" and shifts.opened == [("imran", None)]


def test_page_reports_problems_without_failing(shifts, monkeypatch) -> None:
    assert handover_ui.save("ananya", "x")[5].startswith("You have no open shift")
    assert handover_ui.end("ananya", "x", False)[5].startswith("You have no open")
    assert handover_ui.generate("ananya")[1].startswith("You have no open shift")

    def down(*args, **kwargs):
        raise OperationalError("select", {}, Exception("database down"))

    for name in ("latest_shift", "start_shift", "save_note", "open_shift"):
        monkeypatch.setattr(handover, name, down)
    assert handover_ui.page("ananya")[5] == handover_ui.UNAVAILABLE
    assert handover_ui.start("ananya")[5] == handover_ui.UNAVAILABLE
    assert handover_ui.save("ananya", "x")[5] == handover_ui.UNAVAILABLE
    assert handover_ui.end("ananya", "x", False)[5] == handover_ui.UNAVAILABLE
    assert handover_ui.generate("ananya")[1].startswith("Could not draft")


def test_handing_over_opens_a_titled_chat_that_starts_with_the_note(shifts) -> None:
    handover.ensure_shift("ananya")
    done = handover.hand_over("ananya", "  - Rider R3 off sick.\n- Frozen stock low.  ")
    assert done.manager_id == "karthik"
    [(opened_for, linked)] = shifts.opened
    assert opened_for == "karthik" and linked is not None
    [first] = shifts.posted
    assert first["who"] == "assistant"
    assert first["what"] == (
        "**Handover from Ananya Rao** · Morning shift, ended 9 Oct, 13:55\n\n"
        "- Rider R3 off sick.\n- Frozen stock low.\n\n"
        "Ask me about anything in this handover."
    )
    # A chat with a message is listed in the next manager's chats.
    assert shifts.titles == ["Handover notes · 9 Oct · from Ananya Rao"]


def test_handing_over_without_a_note_still_opens_a_chat_that_says_so(shifts) -> None:
    handover.ensure_shift("ananya")
    handover.hand_over("ananya", "   ")
    [(_, linked)] = shifts.opened
    assert linked is None
    [first] = shifts.posted
    assert first["what"].endswith("No handover note was left for this shift.")
    assert shifts.titles == ["Handover notes · 9 Oct · from Ananya Rao"]
