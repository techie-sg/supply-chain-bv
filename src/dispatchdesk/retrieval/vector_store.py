from typing import Any

from supabase import Client, create_client

from dispatchdesk.config import get_supabase_key, get_supabase_url


def get_supabase_client() -> Client:
    """Create and return a Supabase client."""

    return create_client(get_supabase_url(), get_supabase_key())


def insert_chunks(
    client: Client,
    documents: list[Any],
    ids: list[str],
    embeddings: list[list[float]],
) -> int:
    """Insert embedded documents into Supabase."""

    if not (
        len(documents)
        == len(ids)
        == len(embeddings)
    ):
        raise ValueError(
            "documents, ids and embeddings must have the same length"
        )

    rows = []

    for document, chunk_id, embedding in zip(
        documents,
        ids,
        embeddings,
    ):
        metadata = document.metadata

        rows.append(
            {
                "chunk_id": chunk_id,
                "doc_id": metadata["doc_id"],
                "title": metadata["title"],
                "section": metadata["section"],
                "content": document.page_content,
                "embedding": embedding,
            }
        )

    # Upsert makes ingestion repeatable.
    response = (
        client
        .table("document_chunks")
        .upsert(rows, on_conflict="chunk_id")
        .execute()
    )

    return len(response.data)


def retrieve(
    client: Client,
    query_embedding: list[float],
    match_count: int = 3,
) -> list[dict]:
    """Retrieve the most similar chunks from Supabase."""

    response = client.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_count": match_count,
        },
    ).execute()

    return response.data
