from langchain_groq import ChatGroq
from pydantic import SecretStr

from config import get_settings, require

DEFAULT_MODEL = "openai/gpt-oss-20b"


def get_llm(
    model: str = DEFAULT_MODEL,
) -> ChatGroq:
    """Create the configured Groq chat model."""

    return ChatGroq(
        model=model,
        api_key=SecretStr(require(get_settings().groq_api_key, "GROQ_API_KEY")),
        temperature=0,
    )
