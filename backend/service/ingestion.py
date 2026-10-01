"""Chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

import logging
from pathlib import Path

from queries.vector_store import insert_chunks
from service.chunker import load_corpus
from service.embedder import embed_texts

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "service" / "rag_data" / "corpus"
EXPECTED_CHUNKS = 37
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting DispatchDesk corpus ingestion")

    # 1. Load and chunk corpus
    logger.info("[1/3] Loading corpus")

    documents, _ = load_corpus(
        corpus_dir=CORPUS_DIR,
        repo_root=BACKEND_DIR,
    )

    logger.info("Loaded %s chunks", len(documents))

    if len(documents) != EXPECTED_CHUNKS:
        raise ValueError(f"Expected {EXPECTED_CHUNKS} chunks, got {len(documents)}")

    # 2. Generate Jina embeddings
    logger.info("[2/3] Generating Jina embeddings")

    texts = [document.page_content for document in documents]

    embeddings = embed_texts(texts)

    logger.info(
        "Generated %s embeddings with dimension %s",
        len(embeddings),
        len(embeddings[0]),
    )

    if len(embeddings) != len(documents):
        raise ValueError("Number of embeddings does not match number of chunks")

    # 3. Store in PostgreSQL
    logger.info("[3/3] Upserting into PostgreSQL")

    stored = insert_chunks(
        documents=documents,
        embeddings=embeddings,
    )

    logger.info("Upserted %s chunks into app.documents / app.document_chunks", stored)
    logger.info("Ingestion complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
