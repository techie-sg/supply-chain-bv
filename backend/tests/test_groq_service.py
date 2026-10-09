from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from service import groq_service as groq
from service.groq_service import GroqService


def test_groq_client_is_lazy_reused_and_keeps_message_roles(monkeypatch) -> None:
    created = []
    seen = []

    def client(**kwargs):
        created.append(kwargs)

        def invoke(messages):
            seen.extend(messages)
            return SimpleNamespace(text="answer")

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(groq, "ChatGroq", client)
    service = GroqService(model="test-model", api_key=SecretStr("test-key"))
    assert created == []
    assert service.generate("system", "question") == "answer"
    assert service.generate("system", "follow up") == "answer"
    assert len(created) == 1
    assert created[0]["model"] == "test-model"
    assert created[0]["api_key"].get_secret_value() == "test-key"
    assert created[0]["temperature"] == 0
    assert [(message.type, message.content) for message in seen] == [
        ("system", "system"),
        ("human", "question"),
        ("system", "system"),
        ("human", "follow up"),
    ]


def test_groq_loads_runtime_credentials_on_first_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        groq,
        "get_settings",
        lambda: SimpleNamespace(groq_api_key=SecretStr("runtime")),
    )

    def client(**kwargs):
        assert kwargs["api_key"].get_secret_value() == "runtime"
        return SimpleNamespace(invoke=lambda messages: SimpleNamespace(text="answer"))

    monkeypatch.setattr(groq, "ChatGroq", client)
    assert GroqService().generate("system", "question") == "answer"


def test_groq_preserves_previous_user_and_assistant_turns(monkeypatch) -> None:
    seen = []

    def invoke(messages):
        seen.extend(messages)
        return SimpleNamespace(text="follow-up answer")

    monkeypatch.setattr(
        groq,
        "ChatGroq",
        lambda **kwargs: SimpleNamespace(invoke=invoke),
    )
    service = GroqService(api_key=SecretStr("test-key"))
    assert (
        service.generate(
            "system",
            "Retrieved context: policy\nQuestion: Why?",
            history=[
                {"role": "user", "content": "Should riders jump red lights?"},
                {"role": "assistant", "content": "No. Safety comes first."},
            ],
        )
        == "follow-up answer"
    )
    assert [(message.type, message.content) for message in seen] == [
        ("system", "system"),
        ("human", "Should riders jump red lights?"),
        ("ai", "No. Safety comes first."),
        ("human", "Retrieved context: policy\nQuestion: Why?"),
    ]


class FakeGroqClient:
    """Fake ChatGroq: replies from a script, recording each bound call."""

    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.bound: list[tuple[list, str]] = []
        self.seen: list[list] = []

    def bind_tools(self, schemas, tool_choice):
        self.bound.append((schemas, tool_choice))
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        return self.replies.pop(0)


def text_reply(text):
    return SimpleNamespace(text=text, tool_calls=[])


def echo_tool(calls):
    from service.llm_service import Tool

    def run(args):
        calls.append(args)
        return f"proposed {args['value']}"

    return Tool("propose", "Propose a change.", {"type": "object"}, run)


def test_tool_calls_run_and_their_results_reach_the_model(monkeypatch) -> None:
    from langchain_core.messages import AIMessage

    client = FakeGroqClient(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "propose", "args": {"value": 2}, "id": "c1"}],
            ),
            text_reply("Proposed; press Confirm."),
        ],
    )
    service = GroqService(api_key=SecretStr("key"))
    monkeypatch.setattr(GroqService, "_client", client)
    calls: list[dict] = []
    answer = service.generate_with_tools("system", "set it", [echo_tool(calls)])
    assert answer == "Proposed; press Confirm."
    assert calls == [{"value": 2}]
    assert client.bound[0][0][0]["function"]["name"] == "propose"
    assert [choice for _, choice in client.bound] == ["auto", "auto"]
    final = client.seen[-1]
    assert [message.type for message in final] == ["system", "human", "ai", "tool"]
    assert final[-1].content == "proposed 2" and final[-1].tool_call_id == "c1"


def test_unknown_tools_are_reported_and_tool_rounds_are_capped(monkeypatch) -> None:
    from langchain_core.messages import AIMessage

    def call(name):
        return AIMessage(
            content="",
            tool_calls=[{"name": name, "args": {"value": 1}, "id": name}],
        )

    client = FakeGroqClient(
        [call("missing"), call("propose"), call("propose"), text_reply("done")],
    )
    service = GroqService(api_key=SecretStr("key"))
    monkeypatch.setattr(GroqService, "_client", client)
    calls: list[dict] = []
    assert service.generate_with_tools("s", "u", [echo_tool(calls)]) == "done"
    assert len(calls) == 2
    assert client.seen[1][-1].content == "There is no tool named missing."
    assert [choice for _, choice in client.bound][-1] == "none"
    assert len(client.bound) == groq.MAX_TOOL_ROUNDS + 1


def rejection():
    import httpx

    return groq.groq.BadRequestError(
        "tool_use_failed",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://api.groq.com"),
        ),
        body={"error": {"code": "tool_use_failed"}},
    )


class Rejecting:
    """Rejects the first `failures` tool requests, then answers."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.seen: list[list] = []

    def bind_tools(self, schemas, tool_choice):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        if self.failures:
            self.failures -= 1
            raise rejection()
        return text_reply("answer")


def test_a_rejected_tool_call_is_retried_once(monkeypatch) -> None:
    client = Rejecting(failures=1)
    monkeypatch.setattr(GroqService, "_client", client)
    service = GroqService(api_key=SecretStr("key"))
    assert service.generate_with_tools("s", "u", [echo_tool([])]) == "answer"
    assert len(client.seen) == 2
    assert client.seen[-1][-1].content == "u"


def test_repeated_rejections_answer_without_tools_and_say_so(monkeypatch) -> None:
    client = Rejecting(failures=groq.TOOL_ATTEMPTS)
    monkeypatch.setattr(GroqService, "_client", client)
    service = GroqService(api_key=SecretStr("key"))
    assert service.generate_with_tools("s", "u", [echo_tool([])]) == "answer"
    assert len(client.seen) == groq.TOOL_ATTEMPTS + 1
    final = client.seen[-1][-1].content
    assert final.startswith("u\n\n") and groq.TOOLS_UNAVAILABLE in final
    assert "Do not say you proposed" in groq.TOOLS_UNAVAILABLE


def test_without_tools_the_plain_path_is_used(monkeypatch) -> None:
    client = FakeGroqClient([text_reply("plain")])
    monkeypatch.setattr(GroqService, "_client", client)
    assert (
        GroqService(api_key=SecretStr("k")).generate_with_tools("s", "u", []) == "plain"
    )
    assert client.bound == []


@pytest.mark.parametrize("with_tools", [False, True])
def test_empty_answers_are_rejected_before_being_saved(monkeypatch, with_tools) -> None:
    client = FakeGroqClient([text_reply(" ")])
    monkeypatch.setattr(GroqService, "_client", client)
    service = GroqService(api_key=SecretStr("fake-key"))
    with pytest.raises(RuntimeError, match="empty answer"):
        if with_tools:
            service.generate_with_tools("system", "question", [echo_tool([])])
        else:
            service.generate("system", "question")
