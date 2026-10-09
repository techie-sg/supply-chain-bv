from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import uuid4

import pytest

from database.models import Conversation, Suggestion
from domain.chat import StoredMessage
from domain.memory import SuggestionKind, SuggestionStatus
from service import dreaming, handover, suggestions
from service.dreaming import DreamingService, ReviewReport
from service.preferences import PreferenceError, PreferenceService
from service.rag import NO_GUIDANCE_ANSWER
from service.scenarios import TIMEZONE
from service.suggestions import json_array

NOW = datetime(2026, 10, 8, 23, 30, tzinfo=TIMEZONE)
TODAY = "2026-10-08T19:00:00+05:30"
SHIFT_START = datetime(2026, 10, 8, 14, 0, tzinfo=TIMEZONE)
YESTERDAY = "2026-10-07T19:00:00+05:30"


def message(
    who: Literal["manager", "assistant"],
    what: str,
    when: str = TODAY,
) -> StoredMessage:
    return {"who": who, "what": what, "when": when}


def chat(messages, *, summary=None, dreamed_to=None, title=None) -> Conversation:
    return Conversation(
        id=uuid4(),
        store_id="DS-1",
        manager_id="karthik",
        messages=messages,
        summary=summary,
        dreamed_to=dreamed_to,
        title=title,
    )


class FakeReviewStore:
    """In-memory stand-in for queries.dreaming."""

    def __init__(self) -> None:
        self.preference_store: Any = None
        self.chats: list[Conversation] = []
        self.suggestions: list[Suggestion] = []
        self.notes: list = []
        self.advanced: list[tuple] = []
        self.shift = SimpleNamespace(
            id=uuid4(),
            store_id="DS-1",
            manager_id="karthik",
            started_at=SHIFT_START,
            ended_at=None,
        )
        self.note: str | None = None

    def to_review(self, engine=None):
        return [
            item
            for item in self.chats
            if (item.dreamed_to if item.dreamed_to is not None else -1)
            < len(item.messages) - 1
        ]

    def recent(self, store_id, manager_id, limit, engine=None):
        return [item for item in self.chats if item.messages][:limit]

    def advance(self, conversation_id, to_index, expected, engine=None):
        self.advanced.append((conversation_id, to_index, expected))
        return True

    def add(self, rows, engine=None):
        for row in rows:
            self.suggestions.append(
                Suggestion(id=uuid4(), status=SuggestionStatus.PENDING, **row),
            )
        return len(rows)

    def list(self, store_id, manager_id, kinds, statuses, limit=50, engine=None):
        return [
            item
            for item in reversed(self.suggestions)
            if item.kind in kinds and item.status in statuses
        ][:limit]

    def get(self, suggestion_id, store_id="DS-1", manager_id="karthik", engine=None):
        return next(
            (
                item
                for item in self.suggestions
                if item.id == suggestion_id
                and item.store_id == store_id
                and item.manager_id == manager_id
            ),
            None,
        )

    def resolve(self, suggestion_id, status, store_id, manager_id, engine=None):
        item = self.get(suggestion_id, store_id, manager_id)
        if item is None or item.status != SuggestionStatus.PENDING:
            return False
        item.status = status
        return True

    def replace_draft(self, row, engine=None):
        for item in self.suggestions:
            if (
                item.kind == SuggestionKind.HANDOVER_DRAFT
                and item.status == SuggestionStatus.PENDING
                and item.payload["shift_id"] == row["payload"]["shift_id"]
            ):
                item.status = SuggestionStatus.DISMISSED
        self.add([row])

    def apply(
        self,
        suggestion_id,
        store_id,
        manager_id,
        expected_payload,
        *,
        preference=None,
        handover=None,
        personalization=None,
        engine=None,
    ):
        item = self.get(suggestion_id, store_id, manager_id)
        if item is None or item.status != SuggestionStatus.PENDING:
            raise LookupError("That suggestion is no longer pending.")
        if preference is not None:
            self.preference_store.save(store_id, manager_id, **preference)
        if handover is not None:
            shift_id, note = handover
            if shift_id != self.shift.id or self.shift.ended_at is not None:
                raise LookupError("That shift has already ended.")
            self.note = note
        item.status = SuggestionStatus.ACCEPTED

    def open_shift(self, manager_id, engine=None):
        shift = self.shift
        return (
            shift if shift.manager_id == manager_id and shift.ended_at is None else None
        )


@pytest.fixture
def review(monkeypatch, preference_store) -> FakeReviewStore:
    fake = FakeReviewStore()
    fake.preference_store = preference_store
    for name, method in {
        "conversations_to_review": fake.to_review,
        "recent_conversations": fake.recent,
        "advance_dreamed_to": fake.advance,
        "add_suggestions": fake.add,
        "list_suggestions": fake.list,
        "get_suggestion": fake.get,
        "resolve_suggestion": fake.resolve,
        "replace_handover_draft": fake.replace_draft,
        "apply_suggestion": fake.apply,
        "open_shift": fake.open_shift,
        "note_for": lambda shift_id, engine=None: fake.note,
    }.items():
        for module in (dreaming, suggestions, handover):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, method)
    return fake


class FakeSummaries:
    def __init__(self) -> None:
        self.folded: list = []

    def fold(self, conversation, keep_recent):
        self.folded.append((conversation.id, keep_recent))
        return True


def service(reply) -> DreamingService:
    def generate(system_prompt, user_message):
        return reply(system_prompt, user_message) if callable(reply) else reply

    return DreamingService(generate, cast(Any, FakeSummaries()), PreferenceService)


def test_json_array_takes_the_first_array_in_a_reply() -> None:
    assert json_array("Sure: [1, 2] and more") == [1, 2]
    assert json_array("none") == [] and json_array("[broken") == []
    assert json_array('{"a": 1}') == []


def test_answer_issues_find_missing_guidance_no_reply_and_pushback(review) -> None:
    conversation = chat(
        [
            message("manager", "Old question"),
            message("assistant", "Old answer"),
            message("manager", "Frozen batching?"),
            message("assistant", NO_GUIDANCE_ANSWER),
            message("manager", "Rain plan?"),
            message("manager", "Hello?"),
            message("assistant", "Call the standby rider."),
            message("manager", "That's wrong, he is off today."),
        ],
        dreamed_to=1,
    )
    seen = []

    def reply(system_prompt, user_message):
        seen.append(user_message)
        return "[3]"

    rows = service(reply).answer_issues(conversation)
    issues = [(row["payload"]["issue"], row["payload"]["question"]) for row in rows]
    assert issues == [
        ("no_guidance", "Frozen batching?"),
        ("unanswered", "Rain plan?"),
        ("pushback", "That's wrong, he is off today."),
    ]
    assert rows[2]["payload"]["answer"] == "Call the standby rider."
    assert rows[0]["evidence"] == [
        {"conversation_id": str(conversation.id), "position": 2},
        {"conversation_id": str(conversation.id), "position": 3},
    ]
    # Only manager replies after `dreamed_to` are checked for pushback.
    assert seen[0].count("Manager:") == 3
    assert "3. Assistant: Call the standby rider." in seen[0]


def test_no_replies_means_no_pushback_call(review) -> None:
    def reply(system_prompt, user_message):
        raise AssertionError("no model call expected")

    assert service(reply).answer_issues(chat([message("manager", "Hi")])) == []


def test_nightly_draft_reads_the_shifts_chats_and_replaces_a_pending_one(
    review,
) -> None:
    review.chats = [
        chat(
            [message("manager", "Rain?")],
            summary="- Standby rider approved.",
            title="Rain",
        ),
        chat([message("manager", "Old", YESTERDAY)], summary="- Old chat."),
    ]
    seen = []

    def reply(system_prompt, user_message):
        seen.append(user_message)
        return "  - Standby rider came in at 19:40.  "

    reviewer = service(reply)
    assert reviewer.handover.nightly_draft("karthik")
    assert reviewer.handover.nightly_draft("karthik")
    assert "Chat: Rain\n- Standby rider approved." in seen[0]
    assert "Old chat" not in seen[0]
    drafts = [item for item in review.suggestions if item.kind == "handover_draft"]
    assert [item.status for item in drafts] == ["dismissed", "pending"]
    assert drafts[-1].payload == {
        "shift_id": str(review.shift.id),
        "started_at": SHIFT_START.isoformat(),
        "note": "- Standby rider came in at 19:40.",
    }
    assert drafts[-1].reason == (
        "Handover note for your shift started 8 Oct, 14:00, drafted from 1 chat."
    )


def test_nightly_draft_skips_shifts_with_a_note_or_without_one_open(review) -> None:
    review.chats = [chat([message("manager", "Rain?")], summary="- Rain.")]
    reviewer = service("- Note")
    assert not reviewer.handover.nightly_draft("ananya")
    review.note = "Already written"
    assert not reviewer.handover.nightly_draft("karthik")
    review.note = None
    review.chats = []
    assert not reviewer.handover.nightly_draft("karthik")


def test_handover_uses_recent_messages_for_an_unsummarized_chat(review) -> None:
    review.chats = [chat([message("manager", "Frozen orders waiting")])]
    seen: list[str] = []

    def reply(system_prompt, user_message):
        seen.append(user_message)
        return "Note"

    service(reply).handover.nightly_draft("karthik")
    assert "manager: Frozen orders waiting" in seen[0]
    # A reply with no bullets still leaves a plain note.
    assert service("   ").handover.nightly_draft("karthik")
    assert review.suggestions[-1].payload["note"] == (
        "- Chats this shift: Frozen orders waiting.\n"
        "- No issues, decisions or follow-ups were recorded."
    )


def test_settings_suggestions_keep_only_valid_supported_new_changes(review) -> None:
    chats = [
        chat([message("manager", f"q{index}")], summary=f"- Frozen orders {index}.")
        for index in range(3)
    ]
    review.chats = chats
    ids = [str(item.id) for item in chats]
    reply = (
        "["
        f'{{"code": "frozen_order_waiting_alert", "enabled": true, "value": 5, '
        f'"reason": "Frozen orders came up often.", "chats": {ids!r}}},'
        f'{{"code": "sla_dip_alert", "enabled": true, "value": 85, "reason": "x", '
        f'"chats": ["{ids[0]}", "made-up"]}},'
        f'{{"code": "rider_shortage_alert", "enabled": true, "value": 9, "reason": "x", '
        f'"chats": {ids!r}}},'
        f'{{"code": "cold_chain_isolation", "enabled": false, "value": false, '
        f'"reason": "x", "chats": {ids!r}}},'
        f'{{"code": "orders_piling_up_alert", "enabled": true, "value": 8, "reason": "x", '
        f'"chats": {ids!r}}},'
        f'{{"code": "speeding_alert", "enabled": true, "value": 1, "reason": "x", '
        f'"chats": {ids!r}}}'
        "]"
    ).replace("'", '"')
    reviewer = service(reply)
    assert reviewer.settings.settings_suggestions("DS-1", "karthik") == 1
    [suggestion] = review.suggestions
    assert suggestion.payload == {
        "code": "frozen_order_waiting_alert",
        "enabled": True,
        "value": 5,
        "options": None,
    }
    assert suggestion.reason == "Frozen orders came up often."
    assert len(suggestion.evidence) == 3
    # The same proposal is not repeated while pending (or after a dismissal).
    assert reviewer.settings.settings_suggestions("DS-1", "karthik") == 0


def test_settings_suggestions_need_enough_summarized_chats(review) -> None:
    review.chats = [chat([message("manager", "q")], summary="- s") for _ in range(2)]

    def reply(system_prompt, user_message):
        raise AssertionError("no model call expected")

    assert service(reply).settings.settings_suggestions("DS-1", "karthik") == 0


def test_run_reviews_each_chat_and_isolates_failures(review) -> None:
    review.chats = [
        chat([message("manager", "Frozen?"), message("assistant", NO_GUIDANCE_ANSWER)]),
        chat([message("manager", "Done")], dreamed_to=0),
    ]

    def reply(system_prompt, user_message):
        if "handover note" in system_prompt:
            raise RuntimeError("provider down")
        return "[]"

    reviewer = service(reply)
    report = reviewer.run(NOW)
    assert (report.chats, report.answer_issues, report.handover_drafts) == (1, 1, 0)
    assert report.failures == ["handover draft"]
    # The review folds the chat it reviews; the handover draft then brings
    # every chat of the open shift up to date before drafting.
    folded = cast(Any, reviewer.summaries).folded
    assert folded[0] == (review.chats[0].id, 0)
    assert sorted(folded[1:]) == sorted((chat.id, 0) for chat in review.chats)
    assert review.advanced == [(review.chats[0].id, 1, None)]
    assert report.text() == (
        "Reviewed 1 chat with new messages: found 1 answer issue, drafted "
        "0 handover notes, suggested 0 settings. Failed: handover draft."
    )
    assert ReviewReport().text() == "No new messages since the last review."


def test_a_failed_answer_review_does_not_advance_the_position(review) -> None:
    review.chats = [chat([message("assistant", "Hi"), message("manager", "No, wrong")])]

    def reply(system_prompt, user_message):
        raise RuntimeError("provider down")

    report = service(reply).run(NOW)
    assert review.advanced == []
    assert "answer issues" in report.failures


def test_accepting_a_setting_saves_it_through_the_settings_path(
    review,
    preference_store,
) -> None:
    review.add(
        [
            {
                "store_id": "DS-1",
                "manager_id": "karthik",
                "kind": "setting",
                "payload": {
                    "code": "sla_dip_alert",
                    "enabled": True,
                    "value": 85,
                    "options": None,
                },
                "reason": "SLA came up often.",
                "evidence": [],
            },
        ],
    )
    suggestion = review.suggestions[0]
    assert suggestions.accept_suggestion(suggestion.id, "DS-1", "karthik") == (
        "Saved. SLA dip: on, below 85%, at all times, at most every 60 min."
    )
    assert suggestion.status == "accepted"
    assert [row.code for row in preference_store.rows] == ["sla_dip_alert"]
    with pytest.raises(LookupError):
        suggestions.accept_suggestion(suggestion.id, "DS-1", "karthik")


def test_accepting_a_handover_draft_fills_the_open_shifts_note(review) -> None:
    payload = {
        "shift_id": str(review.shift.id),
        "started_at": SHIFT_START.isoformat(),
        "note": "Draft",
    }
    row = {
        "store_id": "DS-1",
        "manager_id": "karthik",
        "kind": "handover_draft",
        "payload": payload,
        "reason": "r",
        "evidence": [],
    }
    review.add([row, dict(row)])
    first, second = review.suggestions
    with pytest.raises(PreferenceError, match="empty"):
        suggestions.accept_suggestion(second.id, "DS-1", "karthik", "   ")
    assert suggestions.accept_suggestion(
        first.id,
        "DS-1",
        "karthik",
        "Edited note",
    ).startswith("Saved as your shift's handover note.")
    assert review.note == "Edited note"
    assert review.shift.ended_at is None
    review.shift.ended_at = SHIFT_START
    with pytest.raises(LookupError, match="already ended"):
        suggestions.accept_suggestion(second.id, "DS-1", "karthik")


def test_answer_issues_cannot_be_accepted(review) -> None:
    review.add(
        [
            {
                "store_id": "DS-1",
                "manager_id": "karthik",
                "kind": "answer_issue",
                "payload": {"issue": "pushback"},
                "reason": "r",
                "evidence": [],
            },
        ],
    )
    with pytest.raises(LookupError, match="only reviewed"):
        suggestions.accept_suggestion(review.suggestions[0].id, "DS-1", "karthik")
    assert [item.id for item in suggestions.open_answer_issues()] == [
        review.suggestions[0].id,
    ]
    assert suggestions.pending_suggestions() == []


def test_run_review_uses_the_configured_model(
    review,
    monkeypatch,
) -> None:
    class Model:
        def generate(self, system_prompt, user_message):
            return "[]"

    monkeypatch.setattr(dreaming, "create_llm_service", lambda: Model())
    report = dreaming.run_review()
    assert report.chats == 0 and report.failures == []
