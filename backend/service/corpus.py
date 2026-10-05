"""Parse corpus files and attach metadata using a supplied chunking strategy."""

import hashlib
import re
from pathlib import Path

from langchain_core.documents import Document

from service.chunking import ChunkingStrategy
from service.document_parser import DocumentParser

# Match header values by their format, not by where the line ends: PDF
# extraction can join header fields with each other and with stray text.
DOC_ID = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+"
VERSION = r"\d+(?:\.\d+)*(?:\s*\(\d{4}-\d{2}-\d{2}\))?"


class CorpusService:
    def __init__(
        self,
        corpus_dir: Path,
        repo_root: Path,
        strategy: ChunkingStrategy,
        parser: DocumentParser,
    ) -> None:
        self.corpus_dir = corpus_dir
        self.repo_root = repo_root
        self.strategy = strategy
        self.parser = parser

    def load(self) -> tuple[list[Document], list[str]]:
        """Parse source documents to Markdown and chunk them with stable IDs."""
        source_files = sorted(self.corpus_dir.glob(f"*{self.parser.suffix}"))
        if not source_files:
            raise FileNotFoundError(
                f"No {self.parser.suffix} documents found in {self.corpus_dir}",
            )

        documents = []
        ids = []
        for source in source_files:
            markdown = self.parser.to_markdown(source)
            markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
            metadata = self._metadata(markdown, source)
            metadata["file_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()
            for section, body in self.strategy.split(markdown):
                documents.append(
                    Document(
                        page_content=f"{metadata['title']} > {section}\n\n{body}",
                        metadata={**metadata, "section": section},
                    ),
                )
                slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
                ids.append(f"{metadata['doc_id']}#{slug}")

        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate chunk IDs: two chunks share a label")
        return documents, ids

    def _metadata(self, markdown: str, source: Path) -> dict[str, str]:
        patterns = {
            "title": r"(?m)^#\s+(.+?)\s*$",
            "doc_id": rf"\bDocument ID:\**\s*({DOC_ID})\b",
            "version": rf"\bVersion:\**\s*({VERSION})",
        }
        metadata = {"source": source.relative_to(self.repo_root).as_posix()}
        for name, pattern in patterns.items():
            match = re.search(pattern, markdown)
            fallback = "unknown" if name == "version" else source.stem
            metadata[name] = match.group(1).strip() if match else fallback
        return metadata
