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
    cache_hit: NotRequired[bool | None]
    latency_ms: NotRequired[float]


class RetrievalTrace(TypedDict):
    cache: Literal["hit", "miss", "off"]
    chunks: list[str]
    ms: float


class AnswerTrace(TypedDict):
    tools: list[ToolCallTrace]
    preferences: list[str]
    retrieval: NotRequired[RetrievalTrace]


class StoredMessage(TypedDict):
    who: Literal["manager", "assistant"]
    what: str
    when: str
    trace: NotRequired[AnswerTrace]


@dataclass(frozen=True)
class AnswerResult:
    text: str
    trace: AnswerTrace | None = None
