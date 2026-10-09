from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from constants import (
    SUMMARY_IDLE_MINUTES,
    SUMMARY_MAX_RAW_MESSAGES,
)
from database.models import Conversation
from service import summaries
from service.scenarios import TIMEZONE
from service.summaries import SummaryService, history_start, needs_folding, raw_start

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=TIMEZONE)


def chat(count: int, *, covers_to: int | None = None, length: int = 10) -> Conversation:
    return Conversation(
        id=uuid4(),
        store_id="DS-1",
        manager_id="karthik",
        messages=[
            {
                "who": "manager" if index % 2 == 0 else "assistant",
                "what": f"m{index} " + "x" * length,
                "when": "2026-10-07T19:00:00+05:30",
            }
            for index in range(count)
        ],
        summary="Earlier: rain backlog." if covers_to is not None else None,
        summary_covers_to=covers_to,
    )


@pytest.fixture
def saved(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def save(conversation_id, summary, covers_to, expected, engine=None):
        calls.append((conversation_id, summary, covers_to, expected))
        return True

    monkeypatch.setattr(summaries, "save_summary", save)
    return calls


def test_positions_decide_what_is_raw() -> None:
    assert raw_start(chat(4)) == 0 and history_start(chat(4)) == 0
    assert (
        raw_start(chat(20, covers_to=13)),
        history_start(chat(20, covers_to=13)),
    ) == (
        14,
        14,
    )
    # An idle summary covers everything, but the recent window stays raw.
    assert history_start(chat(20, covers_to=19)) == 14


def test_folding_is_needed_over_the_count_or_size_limit() -> None:
    assert not needs_folding(chat(SUMMARY_MAX_RAW_MESSAGES))
    assert needs_folding(chat(SUMMARY_MAX_RAW_MESSAGES + 1))
    assert not needs_folding(chat(20, covers_to=13))
    assert needs_folding(chat(8, length=2000))
    assert (
        summaries.estimate_tokens(
            [
                {
                    "who": "manager",
                    "what": "x" * 400,
                    "when": "2026-10-08T19:00:00+05:30",
                },
            ],
        )
        == 100
    )


def test_fold_rolls_the_previous_summary_forward(saved) -> None:
    seen = []

    def summarize(system_prompt, user_message):
        seen.append((system_prompt, user_message))
        return "  - Rain backlog; standby rider approved.  "

    conversation = chat(30, covers_to=13)
    assert SummaryService(summarize).fold(conversation, keep_recent=6)
    system_prompt, user_message = seen[0]
    assert "Never add facts" in system_prompt
    assert user_message.startswith("Previous summary:\nEarlier: rain backlog.")
    assert "[14] Manager (2026-10-07T19:00:00+05:30): m14" in user_message
    assert "[23] Assistant" in user_message and "[24]" not in user_message
    assert saved == [
        (conversation.id, "- Rain backlog; standby rider approved.", 23, 13),
    ]


def test_nothing_to_fold_or_an_empty_summary_saves_nothing(saved) -> None:
    service = SummaryService(lambda system_prompt, user_message: "   ")
    assert not service.fold(chat(6), keep_recent=6)
    assert not service.fold(chat(10), keep_recent=0)
    assert saved == []


def test_after_answer_folds_all_but_the_recent_window(saved, monkeypatch) -> None:
    service = SummaryService(lambda system_prompt, user_message: "Summary")
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: None)
    assert not service.after_answer("DS-1", "karthik")
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: chat(10))
    assert not service.after_answer("DS-1", "karthik")
    long_chat = chat(18)
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: long_chat)
    assert service.after_answer("DS-1", "karthik")
    assert saved[-1][2:] == (11, None)


def test_idle_chats_are_summarized_in_full_and_failures_are_isolated(
    saved,
    monkeypatch,
) -> None:
    asked = []
    chats = [chat(4), chat(8, covers_to=1)]

    def idle(idle_before, limit, engine=None):
        asked.append((idle_before, limit))
        return chats

    def summarize(system_prompt, user_message):
        if "[0]" in user_message:
            raise RuntimeError("provider down")
        return "Summary"

    monkeypatch.setattr(summaries, "idle_unsummarized", idle)
    assert SummaryService(summarize).summarize_idle(NOW) == 1
    assert asked == [(NOW - timedelta(minutes=SUMMARY_IDLE_MINUTES), 10)]
    assert saved == [(chats[1].id, "Summary", 7, 1)]


def test_entry_points_use_the_configured_model_and_demo_manager(
    saved,
    monkeypatch,
) -> None:
    class Model:
        def generate(self, system_prompt, user_message):
            return "Summary"

    asked = []

    def latest(store_id, manager_id, engine=None):
        asked.append((store_id, manager_id))
        return chat(18)

    monkeypatch.setattr(summaries, "create_llm_service", lambda: Model())
    monkeypatch.setattr(summaries, "latest_conversation", latest)
    assert summaries.summarize_latest_conversation()
    assert asked == [("DS-BLR-014", "karthik")]
    monkeypatch.setattr(summaries, "idle_unsummarized", lambda *args, **kwargs: [])
    assert summaries.summary_service().summarize_idle() == 0


def test_summarize_now_folds_every_message_of_the_open_chat(saved, monkeypatch) -> None:
    service = SummaryService(lambda system_prompt, user_message: "Summary")
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: None)
    assert not service.summarize_now("DS-1", "karthik")

    open_chat = chat(8, covers_to=3)
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: open_chat)
    assert service.summarize_now("DS-1", "karthik")
    assert saved[-1] == (open_chat.id, "Summary", 7, 3)
    fully = chat(8, covers_to=7)
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: fully)
    assert not service.summarize_now("DS-1", "karthik")


def test_summary_on_demand_targets_selected_chat_instead_of_latest(saved, monkeypatch):
    selected = chat(4)
    looked_up = []

    def lookup(chat_id, store_id, manager_id, engine):
        looked_up.append((chat_id, store_id, manager_id))
        return selected

    monkeypatch.setattr(summaries, "resume_conversation", lookup)
    service = SummaryService(lambda *args: "Selected chat summary")
    assert service.summarize_now("DS-1", "karthik", str(selected.id))
    assert looked_up == [(selected.id, "DS-1", "karthik")]
    assert saved[0][0] == selected.id


def test_ui_summarize_now_entry_point_uses_the_demo_manager(saved, monkeypatch) -> None:
    class Model:
        def generate(self, system_prompt, user_message):
            return "Summary"

    asked = []

    def latest(store_id, manager_id, engine=None):
        asked.append((store_id, manager_id))
        return chat(2)

    monkeypatch.setattr(summaries, "create_llm_service", lambda: Model())
    monkeypatch.setattr(summaries, "latest_conversation", latest)
    assert summaries.summarize_open_conversation()
    assert asked == [("DS-BLR-014", "karthik")]
