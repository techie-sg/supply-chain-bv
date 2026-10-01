"""Provider-independent contract for document and query embeddings."""

from abc import ABC, abstractmethod


class EmbeddingService(ABC):
    model: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages for storage, preserving their input order."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a question in the same vector space as the passages."""
