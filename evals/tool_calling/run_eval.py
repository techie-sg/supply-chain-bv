"""Tool-calling eval runner.

Can be executed directly or imported by the notebook.

Usage (from project root):
    uv run --directory backend python ../evals/tool_calling/run_eval.py
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Backend path bootstrap
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent


def _find_backend() -> Path:
    for candidate in (_PROJECT_ROOT / "backend", _PROJECT_ROOT):
        if (candidate / "service" / "rag.py").is_file():
            return candidate
    raise FileNotFoundError("Cannot locate backend/service/rag.py from %s" % _PROJECT_ROOT)


BACKEND_DIR = _find_backend()
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import service.tools as tools_mod  # noqa: E402
from dataclasses import replace  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
from domain.tools import Tool  # noqa: E402
from service.tools import traced_tools  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EVAL_DIR = _HERE
CASES_FILE = EVAL_DIR / "cases.json"
RESULTS_DIR = EVAL_DIR

# ---------------------------------------------------------------------------
# Mock tool responses
# ---------------------------------------------------------------------------

STORE_ID = "DS-BLR-014"
AS_OF = "2026-10-08T19:30:00+05:30"

MOCK_LIVE_STATUS: dict = {
    "store_id": STORE_ID,
    "scenario_key": "normal",
    "as_of": AS_OF,
    "data_age_sec": 5,
    "conditions": {"is_raining": False},
    "queue": {
        "open_orders": 6,
        "packed_waiting": 2,
        "oldest_order_age_sec": 480,
        "orders_truncated": False,
        "orders": [
            {"order_id": "ORD-01-006", "status": "out_for_delivery",    "zone_id": "Z-B", "item_count": 4, "has_frozen_items": False, "assigned_rider_id": "RDR-102", "placed_at": "2026-10-08T19:22:00+05:30", "age_sec": 480},
            {"order_id": "ORD-01-005", "status": "out_for_delivery",    "zone_id": "Z-A", "item_count": 5, "has_frozen_items": False, "assigned_rider_id": "RDR-101", "placed_at": "2026-10-08T19:23:30+05:30", "age_sec": 420},
            {"order_id": "ORD-01-001", "status": "packed_waiting_rider","zone_id": "Z-A", "item_count": 4, "has_frozen_items": False, "assigned_rider_id": None,      "placed_at": "2026-10-08T19:26:00+05:30", "age_sec": 240},
            {"order_id": "ORD-01-002", "status": "packed_waiting_rider","zone_id": "Z-B", "item_count": 3, "has_frozen_items": False, "assigned_rider_id": None,      "placed_at": "2026-10-08T19:27:00+05:30", "age_sec": 180},
            {"order_id": "ORD-01-003", "status": "picking",             "zone_id": "Z-A", "item_count": 2, "has_frozen_items": False, "assigned_rider_id": None,      "placed_at": "2026-10-08T19:28:00+05:30", "age_sec": 120},
            {"order_id": "ORD-01-004", "status": "picking",             "zone_id": "Z-C", "item_count": 5, "has_frozen_items": False, "assigned_rider_id": None,      "placed_at": "2026-10-08T19:29:00+05:30", "age_sec":  60},
        ],
    },
    "riders": [
        {"rider_id": "RDR-101", "name": "Ravi K",    "status": "on_delivery",       "current_zone": "Z-A", "employment_type": "Employed", "hours_on_shift": 5.2, "minutes_since_last_break": 95, "deliveries_today": 21, "eta_back_min": None},
        {"rider_id": "RDR-102", "name": "Imran S",   "status": "on_delivery",       "current_zone": "Z-B", "employment_type": "Gig",      "hours_on_shift": 4.0, "minutes_since_last_break": 70, "deliveries_today": 16, "eta_back_min": None},
        {"rider_id": "RDR-103", "name": "Suresh P",  "status": "available",         "current_zone": "Z-A", "employment_type": "Employed", "hours_on_shift": 3.1, "minutes_since_last_break": 40, "deliveries_today": 12, "eta_back_min": None},
        {"rider_id": "RDR-104", "name": "Manju N",   "status": "available",         "current_zone": "Z-A", "employment_type": "Gig",      "hours_on_shift": 2.5, "minutes_since_last_break": 55, "deliveries_today":  9, "eta_back_min": None},
        {"rider_id": "RDR-105", "name": "Farhan A",  "status": "available",         "current_zone": "Z-A", "employment_type": "Employed", "hours_on_shift": 4.0, "minutes_since_last_break": 60, "deliveries_today": 16, "eta_back_min": None},
        {"rider_id": "RDR-106", "name": "Deepak R",  "status": "on_break",          "current_zone": "Z-A", "employment_type": "Employed", "hours_on_shift": 6.0, "minutes_since_last_break":  0, "deliveries_today": 24, "eta_back_min": None},
        {"rider_id": "RDR-107", "name": "Lakshmi V", "status": "returning",         "current_zone": "Z-B", "employment_type": "Gig",      "hours_on_shift": 2.0, "minutes_since_last_break": 25, "deliveries_today":  8, "eta_back_min":    2},
        {"rider_id": "RDR-108", "name": "Anil M",    "status": "available",         "current_zone": "Z-A", "employment_type": "Gig",      "hours_on_shift": 1.5, "minutes_since_last_break": 20, "deliveries_today":  6, "eta_back_min": None},
        {"rider_id": "RDR-109", "name": "Kiran T",   "status": "standby_off_shift", "current_zone": "Z-A", "employment_type": "Employed", "hours_on_shift": 0.0, "minutes_since_last_break":  0, "deliveries_today":  0, "eta_back_min":   20},
    ],
    "zones": [
        {"zone_id": "Z-A", "zone_name": "Koramangala 4th Block", "distance_from_store_km": 0.8, "avg_ride_min_dry": 3.9, "avg_ride_min_rain": 5.2},
        {"zone_id": "Z-B", "zone_name": "HSR Layout Sector 2",   "distance_from_store_km": 1.6, "avg_ride_min_dry": 4.6, "avg_ride_min_rain": 6.1},
        {"zone_id": "Z-C", "zone_name": "Domlur",                "distance_from_store_km": 2.7, "avg_ride_min_dry": 6.0, "avg_ride_min_rain": 8.0},
    ],
    "summary": {
        "available_riders": 4,
        "riders_returning_within_10_min": 1,
        "pending_per_available_rider": 0.5,
        "note": None,
    },
}

MOCK_METRICS_20261007_2022: dict = {
    "store_id": STORE_ID,
    "period": {"date": "2026-10-07", "start_hour": 20, "end_hour": 22,
               "hours_requested": 2, "hours_returned": 2, "hours_missing": []},
    "hourly": [
        {"hour": 20, "orders":  96, "sla_10min_pct": 44, "avg_pick_pack_min": 3.3,
         "avg_rider_wait_min": 3.6, "avg_ride_min": 6.0, "riders_online": 17, "rain_flag": True},
        {"hour": 21, "orders": 102, "sla_10min_pct": 31, "avg_pick_pack_min": 3.5,
         "avg_rider_wait_min": 4.4, "avg_ride_min": 6.4, "riders_online": 15, "rain_flag": True},
    ],
    "period_summary": {
        "total_orders": 198, "sla_10min_pct": 37.1, "avg_pick_pack_min": 3.4,
        "avg_rider_wait_min": 4.0, "avg_ride_min": 6.2, "avg_riders_online": 16.0,
        "orders_per_rider_online": 12.38, "rain_hours": 2,
    },
    "source": "hourly_metrics (historical aggregates)",
}

MOCK_METRICS_20261007_1921: dict = {
    "store_id": STORE_ID,
    "period": {"date": "2026-10-07", "start_hour": 19, "end_hour": 21,
               "hours_requested": 2, "hours_returned": 2, "hours_missing": []},
    "hourly": [
        {"hour": 19, "orders":  74, "sla_10min_pct": 89, "avg_pick_pack_min": 2.7,
         "avg_rider_wait_min": 1.0, "avg_ride_min": 4.5, "riders_online": 20, "rain_flag": False},
        {"hour": 20, "orders":  96, "sla_10min_pct": 44, "avg_pick_pack_min": 3.3,
         "avg_rider_wait_min": 3.6, "avg_ride_min": 6.0, "riders_online": 17, "rain_flag": True},
    ],
    "period_summary": {
        "total_orders": 170, "sla_10min_pct": 63.6, "avg_pick_pack_min": 3.0,
        "avg_rider_wait_min": 2.3, "avg_ride_min": 5.3, "avg_riders_online": 18.5,
        "orders_per_rider_online": 9.19, "rain_hours": 1,
    },
    "source": "hourly_metrics (historical aggregates)",
}

MOCK_METRICS_20261006_1822: dict = {
    "store_id": STORE_ID,
    "period": {"date": "2026-10-06", "start_hour": 18, "end_hour": 22,
               "hours_requested": 4, "hours_returned": 4, "hours_missing": []},
    "hourly": [
        {"hour": 18, "orders": 52, "sla_10min_pct": 93, "avg_pick_pack_min": 2.4,
         "avg_rider_wait_min": 0.7, "avg_ride_min": 4.3, "riders_online": 18, "rain_flag": False},
        {"hour": 19, "orders": 71, "sla_10min_pct": 90, "avg_pick_pack_min": 2.6,
         "avg_rider_wait_min": 0.9, "avg_ride_min": 4.4, "riders_online": 20, "rain_flag": False},
        {"hour": 20, "orders": 88, "sla_10min_pct": 87, "avg_pick_pack_min": 2.8,
         "avg_rider_wait_min": 1.1, "avg_ride_min": 4.6, "riders_online": 20, "rain_flag": False},
        {"hour": 21, "orders": 84, "sla_10min_pct": 86, "avg_pick_pack_min": 2.8,
         "avg_rider_wait_min": 1.2, "avg_ride_min": 4.6, "riders_online": 20, "rain_flag": False},
    ],
    "period_summary": {
        "total_orders": 295, "sla_10min_pct": 88.5, "avg_pick_pack_min": 2.7,
        "avg_rider_wait_min": 1.0, "avg_ride_min": 4.5, "avg_riders_online": 19.5,
        "orders_per_rider_online": 15.13, "rain_hours": 0,
    },
    "source": "hourly_metrics (historical aggregates)",
}

# Historical metrics: 2026-10-06, 20-22 (dry, good SLA)
MOCK_METRICS_20261006_2022: dict = {
    "store_id": STORE_ID,
    "period": {"date": "2026-10-06", "start_hour": 20, "end_hour": 22,
               "hours_requested": 2, "hours_returned": 2, "hours_missing": []},
    "hourly": [
        {"hour": 20, "orders": 88, "sla_10min_pct": 87, "avg_pick_pack_min": 2.8,
         "avg_rider_wait_min": 1.1, "avg_ride_min": 4.6, "riders_online": 20, "rain_flag": False},
        {"hour": 21, "orders": 84, "sla_10min_pct": 86, "avg_pick_pack_min": 2.8,
         "avg_rider_wait_min": 1.2, "avg_ride_min": 4.6, "riders_online": 20, "rain_flag": False},
    ],
    "period_summary": {
        "total_orders": 172, "sla_10min_pct": 86.5, "avg_pick_pack_min": 2.8,
        "avg_rider_wait_min": 1.15, "avg_ride_min": 4.6, "avg_riders_online": 20.0,
        "orders_per_rider_online": 8.6, "rain_hours": 0,
    },
    "source": "hourly_metrics (historical aggregates)",
}

# Historical metrics: 2026-10-08, 20-22 (yesterday's peak — used by multi-03)
MOCK_METRICS_20261008_2022: dict = {
    "store_id": STORE_ID,
    "period": {"date": "2026-10-08", "start_hour": 20, "end_hour": 22,
               "hours_requested": 2, "hours_returned": 2, "hours_missing": []},
    "hourly": [
        {"hour": 20, "orders": 72, "sla_10min_pct": 88, "avg_pick_pack_min": 2.6,
         "avg_rider_wait_min": 0.9, "avg_ride_min": 4.4, "riders_online": 18, "rain_flag": False},
        {"hour": 21, "orders": 68, "sla_10min_pct": 85, "avg_pick_pack_min": 2.7,
         "avg_rider_wait_min": 1.0, "avg_ride_min": 4.5, "riders_online": 18, "rain_flag": False},
    ],
    "period_summary": {
        "total_orders": 140, "sla_10min_pct": 86.5, "avg_pick_pack_min": 2.65,
        "avg_rider_wait_min": 0.95, "avg_ride_min": 4.45, "avg_riders_online": 18.0,
        "orders_per_rider_online": 7.78, "rain_hours": 0,
    },
    "source": "hourly_metrics (historical aggregates)",
}

# Lookup table: (date, start_hour, end_hour) -> mock response
_METRICS_TABLE: dict[tuple, dict] = {
    ("2026-10-07", 20, 22): MOCK_METRICS_20261007_2022,
    ("2026-10-07", 19, 21): MOCK_METRICS_20261007_1921,
    ("2026-10-06", 18, 22): MOCK_METRICS_20261006_1822,
    ("2026-10-06", 20, 22): MOCK_METRICS_20261006_2022,
    ("2026-10-08", 20, 22): MOCK_METRICS_20261008_2022,
}

_AVAILABLE_PERIODS = ", ".join(
    f"{d} {sh}–{eh}" for d, sh, eh in sorted(_METRICS_TABLE)
)


def _mock_live_status(args: dict) -> str:
    sid = args.get("store_id", "")
    if sid == STORE_ID:
        return json.dumps(MOCK_LIVE_STATUS)
    return json.dumps({"error": {"code": "UNKNOWN_STORE", "message": f"Unknown store: {sid}", "details": {}}})


def _mock_delivery_metrics(args: dict) -> str:
    sid  = args.get("store_id", "")
    date = args.get("date", "")
    sh   = int(args.get("start_hour", -1))
    eh   = int(args.get("end_hour", -1))
    if sid != STORE_ID:
        return json.dumps({"error": {"code": "UNKNOWN_STORE", "message": f"Unknown store: {sid}", "details": {}}})
    mock = _METRICS_TABLE.get((date, sh, eh))
    if mock:
        return json.dumps(mock)
    return json.dumps({"error": {
        "code": "NO_METRICS_FOR_PERIOD",
        "message": f"No data for '{sid}' on {date} {sh}–{eh}. Available: {_AVAILABLE_PERIODS}.",
        "details": {"available_dates": sorted({d for d, _, _ in _METRICS_TABLE})},
    }})


_MOCK_RUN: dict[str, object] = {
    "get_live_dispatch_status": _mock_live_status,
    "get_delivery_metrics":     _mock_delivery_metrics,
}


def _mock_tools(store_id: str) -> list[Tool]:
    """Return Tool objects whose run() returns canned mock data instead of hitting the DB."""
    real_tools = tools_mod.dispatch_tools(store_id)
    return [
        replace(t, run=_MOCK_RUN[t.name])
        for t in real_tools
        if t.name in _MOCK_RUN
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


_MAX_RETRY_WAIT_SEC = 120  # if Groq says wait longer, fail the case fast


def _make_run_tools_with_retry(real_run_tools, *, max_retries: int = 4):
    """Wrap GroqService._run_tools to cap 429 retry-after at _MAX_RETRY_WAIT_SEC.

    langchain-groq raises groq.RateLimitError on 429s. We intercept, wait up to
    _MAX_RETRY_WAIT_SEC, then retry. If the server wants a longer wait we raise
    immediately so the eval case errors rather than stalling for 700+ seconds.
    """
    import groq as groq_lib

    def wrapper(self, messages, tools):
        for attempt in range(1, max_retries + 1):
            try:
                return real_run_tools(self, messages, tools)
            except groq_lib.RateLimitError as exc:
                hdrs = getattr(getattr(exc, "response", None), "headers", {}) or {}
                retry_after = int(hdrs.get("retry-after", 0))
                wait = max(retry_after, 30)
                if wait > _MAX_RETRY_WAIT_SEC or attempt >= max_retries:
                    reason = "cap exceeded" if wait > _MAX_RETRY_WAIT_SEC else "max retries"
                    print(f"\n    [429 retry-after {retry_after}s — {reason} — failing case]", flush=True)
                    raise
                print(f"\n    [429 — waiting {wait}s (attempt {attempt})]", end=" ", flush=True)
                time.sleep(wait)
            except (groq_lib.APIConnectionError, groq_lib.APITimeoutError):
                if attempt < max_retries:
                    wait = 10 * attempt
                    print(f"\n    [connection error — retrying in {wait}s]", end=" ", flush=True)
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    return wrapper


_SYSTEM_PROMPT_PATH = BACKEND_DIR / "resources" / "prompts" / "dispatch_manager_system.md"
_TIMEZONE = ZoneInfo("Asia/Kolkata")


def run_eval_case(case: dict) -> dict:
    """Run one eval case: real LLM via GroqService, mocked tool execution.

    Calls generate_with_tools directly (bypasses RAG/DB) with the same system
    prompt the production agent uses. Tool execution is replaced by mock data.
    Returns a result dict or an error stub — never raises.
    """
    from service.factory import create_llm_service

    t0    = time.monotonic()
    calls: list[dict] = []
    try:
        llm = create_llm_service()
        system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        today = datetime.now(_TIMEZONE).date().isoformat()
        system_prompt += f"\n\nToday's date is {today} (Asia/Kolkata)."

        mock_tools = traced_tools(_mock_tools(case["store_id"]), calls)
        with patch.object(type(llm), "_run_tools", _make_run_tools_with_retry(type(llm)._run_tools)):
            answer = llm.generate_with_tools(
                system_prompt=system_prompt,
                user_message=case["question"],
                tools=mock_tools,
            )
        return {
            "answer":      answer,
            "trace":       calls,
            "llm_calls":   len(calls) + 1,  # tool calls + final answer call
            "elapsed_sec": round(time.monotonic() - t0, 2),
            "_error":      None,
        }
    except Exception as exc:
        return {
            "answer":    f"[ERROR: {exc}]",
            "trace":     [],
            "llm_calls": 0,
            "elapsed_sec": round(time.monotonic() - t0, 2),
            "_error":    str(exc),
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_case(case: dict, result: dict) -> dict:
    tools_called   = [t["tool"] for t in result["trace"]]
    expected_tools = case["expected_tools"]
    called_set     = set(tools_called)
    expected_set   = set(expected_tools)

    tool_selection_correct = called_set == expected_set
    no_spurious_calls      = called_set <= expected_set
    no_missing_calls       = expected_set <= called_set

    arg_scores: list[float] = []
    for trace_entry in result["trace"]:
        tool_name     = trace_entry["tool"]
        expected_args = case["expected_args"].get(tool_name)
        if not expected_args:
            continue
        raw = trace_entry["arguments"]
        actual_args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        correct = sum(1 for k, v in expected_args.items() if actual_args.get(k) == v)
        arg_scores.append(correct / len(expected_args))

    if arg_scores:
        argument_accuracy: float | None = round(statistics.mean(arg_scores), 3)
    elif not expected_tools:
        argument_accuracy = None
    else:
        argument_accuracy = 0.0

    answer_lower = result["answer"].lower()
    keywords     = case.get("answer_keywords", [])
    answer_grounded: bool | None = (
        all(kw.lower() in answer_lower for kw in keywords) if keywords else None
    )

    return {
        "id":                     case["id"],
        "label":                  case["label"],
        "tool_selection_correct": tool_selection_correct,
        "no_spurious_calls":      no_spurious_calls,
        "no_missing_calls":       no_missing_calls,
        "argument_accuracy":      argument_accuracy,
        "answer_grounded":        answer_grounded,
        "tools_called":           tools_called,
        "expected_tools":         expected_tools,
        "trace_errors":           [t["error"] for t in result["trace"] if t.get("error")],
        "run_error":              result.get("_error"),
        "llm_calls":              result.get("llm_calls") or len(result["trace"]) + (1 if not result.get("_error") else 0),
        "elapsed_sec":            result.get("elapsed_sec"),
        "answer":                 result["answer"],
        "_trace":                 result["trace"],
    }


def compute_aggregate(scores: list[dict]) -> dict:
    n = len(scores)
    tool_correct  = [s["tool_selection_correct"] for s in scores]
    no_spurious   = [s["no_spurious_calls"]      for s in scores]
    no_missing    = [s["no_missing_calls"]        for s in scores]
    arg_vals      = [s["argument_accuracy"]       for s in scores if s["argument_accuracy"] is not None]
    ground_vals   = [s["answer_grounded"]         for s in scores if s["answer_grounded"]   is not None]
    llm_calls     = [s["llm_calls"]               for s in scores if s.get("llm_calls")]
    elapsed       = [s["elapsed_sec"]             for s in scores if s.get("elapsed_sec")]

    return {
        "n_cases":                      n,
        "total_cases":                  n,   # overwritten to full count by run()
        "tool_selection_accuracy":      round(sum(tool_correct) / n, 3),
        "no_spurious_calls_rate":       round(sum(no_spurious)  / n, 3),
        "no_missing_calls_rate":        round(sum(no_missing)   / n, 3),
        "argument_accuracy_mean":       round(statistics.mean(arg_vals),    3) if arg_vals    else None,
        "argument_accuracy_n":          len(arg_vals),
        "answer_groundedness_rate":     round(sum(ground_vals) / len(ground_vals), 3) if ground_vals else None,
        "answer_groundedness_n":        len(ground_vals),
        "avg_llm_calls":                round(statistics.mean(llm_calls),   2) if llm_calls else 0,
        "total_elapsed_sec":            round(sum(elapsed),                  1),
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_results(scores: list[dict], aggregate: dict, run_ts: str) -> tuple[Path, Path, Path]:
    payload = {
        "run_ts":    run_ts,
        "aggregate": aggregate,
        "cases":     scores,
    }
    json_path   = RESULTS_DIR / f"results_{run_ts}.json"
    csv_path    = RESULTS_DIR / f"results_{run_ts}.csv"
    trace_path  = RESULTS_DIR / f"results_{run_ts}_trace.csv"

    json_path.write_text(json.dumps(payload, indent=2, default=str))

    # Per-case summary CSV
    summary_fields = [
        "run_ts", "id", "label",
        "tool_selection_correct", "no_spurious_calls", "no_missing_calls",
        "argument_accuracy", "answer_grounded",
        "tools_called", "expected_tools", "trace_errors",
        "llm_calls", "elapsed_sec",
        "answer_snippet",
    ]
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=summary_fields, extrasaction="ignore")
        writer.writeheader()
        for row in scores:
            flat = {**row, "run_ts": run_ts}
            flat["tools_called"]    = "|".join(row["tools_called"])
            flat["expected_tools"]  = "|".join(row["expected_tools"])
            flat["trace_errors"]    = "|".join(row["trace_errors"])
            flat["answer_snippet"]  = row.get("answer", "")[:200].replace("\n", " ")
            writer.writerow(flat)

    # Trace-detail CSV — one row per tool call (cases with no calls get one row with empty trace cols)
    trace_fields = [
        "run_ts", "case_id", "case_label",
        "tool_selection_correct", "no_spurious_calls", "no_missing_calls",
        "argument_accuracy", "answer_grounded",
        "llm_calls", "elapsed_sec",
        "trace_step", "tool_name", "tool_arguments", "tool_error", "tool_as_of",
        "answer_snippet",
    ]
    with trace_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=trace_fields, extrasaction="ignore")
        writer.writeheader()
        for score in scores:
            base = {
                "run_ts":                  run_ts,
                "case_id":                 score["id"],
                "case_label":              score["label"],
                "tool_selection_correct":  score["tool_selection_correct"],
                "no_spurious_calls":       score["no_spurious_calls"],
                "no_missing_calls":        score["no_missing_calls"],
                "argument_accuracy":       score["argument_accuracy"],
                "answer_grounded":         score["answer_grounded"],
                "llm_calls":               score["llm_calls"],
                "elapsed_sec":             score.get("elapsed_sec"),
                "answer_snippet":          score.get("answer", "")[:200].replace("\n", " "),
            }
            trace_calls = [t for t in score.get("_trace", [])]
            if trace_calls:
                for t in trace_calls:
                    writer.writerow({
                        **base,
                        "trace_step":      t.get("step"),
                        "tool_name":       t.get("tool"),
                        "tool_arguments":  t.get("arguments"),
                        "tool_error":      t.get("error"),
                        "tool_as_of":      t.get("as_of"),
                    })
            else:
                writer.writerow({**base, "trace_step": "", "tool_name": "(none)",
                                 "tool_arguments": "", "tool_error": "", "tool_as_of": ""})

    return json_path, csv_path, trace_path


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _bool_str(v: bool | None) -> str:
    if v is None:
        return "N/A"
    return "PASS" if v else "FAIL"


def print_results(scores: list[dict], aggregate: dict) -> None:
    headers = ["id", "label", "tool_sel", "no_spur", "no_miss", "arg_acc", "grounded", "llm_calls", "errors"]
    rows = []
    for s in scores:
        rows.append([
            s["id"],
            s["label"],
            _bool_str(s["tool_selection_correct"]),
            _bool_str(s["no_spurious_calls"]),
            _bool_str(s["no_missing_calls"]),
            f"{s['argument_accuracy']:.0%}" if s["argument_accuracy"] is not None else "N/A",
            _bool_str(s["answer_grounded"]),
            str(s["llm_calls"]),
            ",".join(s["trace_errors"]) if s["trace_errors"] else "",
        ])

    col_widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    sep = "  ".join("-" * w for w in col_widths)

    print("\n── Per-Case Results ─────────────────────────────────────────────")
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*row))

    a = aggregate
    print("\n── Aggregate Metrics ────────────────────────────────────────────")
    print(f"  Tool selection accuracy   {a['tool_selection_accuracy']:.0%}  ({int(a['tool_selection_accuracy']*a['n_cases'])}/{a['n_cases']})")
    print(f"  No spurious calls         {a['no_spurious_calls_rate']:.0%}  ({int(a['no_spurious_calls_rate']*a['n_cases'])}/{a['n_cases']})")
    print(f"  No missing calls          {a['no_missing_calls_rate']:.0%}  ({int(a['no_missing_calls_rate']*a['n_cases'])}/{a['n_cases']})")
    if a["argument_accuracy_mean"] is not None:
        print(f"  Argument accuracy (mean)  {a['argument_accuracy_mean']:.0%}  (n={a['argument_accuracy_n']} cases with tool calls)")
    if a["answer_groundedness_rate"] is not None:
        print(f"  Answer groundedness       {a['answer_groundedness_rate']:.0%}  (n={a['answer_groundedness_n']} cases with keywords)")
    print(f"  Avg LLM calls per query   {a['avg_llm_calls']:.1f}")
    print(f"  Total wall time           {a['total_elapsed_sec']:.1f}s")
    print()

    print("── Agent Answers ─────────────────────────────────────────────────")
    for s in scores:
        print(f"\n[{s['id']}] {s['label']}")
        print(f"  Expected : {s['expected_tools'] or ['(none)']}")
        print(f"  Called   : {s['tools_called']   or ['(none)']}")
        if s["trace_errors"]:
            print(f"  Errors   : {s['trace_errors']}")
        print("  Answer:")
        for line in s["answer"].strip().splitlines():
            print(f"    {line}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(verbose: bool = True) -> tuple[list[dict], dict, str]:
    cases: list[dict] = json.loads(CASES_FILE.read_text())

    if verbose:
        print(f"Running {len(cases)} eval cases (real Groq API calls, mocked tool execution) ...")

    raw_results: list[dict] = []
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    for i, case in enumerate(cases, 1):
        if verbose:
            print(f"  [{i:2}/{len(cases)}] {case['id']:12}  ", end="", flush=True)
        result = run_eval_case(case)
        raw_results.append(result)
        if verbose:
            tools = [t["tool"] for t in result["trace"]]
            err_tag = f"  ERROR={result['_error'][:60]}" if result.get("_error") else ""
            print(f"tools={tools or ['(none)']}  llm_calls={result['llm_calls']}  {result['elapsed_sec']}s{err_tag}")

        # Score and persist after every case so the dashboard updates live.
        scores    = [score_case(c, r) for c, r in zip(cases[:i], raw_results, strict=True)]
        aggregate = compute_aggregate(scores)
        aggregate["total_cases"] = len(cases)   # carry the full count for the progress bar
        save_results(scores, aggregate, run_ts)
        _rebuild_dashboard()

        if i < len(cases):
            # 30s between cases ≈ 2 cases/min.  Per-minute 429s (retry-after ≤ 120s)
            # are retried; hourly 429s (retry-after > 120s) are capped — the case
            # is marked errored and the run continues immediately.
            time.sleep(30)

    json_path, csv_path, trace_path = save_results(scores, aggregate, run_ts)
    if verbose:
        print(f"\nResults saved:")
        print(f"  JSON        → {json_path.relative_to(_PROJECT_ROOT)}")
        print(f"  CSV summary → {csv_path.relative_to(_PROJECT_ROOT)}")
        print(f"  CSV trace   → {trace_path.relative_to(_PROJECT_ROOT)}")
        print_results(scores, aggregate)

    return scores, aggregate, run_ts


def _rebuild_dashboard() -> None:
    """Rebuild the tool-calling dashboard HTML from all result files (fast, ~ms)."""
    dashboard_script = _PROJECT_ROOT / "evals-dashboard" / "build_tool_calling_dashboard.py"
    if dashboard_script.exists():
        import subprocess
        subprocess.run(
            [sys.executable, str(dashboard_script)],
            capture_output=True,
        )


if __name__ == "__main__":
    run()
