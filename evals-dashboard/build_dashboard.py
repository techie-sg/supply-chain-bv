"""Build evals-dashboard.html (self-contained, no deps) from evals-embedding-chunking.md.

Usage: python3 build_dashboard.py [path/to/evals-embedding-chunking.md]
Defaults to ../backend/evals/evals-embedding-chunking.md.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "backend" / "evals" / "evals-embedding-chunking.md"
OUT = HERE / "evals-dashboard.html"


def _cells(line: str) -> list[str]:
    return [c.strip().strip("`") for c in line.strip().strip("|").split("|")]


def parse_tables(md: str) -> dict[str, list[dict[str, str]]]:
    """Tables keyed by 'section-index' (e.g. '1-0', '1-1', '2-0', 'appendix-0')."""
    tables: dict[str, list[dict[str, str]]] = {}
    section, idx, lines = "", 0, md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            m = re.match(r"## (\d+)\.", line)
            section = m.group(1) if m else "appendix" if "Appendix" in line else ""
            idx = 0
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"\|[-| ]+\|$", lines[i + 1]):
            head, rows = _cells(line), []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(dict(zip(head, _cells(lines[i]), strict=False)))
                i += 1
            tables[f"{section}-{idx}"] = rows
            idx += 1
            continue
        i += 1
    return tables


def main() -> None:
    tables = parse_tables(SRC.read_text())
    assert {"1-0", "1-1", "2-0", "3-0", "4-0", "appendix-0"} <= tables.keys(), tables.keys()
    data = {
        "overall": tables["1-0"][0],
        "categories": tables["1-1"],
        "chunking": tables["2-0"],
        "models": tables["3-0"],
        "types": tables["4-0"],
        "questions": tables["appendix-0"],
    }
    html = (HERE / "dashboard_template.html").read_text().replace("/*DATA*/", json.dumps(data))
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
