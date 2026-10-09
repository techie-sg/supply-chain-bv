"""Provider-independent contract for generating assistant responses."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from domain.chat import ChatMessage
from domain.tools import Tool


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
