"""Cache latency and hit rate: `uv run python -m evals.cache_latency`.

Needs the database, a loaded scenario (for metrics and live status) and the
Jina key. It makes no LLM call.
"""

import argparse
import csv
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter

from constants import DEMO_STORE_ID, TIMEZONE
from database.session import database_resources
from evals.run_evals import load_dataset
from logging_config import configure_logging
from service.cache import metrics_cache, reset_caches, retrieval_cache
from service.factory import create_embedding_service, create_llm_service
from service.rag import RAGService, pop_retrieval_trace
from service.tools import get_delivery_metrics, get_live_dispatch_status

OUT = Path(__file__).with_name("cache-latency.csv")


def timed(call: Callable[..., object], *args: object) -> float:
    started = perf_counter()
    call(*args)
    return (perf_counter() - started) * 1000


def row(path: str, cold: list[float], warm: list[float], hit_rate: float | None):
    cold_p50 = median(cold)
    warm_p50 = median(warm)
    cold_p75 = quantiles(cold, n=4)[2] if len(cold) > 1 else cold[0]
    warm_p75 = quantiles(warm, n=4)[2] if len(warm) > 1 else warm[0]
    return {
        "path": path,
        "cold_calls": len(cold),
        "warm_calls": len(warm),
        "cold_p50_ms": round(cold_p50, 2),
        "cold_p75_ms": round(cold_p75, 2),
        "warm_p50_ms": round(warm_p50, 2),
        "warm_p75_ms": round(warm_p75, 2),
        "speedup_p50_x": round(cold_p50 / warm_p50, 1),
        "speedup_p75_x": round(cold_p75 / warm_p75, 1),
        "hit_rate": "" if hit_rate is None else hit_rate,
    }


def measure_retrieval(queries: int, repeats: int) -> dict:
    rag = RAGService(create_embedding_service(), create_llm_service())
    questions = [r["question"] for r in load_dataset() if not r["history"]][:queries]
    reset_caches()
    rag.cache = retrieval_cache()
    cold, warm = [], []
    for question in questions:
        cold.append(timed(rag._retrieve, question, 3))
        trace = pop_retrieval_trace()
        assert trace and trace["cache"] == "miss"
    for _ in range(repeats):
        for question in questions:
            warm.append(timed(rag._retrieve, question, 3))
            trace = pop_retrieval_trace()
            assert trace and trace["cache"] == "hit"
    return row("retrieval", cold, warm, retrieval_cache().stats()["hit_rate"])


def measure_metrics(date: str, repeats: int) -> dict:
    reset_caches()

    def call() -> dict:
        result = get_delivery_metrics(DEMO_STORE_ID, date, 20, 22)
        if "error" in result:
            raise SystemExit(f"Metrics query failed, load a scenario: {result}")
        return result

    cold = [timed(call)]
    warm = [timed(call) for _ in range(repeats)]
    return row("metrics", cold, warm, metrics_cache().stats()["hit_rate"])


def measure_live(repeats: int) -> dict:
    # Never cached: every call is a database read, so cold == warm by design.
    calls = [timed(get_live_dispatch_status, DEMO_STORE_ID) for _ in range(repeats)]
    return row("live_status (uncached)", calls[:1], calls[1:], None)


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--date",
        default=(datetime.now(TIMEZONE) - timedelta(days=1)).date().isoformat(),
        help="finished day to query for metrics (default: yesterday)",
    )
    args = parser.parse_args()
    configure_logging()
    with database_resources():
        rows = [
            measure_retrieval(args.queries, args.repeats),
            measure_metrics(args.date, args.repeats),
            measure_live(args.repeats + 1),
        ]
    with OUT.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(r)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
