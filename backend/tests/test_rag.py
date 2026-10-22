"""On-demand policy search, source validation and tool orchestration."""

import json
from typing import Any

import pytest

from config import Settings
from constants import NO_GUIDANCE_ANSWER
from domain.chat import ChatMessage
from domain.tools import Tool
from service import rag
from service.cache import TTLCache
from service.embedding_service import EmbeddingService
from service.llm_service import LLMService
from service.rag import RAGService


class FakeEmbeddingService(EmbeddingService):
    model = "test-embedding"

    def __init__(self) -> None:
        self.questions: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("Search should embed only its query")

    def embed_query(self, text: str) -> list[float]:
        self.questions.append(text)
        return [0.1]


class FakeLLMService(LLMService):
    model = "test-llm"

    def __init__(self, calls=(), answer="answer") -> None:
        self.calls = calls
        self.answer = answer
        self.messages: list[tuple[str, str]] = []
        self.histories: list[list[ChatMessage]] = []
        self.tools: list[Tool] = []
        self.results: list[dict] = []

    def generate(self, system_prompt, user_message, history=None) -> str:
        raise AssertionError("Normal chat must offer the playbook tool")

    def generate_with_tools(self, system_prompt, user_message, tools, history=None):
        self.messages.append((system_prompt, user_message))
        self.histories.append(list(history or []))
        self.tools = list(tools)
        by_name = {tool.name: tool for tool in tools}
        for name, arguments in self.calls:
            self.results.append(json.loads(by_name[name].run(arguments)))
        return self.answer


SOURCE = {
    "chunk_id": "DD-BATCH-001#2",
    "content": "Frozen orders need a separate delivery.",
    "similarity": 0.9,
}
SEARCH = ("search_playbook", {"query": "frozen order batching rules"})


@pytest.fixture
def retrieval(monkeypatch):
    calls = []

    def retrieve(**kwargs: Any) -> list[dict]:
        calls.append(kwargs)
        return [SOURCE]

    monkeypatch.setattr(rag, "retrieve", retrieve)
    return calls


def test_model_sees_tools_before_any_embedding_or_database_search(monkeypatch):
    embeddings = FakeEmbeddingService()

    class Model(FakeLLMService):
        def generate_with_tools(self, *args, **kwargs):
            assert embeddings.questions == [] and retrieval_calls == []
            return super().generate_with_tools(*args, **kwargs)

    retrieval_calls = []
    monkeypatch.setattr(
        rag,
        "retrieve",
        lambda **kwargs: retrieval_calls.append(kwargs),
    )
    llm = Model(answer="I'll keep replies concise.")
    assert (
        RAGService(embeddings, llm).answer_question("Keep replies short") == llm.answer
    )
    assert embeddings.questions == [] and retrieval_calls == []
    assert [tool.name for tool in llm.tools] == ["search_playbook"]
    assert rag.pop_retrieval_trace() is None
    system, user = llm.messages[0]
    assert system.startswith(rag.PROMPT_PATH.read_text(encoding="utf-8"))
    assert "Today's date" in system
    assert user == "Question: Keep replies short"
    assert "Retrieved context:" not in user


def test_search_returns_existing_chunks_with_source_ids_and_scores(retrieval):
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService([SEARCH], "Single-drop only (DD-BATCH-001#2).")
    trace, results = [], []
    answer = RAGService(embeddings, llm).answer_question(
        "Can I batch frozen orders?",
        top_k=2,
        tool_trace=trace,
        tool_results=results,
    )
    assert answer == llm.answer
    assert embeddings.questions == [SEARCH[1]["query"]]
    assert retrieval == [{"query_embedding": [0.1], "match_count": 2}]
    assert llm.results == [{"query": SEARCH[1]["query"], "sources": [SOURCE]}]
    assert trace[0]["tool"] == "search_playbook" and trace[0]["error"] is None
    assert results[0]["tool"] == "search_playbook"
    assert results[0]["result"]["sources"] == [SOURCE]


def test_live_data_can_be_inspected_before_a_targeted_policy_search(retrieval):
    sequence = []
    embeddings = FakeEmbeddingService()

    class Model(FakeLLMService):
        def generate_with_tools(self, system_prompt, user_message, tools, history=None):
            by_name = {tool.name: tool for tool in tools}
            assert embeddings.questions == []
            live = json.loads(by_name["get_live_dispatch_status"].run({}))
            sequence.append("live")
            assert live["frozen"] is True
            policy = json.loads(
                by_name["search_playbook"].run(
                    {"query": "frozen order batching rules"},
                ),
            )
            sequence.append("policy")
            return f"Separate delivery ({policy['sources'][0]['chunk_id']})."

    live = Tool(
        "get_live_dispatch_status",
        "Live orders.",
        {"type": "object"},
        lambda args: json.dumps({"frozen": True}),
    )
    trace, results = [], []
    answer = RAGService(embeddings, Model()).answer_question(
        "Check waiting orders and suggest batches",
        tools=[live],
        tool_trace=trace,
        tool_results=results,
    )
    assert sequence == ["live", "policy"]
    assert [item["tool"] for item in trace] == [
        "get_live_dispatch_status",
        "search_playbook",
    ]
    assert len(results) == 2 and "DD-BATCH-001#2" in answer


def test_policy_only_question_does_not_require_dispatch_data(retrieval):
    def unexpected_dispatch(arguments):
        raise AssertionError("A policy-only answer should not fetch orders or riders")

    live = Tool("get_live_dispatch_status", "Live facts.", {}, unexpected_dispatch)
    llm = FakeLLMService(
        [
            ("search_playbook", {"query": "rider safety traffic lights"}),
        ],
    )
    trace = []
    RAGService(FakeEmbeddingService(), llm).answer_question(
        "Should I ask my drivers to jump red lights?",
        tools=[live],
        tool_trace=trace,
    )
    assert [item["tool"] for item in trace] == ["search_playbook"]


@pytest.mark.parametrize("failure", [None, RuntimeError("provider unavailable")])
def test_failed_search_does_not_prevent_fetching_operational_facts(
    monkeypatch,
    failure,
):
    def retrieve(**kwargs):
        if failure:
            raise failure
        return []

    monkeypatch.setattr(rag, "retrieve", retrieve)
    snapshot = {
        "as_of": "2026-10-22T18:00:00+05:30",
        "queue": {"open_orders": 7},
        "summary": {},
    }
    live = Tool(
        "get_live_dispatch_status",
        "Live facts.",
        {},
        lambda args: json.dumps(snapshot),
    )
    llm = FakeLLMService(
        [SEARCH, ("get_live_dispatch_status", {})],
        "There are 7 open orders. I cannot verify the batching policy right now.",
    )
    trace = []
    answer = RAGService(FakeEmbeddingService(), llm).answer_question(
        "What is waiting?",
        tools=[live],
        tool_trace=trace,
    )
    assert "**Open orders:** 7" in answer
    assert "can't verify a policy recommendation" in answer
    assert llm.results[0]["sources"] == []
    assert llm.results[0]["error"]["code"] == (
        "GUIDANCE_UNAVAILABLE" if failure else "NO_GUIDANCE"
    )
    assert llm.results[1] == snapshot
    assert trace[0]["error"] is not None and trace[1]["error"] is None


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": "   "}, {"query": "x", "extra": 1}, {"query": "x" * 4001}],
)
def test_invalid_search_arguments_do_not_embed_or_query(arguments, retrieval):
    embeddings = FakeEmbeddingService()
    tool = RAGService(embeddings, FakeLLMService()).search_tool()
    assert json.loads(tool.run(arguments))["error"]["code"] == "INVALID_INPUT"
    assert embeddings.questions == [] and retrieval == []
    assert tool.schema()["function"]["parameters"]["additionalProperties"] is False


def test_invalid_top_k_is_rejected_before_the_model_or_embedding():
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService()
    with pytest.raises(ValueError, match="positive"):
        RAGService(embeddings, llm).answer_question("q", 0)
    assert embeddings.questions == [] and llm.messages == []


@pytest.mark.parametrize("citation", ["DD-BATCH-001#2", "DD-BATCH-001"])
def test_citations_can_reference_a_returned_chunk_or_its_document(retrieval, citation):
    llm = FakeLLMService([SEARCH], f"Separate delivery ({citation}).")
    assert RAGService(FakeEmbeddingService(), llm).answer_question("q") == llm.answer


@pytest.mark.parametrize("citation", ["DD-RIDER-001", "DD-BATCH-001#99"])
def test_unretrieved_policy_citations_are_not_shown(retrieval, citation):
    llm = FakeLLMService([SEARCH], f"It's permitted ({citation}).")
    assert (
        RAGService(FakeEmbeddingService(), llm).answer_question("q")
        == NO_GUIDANCE_ANSWER
    )


def test_old_chat_citations_are_not_evidence_for_this_answer(retrieval):
    llm = FakeLLMService([SEARCH], "Separate delivery (DD-BATCH-001#2).")
    service = RAGService(FakeEmbeddingService(), llm)
    results = []
    assert service.answer_question("first", tool_results=results) == llm.answer
    assert rag.pop_retrieval_trace() is not None
    llm.calls = []
    assert service.answer_question("again", tool_results=results) == NO_GUIDANCE_ANSWER
    assert rag.pop_retrieval_trace() is None


def test_followup_history_reaches_model_but_only_model_query_is_embedded(retrieval):
    history: list[ChatMessage] = [
        {"role": "user", "content": "Can I batch frozen orders?"},
        {"role": "assistant", "content": "No, use a separate delivery."},
    ]
    llm = FakeLLMService([SEARCH], "Cold-chain rules (DD-BATCH-001#2).")
    embeddings = FakeEmbeddingService()
    RAGService(embeddings, llm).answer_question("Why?", history=history)
    assert llm.histories == [history]
    assert embeddings.questions == [SEARCH[1]["query"]]
    assert "Resolve references" in llm.messages[0][1]
    assert llm.messages[0][1].endswith("Question: Why?")


def test_provider_failure_still_propagates_to_ui_error_handler(monkeypatch):
    llm = FakeLLMService()

    def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(llm, "generate_with_tools", fail)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        RAGService(FakeEmbeddingService(), llm).answer_question("q")


class FakePreferences:
    def prompt_block(self) -> str:
        return "<preferences>\n- incentive cap: 300\n</preferences>"


def test_manager_context_and_actual_question_are_kept_separate_from_search(retrieval):
    llm = FakeLLMService()
    embeddings = FakeEmbeddingService()
    RAGService(embeddings, llm).answer_question(
        "Keep it concise",
        preferences=FakePreferences(),
        alerts="<alerts>Known alert</alerts>",
        summary="- Earlier conversation.",
        handover="- Earlier shift.",
        personalization="Name: Casey",
    )
    user = llm.messages[0][1]
    order = [
        user.index(tag)
        for tag in (
            "<preferences>",
            "<alerts>",
            "<handover_notes>",
            "<personalization>",
            "<conversation_summary>",
            "Question:",
        )
    ]
    assert order == sorted(order)
    assert user.endswith("Question: Keep it concise")
    assert "Saved personal context and preferences" in user
    assert "Saved response preferences" not in user
    assert embeddings.questions == []


def test_ui_entry_point_composes_services_and_passes_trace_lists(
    monkeypatch,
    retrieval,
):
    llm = FakeLLMService([SEARCH], "Single drop (DD-BATCH-001#2).")
    embeddings = FakeEmbeddingService()
    monkeypatch.setattr(rag, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(rag, "create_embedding_service", lambda settings: embeddings)
    monkeypatch.setattr(rag, "create_llm_service", lambda settings: llm)
    trace, results = [], []
    assert (
        rag.answer_question("q", tool_trace=trace, tool_results=results) == llm.answer
    )
    assert trace[0]["tool"] == results[0]["tool"] == "search_playbook"


def test_repeat_model_query_skips_embedding_and_database_query(retrieval):
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService([SEARCH])
    service = RAGService(embeddings, llm)
    service.answer_question("First question")
    assert rag.pop_retrieval_trace()["cache"] == "miss"
    llm.calls = [("search_playbook", {"query": "  FROZEN order  batching rules  "})]
    service.answer_question("Different question, same policy")
    trace = rag.pop_retrieval_trace()
    assert trace["cache"] == "hit" and trace["chunks"] == [SOURCE["chunk_id"]]
    assert len(embeddings.questions) == len(retrieval) == 1


def test_search_trace_combines_queries_instead_of_losing_the_first_result(monkeypatch):
    count = 0

    def retrieve(**kwargs):
        nonlocal count
        count += 1
        return [{**SOURCE, "chunk_id": f"DD-BATCH-001#{count}"}]

    monkeypatch.setattr(rag, "retrieve", retrieve)
    llm = FakeLLMService(
        [
            SEARCH,
            ("search_playbook", {"query": "rider breaks"}),
            SEARCH,
        ],
    )
    trace = []
    RAGService(FakeEmbeddingService(), llm).answer_question("q", tool_trace=trace)
    retrieval_trace = rag.pop_retrieval_trace()
    assert retrieval_trace["cache"] == "miss"  # not overwritten by the last cache hit
    assert retrieval_trace["chunks"] == ["DD-BATCH-001#1", "DD-BATCH-001#2"]
    assert len(trace) == 3 and count == 2


def test_cache_keys_separate_top_k_and_model_selected_queries(retrieval):
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService([SEARCH])
    service = RAGService(embeddings, llm)
    service.answer_question("q", top_k=3)
    service.answer_question("q", top_k=2)
    llm.calls = [("search_playbook", {"query": "break rules"})]
    service.answer_question("q", top_k=3)
    assert len(retrieval) == 3


def test_disabled_cache_and_empty_results_are_not_reused(monkeypatch, retrieval):
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService([SEARCH])
    off = RAGService(embeddings, llm, cache=TTLCache(0, 10))
    off.answer_question("q")
    assert rag.pop_retrieval_trace()["cache"] == "off"
    off.answer_question("q")
    assert len(retrieval) == 2
    monkeypatch.setattr(rag, "retrieve", lambda **kwargs: [])
    empty = RAGService(embeddings, llm, cache=TTLCache(10, 10))
    empty.answer_question("none")
    empty.answer_question("none")
    assert len(embeddings.questions) == 4
