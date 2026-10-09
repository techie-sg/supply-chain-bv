"""Review data exposed to the manager interface."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SuggestionView:
    id: UUID
    store_id: str
    manager_id: str
    kind: str
    status: str
    payload: dict[str, Any]
    reason: str
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class MemoryView:
    digest: str | None
    sources: list[dict[str, Any]]
    built_at: datetime
