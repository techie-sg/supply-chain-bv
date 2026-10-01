"""Jina implementation of the embedding service."""

import logging

import requests
from pydantic import SecretStr

from config import get_settings, require
from constants import JINA_API_URL, JINA_MODEL
from service.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


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
                f"Jina embeddings failed ({response.status_code})", response=response
            )

        body = response.json()
        data = sorted(body["data"], key=lambda item: item["index"])
        if len(data) != len(texts):
            raise ValueError(f"Expected {len(texts)} embeddings, received {len(data)}")
        if [item["index"] for item in data] != list(range(len(texts))):
            raise ValueError("Jina returned invalid embedding indices")

        logger.info(
            "Jina embeddings: model=%s inputs=%s tokens=%s",
            self.model,
            len(texts),
            body.get("usage", {}).get("total_tokens"),
        )
        return [item["embedding"] for item in data]
