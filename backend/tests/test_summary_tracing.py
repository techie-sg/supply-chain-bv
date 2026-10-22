"""Summary entry and batch traces include lookup failures and no-work outcomes."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from constants import SUMMARY_RECENT_MESSAGES, TIMEZONE
from database.models import Conversation
from service import summaries
from service.summaries import SummaryService

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=TIMEZONE)


@pytest.fixture
def traces(monkeypatch):
    recorded = []
    associations = []
    active = []

    @contextmanager
    def observation(name, **fields):
        record = {
            "name": name,
            "parent": active[-1] if active else None,
            **deepcopy(fields),
        }
        recorded.append(record)
        active.append(name)

        def update(**values):
            metadata = record.get("metadata", {}) | values.get("metadata", {})
            record.update(deepcopy(values))
            record["metadata"] = metadata

        try:
            yield SimpleNamespace(update=update)
        except Exception as exc:
            update(level="ERROR", status_message=type(exc).__name__)
            raise
        finally:
            active.pop()

    @contextmanager
    def attributes(**fields):
        associations.append(deepcopy(fields))
        yield

    monkeypatch.setattr(summaries, "observation", observation)
    monkeypatch.setattr(summaries, "trace_attributes", attributes)
    return SimpleNamespace(
        recorded=recorded,
        associations=associations,
        named=lambda name: next(
            record for record in recorded if record["name"] == name
        ),
    )


def conversation(count=18):
    return Conversation(
        id=uuid4(),
        store_id="DS-1",
        manager_id="karthik",
        messages=[
            {
                "who": "manager" if index % 2 == 0 else "assistant",
                "what": f"Message {index}",
                "when": NOW.isoformat(),
            }
            for index in range(count)
        ],
        summary=None,
        summary_covers_to=None,
    )


@pytest.mark.parametrize("method", ["after_answer", "summarize_now"])
@pytest.mark.parametrize("selected", [False, True])
def test_summary_entry_lookup_failure_has_identity_and_does_not_retry(
    traces,
    monkeypatch,
    method,
    selected,
):
    error = RuntimeError("database unavailable")
    calls = []

    def lookup(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(summaries, "latest_conversation", lookup)
    monkeypatch.setattr(summaries, "resume_conversation", lookup)
    identifier = str(uuid4()) if selected else None
    service = SummaryService(
        lambda *args: pytest.fail("No model call after failed lookup"),
    )
    with pytest.raises(RuntimeError) as caught:
        getattr(service, method)("DS-1", "karthik", identifier)
    assert caught.value is error
    assert len(calls) == 1
    root = traces.recorded[0]
    assert root["name"] == (
        "summary.after_answer" if method == "after_answer" else "summary.now"
    )
    assert root["level"] == "ERROR"
    assert root["status_message"] == "RuntimeError"
    assert root["user_id"] == "karthik"
    assert root["session_id"] == identifier
    assert root["input"]["store_id"] == "DS-1"
    assert len(traces.recorded) == 1


@pytest.mark.parametrize("method", ["after_answer", "summarize_now"])
def test_resolved_conversation_associates_summary_entry_and_child_fold(
    traces,
    monkeypatch,
    method,
):
    chat = conversation()
    generated, saved = [], []
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: chat)
    monkeypatch.setattr(
        summaries,
        "save_summary",
        lambda *args: saved.append(args) or True,
    )

    def generate(*args):
        generated.append(args)
        return "Grounded summary"

    service = SummaryService(generate)
    assert getattr(service, method)("DS-1", "karthik") is True
    assert len(generated) == len(saved) == 1
    root = traces.recorded[0]
    assert root["metadata"]["conversation_id"] == str(chat.id)
    assert traces.associations == [{"user_id": "karthik", "session_id": str(chat.id)}]
    child = traces.named("conversation.summary")
    assert child["parent"] == root["name"]
    assert child["session_id"] == str(chat.id)
    assert child["user_id"] == "karthik"
    assert root["output"]["summary_saved"] is True
    assert root["output"]["failures"] == 0
    assert root["metadata"]["summary_saved"] is True
    assert root["metadata"]["failures"] == 0
    keep = SUMMARY_RECENT_MESSAGES if method == "after_answer" else 0
    assert root["output"]["covers_to"] == len(chat.messages) - 1 - keep


def test_summary_entry_records_no_chat_and_below_threshold_without_model_calls(
    traces,
    monkeypatch,
):
    service = SummaryService(lambda *args: pytest.fail("No model call for no work"))
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: None)
    assert service.summarize_now("DS-1", "karthik") is False
    assert traces.named("summary.now")["output"] == {
        "summary_saved": False,
        "reason": "no_conversation",
    }
    assert traces.named("summary.now")["metadata"]["reason"] == "no_conversation"
    monkeypatch.setattr(summaries, "latest_conversation", lambda *args: conversation(2))
    assert service.after_answer("DS-1", "karthik") is False
    assert (
        traces.named("summary.after_answer")["output"]["reason"]
        == "below_folding_threshold"
    )
    assert traces.named("summary.after_answer")["metadata"]["summary_saved"] is False
    assert not any(
        record["name"] == "conversation.summary" for record in traces.recorded
    )


def test_empty_summary_batch_is_explicit_and_resets_existing_failure_count(
    traces,
    monkeypatch,
):
    monkeypatch.setattr(summaries, "idle_unsummarized", lambda *args: [])
    service = SummaryService(lambda *args: pytest.fail("No model call for empty batch"))
    service.failures = 4
    assert service.summarize_idle(NOW, force=True) == 0
    assert service.failures == 0
    batch = traces.named("summary.batch")
    assert batch["input"]["force"] is True
    assert batch["input"]["idle_before"] == NOW.isoformat()
    assert batch["metadata"] == {
        "attempted": 0,
        "completed": 0,
        "failures": 0,
        "pending_personalization_attempted": 0,
        "status": "no-work",
    }
    assert batch["output"]["conversation_ids"] == []


def test_partial_summary_batch_reports_attempts_success_and_failure_without_retry(
    traces,
    monkeypatch,
):
    chats = [conversation(4), conversation(4)]
    monkeypatch.setattr(summaries, "idle_unsummarized", lambda *args: chats)
    generated, saved = [], []

    def generate(*args):
        generated.append(args)
        if len(generated) == 1:
            raise RuntimeError("provider unavailable")
        return "Summary"

    monkeypatch.setattr(
        summaries,
        "save_summary",
        lambda *args: saved.append(args) or True,
    )
    service = SummaryService(generate)
    assert service.summarize_idle(NOW) == 1
    assert service.failures == 1
    assert len(generated) == 2 and len(saved) == 1
    batch = traces.named("summary.batch")
    assert batch["metadata"]["attempted"] == 2
    assert batch["metadata"]["completed"] == 1
    assert batch["metadata"]["failures"] == 1
    assert batch["metadata"]["status"] == "partial-failure"
    assert batch["level"] == "WARNING"
    assert set(batch["output"]["conversation_ids"]) == {str(chat.id) for chat in chats}


def test_initial_batch_lookup_error_is_traced_but_preserves_original_failure_counter(
    traces,
    monkeypatch,
):
    error = RuntimeError("lookup unavailable")

    def lookup(*args):
        raise error

    monkeypatch.setattr(summaries, "idle_unsummarized", lookup)
    service = SummaryService(
        lambda *args: pytest.fail("No model call after lookup error"),
    )
    with pytest.raises(RuntimeError) as caught:
        service.summarize_idle(NOW)
    assert caught.value is error
    assert service.failures == 0
    batch = traces.named("summary.batch")
    assert batch["level"] == "ERROR"
    assert batch["metadata"]["status"] == "failed"
    assert batch["metadata"]["attempted"] == batch["metadata"]["completed"] == 0


def test_personalization_only_batch_reports_attempt_and_caught_failure(
    traces,
    monkeypatch,
):
    chat = conversation(4)
    monkeypatch.setattr(summaries, "idle_unsummarized", lambda *args: [])
    monkeypatch.setattr(summaries, "pending_reviews", lambda *args, **kwargs: [chat])
    reviewed = []

    class Review:
        def __init__(self, *args):
            pass

        def review(self, conversation, generate):
            reviewed.append(conversation.id)
            raise RuntimeError("review unavailable")

    monkeypatch.setattr(summaries, "PersonalizationService", Review)
    service = SummaryService(
        lambda *args: pytest.fail("Summary not needed"),
        review_personalization=True,
    )
    assert service.summarize_idle(NOW) == 0
    assert service.failures == 1
    assert reviewed == [chat.id]
    batch = traces.named("summary.batch")
    assert batch["metadata"]["pending_personalization_attempted"] == 1
    assert batch["metadata"]["completed"] == 0
    assert batch["metadata"]["failures"] == 1
    assert batch["metadata"]["status"] == "partial-failure"
