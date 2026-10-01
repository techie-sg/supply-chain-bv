"""Interchangeable strategies for splitting document text."""

import re
from abc import ABC, abstractmethod

from constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE


class ChunkingStrategy(ABC):
    @abstractmethod
    def split(self, text: str) -> list[tuple[str, str]]:
        """Return ordered (chunk label, content) pairs."""


class MarkdownSectionChunkingStrategy(ChunkingStrategy):
    def split(self, text: str) -> list[tuple[str, str]]:
        """Split at level-two headings, keeping each nonempty section intact."""
        matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
        if not matches:
            return [("Document", text.strip())] if text.strip() else []

        sections = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip()
            if body:
                sections.append((match.group(1).strip(), body))
        return sections


class FixedSizeChunkingStrategy(ChunkingStrategy):
    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        if chunk_size <= 0 or not 0 <= overlap < chunk_size:
            raise ValueError(
                "chunk_size must be positive and 0 <= overlap < chunk_size",
            )
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> list[tuple[str, str]]:
        """Split into overlapping character windows; sizes are not token counts."""
        text = text.strip()
        chunks: list[tuple[str, str]] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            body = text[start:end].strip()
            if body:
                chunks.append((f"Chunk {len(chunks) + 1}", body))
            if end == len(text):
                break
            start = end - self.overlap
        return chunks
