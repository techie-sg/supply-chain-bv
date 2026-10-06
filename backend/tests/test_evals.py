import pytest

from evals.run_evals import (
    chunk_labels,
    chunk_sections,
    load_dataset,
    parse_verdict,
    retrieval_scores,
    summarize,
)
from service.rag import retrieval_query


def test_dataset_ids_unique_and_chunk_ids_exist():
    rows = load_dataset()
    known = set().union(*chunk_labels().values())
    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert set(row["relevant_chunk_ids"]) <= set(row["expected_chunk_ids"])
        assert set(row["expected_chunk_ids"]) <= known, row["id"]
        assert bool(row["expected_chunk_ids"]) == bool(row["relevant_chunk_ids"])


def test_chunk_labels_map_stored_index_to_section_slug():
    labels = chunk_labels()
    assert labels["DD-SOP-001#0"] == {"DD-SOP-001#purpose-and-source-boundaries"}


def test_retrieval_scores():
    scores = retrieval_scores(["a", "b"], ["b"], ["x", "b", "a", "y", "z"])
    assert scores["hit@1"] == 0.0
    assert scores["hit@3"] == 1.0
    assert scores["recall@1"] == 0.0
    assert scores["recall@3"] == 1.0
    assert scores["mrr"] == 0.5
    assert retrieval_scores(["a"], ["a"], ["x"])["mrr"] == 0.0


ALL_PASS = '"behavior": 1, "no_invented_facts": 1, "no_execution_claim": 1'


@pytest.mark.parametrize(
    ("reply", "safety_sensitive", "expected"),
    [
        ("{" + ALL_PASS + ', "safety": null}', False, 1),
        ("Sure: {" + ALL_PASS + ', "safety": 1}', True, 1),
        # A judge that skips an applicable check must not pass the row.
        ("{" + ALL_PASS + ', "safety": null}', True, 0),
        ('{"behavior": 1, "no_execution_claim": 0}', False, 0),
        ("no json here", False, 0),
    ],
)
def test_parse_verdict(reply, safety_sensitive, expected):
    row = {"safety_sensitive": safety_sensitive, "requires_live_data": False}
    verdict = parse_verdict(reply, row)
    assert verdict["pass"] == expected
    assert verdict["live_data_honesty"] is None
    assert (verdict["safety"] is None) != safety_sensitive


def test_summarize_skips_missing_values():
    rows = [{"mrr": 1.0, "safety": None}, {"mrr": 0.5}]
    assert summarize(rows, ["mrr", "safety"]) == {"mrr": 0.75}


def test_retrieval_query_includes_latest_exchange():
    history = [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert retrieval_query("q2") == "q2"
    assert retrieval_query("q2", history) == (
        "user: q1\nassistant: a1\nFollow-up question: q2"
    )


def test_retrieval_scores_accepts_chunks_spanning_sections():
    scores = retrieval_scores(["a", "b"], ["b"], [{"x"}, {"a", "b"}])
    assert scores["hit@1"] == 0.0
    assert scores["recall@3"] == 1.0
    assert scores["mrr"] == 0.5


def test_sweep_maps_fixed_chunks_to_overlapped_sections():
    a, b = "x" * 300, "y" * 300
    markdown = f"# T\n\n## Alpha One\n\n{a}\n\n## Beta\n\n{b}\n"
    start = markdown.index(a)
    bodies = [a, markdown[start + 250 : start + 450], b[:50]]
    assert chunk_sections(markdown, bodies, "D") == [
        {"D#alpha-one"},
        {"D#beta"},  # only 50 chars of Alpha One: below MIN_OVERLAP
        {"D#beta"},
    ]
