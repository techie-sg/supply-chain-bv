import re
from types import SimpleNamespace

import requests

from evals.agent_evals import (
    checks,
    compare,
    rate_limit_wait,
    summary,
    table,
    transient_wait,
)

ROW = {
    "expected_tools": '["get_live_dispatch_status"]',
    "expected_guardrail": "not_blocked",
    "eval_focus": "retrieval",
    "expected_chunk_ids": ["DD-SOP-001#triage-sequence"],
    "relevant_chunk_ids": ["DD-SOP-001#triage-sequence"],
}
LABELS = {"DD-SOP-001#3": {"DD-SOP-001#triage-sequence"}}


def trace(tools, status="pass", chunks=()):
    return {
        "tools": [{"tool": tool} for tool in tools],
        "preferences": [],
        "guardrail": {"status": status, "findings": []},
        "retrieval": {"cache": "miss", "chunks": list(chunks), "ms": 1.0},
    }


def test_checks_pass_when_tools_guardrail_retrieval_and_latency_are_met():
    result = checks(
        ROW,
        trace(["get_live_dispatch_status"], chunks=["DD-SOP-001#3"]),
        LABELS,
        5,
        60,
    )
    assert result == {
        "tools": True,
        "latency": True,
        "not_blocked": True,
        "retrieval": True,
    }


def test_checks_fail_each_way():
    row = {**ROW, "expected_tools": '["get_delivery_metrics", "get_delivery_metrics"]'}
    result = checks(row, trace(["get_delivery_metrics"], "blocked"), LABELS, 90, 60)
    assert result == {
        "tools": False,  # one call where two were expected
        "latency": False,
        "not_blocked": False,
        "retrieval": False,
    }


def test_checks_skip_guardrail_and_retrieval_when_not_applicable():
    row = {
        **ROW,
        "expected_guardrail": "any",
        "eval_focus": "unsafe",
        "expected_tools": "[]",
    }
    assert checks(row, None, LABELS, 1, 60) == {"tools": True, "latency": True}


def result(id_, focus, passed, core=False):
    return {
        "id": id_,
        "eval_focus": focus,
        "pass": passed,
        "core": core,
        "failed": [] if passed else ["behavior"],
        "latency_s": 1.0,
    }


def test_summary_compare_and_table():
    before = [result("A", "tool", False, core=True), result("B", "unsafe", True)]
    after = [result("A", "tool", True, core=True), result("B", "unsafe", True)]
    old, new = summary(before), summary(after)
    assert old == {
        "core": 0.0,
        "overall": 0.5,
        "by_focus": {"tool": 0.0, "unsafe": 1.0},
        "rows": 2,
    }
    assert new["core"] == 1.0
    lines = compare({"summary": old, "rows": before}, {"summary": new, "rows": after})
    assert "| core | 0.0 | 1.0 |" in lines
    assert "- A: FAIL → PASS" in lines
    assert "| A | tool | FAIL | behavior | 1.0 |" in table(before)


def http_error(status, retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return requests.HTTPError(
        response=SimpleNamespace(status_code=status, headers=headers),
    )


def test_rate_limit_wait_retries_short_limits_and_stops_on_long_ones():
    assert rate_limit_wait(http_error(429, "7")) == 8
    assert rate_limit_wait(http_error(429)) == 61  # no header: wait a minute
    assert rate_limit_wait(http_error(429, "86400")) is None  # daily cap
    assert rate_limit_wait(http_error(500, "1")) is None
    assert rate_limit_wait(ValueError("boom")) is None


def test_judge_settings_have_no_fallback_models():
    from config import Settings
    from constants import OPENROUTER_MODEL
    from service.factory import create_llm_service

    settings = Settings(_env_file=None, llm_model=OPENROUTER_MODEL)  # type: ignore[call-arg]
    assert create_llm_service(settings).fallback_models  # the agent keeps its fallback
    judge = settings.model_copy(update={"openrouter_fallback_models": []})
    assert create_llm_service(judge).fallback_models == ()


def test_judge_prompt_has_no_sample_scores_to_copy():
    from evals.agent_evals import AGENT_JUDGE_PROMPT

    prompt = AGENT_JUDGE_PROMPT.format(
        safety_rule="s",
        live_rule="l",
        safety_slot="<1 or 0>",
        live_slot="null",
    )
    # A literal 0 or 1 in the example was copied verbatim by the judge model.
    assert not re.search(r'": [01]\b', prompt)


def test_capture_tool_outputs_collects_what_the_model_was_given():
    import json

    from domain.tools import Tool
    from evals.agent_evals import capture_tool_outputs, judge_facts
    from service import tools as dispatch_tools

    tool = Tool("get_x", "d", {}, lambda args: json.dumps({"n": args["n"] + 1}))
    original = dispatch_tools._record_call
    with capture_tool_outputs() as captured:
        traced = dispatch_tools.traced_tools([tool], [], [])
        traced[0].run({"n": 1})
    assert dispatch_tools._record_call is original  # restored afterwards
    assert captured == [{"tool": "get_x", "arguments": {"n": 1}, "result": '{"n": 2}'}]

    row = {"requires_live_data": False}
    facts = judge_facts(row, captured, "ALERTS", "SETTINGS")
    assert facts["tool_results"][0]["result"] == {"n": 2}
    assert facts["alerts"] == "ALERTS" and facts["settings"] == "SETTINGS"
    assert "live_data_the_agent_did_not_fetch" not in facts


def test_transient_wait_retries_provider_hiccups_only():
    assert transient_wait(http_error(503)) == 20
    assert transient_wait(RuntimeError("OpenRouter returned an error response.")) == 20
    assert transient_wait(http_error(400)) is None
    assert transient_wait(RuntimeError("Missing OPENROUTER_KEY")) is None
    assert transient_wait(ValueError("boom")) is None
