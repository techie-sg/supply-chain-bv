"""Text messages shared by the UI and language-model services."""

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class ToolCallTrace(TypedDict):
    tool: str
    arguments: dict[str, Any]
    error: str | None
    as_of: str | None
    stale: bool


class AnswerTrace(TypedDict):
    tools: list[ToolCallTrace]
    preferences: list[str]


class StoredMessage(TypedDict):
    who: Literal["manager", "assistant"]
    what: str
    when: str
    trace: NotRequired[AnswerTrace]


@dataclass(frozen=True)
class AnswerResult:
    text: str
    trace: AnswerTrace | None = None
