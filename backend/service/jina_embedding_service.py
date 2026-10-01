"""Jina implementation of the embedding service."""

from time import perf_counter

import requests
import structlog
from pydantic import SecretStr

from config import get_settings, require
from constants import JINA_API_URL, JINA_MODEL
from service.embedding_service import EmbeddingService

logger = structlog.stdlib.get_logger(__name__)


class JinaEmbeddingService(EmbeddingService):
    def __init__(
        self,
        model: str = JINA_MODEL,
        api_key: SecretStr | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        api_key = require(self._api_key or get_settings().jina_api_key, "JINA_API_KEY")
        started = perf_counter()
        response = requests.post(
            JINA_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": texts,
            },
            timeout=60,
        )
        if not response.ok:
            raise requests.HTTPError(
                f"Jina embeddings failed ({response.status_code})",
                response=response,
            )

        body = response.json()
        data = sorted(body["data"], key=lambda item: item["index"])
        if len(data) != len(texts):
            raise ValueError(f"Expected {len(texts)} embeddings, received {len(data)}")
        if [item["index"] for item in data] != list(range(len(texts))):
            raise ValueError("Jina returned invalid embedding indices")

        logger.info(
            "Jina embeddings completed",
            model=self.model,
            inputs=len(texts),
            tokens=body.get("usage", {}).get("total_tokens"),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return [item["embedding"] for item in data]
