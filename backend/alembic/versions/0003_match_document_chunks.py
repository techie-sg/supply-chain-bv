"""Vector search over app.document_chunks for the RAG retriever."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_match_document_chunks"
down_revision: str | Sequence[str] | None = "0002_documents_id_default"


def upgrade() -> None:
    # pgvector lives in public or extensions depending on how it was installed.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.match_document_chunks(
            query_embedding vector(768),
            match_count int DEFAULT 3
        )
        RETURNS TABLE (chunk_id text, content text, similarity float)
        LANGUAGE sql STABLE
        SET search_path = public, extensions
        AS $$
            SELECT (d.metadata->>'doc_id') || '#' || c.chunk_id,
                   c.content,
                   1 - (c.embedding::vector(768) <=> query_embedding)
            FROM app.document_chunks c
            JOIN app.documents d ON d.id = c.document_id
            ORDER BY c.embedding::vector(768) <=> query_embedding
            LIMIT match_count
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION app.match_document_chunks FROM PUBLIC, anon")
    op.execute("GRANT EXECUTE ON FUNCTION app.match_document_chunks TO service_role")
    op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.match_document_chunks")
