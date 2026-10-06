"""Tests for service/agent.py — tool-calling loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import service.agent as agent_mod
from service.agent import (
    AgentResult,
    _build_messages,
    _dispatch,
    _make_result,
    answer_with_tools,
    run_agent,
)

# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


def test_make_result_returns_agent_result_with_expected_keys() -> None:
    r = _make_result(answer="ok", trace=[], llm_calls=1)
    assert isinstance(r, AgentResult)
    assert r["answer"] == "ok"
    assert r["trace"] == []
    assert r["llm_calls"] == 1


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------


def test_build_messages_structure() -> None:
    msgs = _build_messages("What is the queue?", "S-1", history=None)
    assert msgs[0]["role"] == "system"
    assert "S-1" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "What is the queue?"}


def test_build_messages_includes_history() -> None:
    history = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    msgs = _build_messages("Follow-up?", "S-1", history=history)
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user"]


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------


def test_dispatch_calls_registered_tool() -> None:
    called = {}

    def fake_tool(**kwargs):
        called.update(kwargs)
        return {"result": "ok"}

    with patch.dict(agent_mod.TOOL_REGISTRY, {"my_tool": fake_tool}):
        call = {
            "function": {
                "name": "my_tool",
                "arguments": json.dumps({"store_id": "S-1"}),
            },
        }
        output = _dispatch(call)
    assert json.loads(output) == {"result": "ok"}
    assert called == {"store_id": "S-1"}


def test_dispatch_unknown_tool_returns_error() -> None:
    call = {"function": {"name": "no_such_tool", "arguments": "{}"}}
    result = json.loads(_dispatch(call))
    assert result["error"]["code"] == "UNKNOWN_TOOL"


def test_dispatch_invalid_json_arguments_returns_error() -> None:
    with patch.dict(agent_mod.TOOL_REGISTRY, {"t": dict}):
        call = {"function": {"name": "t", "arguments": "not json"}}
        result = json.loads(_dispatch(call))
    assert result["error"]["code"] == "INVALID_INPUT"


def test_dispatch_wrong_kwargs_returns_error() -> None:
    def strict_tool(store_id: str) -> dict:
        return {}

    with patch.dict(agent_mod.TOOL_REGISTRY, {"strict": strict_tool}):
        call = {"function": {"name": "strict", "arguments": json.dumps({"bad_key": 1})}}
        result = json.loads(_dispatch(call))
    assert result["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------


def _llm_answer(content: str) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content, "tool_calls": None}},
        ],
    }


def _llm_tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "function": {"name": name, "arguments": json.dumps(args)},
                        },
                    ],
                },
            },
        ],
    }


def test_run_agent_direct_answer_no_tool_calls() -> None:
    with patch.object(agent_mod, "_call_llm", return_value=_llm_answer("All good.")):
        result = run_agent("How are things?", store_id="S-1")
    assert result["answer"] == "All good."
    assert result["llm_calls"] == 1
    assert result["trace"] == []


def test_run_agent_one_tool_call_then_answer() -> None:
    responses = [
        _llm_tool_call("get_live_dispatch_status", {"store_id": "S-1"}),
        _llm_answer("Queue looks fine."),
    ]
    fake_dispatch = MagicMock(return_value=json.dumps({"store_id": "S-1", "queue": {}}))
    with (
        patch.object(agent_mod, "_call_llm", side_effect=responses),
        patch.object(agent_mod, "_dispatch", fake_dispatch),
    ):
        result = run_agent("What's the queue?", store_id="S-1")
    assert result["answer"] == "Queue looks fine."
    assert result["llm_calls"] == 2
    assert len(result["trace"]) == 1
    assert result["trace"][0]["tool"] == "get_live_dispatch_status"


def test_run_agent_hits_max_steps() -> None:
    # Always returns a tool call — agent never finishes
    with (
        patch.object(
            agent_mod,
            "_call_llm",
            return_value=_llm_tool_call(
                "get_live_dispatch_status",
                {"store_id": "S-1"},
            ),
        ),
        patch.object(
            agent_mod,
            "_dispatch",
            return_value=json.dumps({"store_id": "S-1"}),
        ),
    ):
        result = run_agent("Loop forever", store_id="S-1", max_steps=2)
    assert "Stopped" in result["answer"]
    assert result["llm_calls"] == 2


# ---------------------------------------------------------------------------
# answer_with_tools
# ---------------------------------------------------------------------------


def test_answer_with_tools_returns_answer_string() -> None:
    with patch.object(agent_mod, "_call_llm", return_value=_llm_answer("Done.")):
        answer = answer_with_tools("Any question?", store_id="S-1")
    assert answer == "Done."
