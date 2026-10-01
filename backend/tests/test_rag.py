from collections.abc import Sequence
from typing import Any

import pytest

from config import Settings
from domain.chat import ChatMessage
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
        self.histories: list[list[ChatMessage]] = []

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        self.messages.append((system_prompt, user_message))
        self.histories.append(list(history or []))
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
    assert service.answer_question("why?", 2) == "answer"
    assert embeddings.questions == ["why?"]
    system, user = llm.messages[0]
    assert system == rag.PROMPT_PATH.read_text(encoding="utf-8")
    assert user == (
        "Retrieved context:\n\n[Source: doc#rain]\nSlow down in rain."
        "\n\n---\n\n[Source: doc#break]\nTake regular breaks.\n\nQuestion: why?"
    )


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


def test_ui_entry_point_passes_conversation_to_llm(monkeypatch) -> None:
    embeddings = FakeEmbeddingService()
    llm = FakeLLMService()
    monkeypatch.setattr(rag, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(rag, "create_embedding_service", lambda settings: embeddings)
    monkeypatch.setattr(rag, "create_llm_service", lambda settings: llm)
    monkeypatch.setattr(
        rag,
        "retrieve",
        lambda **kwargs: [{"chunk_id": "doc#one", "content": "Guidance."}],
    )
    history: list[ChatMessage] = [
        {"role": "user", "content": "Should riders jump red lights?"},
        {"role": "assistant", "content": "No. Safety comes first."},
    ]
    assert rag.answer_question("Why?", history=history) == "answer"
    assert llm.histories == [history]
    assert embeddings.questions == [
        (
            "user: Should riders jump red lights?\n"
            "assistant: No. Safety comes first.\nFollow-up question: Why?"
        ),
    ]
    assert "continuing the conversation" in llm.messages[0][1]
    assert "<context>" in llm.messages[0][1]
    assert llm.messages[0][1].endswith("Question: Why?")


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
