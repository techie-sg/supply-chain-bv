"""Uncited model prose cannot cross the policy boundary, including mixed replies."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from constants import NO_GUIDANCE_ANSWER
from domain.tools import Tool
from service import rag
from service.cache import TTLCache

SOURCE = {
    "chunk_id": "DD-BATCH-001#0",
    "content": "Frozen orders require separate delivery.",
}
LIVE = {
    "as_of": "2026-10-22T18:00:00+05:30",
    "stale": False,
    "queue": {
        "open_orders": 7,
        "packed_waiting": 3,
        "orders": [
            {
                "order_id": "ORDER-1",
                "status": "packed_waiting_rider",
                "zone_id": "A",
                "item_count": 4,
                "has_frozen_items": True,
            },
        ],
    },
    "summary": {"available_riders": 1, "riders_returning_within_10_min": 2},
    "riders": [
        {
            "rider_id": "RIDER-1",
            "name": "Arun",
            "status": "available",
            "hours_on_shift": 2,
            "minutes_since_last_break": 30,
        },
    ],
}
METRICS = {
    "period": {
        "date": "2026-10-22",
        "start_hour": 20,
        "end_hour": 22,
        "hours_missing": [21],
    },
    "period_summary": {
        "total_orders": 23,
        "sla_10min_pct": 47.8,
        "avg_pick_pack_min": 3.2,
        "avg_rider_wait_min": 6.1,
        "avg_ride_min": 5.3,
    },
    "hourly": [
        {
            "hour": 20,
            "orders": 23,
            "sla_10min_pct": 47.8,
            "avg_pick_pack_min": 3.2,
            "avg_rider_wait_min": 6.1,
            "avg_ride_min": 5.3,
        },
    ],
}


def result(name, data):
    return {"tool": name, "result": data}


class Model:
    def __init__(self, answer, calls=()):
        self.answer, self.calls = answer, calls
        self.requests = 0

    def generate_with_tools(self, system_prompt, user_message, tools, history=None):
        self.requests += 1
        by_name = {tool.name: tool for tool in tools}
        for name, arguments in self.calls:
            by_name[name].run(arguments)
        return self.answer


def service(model):
    return rag.RAGService(
        SimpleNamespace(model="test", embed_query=lambda query: [0.1]),
        model,
        cache=TTLCache(0, 10),
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Frozen-item orders may be batched together.",
        "Blend the cold baskets into one trip.",
        "Offer riders extra pay to drive faster.",
        "There are 7 orders. Batch all frozen orders together.",
    ],
)
def test_known_policy_request_cannot_return_unsourced_recommendation_variants(
    answer,
):
    model = Model(answer)
    assert (
        service(model).answer_question("Can I batch these orders?")
        == NO_GUIDANCE_ANSWER
    )
    assert model.requests == 1


@pytest.mark.parametrize(
    "answer",
    [
        "Frozen-item orders may be batched together.",
        "Frozen orders need separate delivery.",
        "Offer an incentive to the riders.",
        "Raise the incentive cap.",
    ],
)
def test_generic_status_request_does_not_excuse_unsourced_policy_in_the_answer(answer):
    model = Model(answer)
    assert service(model).answer_question("Current store status?") == NO_GUIDANCE_ANSWER


@pytest.mark.parametrize("failed", [False, True])
def test_successful_but_uncited_or_failed_search_cannot_authorize_advice(
    monkeypatch,
    failed,
):
    def retrieve(**kwargs):
        if failed:
            raise RuntimeError("offline search failure")
        return [SOURCE]

    monkeypatch.setattr(rag, "retrieve", retrieve)
    model = Model(
        "Frozen orders may be batched.",
        [("search_playbook", {"query": "frozen batching"})],
    )
    assert (
        service(model).answer_question("Can I batch frozen orders?")
        == NO_GUIDANCE_ANSWER
    )
    assert model.requests == 1


@pytest.mark.parametrize("citation", ["DD-BATCH-001#0", "DD-BATCH-001"])
def test_current_successful_search_and_valid_citation_keep_normal_answer(
    monkeypatch,
    citation,
):
    monkeypatch.setattr(rag, "retrieve", lambda **kwargs: [SOURCE])
    model = Model(
        f"Use a separate delivery ({citation}).",
        [("search_playbook", {"query": "frozen batching"})],
    )
    assert service(model).answer_question("Can I batch frozen orders?") == model.answer


def test_previous_turn_sources_and_history_citations_do_not_authorize_this_turn():
    previous_results = [result("search_playbook", {"sources": [SOURCE]})]
    model = Model("Use a separate delivery (DD-BATCH-001#0).")
    assert (
        service(model).answer_question(
            "Again?",
            history=[{"role": "assistant", "content": model.answer}],
            tool_results=previous_results,
        )
        == NO_GUIDANCE_ANSWER
    )


@pytest.mark.parametrize(
    "sources",
    [[], [{"chunk_id": SOURCE["chunk_id"]}], [{**SOURCE, "content": ""}]],
)
def test_incomplete_or_empty_sources_do_not_authorize_a_citation(sources):
    assert (
        rag.RAGService._check_sources(
            "Allowed (DD-BATCH-001#0).",
            [result("search_playbook", {"sources": sources})],
        )
        == NO_GUIDANCE_ANSWER
    )


def test_facts_without_sop_are_rendered_from_tool_data_not_model_advice():
    answer = rag.RAGService._check_sources(
        "There are 999 orders. Batch all frozen orders together.",
        [result("get_live_dispatch_status", LIVE)],
        question="What is the current store status?",
    )
    assert "**Open orders:** 7" in answer
    assert "**Packed and waiting:** 3" in answer
    assert "18:00" in answer
    assert "999" not in answer and "Batch all frozen" not in answer
    assert "Arun" not in answer and "ORDER-1" not in answer
    assert NO_GUIDANCE_ANSWER not in answer


@pytest.mark.parametrize("stale", [False, True])
def test_bad_citation_rejects_narrative_without_erasing_verified_counts(stale):
    answer = rag.RAGService._check_sources(
        "There are 999 orders. Batch all frozen orders (DD-UNKNOWN-999#0).",
        [result("get_live_dispatch_status", {**LIVE, "stale": stale})],
        question="Current store status?",
    )
    assert "**Open orders:** 7" in answer and "18:00" in answer
    assert "999" not in answer and "DD-UNKNOWN" not in answer
    assert "Batch all frozen" not in answer
    assert ("stale" in answer) is stale


def test_bad_citation_cannot_reuse_verified_facts_from_an_earlier_turn():
    old_results = [result("get_live_dispatch_status", LIVE)]
    model = Model("There are 999 orders (DD-UNKNOWN-999#0).")
    assert (
        service(model).answer_question(
            "Current store status?",
            tool_results=old_results,
        )
        == NO_GUIDANCE_ANSWER
    )


def test_failed_search_keeps_verified_facts_with_an_explicit_advice_limitation():
    answer = rag.RAGService._check_sources(
        "Batch frozen orders.",
        [
            result(
                "search_playbook",
                {"error": {"code": "GUIDANCE_UNAVAILABLE"}, "sources": []},
            ),
            result("get_live_dispatch_status", LIVE),
        ],
        question="What is waiting and what should I do?",
    )
    assert "**Open orders:** 7" in answer
    assert "can't verify a policy recommendation" in answer
    assert "Batch frozen orders" not in answer


@pytest.mark.parametrize(
    ("question", "included", "excluded"),
    [
        ("Which orders are waiting?", "ORDER-1", "Arun"),
        ("Which riders are available?", "Arun", "ORDER-1"),
    ],
)
def test_details_are_shown_only_when_requested(question, included, excluded):
    answer = rag.RAGService._check_sources(
        "Batch all orders.",
        [result("get_live_dispatch_status", LIVE)],
        question=question,
    )
    assert included in answer and excluded not in answer


def test_stale_snapshot_is_labeled_and_not_presented_as_current():
    snapshot = {**LIVE, "stale": True}
    answer = rag.RAGService._check_sources(
        "Dispatch now.",
        [result("get_live_dispatch_status", snapshot)],
        question="Current status?",
    )
    assert "stale" in answer and "out of date" in answer and "18:00" in answer
    assert "Dispatch now" not in answer


def test_historical_metrics_report_actual_period_values_and_missing_hours():
    answer = rag.RAGService._check_sources(
        "SLA is 100%. Speed up.",
        [result("get_delivery_metrics", METRICS)],
        question="Show hourly metrics",
    )
    assert "23" in answer and "47.8" in answer and "6.1" in answer
    assert "2026-10-22" in answer and "Missing hours: 21" in answer
    assert "| Hour |" in answer
    assert "Speed up" not in answer and "100%" not in answer


@pytest.mark.parametrize(
    "data",
    [
        {"error": {"code": "NO_SNAPSHOT"}},
        {"queue": {"open_orders": 7}},
        {**LIVE, "as_of": "not-a-time"},
    ],
)
def test_failed_or_unverifiable_live_tool_data_does_not_become_a_factual_report(data):
    assert (
        rag.RAGService._check_sources(
            "Batch the orders.",
            [result("get_live_dispatch_status", data)],
            question="Store status?",
        )
        == NO_GUIDANCE_ANSWER
    )


@pytest.mark.parametrize(
    "question",
    ["Thanks!", "Keep answers short for me.", "What??"],
)
def test_conversational_request_does_not_excuse_unsafe_policy_added_by_model(question):
    answer = rag.RAGService._check_sources(
        "Frozen orders may be batched.",
        [],
        question=question,
    )
    assert answer == NO_GUIDANCE_ANSWER


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (
            "Please keep answers simple; I am not technical",
            "Of course. I'll use plain language and explain anything unfamiliar.",
        ),
        ("Why so long?", "I gave more detail than you needed. I'll keep this brief."),
        ("What??", "Which part would you like me to clarify?"),
        (
            "Should I ask riders to jump the line?",
            "Do you mean queue priority or traffic signals?",
        ),
        ("What is my name?", "Your name is Sunny."),
        ("Thanks", "You're welcome. Let me know what else you'd like to look at."),
    ],
)
def test_natural_style_recall_and_clarification_replies_are_preserved(question, answer):
    history = [
        {"role": "user", "content": "Can I batch frozen orders?"},
        {
            "role": "assistant",
            "content": "Frozen items require separate delivery (DD-BATCH-001#0).",
        },
    ]
    assert (
        rag.RAGService._check_sources(answer, [], question=question, history=history)
        == answer
    )


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (
            "What can you do?",
            "I can help you understand store policies. Which policy do you mean?",
        ),
        (
            "What can you do?",
            "I can help you understand store policies, check orders and riders, and draft customer updates. What would you like to look at?",
        ),
        (
            "Help me with our store policy",
            "I can help you understand store policies. Which policy do you mean?",
        ),
        (
            "Can you explain the playbook?",
            "I can help you understand the playbook.",
        ),
        (
            "Explain the batching policy",
            "Are you asking whether frozen orders can be batched?",
        ),
        (
            "What is our policy?",
            "Could you clarify which policy you mean?",
        ),
    ],
)
def test_policy_capabilities_and_questions_are_not_substantive_advice(question, answer):
    assert rag.RAGService._check_sources(answer, [], question=question) == answer


@pytest.mark.parametrize(
    "answer",
    [
        "Frozen orders may be batched. Which policy do you mean?",
        "The policy requires separate delivery. Which part should I explain?",
        "According to the store policy, separate delivery is optional. What would you like to know?",
        "I can help you understand store policy. Batch all frozen orders. Which part should I clarify?",
        "Batch frozen orders; which policy do you mean?",
        "I can explain that frozen orders are allowed to be batched.",
        "I can help you check orders and riders. You should batch frozen orders. What would you like to look at?",
    ],
)
def test_clarification_or_capability_cannot_hide_a_substantive_policy_claim(answer):
    assert (
        rag.RAGService._check_sources(answer, [], question="What can you do?")
        == NO_GUIDANCE_ANSWER
    )


def test_mixed_style_and_operational_request_cannot_bypass_evidence():
    assert (
        rag.RAGService._check_sources(
            "Batch frozen orders.",
            [],
            question="Keep answers short and tell me whether to batch frozen orders",
        )
        == NO_GUIDANCE_ANSWER
    )


def test_contextual_policy_followup_still_requires_fresh_evidence():
    assert (
        rag.RAGService._check_sources(
            "Yes, put them together.",
            [],
            question="What??",
            history=[{"role": "user", "content": "Can I batch frozen orders?"}],
        )
        == NO_GUIDANCE_ANSWER
    )


def test_settings_response_depends_on_actual_tool_result_not_model_claim():
    proposed = result(
        "propose_setting_change",
        {
            "status": "proposed",
            "setting": "Incentive cap",
            "from": "300",
            "to": "350",
            "saved": False,
        },
    )
    answer = rag.RAGService._check_sources(
        "I saved it and dispatched the rider.",
        [proposed],
        question="Change my cap",
    )
    assert "300" in answer and "350" in answer and "Nothing is saved" in answer
    assert "dispatched" not in answer
    assert (
        rag.RAGService._check_sources(
            "Raise the incentive cap.",
            [],
            question="Should I change my incentive cap?",
        )
        == NO_GUIDANCE_ANSWER
    )


def test_live_fallback_works_through_actual_tool_execution_without_extra_calls():
    calls = []
    live = Tool(
        "get_live_dispatch_status",
        "Read facts",
        {},
        lambda args: calls.append(args) or json.dumps(deepcopy(LIVE)),
    )
    model = Model("Batch all frozen orders.", [(live.name, {})])
    answer = service(model).answer_question("Store status?", tools=[live])
    assert "**Open orders:** 7" in answer and "Batch all frozen" not in answer
    assert model.requests == 1 and calls == [{}]
