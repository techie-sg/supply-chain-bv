"""Compare chunking, embedding model and embedding type on retrieval only:
`uv run python -m evals.sweep`. Searches in memory, so the database is untouched."""

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from config import get_settings
from evals.run_evals import (
    KS,
    CachedParser,
    load_chunks,
    load_dataset,
    retrieval_scores,
    summarize,
)
from service.chunking import (
    ChunkingStrategy,
    FixedSizeChunkingStrategy,
    MarkdownSectionChunkingStrategy,
)
from service.jina_embedding_service import JinaEmbeddingService
from service.rag import retrieval_query

CACHE = Path(__file__).with_name(".embed_cache.json")
OUT = Path(__file__).with_name("sweep-results.csv")
BATCH = 64
NANO = "jina-embeddings-v5-text-nano"
MODELS = (
    NANO,
    "jina-embeddings-v5-text-small",
    "jina-embeddings-v4",
    "jina-embeddings-v3",
)
CHUNKINGS: dict[str, ChunkingStrategy] = {
    "sections": MarkdownSectionChunkingStrategy(),
    **{
        f"fixed-{size}-{overlap}": FixedSizeChunkingStrategy(size, overlap)
        for size in (400, 800, 1600, 3200)
        for overlap in (0, size // 8)
    },
}
# Stage 1 varies chunking on today's model, with and without task adapters;
# stage 2 varies model and adapters on today's chunking. Dims and binary are
# derived locally for all.
CONFIGS = [(name, NANO, tasks) for name in CHUNKINGS for tasks in (False, True)] + [
    ("sections", model, tasks) for model in MODELS[1:] for tasks in (False, True)
]
DIMS = (None, 512, 256, 128)


def embed(
    service: JinaEmbeddingService,
    texts: list[str],
    query: bool,
    store: dict,
) -> np.ndarray:
    """Embed through a disk cache so re-runs and crashes don't re-bill Jina."""
    task = ("query" if query else "passage") if service.task_adapters else "none"
    keys = [
        hashlib.sha256(f"{service.model}|{task}|{t}".encode()).hexdigest()
        for t in texts
    ]
    missing = [i for i, k in enumerate(keys) if k not in store]
    for b in range(0, len(missing), BATCH):
        idx = missing[b : b + BATCH]
        batch = [texts[i] for i in idx]
        vectors = (
            [service.embed_query(t) for t in batch]
            if query
            else service.embed_documents(batch)
        )
        store.update({keys[i]: v for i, v in zip(idx, vectors, strict=True)})
        CACHE.write_text(json.dumps(store))
    return np.array([store[k] for k in keys])


def transform(vectors: np.ndarray, dims: int | None, binary: bool) -> np.ndarray:
    """Matryoshka truncation then renormalise; binary keeps only signs, whose
    dot product ranks exactly like Hamming similarity."""
    v = vectors[:, :dims] if dims else vectors
    if binary:
        return np.sign(v)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def main() -> None:  # pragma: no cover
    settings = get_settings()
    rows = [r for r in load_dataset() if r["expected_chunk_ids"]]
    queries = [retrieval_query(r["question"], r["history"]) for r in rows]
    store = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    parser = CachedParser()
    chunks = {name: load_chunks(parser, s) for name, s in CHUNKINGS.items()}
    keys = [f"{m}@{k}" for m in ("hit", "recall") for k in KS] + ["mrr"]

    results: list[dict[str, Any]] = []
    for chunking, model, tasks in CONFIGS:
        service = JinaEmbeddingService(
            model,
            settings.jina_api_key,
            task_adapters=tasks,
        )
        documents, groups = chunks[chunking]
        texts = [d.page_content for d in documents]
        docs, qs = (
            embed(service, texts, False, store),
            embed(service, queries, True, store),
        )
        for dims in DIMS:
            if dims and dims >= docs.shape[1]:
                continue
            for binary in (False, True):
                ranked = np.argsort(
                    -transform(qs, dims, binary) @ transform(docs, dims, binary).T,
                    axis=1,
                )
                per_q = [
                    retrieval_scores(
                        r["expected_chunk_ids"],
                        r["relevant_chunk_ids"],
                        [groups[i] for i in order[: max(KS)]],
                    )
                    | {"top3_chars": sum(len(texts[i]) for i in order[:3])}
                    for r, order in zip(rows, ranked, strict=True)
                ]
                result = {
                    "chunking": chunking,
                    "model": model,
                    "task_adapters": tasks,
                    "dims": dims or docs.shape[1],
                    "binary": binary,
                    "chunks": len(texts),
                    "avg_chunk_chars": round(sum(map(len, texts)) / len(texts)),
                    **summarize(per_q, [*keys, "top3_chars"]),
                }
                results.append(result)
                print(
                    {
                        k: result[k]
                        for k in (
                            "chunking",
                            "model",
                            "task_adapters",
                            "dims",
                            "binary",
                            "hit@1",
                            "recall@3",
                            "mrr",
                        )
                    },
                )

    with OUT.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"\n{len(results)} configs, {len(rows)} questions: {OUT}")


if __name__ == "__main__":  # pragma: no cover
    main()
