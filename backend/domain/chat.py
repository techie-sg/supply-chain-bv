"""Text messages shared by the UI and language-model services."""

from typing import Literal, TypedDict


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
