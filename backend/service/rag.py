from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import structlog

from config import get_settings
from domain.chat import ChatMessage
from queries.vector_store import retrieve
from service.embedding_service import EmbeddingService
from service.factory import create_embedding_service, create_llm_service
from service.llm_service import LLMService, Tool

logger = structlog.stdlib.get_logger(__name__)

NO_GUIDANCE_ANSWER = (
    "I could not find relevant guidance in the DispatchDesk knowledge base."
)
PROMPT_PATH = (
    Path(__file__).resolve().parent
    / "rag_data"
    / "prompts"
    / "dispatch_manager_system.md"
)


def retrieval_query(
    question: str,
    history: Sequence[ChatMessage] | None = None,
) -> str:
    """Text embedded for retrieval; follow-ups include the latest exchange."""
    if not history:
        return question
    recent_exchange = "\n".join(
        f"{message['role']}: {message['content']}" for message in history[-2:]
    )
    return f"{recent_exchange}\nFollow-up question: {question}"


class PreferenceContext(Protocol):
    """The manager's settings, shown to the model so its answers apply them."""

    def prompt_block(self) -> str: ...


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
        preferences: PreferenceContext | None = None,
        summary: str | None = None,
        handover: str | None = None,
        tools: Sequence[Tool] = (),
    ) -> str:
        """Retrieve evidence and answer using the injected provider services.

        With preferences, the model sees the manager's settings and applies them.
        With tools, the model may call them before answering; the settings tool
        only proposes changes, which the manager confirms outside the model.
        `summary` stands in for older messages that `history` no longer holds.
        `handover` is the latest shift's handover notes.
        """
        if top_k < 1:
            raise ValueError("top_k must be positive")
        started = perf_counter()
        query_embedding = self.embedding_service.embed_query(
            retrieval_query(question, history),
        )
        results = retrieve(query_embedding=query_embedding, match_count=top_k)
        if not results:
            logger.warning("No guidance retrieved", top_k=top_k)
            return NO_GUIDANCE_ANSWER

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
        if summary:
            user_message = (
                "<conversation_summary>\nEarlier in this chat (a summary; it may "
                f"omit details):\n{summary}\n</conversation_summary>\n\n{user_message}"
            )
        if handover:
            user_message = (
                f"<handover_notes>\n{handover}\n</handover_notes>\n\n{user_message}"
            )
        if preferences is not None:
            user_message = f"{preferences.prompt_block()}\n\n{user_message}"
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        answer = (
            self.llm_service.generate_with_tools(
                system_prompt=system_prompt,
                user_message=user_message,
                tools=tools,
                history=history,
            )
            if tools
            else self.llm_service.generate(
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
            )
        )
        logger.info(
            "RAG answer completed",
            retrieved_chunks=len(results),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return answer

    @staticmethod
    def _build_context(results: list[dict[str, Any]]) -> str:
        return "\n\n---\n\n".join(
            f"[Source: {result['chunk_id']}]\n{result['content']}" for result in results
        )


def answer_question(
    question: str,
    top_k: int = 3,
    history: Sequence[ChatMessage] | None = None,
    preferences: PreferenceContext | None = None,
    summary: str | None = None,
    handover: str | None = None,
    tools: Sequence[Tool] = (),
) -> str:
    """UI entry point composing the configured services."""
    settings = get_settings()
    service = RAGService(
        embedding_service=create_embedding_service(settings),
        llm_service=create_llm_service(settings),
    )
    return service.answer_question(
        question,
        top_k,
        history=history,
        preferences=preferences,
        summary=summary,
        handover=handover,
        tools=tools,
    )
