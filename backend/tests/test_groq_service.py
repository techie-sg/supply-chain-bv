from types import SimpleNamespace

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
