from typing import Any

import pytest

from config import Settings
from service import rag
from service.embedding_service import EmbeddingService
from service.llm_service import LLMService
from service.rag import RAGService


class FakeEmbeddingService(EmbeddingService):
    model = "test-embedding"

    def __init__(self) -> None:
        self.questions: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("RAG should embed only the question")

    def embed_query(self, text: str) -> list[float]:
        self.questions.append(text)
        return [0.1]


class FakeLLMService(LLMService):
    model = "test-llm"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.messages.append((system_prompt, user_message))
        return "answer"


def test_rag_uses_injected_services_and_keeps_roles_separate(monkeypatch) -> None:
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService()
    service = RAGService(embeddings, llm)
    results = [
        {"chunk_id": "doc#rain", "content": "Slow down in rain."},
        {"chunk_id": "doc#break", "content": "Take regular breaks."},
    ]

    def retrieve(**kwargs: Any) -> list[dict]:
        assert kwargs == {"query_embedding": [0.1], "match_count": 2}
        return results

    monkeypatch.setattr(rag, "retrieve", retrieve)
    context = {"scenario_key": "rain", "counts": {"orders": 12}}
    assert service.answer_question("why?", 2, scenario_context=context) == "answer"
    assert embeddings.questions == ["why?"]
    system, user = llm.messages[0]
    assert system == rag.PROMPT_PATH.read_text(encoding="utf-8")
    assert "[Source: doc#rain]" in user and "[Source: doc#break]" in user
    assert '"scenario_key": "rain"' in user
    assert "Loaded scenario snapshot" in user
    assert "Question: why?" in user
    assert "scenario_key" not in system


def test_no_evidence_does_not_call_llm(monkeypatch) -> None:
    llm = FakeLLMService()
    service = RAGService(FakeEmbeddingService(), llm)
    monkeypatch.setattr(rag, "retrieve", lambda **kwargs: [])
    assert "could not find" in service.answer_question("q")
    assert llm.messages == []


def test_invalid_top_k_is_rejected_before_embedding() -> None:
    embeddings = FakeEmbeddingService()
    service = RAGService(embeddings, FakeLLMService())
    with pytest.raises(ValueError, match="positive"):
        service.answer_question("q", 0)
    assert embeddings.questions == []


def test_ui_entry_point_composes_configured_services(monkeypatch) -> None:
    monkeypatch.setattr(rag, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService()
    monkeypatch.setattr(rag, "create_embedding_service", lambda settings: embeddings)
    monkeypatch.setattr(rag, "create_llm_service", lambda settings: llm)
    monkeypatch.setattr(
        rag,
        "retrieve",
        lambda **kwargs: [{"chunk_id": "doc#one", "content": "Guidance."}],
    )
    assert rag.answer_question("q") == "answer"
    assert embeddings.questions == ["q"]


def test_provider_failure_propagates_to_ui_error_handler(monkeypatch) -> None:
    def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    llm = FakeLLMService()
    monkeypatch.setattr(llm, "generate", fail)
    monkeypatch.setattr(
        rag,
        "retrieve",
        lambda **kwargs: [{"chunk_id": "doc#one", "content": "Guidance."}],
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        RAGService(FakeEmbeddingService(), llm).answer_question("q")
