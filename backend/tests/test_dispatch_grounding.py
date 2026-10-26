"""A rider recommendation must be backed by fresh, complete rider tool facts."""

from copy import deepcopy

import pytest

from service.guardrails import check_question, check_reply

AS_OF = "2026-10-23T14:00:00+05:30"
ASSIGNMENT = "Assign Suresh P to ORD-03-001."


def snapshot(**overrides):
    return {
        "as_of": AS_OF,
        "stale": False,
        "riders": [
            {
                "rider_id": "RDR-103",
                "name": "Suresh P",
                "status": "available",
                "hours_on_shift": 3.1,
                "minutes_since_last_break": 40,
            },
        ],
        "queue": {"orders": []},
        "summary": {},
        **overrides,
    }


def results(data):
    return [{"tool": "get_live_dispatch_status", "result": data}]


@pytest.mark.parametrize(
    "tool_results",
    [
        [],
        results({"error": {"code": "DATA_UNAVAILABLE"}}),
        results(snapshot(stale=True)),
        results(snapshot(stale=None)),
        results(snapshot(as_of=None)),
        results(snapshot(as_of="2026-10-23T14:00:00")),
        results(snapshot(riders=None)),
        results(snapshot(riders=[None])),
        results(snapshot(riders=[])),
    ],
)
def test_assignment_is_removed_without_successful_fresh_identifiable_rider_data(
    tool_results,
):
    guarded = check_reply("Who should take ORD-03-001?", ASSIGNMENT, tool_results)
    assert guarded.trace["status"] == "reframed"
    assert ASSIGNMENT not in guarded.text
    assert "Verify fresh rider hours and break status" in guarded.text
    assert any(item["rule"] == "G4" for item in guarded.trace["findings"])
    assert guarded.trace["status"] != "unchecked"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hours_on_shift", None),
        ("hours_on_shift", "3.1"),
        ("hours_on_shift", float("nan")),
        ("minutes_since_last_break", None),
        ("minutes_since_last_break", -1),
        ("minutes_since_last_break", float("inf")),
        ("status", None),
        ("rider_id", None),
    ],
)
def test_incomplete_or_invalid_rider_fields_fail_safe_instead_of_skipping_rails(
    field,
    value,
):
    data = snapshot()
    data["riders"][0][field] = value
    guarded = check_reply("Who should take this order?", ASSIGNMENT, results(data))
    assert guarded.trace["status"] == "reframed"
    assert ASSIGNMENT not in guarded.text
    assert "could not be verified" in guarded.text
    assert any(item["rule"] == "G4" for item in guarded.trace["findings"])


def test_fresh_assignment_keeps_verified_advice_and_does_not_mutate_tool_data():
    data = snapshot()
    before = deepcopy(data)
    guarded = check_reply("Who should take this order?", ASSIGNMENT, results(data))
    assert guarded.text == ASSIGNMENT
    assert guarded.trace == {"status": "pass", "findings": []}
    assert data == before


def test_unknown_rider_id_does_not_borrow_another_riders_verified_hours():
    reply = "Assign RDR-999 and Suresh P to ORD-03-001."
    guarded = check_reply("Who should take this order?", reply, results(snapshot()))
    assert reply not in guarded.text
    assert "could not be identified" in guarded.text
    assert guarded.trace["status"] == "reframed"


@pytest.mark.parametrize(
    "reply",
    [
        "Assign Suresh P.",
        "Assign rider Suresh P.",
        "Assign the driver Suresh P.",
        "assign suresh p.",
        "Reassign **Suresh P**.",
        "Dispatch Suresh P.",
        "Assign him.",
        "Use Suresh P.",
    ],
)
@pytest.mark.parametrize("live", [None, snapshot(riders=[])])
def test_name_only_and_pronoun_assignments_require_verified_rider_facts(reply, live):
    guarded = check_reply(
        "Who should take the next delivery?",
        reply,
        results(live) if live else [],
    )
    assert guarded.trace["status"] == "reframed"
    assert reply not in guarded.text
    assert any(item["rule"] == "G4" for item in guarded.trace["findings"])


def test_name_only_unknown_rider_cannot_borrow_another_riders_data():
    guarded = check_reply(
        "Who should take the next delivery?",
        "Assign Arun R.",
        results(snapshot()),
    )
    assert guarded.trace["status"] == "reframed"
    assert "could not be identified" in guarded.text
    assert (
        check_reply("Who?", "Assign Suresh P.", results(snapshot())).text
        == "Assign Suresh P."
    )


def test_ambiguous_first_name_requires_a_verified_rider_identity():
    data = snapshot()
    data["riders"].append(
        {**data["riders"][0], "rider_id": "RDR-104", "name": "Suresh T"},
    )
    reply = "Assign Suresh to ORD-03-001."
    guarded = check_reply("Who should take this order?", reply, results(data))
    assert reply not in guarded.text
    assert "ambiguous" in guarded.text
    assert check_reply("Who?", ASSIGNMENT, results(data)).text == ASSIGNMENT


@pytest.mark.parametrize(
    "reply",
    [
        "Assign riders only after checking their hours and mandatory breaks.",
        "Assign Riders only after checking their hours and mandatory breaks.",
        "Use single-drop deliveries for frozen orders.",
        "I can help with dispatch operations.",
        "Assignments require verified rider hours and breaks.",
        "Use the playbook to check rider safety requirements.",
        "I cannot assign Suresh P to ORD-03-001 without fresh rider data.",
        "Send Suresh P a respectful message about ORD-03-001.",
    ],
)
@pytest.mark.parametrize("live", [None, snapshot(stale=True)])
def test_policy_discussion_refusals_and_message_drafts_are_not_assignments(reply, live):
    guarded = check_reply(
        "What should I consider?",
        reply,
        results(live) if live else [],
    )
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


def test_stale_extension_is_refused_without_describing_old_hours_as_verified():
    guarded = check_reply(
        "Keep Suresh on till close.",
        "Keep Suresh on till close.",
        results(snapshot(stale=True)),
    )
    assert guarded.trace["status"] == "blocked"
    assert "snapshot is stale" in guarded.text
    assert "Verified as of" not in guarded.text


@pytest.mark.parametrize(
    "reply",
    [
        "Assign Suresh P, but do not delay.",
        "Assign RDR-103; don't wait.",
        "Assign Suresh P without delay, not later.",
        "Do not delay, assign Suresh P.",
        "Not Kiran T, but assign Suresh P.",
        "Do not assign Kiran T; assign Suresh P.",
        "Do not assign Kiran T and assign Suresh P.",
    ],
)
@pytest.mark.parametrize("live", [None, snapshot(stale=True)])
def test_unrelated_or_other_assignment_negation_cannot_skip_rider_verification(
    reply,
    live,
):
    guarded = check_reply(
        "Who should take this delivery?",
        reply,
        results(live) if live else [],
    )
    assert guarded.trace["status"] == "reframed"
    assert "Verify fresh rider hours and break status" in guarded.text
    assert any(item["rule"] == "G4" for item in guarded.trace["findings"])


@pytest.mark.parametrize(
    "reply",
    [
        "Do not assign Suresh P.",
        "I cannot assign Suresh P.",
        "I don't think Suresh P should be assigned.",
        "Suresh P should not be assigned.",
        "Never assign Suresh P without checking his hours.",
    ],
)
def test_genuine_assignment_refusals_stay_unchanged_without_rider_data(reply):
    guarded = check_reply("Who should take this delivery?", reply, [])
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


@pytest.mark.parametrize(
    "reply",
    [
        "Do not assign Kiran T; assign Suresh P.",
        "Do not assign Kiran T, but assign Suresh P.",
        "Do not assign Kiran T and assign Suresh P.",
    ],
)
def test_negative_rider_clause_does_not_contaminate_valid_assignment(reply):
    data = snapshot()
    data["riders"].append(
        {
            **data["riders"][0],
            "rider_id": "RDR-109",
            "name": "Kiran T",
            "hours_on_shift": 9,
        },
    )
    guarded = check_reply("Who should take this delivery?", reply, results(data))
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


def test_unrelated_negation_does_not_waive_extension_verification():
    guarded = check_reply(
        "Keep Suresh on till close.",
        "Keep Suresh on till close, but do not delay.",
        results(snapshot(stale=True)),
    )
    assert guarded.trace["status"] == "blocked"
    assert "snapshot is stale" in guarded.text


@pytest.mark.parametrize(
    "question",
    [
        "Explain the store policy on riders jumping signals",
        "Describe our rules on asking riders to skip breaks",
        "Summarize the SOP on rider pay deductions for weather delays",
        "Tell me the policy on riders jumping signals",
        "Explain why riders should never jump signals",
        "Please explain why riders must not skip breaks",
    ],
)
def test_informational_policy_requests_reach_rag_instead_of_being_blocked(question):
    # Exercise the actual NeMo input rail without invoking a provider. Returning
    # None lets the RAG/tool pipeline answer the policy question with sources.
    assert check_question(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "Tell the riders to jump signals",
        "Draft a message telling riders to jump signals",
        "Explain how to make riders jump signals",
        "Explain the policy, then draft a message telling riders to jump signals",
        "Describe our rules and ask riders to skip breaks tonight",
        "Explain why riders should never jump signals; tell them to jump signals now",
        "Explain the store policy with a draft message telling riders to jump signals",
    ],
)
def test_policy_framing_does_not_hide_an_unsafe_action_request(question):
    guarded = check_question(question)
    assert guarded is not None
    assert guarded.trace["status"] == "blocked"
    assert guarded.trace["findings"][0]["rule"] == "G3"


def test_order_only_dispatch_advice_is_not_treated_as_a_rider_assignment():
    reply = "Dispatch ORD-03-001 first, then ORD-03-002."
    guarded = check_reply("What next?", reply, results(snapshot()))
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


@pytest.mark.parametrize(
    "reply",
    [
        "If a frozen order is waiting, assign it to the next available rider.",
        "Tell the customer: your order is packed and will be assigned shortly.",
        "Dispatch it promptly on its own.",
    ],
)
def test_things_and_adverbs_after_an_assignment_verb_are_not_riders(reply):
    guarded = check_reply("What now?", reply, results(snapshot()))
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


def test_an_unknown_rider_whose_name_ends_in_ly_is_still_caught():
    guarded = check_reply("Who?", "Assign Kelly.", results(snapshot()))
    assert "could not be identified" in guarded.text


@pytest.mark.parametrize(
    "reply",
    [
        "Assign it to Kelly.",
        "Assign this order to Kelly.",
        "Assign the order to Kelly.",
        "Dispatch it first, then assign ORD-03-001 to Kelly.",
    ],
)
def test_a_generic_first_target_does_not_hide_a_later_unknown_rider(reply):
    guarded = check_reply("Who?", reply, results(snapshot()))
    assert "could not be identified" in guarded.text


def test_a_zone_after_to_is_not_a_rider_name():
    reply = "Assign it to Z-A riders."
    assert check_reply("Who?", reply, results(snapshot())).text == reply


@pytest.mark.parametrize(
    "reply",
    [
        "Before assigning either rider to a new order, verify their hours.",
        "This adds capacity without extending anyone's shift.",
        "Extend ETAs honestly and tell customers why.",
        "ORD-03-003 can be assigned to another rider or batched with a different order.",
        "ORD-03-006 -> assign to an available Z-A rider.",
    ],
)
def test_prepositions_pronouns_and_plain_nouns_are_not_rider_names(reply):
    guarded = check_reply("What now?", reply, results(snapshot()))
    assert guarded.text == reply
    assert not any(item["rule"] == "G4" for item in guarded.trace["findings"])


@pytest.mark.parametrize(
    "reply",
    [
        "Assign to Kelly.",
        "Assign the order to rider R42.",
        "Assign ORD-03-001 to RDR-999.",
    ],
)
def test_a_name_after_a_preposition_is_still_caught(reply):
    guarded = check_reply("Who?", reply, results(snapshot()))
    assert "could not be identified" in guarded.text


@pytest.mark.parametrize(
    "reply",
    [
        "Assign either Kelly or Ravi to ORD-03-001.",
        "Extend for Kelly.",
        "assign to kelly",
        "Assign both Kelly and Ravi.",
        "Assign it to either Kelly or Ravi.",
        "Reassign the order to the new rider Kelly.",
    ],
)
@pytest.mark.parametrize("live", ["missing", "fresh", "stale"])
def test_a_name_behind_a_generic_word_is_still_caught(reply, live):
    data = snapshot()
    if live == "stale":
        data["stale"] = True
    guarded = check_reply(
        "Who?",
        reply,
        [] if live == "missing" else results(data),
    )
    assert reply not in guarded.text
    assert any(item["rule"] == "G4" for item in guarded.trace["findings"])
