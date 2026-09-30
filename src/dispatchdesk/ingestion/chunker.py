import re
from langchain_core.documents import Document
from pathlib import Path
from transformers import AutoTokenizer


def parse_metadata(markdown: str, source: Path, repo_root: Path) -> dict[str, str]:
    title_match = re.search(
        r"(?m)^#\s+(.+?)\s*$",
        markdown,
    )

    doc_id_match = re.search(
        r"(?im)^\*\*Document ID:\*\*\s*(.+?)\s*$",
        markdown,
    )

    version_match = re.search(
        r"(?im)^\*\*Version:\*\*\s*(.+?)\s*$",
        markdown,
    )

    return {
        "doc_id": doc_id_match.group(1).strip()
        if doc_id_match
        else source.stem,

        "title": title_match.group(1).strip()
        if title_match
        else source.stem,

        "version": version_match.group(1).strip()
        if version_match
        else "unknown",

        "source": source.relative_to(repo_root).as_posix(),
    }


def section_chunks(markdown: str) -> list[tuple[str, str]]:
    """Return (section heading, section body) pairs split at ## headings."""

    matches = list(
        re.finditer(
            r"(?m)^##\s+(.+?)\s*$",
            markdown,
        )
    )

    if not matches:
        return [("Document", markdown.strip())] if markdown.strip() else []

    sections: list[tuple[str, str]] = []

    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(markdown)
        )

        heading = match.group(1).strip()
        body = markdown[match.end():end].strip()

        if body:
            sections.append((heading, body))

    return sections


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_corpus(
    corpus_dir: Path,
    repo_root: Path,
) -> tuple[list[Document], list[str]]:
    """Load Markdown corpus and create stable section-level chunks."""

    source_files = sorted(
        path
        for path in corpus_dir.glob("*.md")
        if path.name.lower() != "readme.md"
    )

    if not source_files:
        raise FileNotFoundError(
            f"No operational Markdown documents found in {corpus_dir}"
        )

    documents: list[Document] = []
    ids: list[str] = []

    for source in source_files:
        markdown = source.read_text(encoding="utf-8")
        metadata = parse_metadata(markdown, source, repo_root)

        for section, body in section_chunks(markdown):
            content = f"{metadata['title']} > {section}\n\n{body}"

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        **metadata,
                        "section": section,
                    },
                )
            )

            ids.append(
                f"{metadata['doc_id']}#{slugify(section)}"
            )

    if len(ids) != len(set(ids)):
        raise ValueError(
            "Duplicate chunk IDs: two sections share a heading"
        )

    return documents, ids

def validate_chunk_sizes(
    documents: list[Document],
    ids: list[str],
    embedding_model: str,
    max_tokens: int = 512,
) -> dict[str, int]:
    """Validate that no chunk exceeds the embedding model's token limit."""

    tokenizer = AutoTokenizer.from_pretrained(embedding_model)

    token_counts = {
        chunk_id: len(tokenizer.encode(document.page_content))
        for chunk_id, document in zip(ids, documents)
    }

    longest_id = max(token_counts, key=token_counts.get)

    print(
        f"Longest chunk: {longest_id} "
        f"({token_counts[longest_id]} tokens, limit {max_tokens})"
    )

    too_long = [
        chunk_id
        for chunk_id, count in token_counts.items()
        if count > max_tokens
    ]

    if too_long:
        raise ValueError(
            f"Chunks over {max_tokens} tokens will be truncated: "
            f"{too_long}"
        )

    return token_counts