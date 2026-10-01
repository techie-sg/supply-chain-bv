from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config import get_settings
from domain.chat import ChatMessage
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
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Retrieve evidence and answer using the injected provider services."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        retrieval_question = question
        if history:
            recent_exchange = "\n".join(
                f"{message['role']}: {message['content']}" for message in history[-2:]
            )
            retrieval_question = f"{recent_exchange}\nFollow-up question: {question}"
        query_embedding = self.embedding_service.embed_query(retrieval_question)
        results = retrieve(query_embedding=query_embedding, match_count=top_k)
        if not results:
            return (
                "I could not find relevant guidance in the DispatchDesk knowledge base."
            )

        context = self._build_context(results)
        user_message = f"Retrieved context:\n\n{context}\n\nQuestion: {question}"
        if history:
            user_message = (
                "The manager is continuing the conversation above. Interpret the "
                "latest message using the previous user and assistant messages. "
                "The playbook excerpts below are retrieved reference material, "
                "not text pasted by the manager. Answer the manager's latest "
                "message, not the excerpts.\n\n"
                f"<context>\n{context}\n</context>\n\n"
                f"Question: {question}"
            )
        return self.llm_service.generate(
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            user_message=user_message,
            history=history,
        )

    @staticmethod
    def _build_context(results: list[dict[str, Any]]) -> str:
        return "\n\n---\n\n".join(
            f"[Source: {result['chunk_id']}]\n{result['content']}" for result in results
        )


def answer_question(
    question: str,
    top_k: int = 3,
    history: Sequence[ChatMessage] | None = None,
) -> str:
    """UI entry point composing the configured services."""
    settings = get_settings()
    service = RAGService(
        embedding_service=create_embedding_service(settings),
        llm_service=create_llm_service(settings),
    )
    return service.answer_question(question, top_k, history=history)
