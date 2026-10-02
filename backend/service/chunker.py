import hashlib
import re
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from transformers import AutoTokenizer

SECTION_CHUNKING = "section"
LATE_CHUNKING = "late"
CHUNKING_STRATEGIES = (SECTION_CHUNKING, LATE_CHUNKING)


def embed_chunks(
    documents: list[Document],
    strategy: str = SECTION_CHUNKING,
) -> list[list[float]]:
    """Embed production chunks with section or contextual late pooling."""
    if strategy == LATE_CHUNKING:
        return contextual_embeddings(documents)
    if strategy == SECTION_CHUNKING:
        from service.embedder import embed_texts

        return embed_texts([document.page_content for document in documents])
    raise ValueError(
        f"Unknown chunking strategy {strategy!r}; choose one of {CHUNKING_STRATEGIES}"
    )


def late_chunk_embeddings(
    token_embeddings: Any,
    offsets: list[tuple[int, int]],
    spans: list[tuple[int, int]],
) -> list[list[float]]:
    """Mean-pool contextual token vectors over character spans for each chunk.

    ``token_embeddings`` is the model's last hidden state for one document,
    with special tokens removed. Offsets and spans are character offsets in
    that same input text, following Jina's chunked-pooling approach.
    """
    import torch

    vectors = token_embeddings[0] if token_embeddings.ndim == 3 else token_embeddings
    if len(offsets) != vectors.shape[0]:
        raise ValueError("Token offsets must match the number of token embeddings")

    pooled: list[list[float]] = []
    for start, end in spans:
        indices = [
            index
            for index, (token_start, token_end) in enumerate(offsets)
            if token_end > start and token_start < end
        ]
        if not indices:
            raise ValueError(f"Chunk span {start}:{end} contains no tokens")
        embedding = vectors[indices].mean(dim=0)
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=0)
        pooled.append(embedding.detach().cpu().tolist())
    return pooled


def contextual_embeddings(
    documents: list[Document],
    model_name: str = "jinaai/jina-embeddings-v2-base-en",
    max_context_tokens: int = 8192,
) -> list[list[float]]:
    """Embed chunks after encoding their complete source documents as context."""
    import torch
    from transformers import AutoModel

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_name, trust_remote_code=True)
    model.eval()
    groups: dict[str, list[int]] = {}
    for index, document in enumerate(documents):
        groups.setdefault(document.metadata["source"], []).append(index)
    embeddings: list[list[float] | None] = [None] * len(documents)

    for indices in groups.values():
        pieces = [documents[index].page_content for index in indices]
        text = "\n\n".join(pieces)
        spans: list[tuple[int, int]] = []
        cursor = 0
        for piece in pieces:
            spans.append((cursor, cursor + len(piece)))
            cursor += len(piece) + 2
        encoded = tokenizer(text, return_tensors="pt", return_offsets_mapping=True)
        if encoded["input_ids"].shape[1] > max_context_tokens:
            raise ValueError(
                f"Document {documents[indices[0]].metadata['source']} has "
                f"{encoded['input_ids'].shape[1]} tokens; late chunking supports "
                f"at most {max_context_tokens}. Split the source document first."
            )
        offsets = [tuple(pair) for pair in encoded.pop("offset_mapping")[0].tolist()]
        with torch.inference_mode():
            output = model(**encoded)
        token_vectors = output.last_hidden_state
        # Strip the leading special token, matching the notebook's tokenizer
        # span annotation behavior.
        if offsets and offsets[0] == (0, 0):
            offsets, token_vectors = offsets[1:], token_vectors[:, 1:, :]
        pooled = late_chunk_embeddings(token_vectors, offsets, spans)
        for index, embedding in zip(indices, pooled):
            embeddings[index] = embedding

    if any(embedding is None for embedding in embeddings):
        raise ValueError("Failed to create a contextual embedding for every chunk")
    return [embedding for embedding in embeddings if embedding is not None]


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
        "doc_id": doc_id_match.group(1).strip() if doc_id_match else source.stem,
        "title": title_match.group(1).strip() if title_match else source.stem,
        "version": version_match.group(1).strip() if version_match else "unknown",
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
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)

        heading = match.group(1).strip()
        body = markdown[match.end() : end].strip()

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
        path for path in corpus_dir.glob("*.md") if path.name.lower() != "readme.md"
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
        metadata["file_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()

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

            ids.append(f"{metadata['doc_id']}#{slugify(section)}")

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate chunk IDs: two sections share a heading")

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

    longest_id = max(token_counts, key=token_counts.__getitem__)

    print(
        f"Longest chunk: {longest_id} "
        f"({token_counts[longest_id]} tokens, limit {max_tokens})"
    )

    too_long = [
        chunk_id for chunk_id, count in token_counts.items() if count > max_tokens
    ]

    if too_long:
        raise ValueError(
            f"Chunks over {max_tokens} tokens will be truncated: {too_long}"
        )

    return token_counts
