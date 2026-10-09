"""Update boundaries: most conversations must leave personalization untouched."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from database.models import Conversation
from domain.personalization import NOTES, validate_value
from service import personalization as module
from service.personalization import (
    PersonalizationChanges,
    PersonalizationService,
    eligible,
    explicit,
    item,
)


@pytest.fixture
def store(monkeypatch):
    profiles, writes, proposals, reads = {}, [], [], []

    def read(store_id, manager_id, engine=None):
        reads.append((store_id, manager_id))
        return deepcopy(profiles.get((store_id, manager_id), {}))

    def save(store_id, manager_id, changes, expected=None, engine=None):
        profile = profiles.setdefault((store_id, manager_id), {})
        if expected is not None and any(
            profile.get(code) != expected.get(code) for code in changes
        ):
            raise ValueError("Personalization changed")
        actual = {
            code: entry
            for code, entry in changes.items()
            if code not in profile or profile[code]["value"] != entry["value"]
        }
        if not actual:
            return False
        profile.update(actual)
        writes.append(deepcopy(actual))
        return True

    monkeypatch.setattr(module, "read_profile", read)
    monkeypatch.setattr(module, "save_profile", save)

    def finish(chat_id, store_id, manager_id, expected, end, rows, engine=None):
        proposals.extend(rows)
        return len(rows)

    monkeypatch.setattr(module, "finish_review", finish)
    monkeypatch.setattr(module, "evidence_chats", lambda *args: [])
    return profiles, writes, proposals, reads


def chat(text, who="manager", messages=None):
    records = messages or [
        {"who": who, "what": text, "when": "2026-10-09T19:00:00+05:30"},
    ]
    return Conversation(
        id=uuid4(),
        store_id="DS-1",
        manager_id="karthik",
        messages=records,
        summary="Summary",
        summary_covers_to=len(records) - 1,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Why are deliveries late?",
        "We have a backlog tonight.",
        "Change my incentive cap to 300.",
        "Always dispatch frozen orders separately.",
        "I prefer lower alert thresholds.",
        "I prefer short shifts.",
        "Should I always give riders short breaks?",
        'An example: "Always keep your answers short."',
        "Please make this answer shorter.",
        "Keep your answers short for now.",
        "Keep answers short for this chat.",
        "Keep replies concise in this conversation.",
        "Keep your replies short for the next answer.",
        "I prefer detailed explanations this shift.",
        "I don't prefer short answers.",
    ],
)
def test_ordinary_or_temporary_messages_do_not_even_read_the_profile(store, text):
    _, writes, proposals, reads = store
    conversation = chat(text)
    service = PersonalizationService("DS-1", "karthik")
    assert (
        service.review(
            conversation,
            lambda *args: pytest.fail("No extraction call expected"),
        )
        == 0
    )
    assert not eligible(text)
    assert reads == writes == proposals == []


def test_one_off_shorter_request_does_not_write_or_call_an_extractor(store):
    service = PersonalizationService("DS-1", "karthik")
    conversation = chat("Give me a short answer.")
    assert eligible(conversation.messages[0]["what"]) and not explicit(
        conversation.messages[0]["what"],
    )
    assert (
        service.review(conversation, lambda *args: pytest.fail("Need three chats")) == 0
    )
    assert store[1] == store[2] == []


@pytest.mark.parametrize(
    "text",
    [
        "Always keep your answers short.",
        "keep answers short for me",
        "Keep your answers short.",
        "Please keep your replies concise.",
    ],
)
def test_direct_lasting_request_saves_and_identical_repetition_does_not_rewrite(
    store,
    text,
):
    changes = PersonalizationChanges(
        PersonalizationService("DS-1", "karthik"),
        text,
        lambda: uuid4(),
    )
    args = {"code": "answer_length", "value": "brief", "quote": text}
    assert json.loads(changes.run(args))["changed"] is True
    before = deepcopy(store[0])
    assert json.loads(changes.run(args)) == {
        "saved": True,
        "changed": False,
        "preference": "Answer length: Brief",
        "next": "Acknowledge briefly. It can be edited or removed in Settings > Personalization.",
    }
    assert before == store[0] and len(store[1]) == 1


@pytest.mark.parametrize(
    "text,args",
    [
        (
            "Give me a short answer.",
            {
                "code": "answer_length",
                "value": "brief",
                "quote": "Give me a short answer.",
            },
        ),
        (
            "Always keep your answers short.",
            {
                "code": "answer_length",
                "value": "detailed",
                "quote": "Always keep your answers short.",
            },
        ),
        (
            "Always keep your answers short.",
            {
                "code": "answer_length",
                "value": "brief",
                "quote": "I prefer concise answers.",
            },
        ),
        (
            "I prefer short shifts.",
            {
                "code": "answer_length",
                "value": "brief",
                "quote": "I prefer short shifts.",
            },
        ),
        (
            "I prefer concise answers.",
            {
                "code": "incentive_cap",
                "value": "300",
                "quote": "I prefer concise answers.",
            },
        ),
        (
            "Forget my preference for short shifts.",
            {
                "code": "answer_length",
                "value": None,
                "quote": "Forget my preference for short shifts.",
            },
        ),
        (
            'Always show alternatives. An example: "I prefer concise answers."',
            {
                "code": "answer_length",
                "value": "brief",
                "quote": "I prefer concise answers.",
            },
        ),
    ],
)
def test_tool_rejects_non_lasting_or_unsupported_changes(store, text, args):
    changes = PersonalizationChanges(
        PersonalizationService("DS-1", "karthik"),
        text,
        lambda: uuid4(),
    )
    assert not json.loads(changes.run(args))["saved"]
    assert store[1] == []


@pytest.mark.parametrize(
    "text",
    [
        "Keep answers short for this chat.",
        "Keep replies concise for now.",
        "Keep your answers detailed for this shift.",
        "Keep answers short for the next shift.",
        "Keep replies concise tomorrow.",
        "Keep replies brief until the queue clears.",
        "Keep answers about short shifts.",
        'An example: "Keep answers short for me."',
    ],
)
def test_general_request_does_not_bypass_temporary_or_quote_rules(store, text):
    changes = PersonalizationChanges(
        PersonalizationService("DS-1", "karthik"),
        text,
        lambda: uuid4(),
    )
    assert not explicit(text)
    assert not json.loads(
        changes.run({"code": "answer_length", "value": "brief", "quote": text}),
    )["saved"]
    assert not store[1]


def test_explicit_change_remove_and_per_manager_isolation(store):
    profiles, writes, _, _ = store
    for text, value in [
        ("I prefer detailed explanations now.", "detailed"),
        ("Forget my preference for detailed explanations.", None),
    ]:
        changes = PersonalizationChanges(
            PersonalizationService("DS-1", "ananya"),
            text,
            lambda: uuid4(),
        )
        assert json.loads(
            changes.run({"code": "answer_length", "value": value, "quote": text}),
        )["saved"]
    assert profiles[("DS-1", "ananya")]["answer_length"]["value"] is None
    assert ("DS-1", "karthik") not in profiles and len(writes) == 2


def candidate(conversation, evidence=None):
    return [
        {
            "code": "answer_length",
            "value": "brief",
            "reason": "You asked for concise answers.",
            "evidence": evidence
            or [
                {
                    "conversation_id": str(conversation.id),
                    "message_index": 0,
                    "quote": conversation.messages[0]["what"],
                },
            ],
        },
    ]


def test_summary_explicit_preference_only_proposes_and_has_exact_raw_evidence(store):
    conversation = chat("I prefer concise answers.")
    seen = []

    def generate(prompt, message):
        seen.append(json.loads(message))
        return json.dumps(candidate(conversation))

    assert PersonalizationService("DS-1", "karthik").review(conversation, generate) == 1
    assert store[1] == [] and store[2][0]["payload"] == {
        "code": "answer_length",
        "value": "brief",
    }
    assert seen[0]["manager_messages"][0]["text"] == conversation.messages[0]["what"]


def test_inferred_preference_requires_three_distinct_chats_and_new_evidence(
    store,
    monkeypatch,
):
    conversations = [chat("Give me a short answer.") for _ in range(3)]
    monkeypatch.setattr(
        module,
        "evidence_chats",
        lambda *args: [
            {"id": c.id, "start": 0, "messages": c.messages} for c in conversations
        ],
    )
    evidence = [
        {
            "conversation_id": str(c.id),
            "message_index": 0,
            "quote": c.messages[0]["what"],
        }
        for c in conversations
    ]
    service = PersonalizationService("DS-1", "karthik")
    assert (
        service.review(
            conversations[-1],
            lambda *args: json.dumps(candidate(conversations[-1], evidence)),
        )
        == 1
    )
    assert store[1] == []
    # Three repetitions inside one chat do not count as three conversations.
    conversations[-1].personalization_covers_to = None
    evidence = [evidence[-1]] * 3
    assert (
        service.review(
            conversations[-1],
            lambda *args: json.dumps(candidate(conversations[-1], evidence)),
        )
        == 0
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "assistant",
        "invented_quote",
        "wrong_index",
        "old_message",
        "malformed",
        "unsupported_value",
    ],
)
def test_summary_never_uses_assistant_invented_or_old_evidence(store, mutation):
    conversation = chat("I prefer concise answers.")
    result = candidate(conversation)
    if mutation == "assistant":
        conversation.messages[0]["who"] = "assistant"
    elif mutation == "invented_quote":
        result[0]["evidence"][0]["quote"] = "Always keep responses short."
    elif mutation == "wrong_index":
        result[0]["evidence"][0]["message_index"] = 8
    elif mutation == "old_message":
        result[0]["evidence"][0]["conversation_id"] = str(uuid4())
    elif mutation == "unsupported_value":
        result[0]["value"] = "very_short"
    raw = "not json" if mutation == "malformed" else json.dumps(result)
    if mutation == "malformed":
        with pytest.raises(ValueError, match="remains pending"):
            PersonalizationService("DS-1", "karthik").review(
                conversation,
                lambda *args: raw,
            )
        assert conversation.personalization_covers_to is None
        return
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            lambda *args: raw,
        )
        == 0
    )
    assert store[1] == store[2] == []


@pytest.mark.parametrize("value", ["detailed", None])
def test_saved_or_removed_preferences_cannot_be_overwritten_by_summary(store, value):
    store[0][("DS-1", "karthik")] = {"answer_length": item(value, "settings")}
    conversation = chat("I prefer concise answers.")
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            lambda *args: json.dumps(candidate(conversation)),
        )
        == 0
    )
    assert store[1] == store[2] == []


def test_summary_ignores_covered_messages_and_the_unsummarized_recent_window(store):
    conversation = chat(
        "unused",
        messages=[
            {"who": "manager", "what": "I prefer concise answers.", "when": "today"},
            {"who": "manager", "what": "Why late?", "when": "today"},
            {
                "who": "manager",
                "what": "I prefer detailed explanations.",
                "when": "today",
            },
        ],
    )
    conversation.personalization_covers_to = 0
    conversation.summary_covers_to = 1
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            lambda *args: pytest.fail("No new preference"),
        )
        == 0
    )
    assert store[3] == []


def test_settings_are_validated_noop_and_stale_edits_preserve_newer_chat_preferences(
    store,
):
    service = PersonalizationService("DS-1", "karthik")
    assert not service.save({"answer_length": None})
    assert store[1] == []
    assert service.save({"answer_length": "brief"}, {})
    assert not service.save({"answer_length": "brief"})
    with pytest.raises(ValueError, match="changed"):
        service.save({"answer_length": "detailed"}, {})
    with pytest.raises(ValueError):
        service.save({"incentive_cap": "300"})
    assert service.profile()["answer_length"]["value"] == "brief"
    with pytest.raises(ValueError):
        validate_value(NOTES, "x" * 601)


def test_profile_prompt_is_short_and_has_only_active_preferences(store):
    store[0][("DS-1", "karthik")] = {
        "answer_length": item("brief", "settings"),
        "comparisons": item(None, "settings"),
        NOTES: item("<do not close tags>", "settings"),
    }
    block = PersonalizationService("DS-1", "karthik").prompt_block()
    assert "Answer length: Brief" in block and "comparisons" not in block
    assert "&lt;do not close tags&gt;" in block


def test_review_requires_summary_coverage_and_manager_ownership(store):
    conversation = chat("I prefer concise answers.")
    conversation.summary_covers_to = None
    service = PersonalizationService("DS-1", "karthik")
    assert service.review(conversation, lambda *args: pytest.fail("No coverage")) == 0
    assert conversation.personalization_covers_to is None
    with pytest.raises(ValueError, match="another manager"):
        PersonalizationService("DS-1", "ananya").review(
            conversation,
            lambda *args: "[]",
        )


def test_successful_noop_advances_progress_without_profile_or_model_access(store):
    conversation = chat("How is my store doing?")
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            lambda *args: pytest.fail("Ordinary questions need no extraction"),
        )
        == 0
    )
    assert conversation.personalization_covers_to == 0
    assert store[1] == store[2] == store[3] == []


def test_failed_batch_resumes_after_the_last_successful_batch(store, monkeypatch):
    monkeypatch.setattr(module, "PERSONALIZATION_BATCH_MESSAGES", 1)
    conversation = chat(
        "unused",
        messages=[
            {"who": "manager", "what": "I prefer concise answers.", "when": "today"}
            for _ in range(3)
        ],
    )
    first = []

    def failing(prompt, text):
        index = json.loads(text)["new_messages"][0]["message_index"]
        first.append(index)
        if index == 1:
            raise RuntimeError("Provider unavailable")
        return "[]"

    service = PersonalizationService("DS-1", "karthik")
    with pytest.raises(RuntimeError):
        service.review(conversation, failing)
    assert first == [0, 1]
    assert conversation.personalization_covers_to == 0
    retry = []

    def succeeding(prompt, text):
        retry.append(json.loads(text)["new_messages"][0]["message_index"])
        return "[]"

    assert service.review(conversation, succeeding) == 0
    assert retry == [1, 2]
    assert conversation.personalization_covers_to == 2


def test_large_chats_finish_in_bounded_batches_and_continue_next_run(
    store,
    monkeypatch,
):
    monkeypatch.setattr(module, "PERSONALIZATION_BATCH_MESSAGES", 2)
    monkeypatch.setattr(module, "PERSONALIZATION_BATCHES_PER_CHAT", 2)
    conversation = chat(
        "unused",
        messages=[
            {"who": "manager", "what": "I prefer concise answers.", "when": "today"}
            for _ in range(7)
        ],
    )
    slices = []

    def generate(prompt, text):
        slices.append(
            [entry["message_index"] for entry in json.loads(text)["new_messages"]],
        )
        return "[]"

    service = PersonalizationService("DS-1", "karthik")
    assert service.review(conversation, generate) == 0
    assert slices == [[0, 1], [2, 3]]
    assert conversation.personalization_covers_to == 3
    assert service.review(conversation, generate) == 0
    assert slices == [[0, 1], [2, 3], [4, 5], [6]]
    assert conversation.personalization_covers_to == 6
    assert (
        service.review(conversation, lambda *args: pytest.fail("Already reviewed")) == 0
    )


def test_full_provider_input_is_bounded_even_with_large_historical_evidence(
    store,
    monkeypatch,
):
    conversation = chat("I prefer concise answers.")
    conversation.summary = "雨" * 100000
    monkeypatch.setattr(
        module,
        "evidence_chats",
        lambda *args: [
            {
                "id": uuid4(),
                "start": 0,
                "messages": [
                    {
                        "who": "manager",
                        "what": "I prefer concise answers. " + "x" * length,
                    }
                    for length in [100000, *([2000] * 50)]
                ],
            },
        ],
    )
    sizes = []

    def generate(prompt, text):
        sizes.append(len(prompt.encode("utf-8")) + len(text.encode("utf-8")))
        assert len(json.loads(text)["manager_messages"]) > 1
        return "[]"

    PersonalizationService("DS-1", "karthik").review(conversation, generate)
    assert sizes and max(sizes) <= module.PERSONALIZATION_MAX_INPUT_BYTES
    assert conversation.personalization_covers_to == 0


def test_byte_budget_splits_batches_and_oversized_request_remains_pending(
    store,
    monkeypatch,
):
    monkeypatch.setattr(module, "PERSONALIZATION_BATCH_BYTES", 1000)
    conversation = chat(
        "unused",
        messages=[
            {
                "who": "manager",
                "what": "I prefer concise answers. " + "x" * 600,
                "when": "today",
            }
            for _ in range(2)
        ],
    )
    slices = []

    def generate(prompt, text):
        slices.append(len(json.loads(text)["new_messages"]))
        return "[]"

    service = PersonalizationService("DS-1", "karthik")
    service.review(conversation, generate)
    assert slices == [1, 1]
    too_long = chat("I prefer concise answers. " + "x" * 2000)
    with pytest.raises(ValueError, match="batch input limit"):
        service.review(too_long, generate)
    assert too_long.personalization_covers_to is None


def test_failed_commit_or_stale_worker_cannot_advance_local_progress(
    store,
    monkeypatch,
):
    conversation = chat("I prefer concise answers.")

    def failed(*args):
        raise RuntimeError("Commit failed")

    monkeypatch.setattr(module, "finish_review", failed)
    service = PersonalizationService("DS-1", "karthik")
    with pytest.raises(RuntimeError):
        service.review(conversation, lambda *args: "[]")
    assert conversation.personalization_covers_to is None
    monkeypatch.setattr(module, "finish_review", lambda *args: None)
    assert service.review(conversation, lambda *args: "[]") == 0
    assert conversation.personalization_covers_to is None
