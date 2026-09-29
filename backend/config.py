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


def get_settings() -> Settings:
    """Read process variables and the local backend/.env file."""
    return Settings()
