from langchain_groq import ChatGroq

from dispatchdesk.config import get_groq_api_key


DEFAULT_MODEL = "openai/gpt-oss-20b"


def get_llm(
    model: str = DEFAULT_MODEL,
) -> ChatGroq:
    """Create the configured Groq chat model."""

    return ChatGroq(
        model=model,
        api_key=get_groq_api_key(),
        temperature=0,
    )
