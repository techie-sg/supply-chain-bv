from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

from dispatchdesk.config import REPO_ROOT

from dispatchdesk.ingestion.embedder import embed_texts
from dispatchdesk.retrieval.vector_store import (
    get_supabase_client,
    retrieve,
)


QUERY = "Why are my deliveries slipping when it rains?"
TOP_K = 3


def main() -> None:
    print("=== DispatchDesk Retrieval Test ===")
    print(f"\nQuery: {QUERY}")

    # 1. Embed query
    query_embedding = embed_texts([QUERY])[0]

    print(
        f"Query embedding dimension: "
        f"{len(query_embedding)}"
    )

    # 2. Search Supabase
    supabase = get_supabase_client()

    results = retrieve(
        client=supabase,
        query_embedding=query_embedding,
        match_count=TOP_K,
    )

    # 3. Display results
    print(f"\nTop {len(results)} results:")

    for rank, result in enumerate(results, start=1):
        print(f"\n--- Result {rank} ---")
        print(f"Chunk ID: {result['chunk_id']}")
        print(f"Similarity: {result['similarity']:.4f}")
        print(f"Section: {result['section']}")
        print(f"Document: {result['doc_id']}")
        print()
        print(result["content"][:700])

    print("\n=== Retrieval test complete ===")


if __name__ == "__main__":
    main()
