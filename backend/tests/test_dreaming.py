from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from database.models import Conversation, Suggestion
from domain.memory import SuggestionKind, SuggestionStatus
from service import dreaming
from service.dreaming import DreamingService, ReviewReport, json_array
from service.preferences import PreferenceError, PreferenceService
from service.rag import NO_GUIDANCE_ANSWER
from service.scenarios import TIMEZONE

NOW = datetime(2026, 10, 8, 23, 30, tzinfo=TIMEZONE)
TODAY = "2026-10-08T19:00:00+05:30"
YESTERDAY = "2026-10-07T19:00:00+05:30"


def message(who: str, what: str, when: str = TODAY) -> dict[str, str]:
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
        self.chats: list[Conversation] = []
        self.suggestions: list[Suggestion] = []
        self.notes: list = []
        self.advanced: list[tuple] = []
        self.managers = [SimpleNamespace(store_id="DS-1", manager_id="karthik")]
        self.digests: dict[str, SimpleNamespace] = {}
        self.saved_digests: list[tuple] = []

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

    def get(self, suggestion_id, engine=None):
        return next(
            (item for item in self.suggestions if item.id == suggestion_id),
            None,
        )

    def resolve(self, suggestion_id, status, engine=None):
        item = self.get(suggestion_id)
        if item is None or item.status != SuggestionStatus.PENDING:
            return False
        item.status = status
        return True

    def dismiss_drafts(self, store_id, manager_id, shift, engine=None):
        count = 0
        for item in self.suggestions:
            if (
                item.kind == SuggestionKind.HANDOVER_DRAFT
                and item.status == SuggestionStatus.PENDING
                and item.payload["shift"] == shift
            ):
                item.status = SuggestionStatus.DISMISSED
                count += 1
        return count

    def save_note(self, store_id, manager_id, shift, note, engine=None):
        row = SimpleNamespace(shift=shift, note=note)
        self.notes.append(row)
        return row

    def latest_notes(self, store_id, engine=None):
        return self.notes

    def all_managers(self, engine=None):
        return self.managers

    def get_digest(self, manager_id, engine=None):
        return self.digests.get(manager_id)

    def save_digest(self, store_id, manager_id, digest, sources, engine=None):
        self.saved_digests.append((manager_id, digest, sources))
        self.digests[manager_id] = SimpleNamespace(
            digest=digest,
            sources=sources,
            built_at=NOW,
        )


@pytest.fixture
def review(monkeypatch, preference_store) -> FakeReviewStore:
    fake = FakeReviewStore()
    for name, method in {
        "conversations_to_review": fake.to_review,
        "recent_conversations": fake.recent,
        "advance_dreamed_to": fake.advance,
        "add_suggestions": fake.add,
        "list_suggestions": fake.list,
        "get_suggestion": fake.get,
        "resolve_suggestion": fake.resolve,
        "dismiss_pending_drafts": fake.dismiss_drafts,
        "save_handover_note": fake.save_note,
        "latest_handover_notes": fake.latest_notes,
        "all_managers": fake.all_managers,
        "get_digest": fake.get_digest,
        "save_digest": fake.save_digest,
    }.items():
        monkeypatch.setattr(dreaming, name, method)
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


def test_handover_draft_uses_todays_chats_and_replaces_a_pending_one(review) -> None:
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
    assert reviewer.handover_draft("DS-1", "karthik", NOW.date())
    assert reviewer.handover_draft("DS-1", "karthik", NOW.date())
    assert "Chat: Rain\n- Standby rider approved." in seen[0]
    assert "Old chat" not in seen[0]
    drafts = [item for item in review.suggestions if item.kind == "handover_draft"]
    assert [item.status for item in drafts] == ["dismissed", "pending"]
    assert drafts[-1].payload == {
        "shift": "2026-10-08",
        "note": "- Standby rider came in at 19:40.",
    }
    assert drafts[-1].reason == "Handover note for 8 Oct, drafted from 1 chat."
    assert not reviewer.handover_draft("DS-1", "karthik", date(2026, 10, 9))


def test_handover_uses_recent_messages_for_an_unsummarized_chat(review) -> None:
    review.chats = [chat([message("manager", "Frozen orders waiting")])]
    seen: list[str] = []

    def reply(system_prompt, user_message):
        seen.append(user_message)
        return "Note"

    service(reply).handover_draft(
        "DS-1",
        "karthik",
        NOW.date(),
    )
    assert "manager: Frozen orders waiting" in seen[0]
    assert not service("   ").handover_draft("DS-1", "karthik", NOW.date())


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
    assert reviewer.settings_suggestions("DS-1", "karthik") == 1
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
    assert reviewer.settings_suggestions("DS-1", "karthik") == 0


def test_settings_suggestions_need_enough_summarized_chats(review) -> None:
    review.chats = [chat([message("manager", "q")], summary="- s") for _ in range(2)]

    def reply(system_prompt, user_message):
        raise AssertionError("no model call expected")

    assert service(reply).settings_suggestions("DS-1", "karthik") == 0


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
    assert cast(Any, reviewer.summaries).folded == [(review.chats[0].id, 0)]
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
    assert dreaming.accept_suggestion(suggestion.id) == (
        "Saved. SLA dip: on, below 85%, at all times, at most every 60 min."
    )
    assert suggestion.status == "accepted"
    assert [row.code for row in preference_store.rows] == ["sla_dip_alert"]
    with pytest.raises(LookupError):
        dreaming.accept_suggestion(suggestion.id)


def test_accepting_a_handover_draft_saves_the_edited_note(review) -> None:
    review.add(
        [
            {
                "store_id": "DS-1",
                "manager_id": "karthik",
                "kind": "handover_draft",
                "payload": {"shift": "2026-10-08", "note": "Draft"},
                "reason": "r",
                "evidence": [],
            },
            {
                "store_id": "DS-1",
                "manager_id": "karthik",
                "kind": "handover_draft",
                "payload": {"shift": "2026-10-08", "note": "Draft"},
                "reason": "r",
                "evidence": [],
            },
        ],
    )
    first, second = review.suggestions
    with pytest.raises(PreferenceError, match="empty"):
        dreaming.accept_suggestion(second.id, "   ")
    assert dreaming.accept_suggestion(first.id, "Edited note") == (
        "Saved as the handover note for the next shift."
    )
    assert [(note.shift, note.note) for note in review.notes] == [
        (date(2026, 10, 8), "Edited note"),
    ]
    assert dreaming.dismiss_suggestion(second.id)
    assert dreaming.handover_block("DS-1") == "Handover from 8 Oct:\nEdited note"


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
        dreaming.accept_suggestion(review.suggestions[0].id)
    assert [item.id for item in dreaming.open_answer_issues()] == [
        review.suggestions[0].id,
    ]
    assert dreaming.pending_suggestions() == []


def test_no_notes_means_no_handover_block(review) -> None:
    assert dreaming.handover_block("DS-1") is None


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


# Memory digest ---------------------------------------------------------------

OLD = "2026-09-30T19:00:00+05:30"


def summarized(when: str, summary: str, covers_to: int = 1) -> Conversation:
    conversation = chat(
        [message("manager", "Q", when), message("assistant", "A", when)],
        summary=summary,
        title="Rain backlog",
    )
    conversation.summary_covers_to = covers_to
    return conversation


def test_digest_reads_last_week_summaries_and_keeps_eight_bullets(review) -> None:
    recent = summarized(TODAY, "Radius shrink deferred.")
    review.chats = [
        recent,
        chat([message("manager", "Unsummarized")]),
        summarized(OLD, "Old"),
    ]
    seen = []

    def reply(system_prompt, user_message):
        seen.append((system_prompt, user_message))
        return "Here you go:\n" + "\n".join(f"* item {n}" for n in range(10))

    assert service(reply).memory_digest("DS-1", "karthik", NOW)
    [(system_prompt, user_message)] = seen
    assert "at most 8 short bullets" in system_prompt
    assert user_message == "Chat on 8 Oct: Rain backlog\nRadius shrink deferred."
    [(manager_id, digest, sources)] = review.saved_digests
    assert manager_id == "karthik"
    assert digest == "\n".join(f"- item {n}" for n in range(8))
    assert sources == [{"conversation_id": str(recent.id), "covers_to": 1}]


def test_digest_skips_the_model_when_its_sources_are_unchanged(review) -> None:
    review.chats = [summarized(TODAY, "Rain backlog.")]
    calls = []

    def reply(system_prompt, user_message):
        calls.append(user_message)
        return "- Rain backlog on 8 Oct."

    reviewer = service(reply)
    assert reviewer.memory_digest("DS-1", "karthik", NOW)
    assert not reviewer.memory_digest("DS-1", "karthik", NOW)
    assert len(calls) == 1
    review.chats[0].summary_covers_to = 3
    assert reviewer.memory_digest("DS-1", "karthik", NOW)
    assert len(calls) == 2


def test_digest_clears_when_no_recent_chats_remain(review) -> None:
    review.digests["karthik"] = SimpleNamespace(
        digest="- Old item",
        sources=[{"conversation_id": "x", "covers_to": 1}],
        built_at=NOW,
    )
    review.chats = [summarized(OLD, "Old")]
    assert service("unused").memory_digest("DS-1", "karthik", NOW)
    assert review.saved_digests == [("karthik", None, [])]
    assert dreaming.memory_block("karthik") is None


def test_none_reply_saves_an_empty_digest(review) -> None:
    review.chats = [summarized(TODAY, "Greeting only.")]
    assert service("NONE").memory_digest("DS-1", "karthik", NOW)
    assert review.saved_digests[0][1] is None


def test_run_rebuilds_digests_and_isolates_their_failures(review) -> None:
    review.chats = [summarized(TODAY, "Rain backlog.")]
    review.chats[0].dreamed_to = 1
    report = service("- Rain backlog on 8 Oct.").run(NOW)
    assert report.digests == 1 and report.failures == []
    assert report.text() == (
        "No new messages since the last review. Updated 1 memory digest."
    )
    review.chats[0].summary_covers_to = 5

    def failing(system_prompt, user_message):
        raise RuntimeError("provider down")

    report = service(failing).run(NOW)
    assert report.digests == 0 and report.failures == ["memory digest"]
    assert review.digests["karthik"].digest == "- Rain backlog on 8 Oct."


def test_memory_block_labels_the_digest_as_earlier_chats(review) -> None:
    review.digests["karthik"] = SimpleNamespace(
        digest="- Z3 floods in heavy rain (said 6 Oct).",
        sources=[],
        built_at=NOW,
    )
    assert dreaming.memory_block("karthik") == (
        "From this manager's chats in the 7 days before 8 Oct:\n"
        "- Z3 floods in heavy rain (said 6 Oct)."
    )
    assert dreaming.memory_digest("karthik") is review.digests["karthik"]
