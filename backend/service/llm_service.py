"""Provider-independent contract for generating assistant responses."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from domain.chat import ChatMessage


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


class LLMService(ABC):
    model: str

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Generate a response with the preceding conversation in message order."""

    def generate_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: Sequence[Tool],
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Generate a response, letting the model call `tools` first.

        Providers without tool support answer without them.
        """
        return self.generate(system_prompt, user_message, history)
