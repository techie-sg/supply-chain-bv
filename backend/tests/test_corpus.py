import hashlib

import pytest

from service.chunking import FixedSizeChunkingStrategy, MarkdownSectionChunkingStrategy
from service.corpus import CorpusService
from service.ingestion import BACKEND_DIR, CORPUS_DIR


def test_current_corpus_retains_existing_chunks_and_metadata() -> None:
    service = CorpusService(CORPUS_DIR, BACKEND_DIR, MarkdownSectionChunkingStrategy())
    documents, ids = service.load()
    assert len(documents) == len(set(ids)) == 37
    assert all("#" in chunk_id for chunk_id in ids)
    assert documents[0].metadata["source"].startswith("service/rag_data/corpus/")
    assert documents[0].metadata["version"] != "unknown"
    assert documents[0].metadata["doc_id"] != "01-dispatch-sop"


def test_corpus_supports_a_different_strategy_without_changing_the_loader(
    tmp_path,
) -> None:
    text = "# Policy\n\n## Rain\nSlow down.\n## Breaks\nTake breaks.\n"
    (tmp_path / "policy.md").write_text(text)
    (tmp_path / "README.md").write_text("Exclude this inventory.")
    service = CorpusService(tmp_path, tmp_path, FixedSizeChunkingStrategy(20, 0))
    documents, ids = service.load()
    assert len(documents) == 3 and len(set(ids)) == 3
    assert (
        documents[0].metadata["file_hash"] == hashlib.sha256(text.encode()).hexdigest()
    )
    assert documents[0].metadata["version"] == "unknown"
    assert documents[0].metadata["doc_id"] == "policy"
    assert documents[0].metadata["title"] == "Policy"
    assert ids == ["policy#chunk-1", "policy#chunk-2", "policy#chunk-3"]


def test_missing_corpus_and_duplicate_chunk_ids_are_rejected(tmp_path) -> None:
    service = CorpusService(tmp_path, tmp_path, MarkdownSectionChunkingStrategy())
    with pytest.raises(FileNotFoundError):
        service.load()
    (tmp_path / "a.md").write_text("# A\n## Same\none\n## Same\ntwo\n")
    with pytest.raises(ValueError, match="Duplicate"):
        service.load()


def test_metadata_falls_back_to_filename(tmp_path) -> None:
    (tmp_path / "plain.md").write_text("plain text")
    documents, _ = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
    ).load()
    assert documents[0].metadata["title"] == "plain"
