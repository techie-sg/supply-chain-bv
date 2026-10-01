"""Provider-independent contract for generating assistant responses."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from domain.chat import ChatMessage


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
