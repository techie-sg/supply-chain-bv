"""Provider-independent contract for generating assistant responses."""

from abc import ABC, abstractmethod


class LLMService(ABC):
    model: str

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str) -> str:
        """Generate a response using separate system and user messages."""
