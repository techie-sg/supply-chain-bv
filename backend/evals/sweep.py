"""Compare chunking, embedding model and embedding type on retrieval only:
`uv run python -m evals.sweep`. Searches in memory, so the database is untouched."""

import csv
import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

from config import get_settings
from evals.run_evals import KS, load_dataset, retrieval_scores, summarize
from service.chunking import (
    ChunkingStrategy,
    FixedSizeChunkingStrategy,
    MarkdownSectionChunkingStrategy,
)
from service.corpus import CorpusService
from service.document_parser import DoclingPdfParser
from service.ingestion import BACKEND_DIR, CORPUS_DIR
from service.jina_embedding_service import JinaEmbeddingService
from service.rag import retrieval_query

CACHE = Path(__file__).with_name(".embed_cache.json")
OUT = Path(__file__).with_name("sweep-results.csv")
BATCH = 64
# A chunk counts as covering a section only past this much shared text, so a
# window that merely clips the next heading gets no credit for it.
MIN_OVERLAP = 100
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


class CachedParser(DoclingPdfParser):
    """Docling is slow; parse each PDF once per run."""

    @cache  # noqa: B019 - one parser lives for the whole run
    def to_markdown(self, source: Path) -> str:
        return super().to_markdown(source)


def slug(section: str) -> str:
    """Same section slug CorpusService uses for chunk IDs."""
    return re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")


def chunk_sections(markdown: str, bodies: list[str], doc_id: str) -> list[set[str]]:
    """Map each chunk body to the `DOC#section` IDs whose text it overlaps."""
    heads = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown))
    spans = [
        (
            h.start(),
            heads[i + 1].start() if i + 1 < len(heads) else len(markdown),
            f"{doc_id}#{slug(h.group(1))}",
        )
        for i, h in enumerate(heads)
    ]
    groups, cursor = [], 0
    for body in bodies:
        start = markdown.find(body, cursor)
        if start < 0:
            raise ValueError(f"chunk not found in {doc_id} markdown")
        cursor, end = start + 1, start + len(body)
        groups.append(
            {
                label
                for s, e, label in spans
                if min(end, e) - max(start, s) >= min(MIN_OVERLAP, e - s, end - start)
            },
        )
    return groups


def load_chunks(
    parser: CachedParser,
    strategy: ChunkingStrategy,
) -> tuple[list[str], list[set[str]]]:
    documents, ids = CorpusService(CORPUS_DIR, BACKEND_DIR, strategy, parser).load()
    if isinstance(strategy, MarkdownSectionChunkingStrategy):
        return [d.page_content for d in documents], [{i} for i in ids]
    groups: list[set[str]] = []
    by_source: dict[str, list[str]] = {}
    for d in documents:
        by_source.setdefault(d.metadata["source"], []).append(
            d.page_content.split("\n\n", 1)[1],
        )
    for source, bodies in by_source.items():
        doc_id = next(
            d.metadata["doc_id"] for d in documents if d.metadata["source"] == source
        )
        groups += chunk_sections(
            parser.to_markdown(BACKEND_DIR / source),
            bodies,
            doc_id,
        )
    return [d.page_content for d in documents], groups


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
        texts, groups = chunks[chunking]
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
