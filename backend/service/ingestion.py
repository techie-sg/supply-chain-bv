"""Chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

import logging
from pathlib import Path

from queries.vector_store import insert_chunks
from service.chunker import load_corpus, validate_chunk_sizes
from service.embedder import embed_texts

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "service" / "rag_data" / "corpus"
EXPECTED_CHUNKS = 37
MAX_CHUNK_TOKENS = 512
CHUNK_TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting DispatchDesk corpus ingestion")

    # 1. Load and chunk corpus
    logger.info("[1/4] Loading corpus")

    documents, ids = load_corpus(
        corpus_dir=CORPUS_DIR,
        repo_root=BACKEND_DIR,
    )

    logger.info("Loaded %s chunks", len(documents))

    if len(documents) != EXPECTED_CHUNKS:
        raise ValueError(f"Expected {EXPECTED_CHUNKS} chunks, got {len(documents)}")

    # 2. Validate chunk sizes
    logger.info("[2/4] Validating chunk sizes")

    validate_chunk_sizes(
        documents=documents,
        ids=ids,
        embedding_model=CHUNK_TOKENIZER_MODEL,
        max_tokens=MAX_CHUNK_TOKENS,
    )

    # 3. Generate Jina embeddings
    logger.info("[3/4] Generating Jina embeddings")

    texts = [document.page_content for document in documents]

    embeddings = embed_texts(texts)

    logger.info(
        "Generated %s embeddings with dimension %s",
        len(embeddings),
        len(embeddings[0]),
    )

    if len(embeddings) != len(documents):
        raise ValueError("Number of embeddings does not match number of chunks")

    # 4. Store in PostgreSQL
    logger.info("[4/4] Upserting into PostgreSQL")

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
