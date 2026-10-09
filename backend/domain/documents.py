"""Prepared document/chunk data passed from ingestion to persistence."""

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PreparedChunk:
    content: str
    embedding: list[float]


@dataclass(frozen=True)
class PreparedDocument:
    file_name: str
    file_hash: bytes
    document_date: date | None
    version: str
    metadata: dict[str, Any]
    chunks: list[PreparedChunk]
