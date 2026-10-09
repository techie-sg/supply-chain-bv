"""Input contracts for the read-only dispatch tools."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LiveStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str = Field(
        min_length=1,
        description="Dark store identifier, as shown for the current scenario.",
    )


class MetricsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str = Field(min_length=1, description="Dark store identifier.")
    date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Calendar date in Asia/Kolkata, YYYY-MM-DD.",
    )
    start_hour: int = Field(
        ge=0,
        le=23,
        description="First hour of the period, inclusive, 24-hour clock.",
    )
    end_hour: int = Field(
        ge=1,
        le=24,
        description=(
            "End of the period, exclusive, 24-hour clock. "
            "Must be greater than start_hour. 8 to 10pm is start_hour=20, end_hour=22."
        ),
    )


@dataclass(frozen=True)
class Tool:
    """A function the model may call; `run` returns the result shown to the model."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function definition, as tool-calling providers expect."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
