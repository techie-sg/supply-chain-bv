"""Store and search document embeddings through the existing Postgres connection."""

from typing import Any, cast

from sqlalchemy import Engine, String, Table, delete, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert

from database.models import Document, DocumentChunk
from database.session import get_session
from domain.documents import PreparedDocument


def insert_chunks(
    documents: list[PreparedDocument],
    engine: Engine | None = None,
) -> int:
    """Persist prepared documents and replace chunks in one transaction."""
    if not documents:
        return 0

    with get_session(engine) as session:
        session.execute(text("SET LOCAL search_path = public, extensions"))
        for document in documents:
            chunks = document.chunks
            metadata = document.metadata
            values = {
                "file_name": document.file_name,
                "file_hash": document.file_hash,
                "document_date": document.document_date,
                "version": document.version,
                "status": "ingested",
                "metadata": metadata,
            }
            document_insert = insert(cast(Table, Document.__table__)).values(values)
            statement = document_insert.on_conflict_do_update(
                constraint="uq_documents_hash_version",
                set_={name: document_insert.excluded[name] for name in values},
            ).returning(Document.id)
            document_id = session.execute(statement).scalar_one()
            # An edited or renamed file (such as .md to .pdf) gets a new hash, so
            # drop its older rows (chunks cascade).
            session.execute(
                delete(Document).where(
                    or_(
                        Document.file_name == values["file_name"],
                        Document.metadata_["doc_id"].astext == metadata["doc_id"],
                    ),
                    Document.id != document_id,
                ),
            )
            session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.chunk_id >= len(chunks),
                ),
            )

            rows = [
                {
                    "document_id": document_id,
                    "chunk_id": index,
                    "content": chunk.content,
                    "embedding": chunk.embedding,
                }
                for index, chunk in enumerate(chunks)
            ]
            chunk_insert = insert(cast(Table, DocumentChunk.__table__)).values(rows)
            session.execute(
                chunk_insert.on_conflict_do_update(
                    constraint="pk_document_chunks",
                    set_={
                        "content": chunk_insert.excluded.content,
                        "embedding": chunk_insert.excluded.embedding,
                    },
                ),
            )
    return sum(len(document.chunks) for document in documents)


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
