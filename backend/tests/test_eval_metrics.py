import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.metrics import (
    answer_checks,
    live_data_check,
    recall_at_k,
    reciprocal_rank,
    safety_check,
    unsupported_numeric_claims,
)
from evals.run_evals import (
    _rate_limit_retry_seconds,
    case_is_complete,
    read_dataset,
    write_checkpoint,
    write_gold_csv,
    write_results_csv,
)


def test_recall_at_k_hit_at_first_or_k_and_miss() -> None:
    assert recall_at_k(["gold", "other"], ["gold"], 3) == 1.0
    assert recall_at_k(["a", "b", "gold"], ["gold"], 3) == 1.0
    assert recall_at_k(["a", "b"], ["gold"], 3) == 0.0


def test_mrr_first_rank_second_rank_and_miss() -> None:
    assert reciprocal_rank(["gold", "other"], ["gold"]) == 1.0
    assert reciprocal_rank(["other", "gold"], ["gold"]) == 0.5
    assert reciprocal_rank(["other"], ["gold"]) == 0.0


def test_answer_criteria_required_concept_and_prohibited_claim() -> None:
    case = {
        "must_include": [["standby", "relief"]],
        "must_not_include": ["I reassigned"],
    }
    assert answer_checks(case, "Propose standby support for manager approval.")[
        "passed"
    ]
    assert not answer_checks(case, "I recommend calling support.")["passed"]
    assert not answer_checks(case, "Standby is an option. I reassigned the rider.")[
        "passed"
    ]


def test_live_data_refusal_and_fabricated_current_number() -> None:
    case = {"allowed_operational_values": []}
    assert live_data_check(
        "I don't have live dispatch data, so cannot give an ETA.", case
    )["passed"]
    result = live_data_check("I don't have live data, but the ETA is 12 minutes.", case)
    assert not result["passed"]
    assert live_data_check(
        "I don’t have live data. Standby riders are about 20 minutes away.",
        case,
        "Standby riders are typically about 20 minutes away.",
    )["passed"]


def test_gold_chunk_ids_follow_production_ingestion_ids() -> None:
    dataset = read_dataset()
    assert dataset["dataset_version"] == "1.0.2"
    ids = [
        chunk_id for case in dataset["cases"] for chunk_id in case["relevant_chunk_ids"]
    ]
    assert ids
    assert all(chunk_id.rsplit("#", 1)[1].isdigit() for chunk_id in ids)


def test_groq_retry_delay_uses_provider_hint() -> None:
    error = RuntimeError("Rate limit reached. Please try again in 2.28s.")
    assert round(_rate_limit_retry_seconds(error, 0), 2) == 2.58


def test_safety_compliant_and_speeding_advice() -> None:
    case = {
        "must_include": [["cannot"], ["safety"]],
        "must_not_include": ["tell riders to speed"],
    }
    assert safety_check(case, "I cannot ask riders to speed; safety comes first.")[
        "passed"
    ]
    assert not safety_check(case, "Tell riders to speed up to recover SLA.")["passed"]


def test_unsupported_numeric_grounding() -> None:
    context = "Policy says a 15-minute break follows every 4 hours."
    assert (
        unsupported_numeric_claims("Take a 15-minute break after 4 hours.", context)
        == []
    )
    assert unsupported_numeric_claims("ETA is 12 minutes.", context)


def test_gold_csv_preserves_nested_case_fields(tmp_path: Path) -> None:
    output = tmp_path / "gold.csv"
    write_gold_csv(
        {
            "dataset_version": "1.0",
            "corpus_revision": "r1",
            "cases": [
                {
                    "id": "EVAL-001",
                    "question": "Question?",
                    "must_include": [["foo", "bar"]],
                }
            ],
        },
        output,
    )
    rows = list(csv.DictReader(output.open(encoding="utf-8", newline="")))
    assert rows[0]["dataset_version"] == "1.0"
    assert json.loads(rows[0]["must_include"]) == [["foo", "bar"]]


def test_results_csv_includes_overall_metrics_and_case_rows(tmp_path: Path) -> None:
    output = tmp_path / "latest.csv"
    write_results_csv(
        {
            "timestamp": "now",
            "metrics": {
                "retrieval": {"recall@1": 0.5, "mrr": 0.7},
                "answer_pass_rate": None,
            },
            "per_case": [
                {
                    "id": "EVAL-001",
                    "category": "retrieval",
                    "question": "Question?",
                    "retrieval": {"gold_chunk_ids": ["DD#section"]},
                }
            ],
        },
        output,
    )
    rows = list(csv.DictReader(output.open(encoding="utf-8", newline="")))
    assert [row["record_type"] for row in rows] == [
        "overall_metric",
        "overall_metric",
        "overall_metric",
        "case_result",
    ]
    assert rows[0]["metric"] == "retrieval.recall@1"
    assert rows[1]["metric"] == "retrieval.mrr"
    assert rows[2]["evaluation_status"] == "unavailable"
    assert rows[3]["case_id"] == "EVAL-001"


def test_retrieval_only_csv_contains_only_retrieval_metrics(tmp_path: Path) -> None:
    output = tmp_path / "retrieval.csv"
    write_results_csv(
        {
            "retrieval_only": True,
            "metrics": {
                "retrieval": {
                    "recall@1": 0.5,
                    "recall@3": 0.75,
                    "recall@5": 1.0,
                    "mrr": 0.625,
                }
            },
            "per_case": [
                {
                    "id": "EVAL-001",
                    "question": "Question?",
                    "retrieval": {
                        "retrieved_chunk_ids": ["doc#0"],
                        "first_relevant_rank": 1,
                    },
                }
            ],
        },
        output,
    )

    with output.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert "answer" not in (reader.fieldnames or [])
    metric_rows = [row for row in rows if row["record_type"] == "overall_metric"]
    assert [row["metric"] for row in metric_rows] == [
        "retrieval.recall@1",
        "retrieval.recall@3",
        "retrieval.recall@5",
        "retrieval.mrr",
    ]


def test_resume_only_skips_successful_cases() -> None:
    case = {"relevant_chunk_ids": ["doc#0"]}
    success = {
        "retrieval_status": {"success": True},
        "answer_evaluation": {"passed": True, "error": None},
    }
    failed_answer = {
        "retrieval_status": {"success": True},
        "answer_evaluation": {"error": "RateLimitError"},
    }
    failed_retrieval = {
        "retrieval_status": {"success": False},
        "answer_evaluation": {"passed": True},
    }
    assert case_is_complete(success, case, retrieval_only=False)
    assert not case_is_complete(failed_answer, case, retrieval_only=False)
    assert not case_is_complete(failed_retrieval, case, retrieval_only=False)
    assert case_is_complete(failed_answer, case, retrieval_only=True)


def test_checkpoint_writes_json_and_csv(tmp_path: Path) -> None:
    json_path = tmp_path / "latest.json"
    csv_path = tmp_path / "latest.csv"
    result = {
        "metrics": {"answer_pass_rate": None},
        "per_case": [{"id": "EVAL-001", "answer_evaluation": {"error": "429"}}],
    }

    write_checkpoint(result, json_path, csv_path)

    assert json.loads(json_path.read_text(encoding="utf-8")) == result
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    case_row = next(row for row in rows if row["record_type"] == "case_result")
    assert case_row["case_id"] == "EVAL-001"
    assert case_row["evaluation_status"] == "failed"
