import json
from uuid import uuid4

import pytest

from database.models import Conversation
from domain.personalization import NOTES, validate_value
from service import personalization as module
from service.personalization import PersonalizationService, item


@pytest.fixture
def context(monkeypatch):
    profile = {}
    writes = []
    monkeypatch.setattr(module, "read_profile", lambda *args: dict(profile))

    def finish(chat_id, store_id, manager_id, expected, end, updates, engine=None):
        for update in updates:
            profile[NOTES] = item(update["payload"]["value"], "dreaming")
        writes.extend(updates)
        return len(updates)

    monkeypatch.setattr(module, "finish_review", finish)
    return profile, writes


def chat(text):
    return Conversation(
        id=uuid4(),
        store_id="S",
        manager_id="M",
        summary="Summary",
        summary_covers_to=0,
        messages=[
            {"who": "manager", "what": text, "when": "2026-10-09T19:00:00+05:30"},
        ],
    )


def candidate(c, value):
    return json.dumps(
        [
            {
                "code": NOTES,
                "value": value,
                "reason": "Manager supplied.",
                "evidence": [
                    {
                        "conversation_id": str(c.id),
                        "message_index": 0,
                        "quote": c.messages[0]["what"],
                    },
                ],
            },
        ],
    )


@pytest.mark.parametrize(
    "text,value",
    [
        ("My name is Sunny Gupta.", "Name: Sunny Gupta."),
        ("My email is sunny@example.com.", "Email: sunny@example.com."),
        ("I prefer practical store examples.", "Use store examples."),
        (
            "Call me Sunny and explain simply.",
            "Call the manager Sunny. Explain simply.",
        ),
    ],
)
def test_dreaming_saves_personal_context(context, text, value):
    c = chat(text)
    s = PersonalizationService("S", "M")
    assert s.review(c, lambda *args: candidate(c, value)) == 1
    assert (
        context[0][NOTES]["value"] == value
        and context[0][NOTES]["source"] == "dreaming"
    )
    assert c.personalization_covers_to == 0
    assert s.review(c, lambda *args: pytest.fail("Already reviewed")) == 0


def test_operational_chat_no_update(context):
    c = chat("Six orders are waiting.")
    assert PersonalizationService("S", "M").review(c, lambda *args: "[]") == 0
    assert not context[1] and c.personalization_covers_to == 0


def test_existing_context_is_preserved_in_input(context):
    context[0][NOTES] = item("Name: Sunny.", "dreaming")
    c = chat("My email is sunny@example.com.")

    def extract(prompt, payload):
        assert "Sunny" in json.loads(payload)["current_personal_context"]
        return candidate(c, "Name: Sunny. Email: sunny@example.com.")

    assert PersonalizationService("S", "M").review(c, extract) == 1
    assert context[1][0]["expected_profile"][NOTES]["value"] == "Name: Sunny."


def test_invented_evidence_rejected(context):
    c = chat("My name is Sunny.")
    result = json.loads(candidate(c, "Name: Sunny."))
    result[0]["evidence"][0]["quote"] = "Invented"
    assert (
        PersonalizationService("S", "M").review(c, lambda *args: json.dumps(result))
        == 0
    )
    assert not context[1]


def test_failed_extraction_remains_pending(context):
    c = chat("My name is Sunny.")
    with pytest.raises(ValueError, match="remains pending"):
        PersonalizationService("S", "M").review(c, lambda *args: "invalid")
    assert c.personalization_covers_to is None


def test_no_summary_no_review(context):
    c = chat("My name is Sunny.")
    c.summary_covers_to = None
    assert (
        PersonalizationService("S", "M").review(
            c,
            lambda *args: pytest.fail("No summary"),
        )
        == 0
    )


def test_assistant_is_not_evidence(context):
    c = chat("My name is Sunny.")
    c.messages[0]["who"] = "assistant"
    assert (
        PersonalizationService("S", "M").review(
            c,
            lambda *args: pytest.fail("Not a manager"),
        )
        == 0
    )


def test_context_limit():
    with pytest.raises(ValueError):
        validate_value(NOTES, "x" * 4001)


def test_large_chat_review_is_batched_and_resumes(context):
    c = chat("Routine store question")
    c.messages *= 420
    c.summary_covers_to = 419
    batches = []

    def extract(prompt, payload):
        messages = json.loads(payload)["manager_messages"]
        assert len(messages) <= 60
        batches.append(messages[0]["message_index"])
        return "[]"

    service = PersonalizationService("S", "M")
    assert service.review(c, extract) == 0
    assert batches == [0, 60, 120, 180, 240]
    assert c.personalization_covers_to == 299
    assert service.review(c, extract) == 0
    assert batches[-2:] == [300, 360]
    assert c.personalization_covers_to == 419


@pytest.mark.parametrize(
    "text",
    [
        "Order waiting alert threshold: 3 minutes.",
        "Incentive cap: 300 rupees.",
        "surge_only_batching: true.",
        "Cold-chain isolation: off.",
    ],
)
def test_dreaming_cannot_store_operational_setting_assignments(context, text):
    c = chat(text)
    assert (
        PersonalizationService("S", "M").review(c, lambda *args: candidate(c, text))
        == 0
    )
    assert not context[1]
    assert c.personalization_covers_to == 0


def test_mixed_personal_and_operational_context_keeps_only_personal_details(context):
    c = chat(
        "My name is Casey. My email is casey@example.com. Keep replies short. Set my order waiting alert threshold to 3 minutes.",
    )
    extracted = "Name: Casey. Email: casey@example.com. Keep replies short. Order waiting alert threshold: 3 minutes."
    assert (
        PersonalizationService("S", "M").review(
            c,
            lambda *args: candidate(c, extracted),
        )
        == 1
    )
    saved = context[0][NOTES]["value"]
    assert (
        "Casey" in saved
        and "casey@example.com" in saved
        and "Keep replies short" in saved
    )
    assert "threshold" not in saved and "3 minutes" not in saved


def test_existing_learned_settings_do_not_enter_chat_context(context):
    context[0][NOTES] = item(
        "Name: Casey. Order waiting alert threshold: 3 minutes.",
        "dreaming",
    )
    block = PersonalizationService("S", "M").prompt_block()
    assert "Casey" in block and "threshold" not in block


@pytest.mark.parametrize(
    "text",
    [
        "Explain rider shortage alerts in plain language.",
        "Explain the rider shortage alert in 3 bullet points.",
    ],
)
def test_personal_instructions_about_explaining_alerts_are_preserved(context, text):
    c = chat(text)
    assert (
        PersonalizationService("S", "M").review(c, lambda *args: candidate(c, text))
        == 1
    )
    assert context[0][NOTES]["value"] == text


def test_compound_sentence_keeps_a_name_but_excludes_a_setting(context):
    c = chat("My name is Casey and my incentive cap is 300 rupees.")
    assert (
        PersonalizationService("S", "M").review(
            c,
            lambda *args: candidate(c, "Name: Casey and incentive cap: 300 rupees."),
        )
        == 1
    )
    assert context[0][NOTES]["value"] == "Name: Casey"
