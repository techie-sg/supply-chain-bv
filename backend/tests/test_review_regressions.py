"""Regression cases from review, isolated from providers and database state."""

import json
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from constants import TIMEZONE
from database.models import Conversation
from domain.personalization import NOTES
from service import handover, personalization


@pytest.fixture(autouse=True)
def no_external_tracing(monkeypatch):
    span = SimpleNamespace(update=lambda **kwargs: None)
    for module in (handover, personalization):
        monkeypatch.setattr(module, "observation", lambda *a, **k: nullcontext(span))


def conversation(messages, *, summary=None, covers_to=None):
    return Conversation(
        id=uuid4(),
        store_id="store",
        manager_id="manager",
        messages=messages,
        title="Review case",
        summary=summary,
        summary_covers_to=covers_to,
    )


def message(text, when="2026-10-22T10:00:00+05:30", who="manager"):
    return {"who": who, "what": text, "when": when}


@pytest.fixture
def review_store(monkeypatch):
    writes = []
    monkeypatch.setattr(personalization, "read_profile", lambda *a: {})

    def finish(chat_id, store_id, manager_id, expected, end, updates, engine=None):
        writes.append({"covers_to": end, "updates": updates})
        return len(updates)

    monkeypatch.setattr(personalization, "finish_review", finish)
    return writes


@pytest.mark.parametrize(
    "response",
    [
        "[]",
        "```json\n[]\n```",
        "```\n[]\n```",
        "[]\n``` ",
        "\r\n```JSON\r\n[]\r\n```\r\n",
    ],
)
def test_valid_empty_personalization_with_optional_fences_advances_once(
    review_store,
    response,
):
    chat = conversation(
        [message("Six orders are waiting.")],
        summary="Summary",
        covers_to=0,
    )
    generate = Mock(return_value=response)
    service = personalization.PersonalizationService("store", "manager")
    assert service.review(chat, generate) == 0
    assert chat.personalization_covers_to == 0
    assert review_store == [{"covers_to": 0, "updates": []}]
    assert service.review(chat, generate) == 0
    assert generate.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        "prefix\n[]",
        "[]\ncommentary",
        "prefix\n```json\n[]\n```",
        "```json\n[]",
        "```json\n[]\n```\ncommentary",
        "```python\n[]\n```",
        "[]\n```\nextra",
        "[]\n```\n```",
        "[{}]\n```",
        "null\n```",
        None,
    ],
)
def test_invalid_personalization_content_or_schema_remains_pending(
    review_store,
    response,
):
    chat = conversation([message("Call me Sunny.")], summary="Summary", covers_to=0)
    with pytest.raises(ValueError, match="remains pending"):
        personalization.PersonalizationService("store", "manager").review(
            chat,
            lambda *a: response,
        )
    assert chat.personalization_covers_to is None
    assert not review_store


def test_fenced_personalization_keeps_schema_and_evidence_checks(review_store):
    chat = conversation([message("Call me Sunny.")], summary="Summary", covers_to=0)
    candidate = {
        "code": NOTES,
        "value": "Call the manager Sunny.",
        "reason": "The manager supplied their name.",
        "evidence": [
            {
                "conversation_id": str(chat.id),
                "message_index": 0,
                "quote": "Call me Sunny.",
            },
        ],
    }
    response = f"```json\n{json.dumps([candidate])}\n```"
    assert (
        personalization.PersonalizationService("store", "manager").review(
            chat,
            lambda *a: response,
        )
        == 1
    )
    assert (
        review_store[0]["updates"][0]["payload"]["value"] == "Call the manager Sunny."
    )


def test_failed_handover_summary_includes_every_uncovered_message(monkeypatch):
    urgent = "Details: " + "x" * 350 + " Freezer failed; isolate frozen orders."
    covered_question = "Already summarized question"
    chat = conversation(
        [
            message(covered_question),
            message("Already summarized answer", who="assistant"),
            message(urgent),
            *[message(f"Later update {index}") for index in range(8)],
        ],
        summary="The covered discussion approved a standby rider.",
        covers_to=1,
    )
    shift = SimpleNamespace(
        id=uuid4(),
        store_id="store",
        manager_id="manager",
        started_at=datetime(2026, 10, 22, 6, tzinfo=TIMEZONE),
        ended_at=None,
    )
    monkeypatch.setattr(handover, "shift_chats", lambda *a: [chat])
    generate = Mock(return_value="- Isolate frozen orders.")
    service = handover.HandoverService(
        generate,
        summaries=SimpleNamespace(
            fold=Mock(side_effect=RuntimeError("provider unavailable")),
        ),
    )
    assert service.draft(shift)[0] == "- Isolate frozen orders."
    supplied = generate.call_args.args[1]
    assert chat.summary in supplied
    assert urgent in supplied
    assert all(f"Later update {index}" in supplied for index in range(8))
    assert covered_question not in supplied


def test_shift_boundaries_exclude_full_chat_summary_and_other_shifts(monkeypatch):
    inside = [message(f"Current shift message {index}") for index in range(8)]
    chat = conversation(
        [
            message("PREVIOUS SHIFT", when="2026-10-21T10:00:00+05:30"),
            *inside,
            message("NEXT SHIFT", when="2026-10-22T18:00:00+05:30"),
        ],
        summary="PREVIOUS SHIFT: historical decisions",
        covers_to=0,
    )
    shift = SimpleNamespace(
        id=uuid4(),
        store_id="store",
        manager_id="manager",
        started_at=datetime(2026, 10, 22, 6, tzinfo=TIMEZONE),
        ended_at=datetime(2026, 10, 22, 14, tzinfo=TIMEZONE),
    )
    monkeypatch.setattr(handover, "shift_chats", lambda *a: [chat])
    generate = Mock(return_value="- Current shift handover.")
    handover.HandoverService(generate).draft(shift)
    supplied = generate.call_args.args[1]
    assert all(item["what"] in supplied for item in inside)
    assert "PREVIOUS SHIFT" not in supplied
    assert "NEXT SHIFT" not in supplied


def test_large_handover_fails_before_generation_without_dropping_messages(monkeypatch):
    urgent = "Freezer failed; isolate all frozen orders. " + "Rain alert. " * 100
    chat = conversation([message(urgent)])
    original = list(chat.messages)
    shift = SimpleNamespace(
        id=uuid4(),
        store_id="store",
        manager_id="manager",
        started_at=datetime(2026, 10, 22, 6, tzinfo=TIMEZONE),
        ended_at=None,
    )
    monkeypatch.setattr(handover, "shift_chats", lambda *a: [chat])
    prompt_bytes = len(
        (handover.PROMPTS / "handover_draft.md")
        .read_text(encoding="utf-8")
        .encode("utf-8"),
    )
    monkeypatch.setattr(handover, "HANDOVER_MAX_INPUT_BYTES", prompt_bytes + 100)
    generate = Mock(return_value="- An incomplete note.")
    with pytest.raises(ValueError, match="too much unsummarized conversation"):
        handover.HandoverService(generate).draft(shift)
    generate.assert_not_called()
    assert chat.messages == original
    assert chat.messages[0]["what"] == urgent
