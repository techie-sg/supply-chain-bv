import re
from pathlib import Path
from typing import Any, cast

from supabase import Client, create_client

from config import get_settings, require


def get_supabase_client() -> Client:
    """Create and return a Supabase client."""

    settings = get_settings()
    return create_client(
        require(settings.supabase_url, "SUPABASE_URL"),
        require(settings.supabase_key, "SUPABASE_KEY"),
    )


def insert_chunks(
    client: Client,
    documents: list[Any],
    embeddings: list[list[float]],
) -> int:
    """Upsert one app.documents row per source file and its app.document_chunks."""

    if len(documents) != len(embeddings):
        raise ValueError("documents and embeddings must have the same length")

    by_source: dict[str, list[tuple[Any, list[float]]]] = {}
    for document, embedding in zip(documents, embeddings):
        by_source.setdefault(document.metadata["source"], []).append(
            (document, embedding)
        )

    db = client.schema("app")
    stored = 0

    for source, chunks in by_source.items():
        metadata = chunks[0][0].metadata
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", metadata["version"])

        # Upsert on (file_hash, version) makes ingestion repeatable.
        document_row = (
            db.table("documents")
            .upsert(
                {
                    "file_name": Path(source).name,
                    "file_hash": "\\x" + metadata["file_hash"],
                    "document_date": date_match.group(0) if date_match else None,
                    "version": metadata["version"],
                    "status": "ingested",
                    "metadata": {
                        "doc_id": metadata["doc_id"],
                        "title": metadata["title"],
                        "source": source,
                    },
                },
                on_conflict="file_hash,version",
            )
            .execute()
        )
        document_id = cast(list[dict], document_row.data)[0]["id"]

        rows = [
            {
                "document_id": document_id,
                "chunk_id": index,
                "content": document.page_content,
                "embedding": embedding,
            }
            for index, (document, embedding) in enumerate(chunks)
        ]
        response = (
            db.table("document_chunks")
            .upsert(rows, on_conflict="document_id,chunk_id")
            .execute()
        )
        stored += len(response.data)

    return stored


def retrieve(
    client: Client,
    query_embedding: list[float],
    match_count: int = 3,
) -> list[dict]:
    """Retrieve the most similar chunks from Supabase."""

    response = (
        client.schema("app")
        .rpc(
            "match_document_chunks",
            {
                "query_embedding": query_embedding,
                "match_count": match_count,
            },
        )
        .execute()
    )

    return cast(list[dict], response.data)
