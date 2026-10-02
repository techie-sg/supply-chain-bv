"""Chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

from pathlib import Path

from service.chunker import (
    CHUNKING_STRATEGIES,
    LATE_CHUNKING,
    SECTION_CHUNKING,
    embed_chunks,
    load_corpus,
    validate_chunk_sizes,
)
from service.vector_store import get_supabase_client, insert_chunks

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "service" / "rag_data" / "corpus"
EXPECTED_CHUNKS = 37
MAX_CHUNK_TOKENS = 512
CHUNK_TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"


def main(chunking_strategy: str = SECTION_CHUNKING) -> None:
    print("=== DispatchDesk Corpus Ingestion ===")

    # 1. Load and chunk corpus
    print("\n[1/4] Loading corpus...")

    documents, ids = load_corpus(
        corpus_dir=CORPUS_DIR,
        repo_root=BACKEND_DIR,
    )

    print(f"Loaded {len(documents)} chunks")

    if len(documents) != EXPECTED_CHUNKS:
        raise ValueError(f"Expected {EXPECTED_CHUNKS} chunks, got {len(documents)}")

    # 2. Validate chunk sizes
    print("\n[2/4] Validating chunk sizes...")

    validate_chunk_sizes(
        documents=documents,
        ids=ids,
        embedding_model=CHUNK_TOKENIZER_MODEL,
        max_tokens=MAX_CHUNK_TOKENS,
    )

    # 3. Generate Jina embeddings
    print("\n[3/4] Generating Jina embeddings...")

    embeddings = embed_chunks(documents, strategy=chunking_strategy)

    print(f"Generated {len(embeddings)} embeddings with dimension {len(embeddings[0])}")

    if len(embeddings) != len(documents):
        raise ValueError("Number of embeddings does not match number of chunks")

    # 4. Store in Supabase
    print("\n[4/4] Upserting into Supabase...")

    supabase = get_supabase_client()

    stored = insert_chunks(
        client=supabase,
        documents=documents,
        embeddings=embeddings,
    )

    print(f"Upserted {stored} chunks into app.documents / app.document_chunks")
    print("\n=== Ingestion complete ===")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunking-strategy",
        choices=CHUNKING_STRATEGIES,
        default=SECTION_CHUNKING,
        help="Embedding strategy for section chunks (default: section)",
    )
    parser.add_argument(
        "--late-chunking",
        action="store_true",
        help="Alias for --chunking-strategy late",
    )
    args = parser.parse_args()
    if args.late_chunking and args.chunking_strategy != SECTION_CHUNKING:
        parser.error("--late-chunking cannot be combined with --chunking-strategy")
    strategy = LATE_CHUNKING if args.late_chunking else args.chunking_strategy
    main(chunking_strategy=strategy)
