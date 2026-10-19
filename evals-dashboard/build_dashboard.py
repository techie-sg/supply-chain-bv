"""Build evals-dashboard.html (self-contained) from sweep-results.csv.

Usage: python3 build_dashboard.py [path/to/sweep-results.csv]
Defaults to ../backend/evals/sweep-results.csv (made by `uv run python -m evals.sweep`).
"""

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "backend" / "evals" / "sweep-results.csv"
OUT = HERE / "evals-dashboard.html"
NANO = "jina-embeddings-v5-text-nano"
SIZES = {"sections", "fixed-400-0", "fixed-800-0", "fixed-1600-0", "fixed-3200-0"}

with SRC.open(encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))
# Adapters on, float vectors, full dims; nano across chunkings, every model on sections.
full = {m: max(int(r["dims"]) for r in rows if r["model"] == m) for m in {r["model"] for r in rows}}
keep = [
    r for r in rows
    if r["task_adapters"] == "True" and r["binary"] == "False" and int(r["dims"]) == full[r["model"]]
    and r["chunking"] in SIZES and (r["model"] == NANO or r["chunking"] == "sections")
]
data = [
    {"model": r["model"], "chunking": r["chunking"], "k": k, "recall": float(r[f"recall@{k}"]),
     "precision": float(r[f"precision@{k}"]), "chunks": int(r["chunks"]), "avg_chunk_chars": int(r["avg_chunk_chars"])}
    for r in keep for k in (1, 3, 5)
]
html = (HERE / "dashboard_template.html").read_text().replace("/*DATA*/", json.dumps(data))
OUT.write_text(html)
print(f"wrote {OUT} ({len(data)} rows)")
