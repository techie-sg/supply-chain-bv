import pytest

from service.chunking import FixedSizeChunkingStrategy, MarkdownSectionChunkingStrategy


def test_markdown_sections_preserve_headings_and_skip_empty_sections() -> None:
    strategy = MarkdownSectionChunkingStrategy()
    assert strategy.split("# Title\n## One\nFirst\n## Empty\n## Two\nSecond") == [
        ("One", "First"),
        ("Two", "Second"),
    ]
    assert strategy.split("no headings") == [("Document", "no headings")]
    assert strategy.split("   ") == []


def test_fixed_size_windows_overlap_without_redundant_trailing_chunk() -> None:
    strategy = FixedSizeChunkingStrategy(chunk_size=6, overlap=2)
    assert strategy.split("abcdefghijklmnop") == [
        ("Chunk 1", "abcdef"),
        ("Chunk 2", "efghij"),
        ("Chunk 3", "ijklmn"),
        ("Chunk 4", "mnop"),
    ]
    assert strategy.split("abcdef") == [("Chunk 1", "abcdef")]
    assert strategy.split("   ") == []
    assert FixedSizeChunkingStrategy(3, 0).split("abcdef") == [
        ("Chunk 1", "abc"),
        ("Chunk 2", "def"),
    ]


@pytest.mark.parametrize("size, overlap", [(0, 0), (-1, 0), (4, -1), (4, 4), (4, 5)])
def test_invalid_windows_are_rejected(size, overlap) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        FixedSizeChunkingStrategy(size, overlap)
