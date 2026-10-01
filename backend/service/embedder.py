import logging
from typing import Literal

import requests

from config import get_settings, require

JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL = "jina-embeddings-v5-text-nano"
logger = logging.getLogger(__name__)


def embed_texts(
    texts: list[str],
    api_key: str | None = None,
    model: str = JINA_MODEL,
    *,
    task: Literal["retrieval.passage", "retrieval.query"] = "retrieval.passage",
) -> list[list[float]]:
    """Generate embeddings for a list of texts using Jina."""

    if not texts:
        return []

    api_key = api_key or require(get_settings().jina_api_key, "JINA_API_KEY")

    response = requests.post(
        JINA_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": texts,
            "task": task,
            "normalized": True,
        },
        timeout=60,
    )

    if not response.ok:
        raise requests.HTTPError(
            f"Jina embeddings failed ({response.status_code})",
            response=response,
        )

    body = response.json()
    data = body["data"]

    # Jina returns embeddings with an index.
    data = sorted(data, key=lambda item: item["index"])

    embeddings = [item["embedding"] for item in data]

    if len(embeddings) != len(texts):
        raise ValueError(
            f"Expected {len(texts)} embeddings, received {len(embeddings)}"
        )

    logger.info(
        "Jina embeddings: model=%s task=%s inputs=%s tokens=%s",
        model,
        task,
        len(texts),
        body.get("usage", {}).get("total_tokens"),
    )

    return embeddings
