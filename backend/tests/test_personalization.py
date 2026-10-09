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
    monkeypatch.setattr(
        module,
        "propose_personalization",
        lambda row, engine=None: proposals.append(row) or True,
    )
    monkeypatch.setattr(module, "evidence_chats", lambda *args: [])
    return profiles, writes, proposals, reads


def chat(text, who="manager", messages=None):
    return Conversation(
        id=uuid4(),
        store_id="DS-1",
        manager_id="karthik",
        messages=messages
        or [{"who": who, "what": text, "when": "2026-10-09T19:00:00+05:30"}],
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
            0,
            0,
            "Always use short answers.",
            lambda *args: pytest.fail("No extraction call expected"),
        )
        == 0
    )
    assert not eligible(text)
    assert reads == writes == proposals == []


def test_one_off_shorter_request_does_not_write_or_call_an_extractor(store):
    service = PersonalizationService("DS-1", "karthik")
    conversation = chat("Keep your answers short.")
    assert eligible(conversation.messages[0]["what"]) and not explicit(
        conversation.messages[0]["what"],
    )
    assert (
        service.review(
            conversation,
            0,
            0,
            "Summary",
            lambda *args: pytest.fail("Need three chats"),
        )
        == 0
    )
    assert store[1] == store[2] == []


def test_direct_lasting_request_saves_and_identical_repetition_does_not_rewrite(store):
    text = "Always keep your answers short."
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
            "Keep your answers short.",
            {
                "code": "answer_length",
                "value": "brief",
                "quote": "Keep your answers short.",
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

    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            0,
            0,
            "Context",
            generate,
        )
        == 1
    )
    assert store[1] == [] and store[2][0]["payload"] == {
        "code": "answer_length",
        "value": "brief",
    }
    assert seen[0]["manager_messages"][0]["text"] == conversation.messages[0]["what"]


def test_inferred_preference_requires_three_distinct_chats_and_new_evidence(
    store,
    monkeypatch,
):
    conversations = [chat("Keep your answers short.") for _ in range(3)]
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
            0,
            0,
            "Summary",
            lambda *args: json.dumps(candidate(conversations[-1], evidence)),
        )
        == 1
    )
    assert store[1] == []
    # Three repetitions inside one chat do not count as three conversations.
    evidence = [evidence[-1]] * 3
    assert (
        service.review(
            conversations[-1],
            0,
            0,
            "Summary",
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
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            0,
            0,
            "Summary",
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
            0,
            0,
            "Summary",
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
    assert (
        PersonalizationService("DS-1", "karthik").review(
            conversation,
            1,
            1,
            "Summary",
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
