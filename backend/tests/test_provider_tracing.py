"""Exercise real provider/tool orchestration with offline transports and trace sinks."""

import json
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
import requests
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from constants import NO_GUIDANCE_ANSWER, OPENROUTER_FALLBACK_MODELS
from domain.tools import Tool
from service import groq_service, jina_embedding_service, openrouter_service, rag, tools
from service.cache import TTLCache
from service.provider_tracing import http_metadata, usage_fields


@pytest.fixture
def traces(monkeypatch):
    records = []
    active = []

    @contextmanager
    def record(name, **fields):
        entry = {
            "name": name,
            "parent": active[-1] if active else None,
            **deepcopy(fields),
        }
        records.append(entry)

        def update(**values):
            values = deepcopy(values)
            entry.setdefault("metadata", {}).update(values.pop("metadata", {}))
            entry.update(values)

        active.append(name)
        try:
            yield SimpleNamespace(update=update)
        except BaseException as exc:
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            active.pop()

    for module in (
        groq_service,
        jina_embedding_service,
        openrouter_service,
        rag,
        tools,
    ):
        monkeypatch.setattr(module, "observation", record)
    return records


def completion(content="answer", calls=None, **fields):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls or [],
                },
                "finish_reason": "tool_calls" if calls else "stop",
            },
        ],
        **fields,
    }


def install_transport(monkeypatch, *responses):
    pending = iter(responses)
    sent = []

    def post(url, **kwargs):
        sent.append(deepcopy(kwargs))
        body = next(pending)
        return (
            body
            if isinstance(body, requests.Response)
            else SimpleNamespace(ok=True, json=lambda: body)
        )

    monkeypatch.setattr(openrouter_service.requests, "post", post)
    return sent


def test_openrouter_tool_rounds_record_fallback_usage_without_duplicate_tools(
    monkeypatch,
    traces,
):
    fallback = OPENROUTER_FALLBACK_MODELS[0]
    call = {"id": "call-1", "function": {"name": "status", "arguments": "{}"}}
    sent = install_transport(
        monkeypatch,
        completion(
            None,
            [call],
            id="generation-1",
            model=fallback,
            provider="NVIDIA",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {
                    "cached_tokens": 30,
                    "cache_write_tokens": 10,
                },
                "completion_tokens_details": {"reasoning_tokens": 5},
                "cost": 0,
            },
        ),
        completion("reported", id="generation-2", model=fallback),
    )
    available = tools.traced_tools(
        [Tool("status", "Read status", {}, lambda arguments: '{"orders":2}')],
        [],
    )
    assert (
        openrouter_service.OpenRouterService(
            api_key=SecretStr("test-key"),
        ).generate_with_tools("system", "question", available)
        == "reported"
    )
    generations = [entry for entry in traces if entry["kind"] == "generation"]
    assert len(generations) == 2
    assert generations[0]["model"] == fallback
    assert generations[0]["metadata"]["upstream_provider"] == "NVIDIA"
    assert generations[0]["metadata"]["fallback_selected"] is True
    assert generations[0]["usage_details"] == {
        "input": 60,
        "input_cached_tokens": 30,
        "input_cache_creation": 10,
        "output": 15,
        "output_reasoning_tokens": 5,
        "total": 120,
    }
    assert generations[0]["cost_details"] == {"total": 0}
    assert generations[0]["metadata"]["cost_source"] == "provider_reported"
    assert "cost_details" not in generations[1]
    assert generations[1]["metadata"]["cost_source"] == "unknown"
    assert len([entry for entry in traces if entry["kind"] == "tool"]) == 1
    assert sent[1]["json"]["messages"][-1]["content"] == '{"orders":2}'
    assert "test-key" not in str(traces)


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("missing", "{}", "UNKNOWN_TOOL"),
        ("status", "not-json", "INVALID_INPUT"),
        ("status", "[]", "INVALID_INPUT"),
    ],
)
def test_rejected_openrouter_calls_are_traced_without_executing(
    monkeypatch,
    traces,
    name,
    arguments,
    error,
):
    install_transport(
        monkeypatch,
        completion(
            None,
            [{"id": "call", "function": {"name": name, "arguments": arguments}}],
        ),
        completion(),
    )
    called = []
    tool = Tool(
        "status",
        "read",
        {},
        lambda arguments: called.append(arguments) or "ok",
    )
    assert (
        openrouter_service.OpenRouterService(
            api_key=SecretStr("test"),
        ).generate_with_tools("s", "q", [tool])
        == "answer"
    )
    assert called == []
    rejected = [entry for entry in traces if entry["kind"] == "tool"]
    assert len(rejected) == 1
    assert rejected[0]["status_message"] == error
    assert rejected[0]["level"] == "WARNING"
    assert rejected[0]["output"]


@pytest.mark.parametrize("calls", ["invalid-list", [{"function": {"name": "status"}}]])
def test_malformed_openrouter_call_shapes_are_traced_before_original_error(
    monkeypatch,
    traces,
    calls,
):
    install_transport(monkeypatch, completion(None, calls))
    with pytest.raises(RuntimeError, match="invalid tool call"):
        openrouter_service.OpenRouterService(
            api_key=SecretStr("test"),
        ).generate_with_tools(
            "s",
            "q",
            [Tool("status", "read", {}, lambda arguments: "unused")],
        )
    rejected = [entry for entry in traces if entry["kind"] == "tool"]
    assert len(rejected) == 1 and rejected[0]["level"] == "WARNING"


def test_failed_openrouter_request_has_only_safe_status_and_rate_headers(
    monkeypatch,
    traces,
):
    response = requests.Response()
    response.status_code = 429
    response.headers.update(
        {
            "Retry-After": "30",
            "X-RateLimit-Remaining-Tokens": "0",
            "X-Request-ID": "request-123",
            "Authorization": "private-key",
            "Set-Cookie": "private-session",
        },
    )
    response._content = b'{"error":"private body"}'
    install_transport(monkeypatch, response)
    with pytest.raises(requests.HTTPError):
        openrouter_service.OpenRouterService(api_key=SecretStr("test")).generate(
            "s",
            "q",
        )
    assert traces[0]["metadata"]["http_status"] == 429
    assert traces[0]["metadata"]["rate_limit"]["retry-after"] == "30"
    assert traces[0]["metadata"]["provider_request_id"] == "request-123"
    assert "private" not in str(traces)
    assert traces[0]["metadata"]["cost_source"] == "unknown"


class GroqClient:
    def __init__(self, responses):
        self.responses = iter(responses)

    def invoke(self, messages):
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response

    def model_copy(self, **kwargs):
        return self

    def bind_tools(self, *args, **kwargs):
        return self


def test_groq_retry_and_invalid_calls_have_separate_observations(monkeypatch, traces):
    malformed = {"name": "broken", "args": "bad-json", "id": "call", "error": "parse"}
    service = groq_service.GroqService(model="requested")
    service.__dict__["_client"] = GroqClient(
        [
            AIMessage(content="", invalid_tool_calls=[malformed]),
            AIMessage(
                content="answer",
                response_metadata={"model_name": "served", "finish_reason": "stop"},
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_token_details": {"cache_read": 30},
                    "output_token_details": {"reasoning": 5},
                },
            ),
        ],
    )
    assert service.generate("s", "q") == "answer"
    generations = [entry for entry in traces if entry["kind"] == "generation"]
    assert len(generations) == 2
    assert generations[1]["model"] == "served"
    assert generations[1]["metadata"]["retry"] == "empty_answer"
    assert generations[1]["usage_details"] == {
        "input": 70,
        "input_cached_tokens": 30,
        "output": 15,
        "output_reasoning_tokens": 5,
        "total": 120,
    }
    assert generations[1]["metadata"]["cost_source"] == "unknown"
    rejected = next(entry for entry in traces if entry["kind"] == "tool")
    assert rejected["status_message"] == "INVALID_TOOL_ARGUMENTS"


def test_unknown_groq_tool_and_http_error_are_traced(traces):
    service = groq_service.GroqService()
    service.__dict__["_client"] = GroqClient(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "missing", "args": {}, "id": "call"}],
            ),
            AIMessage(content="answer"),
        ],
    )
    assert (
        service.generate_with_tools(
            "s",
            "q",
            [Tool("status", "read", {}, lambda arguments: "unused")],
        )
        == "answer"
    )
    assert (
        next(entry for entry in traces if entry["kind"] == "tool")["status_message"]
        == "UNKNOWN_TOOL"
    )
    response = httpx.Response(
        429,
        headers={"retry-after": "10", "authorization": "private"},
        request=httpx.Request("POST", "https://api.groq.com"),
    )
    failure = groq_service.groq.RateLimitError(
        "private body",
        response=response,
        body={"secret": "private"},
    )
    with pytest.raises(groq_service.groq.RateLimitError):
        service._invoke(GroqClient([failure]), [])
    assert traces[-1]["metadata"]["rate_limit"] == {"retry-after": "10"}
    assert "private" not in str(traces)


@pytest.mark.parametrize("usage", [{}, {"total_tokens": 7}])
def test_jina_usage_never_invents_zero_or_exports_vectors(monkeypatch, traces, usage):
    vector = [1.23456, 6.54321]
    install_transport(
        monkeypatch,
        {"data": [{"index": 0, "embedding": vector}], "usage": usage},
    )
    assert (
        jina_embedding_service.JinaEmbeddingService(
            api_key=SecretStr("test"),
        ).embed_query("query")
        == vector
    )
    assert traces[0]["output"] == {"embeddings": 1, "dimensions": [2]}
    assert traces[0]["usage_details"] == ({"input": 7, "total": 7} if usage else {})
    assert traces[0]["metadata"]["cost_source"] == "unknown"
    assert "1.23456" not in str(traces)


def test_rag_preserves_raw_and_filtered_answers_with_prompt_provenance(traces):
    preferences = SimpleNamespace(prompt_block=lambda: "Settings: cap 350")
    service = rag.RAGService(
        SimpleNamespace(model="embedding"),
        SimpleNamespace(
            generate_with_tools=lambda **kwargs: "Advice from DD-UNKNOWN-999#0.",
        ),
        cache=TTLCache(60, 10),
    )
    assert (
        service.answer_question("question", preferences=preferences)
        == NO_GUIDANCE_ANSWER
    )
    context = next(entry for entry in traces if entry["name"] == "context.settings")
    assert context["output"] == "Settings: cap 350"
    assert context["metadata"]["sha256"] == sha256(b"Settings: cap 350").hexdigest()
    chain = next(entry for entry in traces if entry["name"] == "rag.answer")
    assert chain["output"] == {
        "model_answer": "Advice from DD-UNKNOWN-999#0.",
        "answer": NO_GUIDANCE_ANSWER,
    }
    assert chain["metadata"]["source_check_changed"] is True
    assert chain["metadata"]["prompt_source"] == "dispatch_manager_system.md"
    assert len(chain["metadata"]["system_prompt_sha256"]) == 64


def test_retrieval_cache_miss_and_failure_are_visible_before_embedding(
    monkeypatch,
    traces,
):
    def fail(query):
        raise RuntimeError("embedding unavailable")

    service = rag.RAGService(
        SimpleNamespace(model="embedding", embed_query=fail),
        SimpleNamespace(),
        cache=TTLCache(60, 10),
    )
    with pytest.raises(RuntimeError):
        service._retrieve("query", 3)
    assert traces[0]["metadata"]["cache"] == "miss"
    assert traces[0]["error_type"] == "RuntimeError"


def test_full_rag_trace_links_generations_search_embedding_cache_and_final_answer(
    monkeypatch,
    traces,
):
    query = "frozen order dispatch"
    call = {
        "id": "search-1",
        "function": {
            "name": "search_playbook",
            "arguments": json.dumps({"query": query}),
        },
    }
    sent = install_transport(
        monkeypatch,
        completion(None, [call]),
        {"data": [{"index": 0, "embedding": [0.2, 0.4]}], "usage": {"total_tokens": 4}},
        completion("Deliver separately (DD-TEST-001#0)."),
    )
    source = {
        "chunk_id": "DD-TEST-001#0",
        "content": "Frozen orders need separate delivery.",
        "similarity": 0.91,
    }
    retrieved = []

    def retrieve(**kwargs):
        retrieved.append(kwargs)
        return [source]

    monkeypatch.setattr(rag, "retrieve", retrieve)
    service = rag.RAGService(
        jina_embedding_service.JinaEmbeddingService(api_key=SecretStr("test-jina")),
        openrouter_service.OpenRouterService(api_key=SecretStr("test-router")),
        cache=TTLCache(60, 10),
    )
    question = "frozen " * 3000
    assert service.answer_question(question) == "Deliver separately (DD-TEST-001#0)."
    assert retrieved == [{"query_embedding": [0.2, 0.4], "match_count": 3}]
    assert len(sent) == 3
    generations = [entry for entry in traces if entry["kind"] == "generation"]
    assert len(generations) == 2
    assert generations[0]["input"]["messages"][-1]["content"].endswith(question)
    assert len(generations[0]["input"]["messages"][-1]["content"]) > 16_000
    assert all(entry["parent"] == "rag.answer" for entry in generations)
    search = next(entry for entry in traces if entry["name"] == "search_playbook")
    assert search["parent"] == "rag.answer"
    assert (
        next(entry for entry in traces if entry["kind"] == "retriever")["parent"]
        == "search_playbook"
    )
    assert (
        next(entry for entry in traces if entry["kind"] == "embedding")["parent"]
        == "playbook.retrieve"
    )
    assert len([entry for entry in traces if entry["kind"] == "tool"]) == 1
    assert service._retrieve(query, 3) == [source]
    assert traces[-1]["metadata"]["cache"] == "hit"
    assert len(sent) == 3


def test_cache_lookup_failure_and_provider_timeout_preserve_failure_metadata(
    monkeypatch,
    traces,
):
    def fail(*args, **kwargs):
        raise requests.Timeout("transport unavailable")

    service = rag.RAGService(
        SimpleNamespace(model="embedding"),
        SimpleNamespace(),
        cache=SimpleNamespace(enabled=True, get=fail),
    )
    with pytest.raises(requests.Timeout):
        service._retrieve("query", 3)
    assert traces[0]["metadata"]["cache"] == "lookup"
    monkeypatch.setattr(openrouter_service.requests, "post", fail)
    with pytest.raises(requests.Timeout):
        openrouter_service.OpenRouterService(api_key=SecretStr("test")).generate(
            "s",
            "q",
        )
    assert traces[-1]["error_type"] == "Timeout"
    assert traces[-1]["metadata"]["cost_source"] == "unknown"


def test_metrics_cache_miss_and_tool_error_are_visible(monkeypatch, traces):
    monkeypatch.setattr(tools, "metrics_cache", lambda: TTLCache(60, 10))
    monkeypatch.setattr(
        tools,
        "read_delivery_metrics",
        lambda *args: SimpleNamespace(rows=[], known_store_ids=[]),
    )
    tool = tools.traced_tools(tools.dispatch_tools("store"), [])[1]
    assert (
        json.loads(
            tool.run(
                {
                    "store_id": "store",
                    "date": "2026-10-22",
                    "start_hour": 1,
                    "end_hour": 2,
                },
            ),
        )["error"]["code"]
        == "UNKNOWN_STORE"
    )
    assert traces[0]["metadata"]["cache"] == "miss"
    assert traces[0]["level"] == "WARNING"


@pytest.mark.parametrize("cost", [None, -1, float("nan"), float("inf"), True, "0"])
def test_unknown_cost_is_explicit_and_not_fabricated(cost):
    fields = usage_fields({"prompt_tokens": 10, "cost": cost}, provider="openrouter")
    assert fields["metadata"]["cost_source"] == "unknown"
    assert "cost_details" not in fields


def test_invalid_usage_breakdown_retains_totals_without_negative_buckets():
    fields = usage_fields(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 99,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 8},
        },
        provider="openrouter",
    )
    assert fields["usage_details"] == {"input": 10, "output": 5, "total": 99}
    assert fields["metadata"]["usage_inconsistent"] is True
    assert (
        fields["metadata"]["provider_usage"]["prompt_tokens_details"]["cached_tokens"]
        == 20
    )


def test_header_allowlist_rejects_secret_headers_and_invalid_request_ids():
    assert http_metadata(
        SimpleNamespace(
            status_code=429,
            headers={
                "X-Request-ID": "bad\nid",
                "Set-Cookie": "secret",
                "Authorization": "secret",
                "Retry-After": "2",
            },
        ),
    ) == {"http_status": 429, "rate_limit": {"retry-after": "2"}}


@pytest.mark.parametrize("first_failure", [429, 503, "transport"])
def test_groq_sdk_retries_emit_one_safe_attempt_span_per_http_send(
    monkeypatch,
    traces,
    first_failure,
):
    import groq._base_client

    monkeypatch.setattr(
        groq._base_client.BaseClient,
        "_calculate_retry_timeout",
        lambda *args, **kwargs: 0,
    )
    attempts = []

    def respond(request):
        attempts.append(request.headers.get("x-stainless-retry-count"))
        if len(attempts) == 1:
            if first_failure == "transport":
                raise httpx.ConnectError("private transport error", request=request)
            return httpx.Response(
                first_failure,
                headers={
                    "retry-after": "1",
                    "x-request-id": "attempt-1",
                    "authorization": "private-key",
                },
                json={"error": {"message": "private body", "type": "rate_limit"}},
            )
        body = completion(
            "answer",
            model="test-model",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )
        body.update(id="success", created=1, object="chat.completion")
        body["choices"][0]["index"] = 0
        return httpx.Response(200, headers={"x-request-id": "attempt-2"}, json=body)

    with groq_service.TracedGroqHTTPClient(
        transport=httpx.MockTransport(respond),
    ) as http_client:
        with groq_service.groq.Groq(
            api_key="test-key",
            http_client=http_client,
            max_retries=1,
        ) as sdk:
            service = groq_service.GroqService(model="test-model")
            service.__dict__["_client"] = groq_service.ChatGroq(
                model="test-model",
                api_key=SecretStr("test-key"),
                client=sdk.chat.completions,
                async_client=SimpleNamespace(),
            )
            assert service.generate("system", "question") == "answer"
        assert http_client.is_closed
    assert attempts == ["0", "1"]
    generations = [entry for entry in traces if entry["kind"] == "generation"]
    http_attempts = [entry for entry in traces if entry["name"] == "groq.http_attempt"]
    assert len(generations) == 1
    assert len(http_attempts) == 2
    assert [entry["metadata"]["attempt"] for entry in http_attempts] == [1, 2]
    assert all(entry["parent"] == "groq.completion" for entry in http_attempts)
    if first_failure == "transport":
        assert http_attempts[0]["error_type"] == "ConnectError"
    else:
        assert http_attempts[0]["metadata"]["http_status"] == first_failure
        assert http_attempts[0]["metadata"]["rate_limit"]["retry-after"] == "1"
        assert http_attempts[0]["level"] == "WARNING"
    assert http_attempts[1]["metadata"]["http_status"] == 200
    assert "private" not in str(traces) and "test-key" not in str(traces)


def test_groq_closes_owned_http_client_when_model_construction_fails(
    monkeypatch,
    traces,
):
    http_client = groq_service.TracedGroqHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    service = groq_service.GroqService(api_key=SecretStr("test"))
    service.__dict__["_http_client"] = http_client

    def fail(**kwargs):
        raise ValueError("invalid model configuration")

    monkeypatch.setattr(groq_service, "ChatGroq", fail)
    with pytest.raises(ValueError, match="invalid model configuration"):
        service.generate("s", "q")
    assert http_client.is_closed


def test_groq_service_close_releases_owned_client(monkeypatch, traces):
    service = groq_service.GroqService()
    http_client = groq_service.TracedGroqHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    service.__dict__["_http_client"] = http_client
    service.close()
    service.close()
    assert http_client.is_closed
