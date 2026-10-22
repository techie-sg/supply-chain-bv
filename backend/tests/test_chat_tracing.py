"""Workflow coverage and browser timing without provider or database access."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import gradio as gr
import pytest

from domain.chat import AnswerResult
from service import conversations, handover, personalization
from ui import chat as chat_ui
from ui import handover as handover_ui


@pytest.fixture
def observations(monkeypatch):
    spans = []
    attributes = []

    @contextmanager
    def observe(name, **fields):
        span = SimpleNamespace(name=name, fields=fields, updates=[], failed=None)
        span.update = lambda **values: span.updates.append(values)
        span.trace_context = lambda: {
            "trace_id": "a" * 32,
            "parent_span_id": "b" * 16,
        }
        spans.append(span)
        try:
            yield span
        except BaseException as exc:
            span.failed = type(exc).__name__
            raise

    @contextmanager
    def propagate(**values):
        attributes.append(values)
        yield

    monkeypatch.setattr(conversations, "observation", observe)
    monkeypatch.setattr(conversations, "trace_attributes", propagate)
    monkeypatch.setattr(chat_ui, "observation", observe)
    monkeypatch.setattr(personalization, "observation", observe)
    monkeypatch.setattr(handover, "observation", observe)
    monkeypatch.setattr(handover_ui, "observation", observe)
    return spans, attributes


def test_chat_trace_starts_before_initial_database_failure(monkeypatch, observations):
    spans, _ = observations

    def unavailable(*args):
        assert spans[0].name == "conversation.answer"
        raise RuntimeError("mock database unavailable")

    monkeypatch.setattr(conversations, "read_answer_context", unavailable)
    service = conversations.ConversationService("store", "manager", lambda *a: None)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.ask("What happened?")
    assert spans[0].fields["user_id"] == "manager"
    assert spans[0].fields["input"] == {"question": "What happened?"}
    assert spans[0].failed == "RuntimeError"
    assert spans[1].name == "db.conversation.read_context"


def test_new_chat_binds_resolved_session_and_times_both_writes(
    monkeypatch,
    observations,
):
    spans, attributes = observations
    chat_id = uuid4()
    writes = []
    monkeypatch.setattr(conversations, "read_answer_context", lambda *a: None)
    monkeypatch.setattr(
        conversations,
        "start_conversation",
        lambda *a: SimpleNamespace(id=chat_id),
    )
    monkeypatch.setattr(
        conversations,
        "append_message",
        lambda chat_id, message, engine: writes.append(message["who"]),
    )
    service = conversations.ConversationService(
        "store",
        "manager",
        lambda *a, **k: AnswerResult("Answer"),
    )
    assert service.ask("Question").text == "Answer"
    assert attributes == [{"user_id": "manager", "session_id": str(chat_id)}]
    assert writes == ["manager", "assistant"]
    assert {span.name for span in spans} >= {
        "db.conversation.create",
        "db.conversation.save_question",
        "db.conversation.save_answer",
        "context.history",
    }
    assert any(
        update.get("metadata", {}).get("conversation_id") == str(chat_id)
        for update in spans[0].updates
    )


def test_resumed_chat_question_write_failure_stays_inside_root_trace(
    monkeypatch,
    observations,
):
    spans, attributes = observations
    chat_id = uuid4()
    monkeypatch.setattr(
        conversations,
        "read_answer_context",
        lambda *a: {"id": chat_id, "messages": [], "summary": None},
    )

    def failed_write(*args):
        raise RuntimeError("mock question write failed")

    monkeypatch.setattr(conversations, "append_message", failed_write)
    answer = lambda *a, **k: pytest.fail("Answer must not run after a failed write")
    service = conversations.ConversationService(
        "store",
        "manager",
        answer,
        conversation_id=str(chat_id),
    )
    with pytest.raises(RuntimeError, match="question write failed"):
        service.ask("Question")
    assert spans[0].fields["session_id"] == str(chat_id)
    assert spans[0].failed == "RuntimeError"
    assert attributes == [{"user_id": "manager", "session_id": str(chat_id)}]
    [failed] = [s for s in spans if s.name == "db.conversation.save_question"]
    assert failed.failed == "RuntimeError"


def test_context_provenance_uses_existing_reads_once(monkeypatch, observations):
    spans, _ = observations
    reads = []
    preferences = SimpleNamespace(
        store_id="store",
        manager_id="manager",
        effective=list,
    )
    monkeypatch.setattr(conversations, "check_question", lambda *a: None)
    monkeypatch.setattr(conversations, "dispatch_tools", lambda *a: [])

    def read(name, value):
        reads.append(name)
        return value

    monkeypatch.setattr(
        conversations,
        "chat_handover_block",
        lambda *a: read("handover", "handover text"),
    )
    monkeypatch.setattr(
        conversations,
        "_alerts",
        lambda *a: read("alerts", "alerts text"),
    )
    monkeypatch.setattr(
        conversations,
        "manager_personalization",
        lambda *a: SimpleNamespace(
            prompt_block=lambda: read("personalization", "Be brief"),
        ),
    )
    seen = {}

    def answer(question, **context):
        seen.update(context)
        return "Answer"

    monkeypatch.setattr(conversations, "answer_question", answer)
    monkeypatch.setattr(
        conversations,
        "check_reply",
        lambda question, reply, *a: SimpleNamespace(
            text=reply,
            trace={"status": "pass", "findings": []},
        ),
    )
    conversations._answer("Question", history=[], preferences=preferences)
    assert reads == ["handover", "alerts", "personalization"]
    assert seen["personalization"] == "Be brief"
    assert seen["preferences"] is preferences
    [context] = [span for span in spans if span.name == "context.load"]
    metadata = context.updates[-1]["metadata"]
    assert len(metadata["personalization_fingerprint"]) == 64
    assert metadata["manager_id"] == "manager"
    assert "Be brief" not in json.dumps(metadata)


def test_browser_timing_measures_queue_and_links_only_server_trace(
    monkeypatch,
    observations,
):
    spans, _ = observations
    request_id = uuid4().hex
    ticks = iter([1.0, 1.25, 1.75])
    monkeypatch.setattr(chat_ui, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(chat_ui, "time", lambda: 1000.0)
    monkeypatch.setattr(chat_ui, "ask_question", lambda *a, **k: ("Answer", [], None))
    timing = chat_ui.receive_request(
        json.dumps(
            {
                "request_id": request_id,
                "sent_monotonic_ms": 100,
                "trace_context": {"trace_id": "client-injected"},
            },
        ),
        "manager",
        "chat",
        "Question",
    )
    [response] = chat_ui.respond_to_pending("Question", [], "manager", "chat", timing)
    assert len(response) == 4
    assert timing["queue_wait_ms"] == 250
    assert timing["server_duration_ms"] == 500
    acknowledged = chat_ui.acknowledge_response(
        json.dumps({"request_id": request_id, "rendered_monotonic_ms": 800}),
        timing,
    )
    assert acknowledged["acknowledged"]
    browser_span = spans[-1]
    assert browser_span.name == "chat.browser_rendered"
    assert browser_span.fields["trace_context"]["trace_id"] == "a" * 32
    assert browser_span.fields["metadata"]["browser_elapsed_ms"] == 700
    assert browser_span.fields["metadata"]["trace_id"] == "a" * 32
    assert len(spans) == 3
    assert spans[0].name == "chat.received"
    assert spans[0].fields["input"] == {"question": "Question"}
    assert spans[1].fields["trace_context"] == timing["receipt_trace_context"]
    assert (
        chat_ui.acknowledge_response(
            json.dumps({"request_id": request_id}),
            acknowledged,
        )
        == acknowledged
    )
    assert len(spans) == 3


def test_browser_timing_rejects_cross_request_ack_and_invalid_durations(observations):
    spans, _ = observations
    timing = {"request_id": "original", "manager_id": "manager"}
    assert chat_ui.acknowledge_response('{"request_id":"other"}', timing) is timing
    assert not spans
    assert chat_ui._duration(1, 2) is None
    assert chat_ui._duration(float("inf"), 0) is None
    assert chat_ui._duration(3_600_001, 0) is None
    assert chat_ui._duration(10**1000, 0) is None


def test_receipt_records_a_query_even_if_it_never_leaves_the_queue(observations):
    spans, _ = observations
    timing = chat_ui.receive_request(
        json.dumps({"trace_context": {"trace_id": "client-injected"}}),
        "manager",
        None,
        "A question that may be cancelled",
    )
    assert len(spans) == 1
    assert spans[0].name == "chat.received"
    assert spans[0].fields["metadata"]["session_unresolved"] is True
    assert spans[0].fields["session_id"] is None
    assert spans[0].fields["input"]["question"] == "A question that may be cancelled"
    assert timing["receipt_trace_context"]["trace_id"] == "a" * 32


def test_failed_chat_retains_timing_for_the_failure_render_ack(
    monkeypatch,
    observations,
):
    spans, _ = observations

    def unavailable(*args, **kwargs):
        raise RuntimeError("mock provider unavailable")

    monkeypatch.setattr(chat_ui, "ask_question", unavailable)
    timing = chat_ui.receive_request(None, "manager", "chat", "Question")
    stream = chat_ui.respond_to_pending("Question", [], "manager", "chat", timing)
    assert next(stream)[-1]["status"] == "failed"
    with pytest.raises(gr.Error, match="unavailable"):
        next(stream)
    assert timing["server_duration_ms"] is not None
    assert timing["trace_context"]["trace_id"] == "a" * 32
    assert spans[-1].failed == "RuntimeError"


def test_browser_receipt_is_server_state_before_the_workspace_queue(ui_app):
    receives = [f for f in ui_app.fns.values() if f.fn is chat_ui.receive_request]
    responses = [f for f in ui_app.fns.values() if f.fn is chat_ui.respond_to_pending]
    acknowledgments = [
        f for f in ui_app.fns.values() if f.fn is chat_ui.acknowledge_response
    ]
    assert len(receives) == len(responses) == 2
    assert len(acknowledgments) == 4
    for response in responses:
        receipt = ui_app.fns[response.trigger_after]
        assert receipt.fn is chat_ui.receive_request and not receipt.queue
        assert receipt.outputs[0] is response.inputs[-1]
        assert response.outputs[-1] is response.inputs[-1]
        assert response.concurrency_id == "workspace"
    assert all(not acknowledgment.queue for acknowledgment in acknowledgments)


@pytest.mark.parametrize("outcome", ["saved", "no-change", "conflict"])
def test_direct_personalization_save_records_content_and_metadata_outcome(
    monkeypatch,
    observations,
    outcome,
):
    spans, _ = observations
    value = "Call me Sunny"
    current = (
        {personalization.NOTES: {"value": value}} if outcome == "no-change" else {}
    )
    writes = []

    def save(*args):
        writes.append(args)
        if outcome == "conflict":
            raise ValueError("Personalization changed since you opened it")
        return True

    monkeypatch.setattr(personalization, "save_profile", save)
    service = personalization.PersonalizationService("store", "manager")
    if outcome == "conflict":
        with pytest.raises(ValueError, match="changed"):
            service.save({personalization.NOTES: value}, expected=current)
    else:
        assert service.save({personalization.NOTES: value}, expected=current) is (
            outcome == "saved"
        )
    [span] = spans
    assert span.name == "personalization.save"
    assert span.fields["user_id"] == "manager"
    assert span.fields["metadata"]["source"] == "settings"
    metadata = {}
    for update in span.updates:
        metadata.update(update.get("metadata", {}))
    assert metadata["status"] == outcome
    assert len(metadata["values_fingerprint"]) == 64
    assert value not in json.dumps(metadata)
    assert bool(writes) is (outcome != "no-change")


@pytest.mark.parametrize(
    ("entrypoint", "query", "arguments", "span_name"),
    [
        ("begin_shift", "start_shift", ("manager",), "handover.shift.start"),
        ("generate_note", "_open", ("manager",), "handover.generate"),
        ("save_shift_note", "_open", ("manager", "note"), "handover.note.save"),
        ("finish_shift", "_open", ("manager", "note"), "handover.shift.finish"),
        ("hand_over", "next_manager", ("manager", "note"), "handover.complete"),
    ],
)
def test_handover_entrypoints_trace_the_first_database_failure(
    monkeypatch,
    observations,
    entrypoint,
    query,
    arguments,
    span_name,
):
    spans, _ = observations

    def unavailable(*args):
        raise RuntimeError("mock database unavailable")

    monkeypatch.setattr(handover, query, unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        getattr(handover, entrypoint)(*arguments)
    assert spans[0].name == span_name
    assert spans[0].fields["user_id"] == "manager"
    assert spans[0].failed == "RuntimeError"


def test_handover_ui_traces_caught_initial_lookup_failure(monkeypatch, observations):
    spans, _ = observations

    def unavailable(*args):
        raise RuntimeError("mock database unavailable")

    monkeypatch.setattr(handover_ui, "next_manager", unavailable)
    monkeypatch.setattr(handover_ui, "page", lambda *a: ("unavailable",))
    assert handover_ui.end("manager", "note", True) == ("unavailable",)
    [span] = spans
    assert span.name == "handover.submit"
    assert span.updates[-1]["metadata"]["status"] == "failed"
    assert span.updates[-1]["level"] == "ERROR"


@pytest.mark.parametrize("outcome", ["read-failed", "provider-failed", "saved"])
def test_title_workflow_captures_early_errors_and_resolved_session(
    monkeypatch,
    observations,
    outcome,
):
    spans, attributes = observations
    chat_id = uuid4()

    def read(*args):
        if outcome == "read-failed":
            raise RuntimeError("mock database unavailable")
        return {
            "id": chat_id,
            "title": None,
            "question": "Question",
            "answer": "Answer",
        }

    def title(*args):
        if outcome == "provider-failed":
            raise RuntimeError("mock title unavailable")
        return "A title"

    monkeypatch.setattr(conversations, "read_title_exchange", read)
    monkeypatch.setattr(conversations, "set_title", lambda *a: True)
    service = conversations.ConversationService("store", "manager", None, titler=title)
    if outcome == "read-failed":
        with pytest.raises(RuntimeError, match="database unavailable"):
            service.title_latest()
        assert spans[0].failed == "RuntimeError"
    else:
        assert service.title_latest() == ("A title" if outcome == "saved" else None)
        assert attributes == [{"user_id": "manager", "session_id": str(chat_id)}]
        assert spans[0].updates[-1]["metadata"]["status"] == (
            "saved" if outcome == "saved" else "failed"
        )
    assert spans[0].name == "conversation.title"
    assert spans[0].fields["user_id"] == "manager"
