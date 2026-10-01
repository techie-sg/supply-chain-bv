import json
from pathlib import Path
from typing import Any

from config import get_settings
from queries.vector_store import retrieve
from service.embedding_service import EmbeddingService
from service.factory import create_embedding_service, create_llm_service
from service.llm_service import LLMService

PROMPT_PATH = (
    Path(__file__).resolve().parent
    / "rag_data"
    / "prompts"
    / "dispatch_manager_system.md"
)


class RAGService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
    ) -> None:
        self.embedding_service = embedding_service
        self.llm_service = llm_service

    def answer_question(
        self,
        question: str,
        top_k: int = 3,
        *,
        scenario_context: dict[str, Any] | None = None,
    ) -> str:
        """Retrieve evidence and answer using the injected provider services."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_embedding = self.embedding_service.embed_query(question)
        results = retrieve(query_embedding=query_embedding, match_count=top_k)
        if not results:
            return (
                "I could not find relevant guidance in the DispatchDesk knowledge base."
            )

        context = self._build_context(results)
        scenario = ""
        if scenario_context is not None:
            scenario = (
                "\n\nLoaded scenario snapshot (synthetic data captured at its as_of "
                "timestamp; not a continuously live feed):\n"
                + json.dumps(scenario_context, ensure_ascii=False)
            )
        return self.llm_service.generate(
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            user_message=(
                f"Retrieved context:\n\n{context}{scenario}\n\nQuestion: {question}"
            ),
        )

    @staticmethod
    def _build_context(results: list[dict[str, Any]]) -> str:
        return "\n\n---\n\n".join(
            f"[Source: {result['chunk_id']}]\n{result['content']}" for result in results
        )


def answer_question(
    question: str,
    top_k: int = 3,
    *,
    scenario_context: dict[str, Any] | None = None,
) -> str:
    """UI entry point composing the configured services."""
    settings = get_settings()
    service = RAGService(
        embedding_service=create_embedding_service(settings),
        llm_service=create_llm_service(settings),
    )
    return service.answer_question(question, top_k, scenario_context=scenario_context)
