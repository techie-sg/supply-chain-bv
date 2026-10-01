"""Store and search document embeddings through the existing Postgres connection."""

import re
from datetime import date
from pathlib import Path
from typing import Any, cast

from langchain_core.documents import Document as CorpusDocument
from sqlalchemy import Engine, String, Table, delete, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert

from database.models import Document, DocumentChunk
from database.session import get_session


def insert_chunks(
    documents: list[CorpusDocument],
    embeddings: list[list[float]],
    engine: Engine | None = None,
) -> int:
    """Upsert document rows and their chunks together in one transaction."""
    if len(documents) != len(embeddings):
        raise ValueError("documents and embeddings must have the same length")
    if not documents:
        return 0

    by_source: dict[str, list[tuple[CorpusDocument, list[float]]]] = {}
    for document, embedding in zip(documents, embeddings, strict=True):
        by_source.setdefault(document.metadata["source"], []).append(
            (document, embedding)
        )

    with get_session(engine) as session:
        session.execute(text("SET LOCAL search_path = public, extensions"))
        for source, chunks in by_source.items():
            metadata = chunks[0][0].metadata
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", metadata["version"])
            values = {
                "file_name": Path(source).name,
                "file_hash": bytes.fromhex(metadata["file_hash"]),
                "document_date": date.fromisoformat(date_match.group(0))
                if date_match
                else None,
                "version": metadata["version"],
                "status": "ingested",
                "metadata": {
                    "doc_id": metadata["doc_id"],
                    "title": metadata["title"],
                    "source": source,
                },
            }
            document_insert = insert(cast(Table, Document.__table__)).values(values)
            statement = document_insert.on_conflict_do_update(
                constraint="uq_documents_hash_version",
                set_={name: document_insert.excluded[name] for name in values},
            ).returning(Document.id)
            document_id = session.execute(statement).scalar_one()
            session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.chunk_id >= len(chunks),
                )
            )

            rows = [
                {
                    "document_id": document_id,
                    "chunk_id": index,
                    "content": document.page_content,
                    "embedding": embedding,
                }
                for index, (document, embedding) in enumerate(chunks)
            ]
            chunk_insert = insert(cast(Table, DocumentChunk.__table__)).values(rows)
            session.execute(
                chunk_insert.on_conflict_do_update(
                    constraint="pk_document_chunks",
                    set_={
                        "content": chunk_insert.excluded.content,
                        "embedding": chunk_insert.excluded.embedding,
                    },
                )
            )
    return len(documents)


def retrieve(
    query_embedding: list[float],
    match_count: int = 3,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    """Retrieve nearest chunks using pgvector cosine distance directly."""
    if match_count < 1:
        raise ValueError("match_count must be positive")
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    statement = (
        select(
            (
                Document.metadata_["doc_id"].astext
                + "#"
                + sql_cast(DocumentChunk.chunk_id, String)
            ).label("chunk_id"),
            DocumentChunk.content,
            (1 - distance).label("similarity"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(distance)
        .limit(match_count)
    )
    with get_session(engine) as session:
        session.execute(text("SET LOCAL search_path = public, extensions"))
        return [dict(row) for row in session.execute(statement).mappings()]
