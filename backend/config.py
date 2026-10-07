"""Typed runtime settings loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, GROQ_MODEL, JINA_MODEL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env",
        extra="ignore",
    )

    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL"),
    )
    jina_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    embedding_provider: str = "jina"
    embedding_model: str = JINA_MODEL
    # Changing this changes stored vectors: re-run ingestion after toggling it.
    embedding_task_adapters: bool = False
    llm_provider: str = "groq"
    llm_model: str = GROQ_MODEL
    chunking_strategy: str = "markdown_sections"
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, gt=0)
    chunk_overlap: int = Field(default=DEFAULT_CHUNK_OVERLAP, ge=0)


def get_settings() -> Settings:
    """Read process variables and the local backend/.env file."""
    return Settings()


def require(value: SecretStr | str | None, name: str) -> str:
    """Return a configured value or raise without leaking it."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if value is None or not value.strip():
        raise RuntimeError(f"Set {name} before using this feature")
    return value
