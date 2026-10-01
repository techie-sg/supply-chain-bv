"""Typed runtime settings loaded from environment variables."""

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env", extra="ignore"
    )

    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL"),
    )
    jina_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None


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
