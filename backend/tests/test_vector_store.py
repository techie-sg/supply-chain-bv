"""Integration checks against a dedicated TEST_DATABASE_URL with pgvector."""

import os
from collections.abc import Iterator

import pytest
from langchain_core.documents import Document as CorpusDocument
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from database.models import Document, DocumentChunk
from database.session import Base, build_engine
from queries.vector_store import insert_chunks, retrieve


def corpus_document(
    content: str,
    source: str = "policy.md",
    **overrides,
) -> CorpusDocument:
    return CorpusDocument(
        page_content=content,
        metadata={
            "doc_id": "POLICY",
            "title": "Policy",
            "source": source,
            "version": "1.1 (2026-09-29)",
            "file_hash": "ab" * 32,
            **overrides,
        },
    )


@pytest.fixture
def vector_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated pgvector test database")
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            Base.metadata.tables["app.documents"].create(connection, checkfirst=True)
            Base.metadata.tables["app.document_chunks"].create(
                connection,
                checkfirst=True,
            )
            connection.execute(delete(DocumentChunk))
            connection.execute(delete(Document))
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(delete(DocumentChunk))
            connection.execute(delete(Document))
        engine.dispose()


def test_upsert_is_repeatable_and_preserves_document_id(vector_engine: Engine) -> None:
    documents = [corpus_document("one"), corpus_document("two")]
    embeddings = [[1.0, 0.0], [0.0, 1.0]]
    assert insert_chunks(documents, embeddings, vector_engine) == 2
    with vector_engine.connect() as connection:
        original_id = connection.execute(select(Document.id)).scalar_one()
    assert insert_chunks(documents, embeddings, vector_engine) == 2
    with vector_engine.connect() as connection:
        assert connection.execute(select(Document.id)).scalar_one() == original_id
        assert (
            connection.execute(
                select(func.count()).select_from(DocumentChunk),
            ).scalar_one()
            == 2
        )
        assert connection.execute(
            select(Document.file_hash),
        ).scalar_one() == bytes.fromhex("ab" * 32)
        assert (
            str(connection.execute(select(Document.document_date)).scalar_one())
            == "2026-09-29"
        )


def test_reingestion_removes_chunks_left_by_the_previous_strategy(
    vector_engine: Engine,
) -> None:
    insert_chunks(
        [corpus_document("one"), corpus_document("two"), corpus_document("three")],
        [[1.0, 0.0]] * 3,
        vector_engine,
    )
    insert_chunks([corpus_document("combined")], [[1.0, 0.0]], vector_engine)
    assert [
        result["content"] for result in retrieve([1.0, 0.0], engine=vector_engine)
    ] == ["combined"]


def test_retrieval_uses_cosine_ranking_without_rpc(vector_engine: Engine) -> None:
    documents = [corpus_document("near"), corpus_document("far")]
    assert retrieve([1.0, 0.0], engine=vector_engine) == []
    insert_chunks(documents, [[1.0, 0.0], [0.0, 1.0]], vector_engine)
    results = retrieve([1.0, 0.0], match_count=1, engine=vector_engine)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "POLICY#0"
    assert results[0]["content"] == "near"
    assert results[0]["similarity"] == pytest.approx(1.0)
    assert [
        result["content"] for result in retrieve([1.0, 0.0], engine=vector_engine)
    ] == ["near", "far"]


def test_multi_document_write_rolls_back_on_failure(vector_engine: Engine) -> None:
    documents = [
        corpus_document("valid"),
        corpus_document("invalid", "bad.md", file_hash="ab"),
    ]
    with pytest.raises(IntegrityError):
        insert_chunks(documents, [[1.0, 0.0], [0.0, 1.0]], vector_engine)
    with vector_engine.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(Document)).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                select(func.count()).select_from(DocumentChunk),
            ).scalar_one()
            == 0
        )


def test_document_without_version_date(vector_engine: Engine) -> None:
    insert_chunks(
        [corpus_document("guidance", version="unknown")],
        [[1.0, 0.0]],
        vector_engine,
    )
    with vector_engine.connect() as connection:
        assert connection.execute(select(Document.document_date)).scalar_one() is None


def test_pgvector_in_extensions_schema(vector_engine: Engine) -> None:
    with vector_engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS extensions"))
        connection.execute(text("ALTER EXTENSION vector SET SCHEMA extensions"))
    try:
        insert_chunks([corpus_document("guidance")], [[1.0, 0.0]], vector_engine)
        assert retrieve([1.0, 0.0], engine=vector_engine)[0]["content"] == "guidance"
    finally:
        with vector_engine.begin() as connection:
            connection.execute(text("ALTER EXTENSION vector SET SCHEMA public"))


def test_invalid_inputs_fail_without_database_access() -> None:
    assert insert_chunks([], []) == 0
    with pytest.raises(ValueError, match="same length"):
        insert_chunks([corpus_document("one")], [])
    with pytest.raises(ValueError, match="positive"):
        retrieve([1.0, 0.0], match_count=0)
