"""Review data exposed to the manager interface."""

from dataclasses import dataclass
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
