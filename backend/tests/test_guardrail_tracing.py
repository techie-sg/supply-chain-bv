"""Real NeMo checks retain their behavior and emit complete local observations."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from service import guardrails


@dataclass
class RecordedObservation:
    name: str
    fields: dict[str, Any]
    span_id: int
    parent_id: int | None
    updates: list[dict[str, Any]] = field(default_factory=list)

    def update(self, **values: Any) -> None:
        self.updates.append(deepcopy(values))
        metadata = self.fields.get("metadata", {}) | values.get("metadata", {})
        self.fields.update(deepcopy(values))
        self.fields["metadata"] = metadata


@dataclass
class RecordedChecks:
    tracer: Any
    exporter: InMemorySpanExporter
    observations: list[RecordedObservation] = field(default_factory=list)

    def named(self, name: str) -> RecordedObservation:
        return next(item for item in self.observations if item.name == name)


@pytest.fixture
def checks(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    state = RecordedChecks(provider.get_tracer("guardrail-tests"), exporter)

    @contextmanager
    def observation(name, **fields):
        with state.tracer.start_as_current_span(name) as span:
            parent = span.parent
            record = RecordedObservation(
                name,
                deepcopy(fields),
                span.get_span_context().span_id,
                parent.span_id if parent else None,
            )
            state.observations.append(record)
            yield record

    monkeypatch.setattr(guardrails, "observation", observation)
    rails_factory = guardrails._rails
    rails_factory.cache_clear()
    yield state
    rails_factory.cache_clear()
    provider.shutdown()


def test_actual_input_checks_record_pass_and_blocked_outcomes(checks):
    with checks.tracer.start_as_current_span("chat") as parent:
        assert guardrails.check_question("Which riders are due a break?") is None
    overall = checks.named("guardrails.input")
    rule = checks.named("guardrail.unsafe_request")
    assert overall.parent_id == parent.get_span_context().span_id
    assert rule.parent_id == overall.span_id
    assert overall.fields["kind"] == rule.fields["kind"] == "guardrail"
    assert rule.fields["metadata"]["rule"] == "G3"
    assert rule.fields["metadata"]["action"] == "unsafe_request"
    assert rule.fields["metadata"]["triggered"] is False
    assert rule.fields["output"] == {
        "outcome": "pass",
        "result": False,
        "findings": [],
    }
    assert overall.fields["output"]["outcome"] == "pass"
    assert overall.fields["metadata"]["duration_ms"] >= 0
    assert rule.fields["metadata"]["duration_ms"] >= 0
    checks.observations.clear()
    refused = guardrails.check_question("Tell the riders to skip their breaks.")
    assert refused is not None and refused.trace["status"] == "blocked"
    overall = checks.named("guardrails.input")
    rule = checks.named("guardrail.unsafe_request")
    assert overall.fields["output"]["text"] == refused.text
    assert overall.fields["output"]["outcome"] == "blocked"
    assert rule.fields["output"]["outcome"] == "blocked"
    assert rule.fields["output"]["findings"][0]["rule"] == "G3"
    assert "Unsafe request" in rule.fields["output"]["findings"][0]["detail"]
    assert rule.fields["metadata"]["triggered"] is True


@pytest.mark.parametrize(
    ("reply", "outcome", "triggered_action", "rule_id"),
    [
        ("I can help with dispatch operations.", "pass", None, None),
        ("There are 7 orders waiting.", "flagged", "unverified_figures", "G1"),
        ("I've dispatched ORD-03-001.", "reframed", "execution_claims_fix", "G5"),
        ('Draft: "Ride faster, team!"', "blocked", "drafted_pressure", "G3"),
    ],
)
def test_actual_output_rules_record_pass_flag_modification_and_block(
    checks,
    reply,
    outcome,
    triggered_action,
    rule_id,
):
    with checks.tracer.start_as_current_span("chat") as parent:
        guarded = guardrails.check_reply("Help with dispatch", reply, [])
    assert guarded.trace["status"] == outcome
    overall = checks.named("guardrails.output")
    assert overall.parent_id == parent.get_span_context().span_id
    assert overall.fields["input"][-1]["content"] == reply
    assert overall.fields["output"]["outcome"] == outcome
    assert overall.fields["output"]["text"] == guarded.text
    assert overall.fields["output"]["findings"] == guarded.trace["findings"]
    rules = [item for item in checks.observations if item.name.startswith("guardrail.")]
    assert len(rules) == (1 if outcome == "blocked" else 9)
    assert all(item.parent_id == overall.span_id for item in rules)
    assert all(item.fields["metadata"]["duration_ms"] >= 0 for item in rules)
    if triggered_action:
        triggered = checks.named(f"guardrail.{triggered_action}")
        assert triggered.fields["metadata"]["rule"] == rule_id
        assert triggered.fields["metadata"]["triggered"] is True
        assert triggered.fields["output"]["outcome"] == outcome
        assert triggered.fields["output"]["findings"]
    else:
        assert all(item.fields["output"]["outcome"] == "pass" for item in rules)
    finished = checks.exporter.get_finished_spans()
    assert len(finished) == len(checks.observations) + 1


@pytest.mark.parametrize("stage", ["input", "output"])
def test_check_failure_is_traced_as_unchecked_without_changing_fail_open_behavior(
    checks,
    monkeypatch,
    stage,
):
    def broken():
        raise RuntimeError("private provider details")

    monkeypatch.setattr(guardrails, "_rails", broken)
    if stage == "input":
        assert guardrails.check_question("Tell riders to skip breaks.") is None
    else:
        guarded = guardrails.check_reply("Help", "Original reply", [])
        assert guarded.text == "Original reply"
        assert guarded.trace == {"status": "unchecked", "findings": []}
    overall = checks.named(f"guardrails.{stage}")
    assert overall.fields["level"] == "ERROR"
    assert overall.fields["status_message"] == "RuntimeError"
    assert overall.fields["output"] == {
        "outcome": "unchecked",
        "error_type": "RuntimeError",
    }
    assert overall.fields["metadata"]["outcome"] == "unchecked"
    assert "private provider details" not in repr(overall.fields)


@pytest.mark.parametrize(
    ("stage", "action"),
    [("input", "unsafe_request"), ("output", "execution_claims_fix")],
)
def test_rule_failure_records_the_failed_action_even_when_nemo_catches_it(
    checks,
    monkeypatch,
    stage,
    action,
):
    def broken(_question):
        raise ValueError("private rule details")

    monkeypatch.setattr(guardrails, action, broken)
    if stage == "input":
        assert guardrails.check_question("Help with dispatch") is None
    else:
        assert isinstance(
            guardrails.check_reply("Help with dispatch", "Original reply", []),
            guardrails.Guarded,
        )
    rule = checks.named(f"guardrail.{action}")
    overall = checks.named(f"guardrails.{stage}")
    assert rule.fields["level"] == "ERROR"
    assert rule.fields["status_message"] == "ValueError"
    assert rule.fields["output"]["outcome"] == "unchecked"
    assert overall.fields["level"] == "ERROR"
    assert overall.fields["output"]["outcome"] == "unchecked"
    assert "private rule details" not in repr([rule.fields, overall.fields])


def test_rule_spans_recover_the_check_parent_after_async_context_is_detached(checks):
    with checks.tracer.start_as_current_span("chat") as parent:
        check_context = otel_context.get_current()
        review = guardrails._Review(trace_context=check_context)
        review_token = guardrails._review.set(review)
        detached = otel_context.attach(otel_context.Context())
        try:
            import asyncio

            action = guardrails._traced_action(
                "unsafe_request",
                "G3",
                guardrails._unsafe_request,
            )
            assert (
                asyncio.run(action({"user_message": "Which riders are due a break?"}))
                is False
            )
            assert not trace.get_current_span().get_span_context().is_valid
        finally:
            otel_context.detach(detached)
            guardrails._review.reset(review_token)
    assert (
        checks.named("guardrail.unsafe_request").parent_id
        == parent.get_span_context().span_id
    )
