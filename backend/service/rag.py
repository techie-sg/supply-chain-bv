from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from queries.vector_store import retrieve
from service.embedder import embed_texts
from service.llm import get_llm

PROMPT_PATH = (
    Path(__file__).resolve().parent
    / "rag_data"
    / "prompts"
    / "dispatch_manager_system.md"
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
    results = retrieve(
        query_embedding=query_embedding,
        match_count=top_k,
    )

    if not results:
        return "I could not find relevant guidance in the DispatchDesk knowledge base."

    # 3. Build grounded context
    context = build_context(results)

    # 4. Load system prompt
    system_prompt = load_system_prompt()

    # 5. Ask LLM
    llm = get_llm()

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(f"Retrieved context:\n\n{context}\n\nQuestion: {question}")
        ),
    ]

    response = llm.invoke(messages)

    return response.text
