import hashlib
from pathlib import Path

import pytest

from resources import BACKEND_DIR, CORPUS_DIR
from service.chunking import FixedSizeChunkingStrategy, MarkdownSectionChunkingStrategy
from service.corpus import CorpusService
from service.document_parser import DoclingPdfParser, DocumentParser


class TextParser(DocumentParser):
    suffix = ".txt"

    def to_markdown(self, source: Path) -> str:
        return source.read_text()


def test_current_pdf_corpus_retains_existing_chunks_and_metadata() -> None:
    service = CorpusService(
        CORPUS_DIR,
        BACKEND_DIR,
        MarkdownSectionChunkingStrategy(),
        DoclingPdfParser(),
    )
    documents, ids = service.load()
    assert len(documents) == len(set(ids)) == 37
    assert all("#" in chunk_id for chunk_id in ids)
    first = documents[0].metadata
    assert first["source"] == "service/rag_data/corpus/01-dispatch-sop.pdf"
    assert first["title"] == "Dispatch SOP: safe queue triage and proposal workflow"
    assert (first["doc_id"], first["version"]) == ("DD-SOP-001", "1.1 (2026-09-29)")
    assert first["section"] == "Purpose and source boundaries"
    versions = {
        document.metadata["doc_id"]: document.metadata["version"]
        for document in documents
    }
    assert versions == {
        "DD-SOP-001": "1.1 (2026-09-29)",
        "DD-DIAG-001": "1.1 (2026-09-29)",
        "DD-BATCH-001": "1.1 (2026-09-29)",
        "DD-WEATHER-001": "1.1 (2026-09-29)",
        "DD-COMMS-001": "1.1 (2026-09-29)",
        "DD-RIDER-001": "1.1 (2026-09-29)",
        "DD-QUICKREF-001": "1.0 (2026-09-29)",
    }


def test_corpus_supports_a_different_strategy_without_changing_the_loader(
    tmp_path,
) -> None:
    text = "# Policy\n\n## Rain\nSlow down.\n## Breaks\nTake breaks.\n"
    (tmp_path / "policy.txt").write_text(text)
    (tmp_path / "README.md").write_text("Exclude this inventory.")
    service = CorpusService(
        tmp_path,
        tmp_path,
        FixedSizeChunkingStrategy(20, 0),
        TextParser(),
    )
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
    service = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
        TextParser(),
    )
    with pytest.raises(FileNotFoundError):
        service.load()
    (tmp_path / "a.txt").write_text("# A\n## Same\none\n## Same\ntwo\n")
    with pytest.raises(ValueError, match="Duplicate"):
        service.load()


def test_metadata_falls_back_to_filename(tmp_path) -> None:
    (tmp_path / "plain.txt").write_text("plain text")
    documents, _ = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
        TextParser(),
    ).load()
    assert documents[0].metadata["title"] == "plain"


def test_metadata_is_read_from_header_fields_joined_by_pdf_extraction(
    tmp_path,
) -> None:
    (tmp_path / "policy.txt").write_text(
        "# Policy\n\nDocument ID: DD-X-001 Version: 2.0 (2026-10-01) "
        "(wrapped Authority text), sections 4 and 5.\n\n## Rain\nSlow down.\n",
    )
    documents, _ = CorpusService(
        tmp_path,
        tmp_path,
        MarkdownSectionChunkingStrategy(),
        TextParser(),
    ).load()
    assert documents[0].metadata["doc_id"] == "DD-X-001"
    assert documents[0].metadata["version"] == "2.0 (2026-10-01)"
