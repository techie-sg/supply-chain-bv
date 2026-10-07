"""Tests for service/agent.py — tool-calling loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import service.agent as agent_mod
from domain.chat import ChatMessage
from service.agent import (
    AgentResult,
    ToolClient,
    _build_messages,
    _dispatch,
    _make_result,
    _trace_entry,
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
    history: list[ChatMessage] = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    msgs = _build_messages("Follow-up?", "S-1", history=history)
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user"]


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------


class FakeClient:
    """Stands in for ToolClient: no subprocess, records calls."""

    def __init__(self, output: str = '{"store_id": "S-1"}') -> None:
        self.output = output
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def tool_definitions(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "get_live_dispatch_status"}}]

    def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self.output


def test_dispatch_sends_name_and_arguments_to_the_client() -> None:
    client = FakeClient('{"result": "ok"}')
    call = {
        "function": {"name": "my_tool", "arguments": json.dumps({"store_id": "S-1"})},
    }
    assert json.loads(_dispatch(call, client)) == {"result": "ok"}
    assert client.calls == [("my_tool", {"store_id": "S-1"})]


def test_dispatch_invalid_json_arguments_returns_error() -> None:
    client = FakeClient()
    call = {"function": {"name": "t", "arguments": "not json"}}
    assert json.loads(_dispatch(call, client))["error"]["code"] == "INVALID_INPUT"
    assert client.calls == []


def test_trace_entry_survives_a_non_json_result() -> None:
    call = {"function": {"name": "t", "arguments": "{}"}}
    entry = _trace_entry(1, call, "plain text")
    assert entry["error"] is None and entry["as_of"] is None


# ---------------------------------------------------------------------------
# ToolClient against the real MCP server (no database needed for these calls)
# ---------------------------------------------------------------------------


def test_tool_client_lists_tools_and_rejects_bad_arguments() -> None:
    with ToolClient() as client:
        names = {d["function"]["name"] for d in client.tool_definitions()}
        assert names == {"get_live_dispatch_status", "get_delivery_metrics"}
        bad = json.loads(client.call("get_delivery_metrics", {"store_id": "S-1"}))
    assert bad["error"]["code"] == "INVALID_INPUT"


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
        result = run_agent("How are things?", store_id="S-1", client=FakeClient())
    assert result["answer"] == "All good."
    assert result["llm_calls"] == 1
    assert result["trace"] == []


def test_run_agent_one_tool_call_then_answer() -> None:
    responses = [
        _llm_tool_call("get_live_dispatch_status", {"store_id": "S-1"}),
        _llm_answer("Queue looks fine."),
    ]
    client = FakeClient(json.dumps({"store_id": "S-1", "queue": {}}))
    with patch.object(agent_mod, "_call_llm", side_effect=responses):
        result = run_agent("What's the queue?", store_id="S-1", client=client)
    assert client.calls == [("get_live_dispatch_status", {"store_id": "S-1"})]
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
    ):
        result = run_agent(
            "Loop forever",
            store_id="S-1",
            max_steps=2,
            client=FakeClient(),
        )
    assert "Stopped" in result["answer"]
    assert result["llm_calls"] == 2


# ---------------------------------------------------------------------------
# answer_with_tools
# ---------------------------------------------------------------------------


def test_answer_with_tools_returns_answer_string() -> None:
    with (
        patch.object(agent_mod, "ToolClient", FakeClient),
        patch.object(agent_mod, "_call_llm", return_value=_llm_answer("Done.")),
    ):
        answer = answer_with_tools("Any question?", store_id="S-1")
    assert answer == "Done."


def test_call_llm_retries_a_rate_limit_then_succeeds() -> None:
    limited = MagicMock(status_code=429, headers={"retry-after": "1"})
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"choices": []}
    with (
        patch.object(agent_mod.requests, "post", side_effect=[limited, ok]) as post,
        patch.object(agent_mod.time, "sleep") as sleep,
        patch.object(agent_mod, "require", return_value="key"),
    ):
        assert agent_mod._call_llm([], []) == {"choices": []}
    assert post.call_count == 2
    sleep.assert_called_once_with(1.5)


def test_run_agent_rejects_an_empty_final_answer() -> None:
    with patch.object(agent_mod, "_call_llm", return_value=_llm_answer("")):
        try:
            run_agent("Anything?", store_id="S-1", client=FakeClient())
        except RuntimeError as exc:
            assert "empty answer" in str(exc)
        else:
            raise AssertionError("an empty answer must not be returned")
