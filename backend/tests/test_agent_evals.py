from evals.agent_evals import checks, compare, summary, table

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
