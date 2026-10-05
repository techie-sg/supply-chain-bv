from pathlib import Path
from typing import Any

import pytest

from config import Settings
from service import ingestion
from service.chunking import FixedSizeChunkingStrategy, MarkdownSectionChunkingStrategy
from service.corpus import CorpusService
from service.document_parser import DocumentParser
from service.embedding_service import EmbeddingService
from service.ingestion import IngestionService


class TextParser(DocumentParser):
    suffix = ".txt"

    def to_markdown(self, source: Path) -> str:
        return source.read_text()


class FakeEmbeddingService(EmbeddingService):
    model = "test-embedding"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.1] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("ingestion must embed passages")


@pytest.mark.parametrize(
    "strategy",
    [MarkdownSectionChunkingStrategy(), FixedSizeChunkingStrategy(10, 2)],
)
def test_ingestion_accepts_each_strategy_and_preserves_embedding_alignment(
    tmp_path,
    monkeypatch,
    strategy,
) -> None:
    (tmp_path / "policy.txt").write_text("# Policy\n## Rain\nSlow down in rain.")
    corpus = CorpusService(tmp_path, tmp_path, strategy, TextParser())
    embeddings = FakeEmbeddingService()
    stored = []

    def insert_chunks(**kwargs: Any) -> int:
        documents = kwargs["documents"]
        assert [document.page_content for document in documents] == embeddings.texts
        assert len(kwargs["embeddings"]) == len(documents)
        stored.extend(documents)
        return len(documents)

    monkeypatch.setattr(ingestion, "insert_chunks", insert_chunks)
    assert IngestionService(corpus, embeddings).run() == len(stored) > 0


def test_empty_corpus_is_rejected_before_embedding_or_database_work(tmp_path) -> None:
    (tmp_path / "empty.txt").write_text("   ")
    embeddings = FakeEmbeddingService()
    corpus = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
        TextParser(),
    )
    with pytest.raises(ValueError, match="no chunks"):
        IngestionService(corpus, embeddings).run()
    assert embeddings.texts == []


def test_misaligned_embeddings_are_rejected_before_database_write(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "policy.txt").write_text(
        "# Policy\n## Rain\nSlow down.\n## Dry\nNormal.",
    )
    embeddings = FakeEmbeddingService()
    monkeypatch.setattr(embeddings, "embed_documents", lambda texts: [[0.1]])
    corpus = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
        TextParser(),
    )
    with pytest.raises(ValueError, match="does not match"):
        IngestionService(corpus, embeddings).run()


def test_ingestion_cli_composes_services(tmp_path, monkeypatch) -> None:
    (tmp_path / "policy.txt").write_text("# Policy\n## Rain\nSlow down.")
    monkeypatch.setattr(ingestion, "DoclingPdfParser", TextParser)
    monkeypatch.setattr(ingestion, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(ingestion, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(ingestion, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(
        ingestion,
        "create_embedding_service",
        lambda settings: FakeEmbeddingService(),
    )
    stored = []

    def insert_chunks(**kwargs):
        stored.append(len(kwargs["documents"]))
        return len(kwargs["documents"])

    monkeypatch.setattr(ingestion, "insert_chunks", insert_chunks)
    ingestion.main()
    assert stored == [1]
