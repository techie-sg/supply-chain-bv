from langchain_core.messages import HumanMessage, SystemMessage

from dispatchdesk.config import PROMPT_PATH
from dispatchdesk.ingestion.embedder import embed_texts
from dispatchdesk.llm.client import get_llm
from dispatchdesk.retrieval.vector_store import (
    get_supabase_client,
    retrieve,
)


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_context(results: list[dict]) -> str:
    """Convert retrieved chunks into grounded context."""

    sections = []

    for result in results:
        sections.append(
            f"""[Source: {result["chunk_id"]}]
{result["content"]}"""
        )

    return "\n\n---\n\n".join(sections)


def answer_question(
    question: str,
    top_k: int = 3,
) -> str:
    """Retrieve relevant evidence and generate a grounded answer."""

    # 1. Embed question
    query_embedding = embed_texts([question])[0]

    # 2. Retrieve evidence
    supabase = get_supabase_client()

    results = retrieve(
        client=supabase,
        query_embedding=query_embedding,
        match_count=top_k,
    )

    if not results:
        return (
            "I could not find relevant guidance in the "
            "DispatchDesk knowledge base."
        )

    # 3. Build grounded context
    context = build_context(results)

    # 4. Load system prompt
    system_prompt = load_system_prompt()

    # 5. Ask LLM
    llm = get_llm()

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Retrieved context:\n\n"
                f"{context}\n\n"
                f"Question: {question}"
            )
        ),
    ]

    response = llm.invoke(messages)

    return response.content
