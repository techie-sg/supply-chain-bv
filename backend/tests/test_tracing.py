"""Tracing keeps complete redacted payloads without changing application behavior."""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from pydantic import SecretStr

from config import Settings
from service import tracing


@dataclass
class FakeSDK:
    fail_at: str | None = None
    constructors: list[dict[str, Any]] = field(default_factory=list)
    exporter_constructors: list[dict[str, Any]] = field(default_factory=list)
    starts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    exits: list[tuple[Any, Any, Any]] = field(default_factory=list)
    shutdowns: int = 0
    exporter_shutdowns: int = 0

    def fail(self, stage: str) -> None:
        if self.fail_at == stage:
            raise RuntimeError(f"SDK {stage} failure")


class FakeObservation:
    def __init__(self, sdk: FakeSDK) -> None:
        self.sdk = sdk

    def update(self, **fields: Any) -> None:
        self.sdk.fail("update")
        self.sdk.updates.append(deepcopy(fields))

    @property
    def trace_id(self) -> str:
        self.sdk.fail("context")
        return "fake-trace-id"

    @property
    def id(self) -> str:
        self.sdk.fail("context")
        return "fake-span-id"


class FakeExporter:
    def __init__(self, sdk: FakeSDK) -> None:
        self.sdk = sdk

    def export(self, spans):
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.sdk.exporter_shutdowns += 1

    def force_flush(self, timeout_millis=30_000) -> bool:
        return True


class FakeContext:
    def __init__(self, sdk: FakeSDK, attributes: bool = False) -> None:
        self.sdk = sdk
        self.attributes = attributes

    def __enter__(self) -> FakeObservation:
        self.sdk.fail("attributes_enter" if self.attributes else "enter")
        return FakeObservation(self.sdk)

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> Literal[False]:
        self.sdk.exits.append((exc_type, exc, traceback))
        self.sdk.fail("attributes_exit" if self.attributes else "exit")
        return False


class FakeClient:
    def __init__(self, sdk: FakeSDK) -> None:
        self.sdk = sdk

    def start_as_current_observation(self, **fields: Any) -> FakeContext:
        self.sdk.fail("start")
        self.sdk.starts.append(deepcopy(fields))
        return FakeContext(self.sdk)

    def shutdown(self) -> None:
        self.sdk.shutdowns += 1
        self.sdk.fail("shutdown")


@pytest.fixture
def sdk(monkeypatch):
    state = FakeSDK()

    def construct(**kwargs):
        state.constructors.append(
            {
                name: value if name == "span_exporter" else deepcopy(value)
                for name, value in kwargs.items()
            },
        )
        state.fail("constructor")
        return FakeClient(state)

    def attributes(**kwargs):
        state.fail("attributes")
        state.attributes.append(deepcopy(kwargs))
        return FakeContext(state, attributes=True)

    def exporter(**kwargs):
        state.exporter_constructors.append(deepcopy(kwargs))
        state.fail("exporter")
        return FakeExporter(state)

    monkeypatch.setattr(tracing, "Langfuse", construct)
    monkeypatch.setattr(tracing, "propagate_attributes", attributes)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", exporter)
    tracing.get_tracing_service.cache_clear()
    yield state
    tracing.shutdown_tracing()
    tracing.get_tracing_service.cache_clear()


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "langfuse_public_key": "pk-fake-test",
        "langfuse_secret_key": SecretStr("sk-fake-test"),
        "langfuse_tracing_enabled": True,
        "langfuse_capture_content": True,
    }
    return Settings(**(values | overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"langfuse_public_key": None, "langfuse_secret_key": None},
        {"langfuse_public_key": None},
        {"langfuse_secret_key": None},
        {"langfuse_public_key": " "},
        {"langfuse_secret_key": SecretStr(" ")},
        {"langfuse_tracing_enabled": False},
    ],
)
def test_unconfigured_or_disabled_tracing_never_constructs_sdk(sdk, overrides):
    service = tracing.LangfuseService(settings(**overrides))
    calls = []
    with service.observation("answer", input="question") as observation:
        calls.append("answered")
        observation.update(output="answer")
    assert calls == ["answered"]
    assert sdk.constructors == []
    assert sdk.exporter_constructors == []
    assert sdk.starts == sdk.updates == []


def test_observation_keeps_identity_model_usage_and_merges_metadata(sdk):
    service = tracing.LangfuseService(settings())
    with service.observation(
        "answer",
        kind="generation",
        input="question",
        model="test/model",
        metadata={"store_id": "DS-1"},
        user_id="manager-1",
        session_id="conversation-1",
    ) as observation:
        observation.update(
            output="answer",
            metadata={"attempt": 2},
            usage_details={"input": 20, "output": 10},
        )
    assert len(sdk.constructors) == len(sdk.starts) == 1
    assert sdk.starts[0]["name"] == "answer"
    assert sdk.starts[0]["as_type"] == "generation"
    assert sdk.starts[0]["input"] == "question"
    assert sdk.starts[0]["model"] == "test/model"
    assert sdk.updates[-1]["output"] == "answer"
    assert sdk.updates[-1]["usage_details"] == {"input": 20, "output": 10}
    assert sdk.updates[-1]["metadata"] == {"store_id": "DS-1", "attempt": 2}
    assert any(item.get("user_id") == "manager-1" for item in sdk.attributes)
    assert any(item.get("session_id") == "conversation-1" for item in sdk.attributes)


@pytest.mark.parametrize(
    "failure",
    [
        "constructor",
        "exporter",
        "attributes",
        "attributes_enter",
        "start",
        "enter",
        "update",
        "exit",
        "attributes_exit",
    ],
)
def test_sdk_failures_cannot_replay_or_interrupt_application_body(sdk, failure):
    sdk.fail_at = failure
    service = tracing.LangfuseService(settings())
    calls = []
    with service.observation("answer", user_id="manager-1") as observation:
        calls.append("application side effect")
        observation.update(output="answer")
    assert calls == ["application side effect"]


@pytest.mark.parametrize("failure", [None, "update", "exit", "attributes_exit"])
def test_original_application_exception_survives_tracing_failures(sdk, failure):
    sdk.fail_at = failure
    service = tracing.LangfuseService(settings())
    failure_from_app = ValueError("private customer detail must not be captured")
    calls = []
    with (
        pytest.raises(ValueError) as caught,
        service.observation("answer", user_id="manager-1"),
    ):
        calls.append("application side effect")
        raise failure_from_app
    assert caught.value is failure_from_app
    assert calls == ["application side effect"]
    captured = json.dumps([sdk.starts, sdk.updates, sdk.attributes], default=str)
    assert "private customer detail" not in captured
    assert all(exc is not failure_from_app for _, exc, _ in sdk.exits)
    if failure is None:
        assert all(exc is None for _, exc, _ in sdk.exits)
        assert any(item.get("level") == "ERROR" for item in sdk.updates)
        assert "ValueError" in captured


def test_capture_disabled_omits_content_without_losing_operational_telemetry(sdk):
    service = tracing.LangfuseService(settings(langfuse_capture_content=False))
    with service.observation(
        "generate",
        kind="generation",
        input={"message": "private question"},
        model="test/model",
        metadata={"store_id": "DS-1"},
    ) as observation:
        observation.update(
            output="private answer",
            usage_details={"input": 20, "output": 10},
        )
    captured = json.dumps([sdk.starts, sdk.updates], default=str)
    assert "private question" not in captured
    assert "private answer" not in captured
    assert sdk.starts[0]["model"] == "test/model"
    assert sdk.starts[0]["metadata"] == {"store_id": "DS-1"}
    assert sdk.updates[-1]["usage_details"] == {"input": 20, "output": 10}
    assert "input" not in sdk.starts[0]
    assert "output" not in sdk.updates[-1]


def test_credentials_are_redacted_recursively_in_every_recorded_payload(sdk):
    service = tracing.LangfuseService(settings())
    data = {
        "message": "ordinary question",
        "api_key": "fake-api-secret",
        "nested": [{"Authorization": "Bearer fake-auth-secret"}],
    }
    metadata = {"password": "fake-password-secret", "attempt": 1}
    with service.observation("answer", input=data, metadata=metadata) as observation:
        observation.update(
            output={"secret_key": "fake-output-secret", "answer": "ordinary answer"},
            metadata={"access_token": "fake-token-secret"},
        )
    captured = json.dumps([sdk.starts, sdk.updates], default=str)
    for value in (
        "fake-api-secret",
        "fake-auth-secret",
        "fake-password-secret",
        "fake-output-secret",
        "fake-token-secret",
    ):
        assert value not in captured
    assert "ordinary question" in captured
    assert "ordinary answer" in captured
    assert data["api_key"] == "fake-api-secret"
    assert metadata["password"] == "fake-password-secret"


def test_large_deep_payloads_and_full_history_reach_sdk_without_truncation(sdk):
    service = tracing.LangfuseService(settings())
    large = "x" * 50_000
    history = [{"role": "user", "content": f"Message {index}"} for index in range(200)]
    history[-1]["api_key"] = "fake-tail-secret"
    deep: dict[str, Any] = {"answer": large, "password": "fake-deep-secret"}
    for _ in range(12):
        deep = {"nested": deep}
    with service.observation(
        "answer",
        input={"question": large, "messages": history},
    ) as observation:
        observation.update(output=deep)
    sent_input = sdk.starts[0]["input"]
    assert sent_input["question"] == large
    assert len(sent_input["messages"]) == 200
    assert sent_input["messages"][-1] == {
        "role": "user",
        "content": "Message 199",
        "api_key": "[redacted]",
    }
    output = sdk.updates[-1]["output"]
    for _ in range(12):
        output = output["nested"]
    assert output == {"answer": large, "password": "[redacted]"}
    captured = json.dumps([sent_input, sdk.updates[-1]["output"]])
    assert "fake-tail-secret" not in captured
    assert "fake-deep-secret" not in captured


def test_redaction_retains_shared_values_handles_cycles_and_serializes_types(sdk):
    class Choice(Enum):
        PICK = "pick"

    class PrivateObject:
        def __str__(self):
            return "private object must not be serialized"

    shared = {"message": "keep both references"}
    circular: list[Any] = []
    circular.append(circular)
    identifier = UUID("01234567-89ab-cdef-0123-456789abcdef")
    timestamp = datetime(2026, 10, 22, 12, 30, tzinfo=UTC)
    data = {
        "left": shared,
        "right": shared,
        "cycle": circular,
        "day": date(2026, 10, 22),
        "timestamp": timestamp,
        "id": identifier,
        "choice": Choice.PICK,
        "secret_object": SecretStr("fake-object-secret"),
        "private_object": PrivateObject(),
    }
    service = tracing.LangfuseService(settings())
    with service.observation("answer", input=data):
        pass
    recorded = sdk.starts[0]["input"]
    assert recorded["left"] == recorded["right"] == shared
    assert recorded["cycle"] == ["[circular reference]"]
    assert recorded["day"] == "2026-10-22"
    assert recorded["timestamp"] == timestamp.isoformat()
    assert recorded["id"] == str(identifier)
    assert recorded["choice"] == "pick"
    assert recorded["secret_object"] == "[SecretStr]"
    assert recorded["private_object"] == "[PrivateObject]"
    assert "fake-object-secret" not in json.dumps(recorded)


def test_decorator_preserves_result_without_implicitly_capturing_arguments(
    sdk,
    monkeypatch,
):
    monkeypatch.setattr(tracing, "get_settings", settings)
    calls = []

    @tracing.traced("work")
    def work(question, *, password):
        calls.append(question)
        return "private return value"

    assert work("private argument", password="fake-password") == "private return value"
    assert calls == ["private argument"]
    assert len(sdk.starts) == 1
    captured = json.dumps([sdk.starts, sdk.updates], default=str)
    assert "private argument" not in captured
    assert "private return value" not in captured
    assert "fake-password" not in captured


def test_shutdown_reuses_singleton_and_is_idempotent_even_when_sdk_fails(
    sdk,
    monkeypatch,
):
    monkeypatch.setattr(tracing, "get_settings", settings)
    service = tracing.get_tracing_service()
    assert tracing.get_tracing_service() is service
    with tracing.observation("work"):
        pass
    sdk.fail_at = "shutdown"
    tracing.shutdown_tracing()
    tracing.shutdown_tracing()
    assert sdk.shutdowns == 1


def test_shutdown_without_observations_does_not_create_sdk(sdk, monkeypatch):
    monkeypatch.setattr(tracing, "get_settings", settings)
    tracing.shutdown_tracing()
    assert sdk.constructors == []


def test_complete_metadata_stays_on_observation_without_oversized_propagation(sdk):
    metadata = {f"entry_{index}": index for index in range(200)}
    metadata.update(long_context="x" * 1000, database_url="fake-db-secret")
    service = tracing.LangfuseService(settings())
    with service.observation("answer", metadata=metadata):
        pass
    recorded = sdk.starts[0]["metadata"]["details"]
    assert recorded["entry_199"] == 199
    assert recorded["long_context"] == "x" * 1000
    assert recorded["database_url"] == "[redacted]"
    assert len(recorded) == 202
    assert "long_context" not in sdk.attributes[0]["metadata"]


def test_reporting_exporter_is_passed_to_sdk_and_exposes_actual_export_status(sdk):
    service = tracing.LangfuseService(
        settings(langfuse_base_url="https://langfuse.example/"),
    )
    exporter = sdk.constructors[0]["span_exporter"]
    assert sdk.exporter_constructors[0]["endpoint"] == (
        "https://langfuse.example/api/public/otel/v1/traces"
    )
    assert service.export_status() == {
        "enabled": True,
        "accepted_spans": 0,
        "failed_spans": 0,
        "last_export_at": None,
        "last_export_result": None,
    }
    assert exporter.export([object(), object()]) == SpanExportResult.SUCCESS
    status = service.export_status()
    assert status["enabled"] is True
    assert status["accepted_spans"] == 2
    assert status["failed_spans"] == 0
    assert status["last_export_result"] == "accepted"
    assert status["last_export_at"] is not None


def test_resolved_conversation_attributes_are_associated_without_capturing_secrets(
    sdk,
    monkeypatch,
):
    monkeypatch.setattr(tracing, "get_settings", settings)
    calls = []
    with tracing.trace_attributes(
        user_id="manager-1",
        session_id="conversation-1",
        metadata={"store_id": "DS-1", "api_key": "fake-associated-secret"},
    ):
        calls.append("application work")
        with tracing.observation("answer"):
            pass
    assert calls == ["application work"]
    assert sdk.attributes[0] == {
        "user_id": "manager-1",
        "session_id": "conversation-1",
        "metadata": {"store_id": "DS-1"},
    }
    assert "fake-associated-secret" not in repr(sdk.attributes)


@pytest.mark.parametrize(
    "failure",
    ["attributes", "attributes_enter", "attributes_exit"],
)
@pytest.mark.parametrize("application_fails", [False, True])
def test_association_failures_cannot_repeat_work_or_replace_application_exception(
    sdk,
    monkeypatch,
    failure,
    application_fails,
):
    monkeypatch.setattr(tracing, "get_settings", settings)
    sdk.fail_at = failure
    calls = []
    application_error = ValueError("private application failure")

    def work():
        with tracing.trace_attributes(user_id="manager-1", session_id="chat-1"):
            calls.append("application work")
            if application_fails:
                raise application_error

    if application_fails:
        with pytest.raises(ValueError) as caught:
            work()
        assert caught.value is application_error
    else:
        work()
    assert calls == ["application work"]
    assert "private application failure" not in repr(sdk.attributes)
    assert all(exc is not application_error for _, exc, _ in sdk.exits)


def test_disabled_association_does_not_construct_sdk_or_propagate_attributes(
    sdk,
    monkeypatch,
):
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: settings(langfuse_tracing_enabled=False),
    )
    calls = []
    with tracing.trace_attributes(user_id="manager-1"):
        calls.append("application work")
    assert calls == ["application work"]
    assert sdk.constructors == sdk.exporter_constructors == sdk.attributes == []


def test_observation_context_returns_public_identifiers_without_sdk_internals(sdk):
    service = tracing.LangfuseService(settings())
    with service.observation("answer") as observation:
        assert observation.trace_context() == {
            "trace_id": "fake-trace-id",
            "parent_span_id": "fake-span-id",
        }
    assert tracing.Observation().trace_context() is None
    assert (
        tracing.Observation(SimpleNamespace(trace_id=123, id=None)).trace_context()
        is None
    )


def test_context_getter_failure_is_safe_and_does_not_interrupt_work(sdk):
    service = tracing.LangfuseService(settings())
    sdk.fail_at = "context"
    calls = []
    with service.observation("answer") as observation:
        assert observation.trace_context() is None
        calls.append("application work")
        observation.update(output="answer")
    assert calls == ["application work"]
    assert sdk.updates[-1]["output"] == "answer"
