from pathlib import Path
import sys

# Allow imports from src/
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

from dispatchdesk.config import (
    CHUNK_TOKENIZER_MODEL,
    CORPUS_DIR,
    EXPECTED_CHUNKS,
    MAX_CHUNK_TOKENS,
    REPO_ROOT,
)

from dispatchdesk.ingestion.chunker import (
    load_corpus,
    validate_chunk_sizes,
)
from dispatchdesk.ingestion.embedder import embed_texts
from dispatchdesk.retrieval.vector_store import (
    get_supabase_client,
    insert_chunks,
)


def main() -> None:
    print("=== DispatchDesk Corpus Ingestion ===")

    # 1. Load and chunk corpus
    print("\n[1/4] Loading corpus...")

    documents, ids = load_corpus(
        corpus_dir=CORPUS_DIR,
        repo_root=REPO_ROOT,
    )

    print(f"Loaded {len(documents)} chunks")

    if len(documents) != EXPECTED_CHUNKS:
        raise ValueError(
            f"Expected {EXPECTED_CHUNKS} chunks, "
            f"got {len(documents)}"
        )

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

    texts = [document.page_content for document in documents]

    embeddings = embed_texts(texts)

    print(
        f"Generated {len(embeddings)} embeddings "
        f"with dimension {len(embeddings[0])}"
    )

    if len(embeddings) != len(documents):
        raise ValueError(
            "Number of embeddings does not match number of chunks"
        )

    # 4. Store in Supabase
    print("\n[4/4] Upserting into Supabase...")

    supabase = get_supabase_client()

    stored = insert_chunks(
        client=supabase,
        documents=documents,
        ids=ids,
        embeddings=embeddings,
    )

    print(f"Upserted {stored} chunks into Supabase")
    print("\n=== Ingestion complete ===")


if __name__ == "__main__":
    main()
