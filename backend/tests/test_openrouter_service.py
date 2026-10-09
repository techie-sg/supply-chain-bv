from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests
from pydantic import SecretStr

from constants import LLM_MAX_TOOL_ROUNDS, OPENROUTER_API_URL, OPENROUTER_MODEL
from domain.tools import Tool
from service import openrouter_service as openrouter
from service.openrouter_service import OpenRouterService


def reply(content="answer", calls=None, **extra):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls or [],
                    **extra,
                },
                "finish_reason": "tool_calls" if calls else "stop",
            },
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def post(self, url, **kwargs):
        self.sent.append((url, deepcopy(kwargs)))
        response = self.responses.pop(0)
        return (
            response
            if isinstance(response, requests.Response)
            else SimpleNamespace(ok=True, json=lambda: response)
        )


@pytest.fixture
def transport(monkeypatch):
    def install(*responses):
        fake = Transport(responses)
        monkeypatch.setattr(openrouter.requests, "post", fake.post)
        return fake

    return install


def service():
    return OpenRouterService(api_key=SecretStr("fake-key"))


def tool_call(name="propose", arguments='{"value": 300}', call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def echo_tool(called):
    def run(arguments):
        called.append(arguments)
        return f"Proposed {arguments['value']}; awaiting confirmation."

    return Tool(
        "propose",
        "Propose a change for confirmation.",
        {"type": "object"},
        run,
    )


def test_plain_generation_keeps_history_and_uses_the_free_model(transport):
    fake = transport(
        reply(
            "A grounded answer.",
            reasoning_details=[{"type": "reasoning.text", "text": "private reasoning"}],
        ),
    )
    model = service()
    assert fake.sent == []
    assert (
        model.generate(
            "policy",
            "Why?",
            history=[
                {"role": "user", "content": "Can I batch frozen orders?"},
                {"role": "assistant", "content": "No."},
            ],
        )
        == "A grounded answer."
    )
    url, request = fake.sent[0]
    assert url == OPENROUTER_API_URL
    assert request["headers"]["Authorization"] == "Bearer fake-key"
    assert request["timeout"] == (10, 120)
    payload = request["json"]
    assert payload["model"] == OPENROUTER_MODEL
    assert payload["reasoning"] == {"enabled": False}
    assert payload["temperature"] == 0
    assert "tools" not in payload and "tool_choice" not in payload
    assert payload["messages"] == [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "Can I batch frozen orders?"},
        {"role": "assistant", "content": "No."},
        {"role": "user", "content": "Why?"},
    ]


def test_key_is_loaded_only_when_generation_starts(monkeypatch, transport):
    loaded = []

    def settings():
        loaded.append(True)
        return SimpleNamespace(openrouter_api_key=SecretStr("runtime-key"))

    monkeypatch.setattr(openrouter, "get_settings", settings)
    fake = transport(reply())
    model = OpenRouterService()
    assert loaded == []
    assert model.generate("policy", "question") == "answer"
    assert loaded == [True]
    assert fake.sent[0][1]["headers"]["Authorization"] == "Bearer runtime-key"


def test_missing_key_fails_before_sending_a_request(transport):
    fake = transport()
    with pytest.raises(RuntimeError, match="Set OPENROUTER_KEY"):
        OpenRouterService().generate("policy", "question")
    assert fake.sent == []


def test_tool_results_and_reasoning_details_reach_the_next_request(transport):
    details = [{"type": "reasoning.encrypted", "data": "opaque", "id": "r1"}]
    fake = transport(
        reply(None, [tool_call()], reasoning_details=details),
        reply("Confirm the proposed change."),
    )
    called = []
    assert (
        service().generate_with_tools("policy", "Change my cap", [echo_tool(called)])
        == "Confirm the proposed change."
    )
    assert called == [{"value": 300}]
    first, second = [request["json"] for _, request in fake.sent]
    assert first["tools"] == second["tools"]
    assert first["provider"] == {"require_parameters": True}
    assert first["tool_choice"] == second["tool_choice"] == "auto"
    assert second["messages"][-2]["reasoning_details"] == details
    assert second["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "Proposed 300; awaiting confirmation.",
    }


@pytest.mark.parametrize("arguments", ["bad-json", "[]", "null", None])
def test_invalid_tool_arguments_are_reported_without_executing(transport, arguments):
    fake = transport(reply(None, [tool_call(arguments=arguments)]), reply())
    called = []
    assert service().generate_with_tools("s", "u", [echo_tool(called)]) == "answer"
    assert called == []
    assert (
        "Invalid tool arguments" in fake.sent[-1][1]["json"]["messages"][-1]["content"]
    )


def test_multiple_and_unknown_tools_keep_their_result_ids(transport):
    fake = transport(
        reply(
            None,
            [tool_call("missing", call_id="unknown"), tool_call(call_id="known")],
        ),
        reply(),
    )
    called = []
    service().generate_with_tools("s", "u", [echo_tool(called)])
    assert called == [{"value": 300}]
    messages = fake.sent[-1][1]["json"]["messages"][-2:]
    assert messages[0]["tool_call_id"] == "unknown"
    assert messages[0]["content"] == "There is no tool named missing."
    assert messages[1]["tool_call_id"] == "known"


def test_tool_rounds_are_bounded_without_reexecuting_actions(transport):
    fake = transport(
        *[
            reply(None, [tool_call(call_id=f"c{index}")])
            for index in range(LLM_MAX_TOOL_ROUNDS)
        ],
        reply("Final answer."),
    )
    called = []
    assert (
        service().generate_with_tools("s", "u", [echo_tool(called)]) == "Final answer."
    )
    assert len(called) == LLM_MAX_TOOL_ROUNDS
    final = fake.sent[-1][1]["json"]
    assert "tools" not in final and "tool_choice" not in final
    assert "No more tools" in final["messages"][-1]["content"]
    assert len(fake.sent) == LLM_MAX_TOOL_ROUNDS + 1


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("content", [None, "", "  "])
def test_empty_answers_are_not_saved_as_replies(transport, content, with_tools):
    transport(reply(content))
    with pytest.raises(RuntimeError, match="empty answer"):
        if with_tools:
            service().generate_with_tools("s", "u", [echo_tool([])])
        else:
            service().generate("s", "u")


@pytest.mark.parametrize("code", [401, 429, 500])
def test_http_errors_are_reported_without_exposing_provider_text(transport, code):
    response = requests.Response()
    response.status_code = code
    response._content = b'{"error":{"message":"private input or key"}}'
    fake = transport(response)
    with pytest.raises(
        requests.HTTPError,
        match=f"OpenRouter generation failed \\({code}\\)",
    ) as error:
        service().generate("s", "u")
    assert "private" not in str(error.value)
    assert len(fake.sent) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "private"}},
        {},
        {"choices": []},
        {"choices": [{"message": {"role": "user", "content": "wrong"}}]},
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "partial"},
                    "finish_reason": "length",
                },
            ],
        },
    ],
)
def test_invalid_completions_are_rejected(transport, body):
    transport(body)
    with pytest.raises(RuntimeError):
        service().generate("s", "u")


def test_error_after_a_tool_does_not_replay_the_action(transport):
    response = requests.Response()
    response.status_code = 429
    transport(reply(None, [tool_call()]), response)
    called = []
    with pytest.raises(requests.HTTPError):
        service().generate_with_tools("s", "u", [echo_tool(called)])
    assert called == [{"value": 300}]


def test_empty_tool_list_uses_plain_generation(transport):
    fake = transport(reply())
    assert service().generate_with_tools("s", "u", []) == "answer"
    assert "tools" not in fake.sent[0][1]["json"]
