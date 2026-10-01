"""Load corpus files and attach metadata using a supplied chunking strategy."""

import hashlib
import re
from pathlib import Path

from langchain_core.documents import Document

from service.chunking import ChunkingStrategy


class CorpusService:
    def __init__(
        self,
        corpus_dir: Path,
        repo_root: Path,
        strategy: ChunkingStrategy,
    ) -> None:
        self.corpus_dir = corpus_dir
        self.repo_root = repo_root
        self.strategy = strategy

    def load(self) -> tuple[list[Document], list[str]]:
        """Load Markdown files, excluding the README, with stable chunk IDs."""
        source_files = sorted(
            path
            for path in self.corpus_dir.glob("*.md")
            if path.name.lower() != "readme.md"
        )
        if not source_files:
            raise FileNotFoundError(
                f"No operational Markdown documents found in {self.corpus_dir}"
            )

        documents = []
        ids = []
        for source in source_files:
            raw = source.read_bytes()
            markdown = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            metadata = self._metadata(markdown, source)
            metadata["file_hash"] = hashlib.sha256(raw).hexdigest()
            for section, body in self.strategy.split(markdown):
                documents.append(
                    Document(
                        page_content=f"{metadata['title']} > {section}\n\n{body}",
                        metadata={**metadata, "section": section},
                    )
                )
                slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
                ids.append(f"{metadata['doc_id']}#{slug}")

        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate chunk IDs: two chunks share a label")
        return documents, ids

    def _metadata(self, markdown: str, source: Path) -> dict[str, str]:
        patterns = {
            "title": r"(?m)^#\s+(.+?)\s*$",
            "doc_id": r"(?im)^\*\*Document ID:\*\*\s*(.+?)\s*$",
            "version": r"(?im)^\*\*Version:\*\*\s*(.+?)\s*$",
        }
        metadata = {"source": source.relative_to(self.repo_root).as_posix()}
        for name, pattern in patterns.items():
            match = re.search(pattern, markdown)
            fallback = "unknown" if name == "version" else source.stem
            metadata[name] = match.group(1).strip() if match else fallback
        return metadata
