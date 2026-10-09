"""Build tool-calling-eval.html from evals/tool_calling/results_*.json.

Reads all result files and renders the latest run with:
  - progress bar when a run is in progress (n_cases < total_cases)
  - auto-refresh every 30s while in progress
  - per-case table with expandable Q&A, category breakdown, run history

Usage (from project root):
    python3 evals-dashboard/build_tool_calling_dashboard.py
"""

import json
import sys
from pathlib import Path

HERE        = Path(__file__).parent
RESULTS_DIR = HERE.parent / "evals" / "tool_calling"
CASES_FILE  = RESULTS_DIR / "cases.json"
OUT         = HERE / "tool-calling-eval.html"

PASS_CLR = "#1b7f4b"
FAIL_CLR = "#c0392b"
WARN_CLR = "#c98500"
NA_CLR   = "#8a8983"


def load_runs() -> list[dict]:
    files = sorted(RESULTS_DIR.glob("results_????????_??????.json"))
    if not files:
        print(f"No result files found in {RESULTS_DIR}", file=sys.stderr)
        sys.exit(1)
    runs = []
    for f in files:
        try:
            runs.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"Skip {f.name}: {e}", file=sys.stderr)
    return runs


def load_questions() -> dict[str, str]:
    if not CASES_FILE.exists():
        return {}
    try:
        return {c["id"]: c.get("question", "") for c in json.loads(CASES_FILE.read_text())}
    except Exception:
        return {}


def pct(v) -> str:
    return "N/A" if v is None else f"{v:.0%}"


def cell(v) -> str:
    if v is None:   return f'<td style="color:{NA_CLR}">N/A</td>'
    if v is True:   return f'<td style="color:{PASS_CLR}">PASS</td>'
    if v is False:  return f'<td style="color:{FAIL_CLR}">FAIL</td>'
    if isinstance(v, float): return f'<td class="n">{v:.0%}</td>'
    return f"<td>{v}</td>"


def build_html(runs: list[dict]) -> str:
    latest     = runs[-1]
    agg        = latest["aggregate"]
    cases      = latest["cases"]
    run_ts     = latest["run_ts"]
    questions  = load_questions()
    n_done     = agg["n_cases"]
    n_total    = agg.get("total_cases", n_done)
    in_progress = n_done < n_total

    # ── progress banner ───────────────────────────────────────────────────────
    progress_html = ""
    if in_progress:
        pct_done = int(n_done / n_total * 100)
        progress_html = f"""
  <div class="progress-card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-weight:600">Running… {n_done} / {n_total} cases complete</span>
      <span style="font-size:12px;color:var(--ink2)">Auto-refreshing every 30s</span>
    </div>
    <div class="progress-track">
      <div class="progress-bar" style="width:{pct_done}%"></div>
    </div>
    <p style="font-size:12px;color:var(--ink2);margin:6px 0 0">
      Metrics below are partial — based on {n_done} of {n_total} cases scored so far.
    </p>
  </div>"""

    auto_refresh = '<meta http-equiv="refresh" content="30">' if in_progress else ""

    # ── aggregate tiles ───────────────────────────────────────────────────────
    tiles_html = ""
    metrics = [
        ("Tool selection",    agg["tool_selection_accuracy"],    f"{int(agg['tool_selection_accuracy']*n_done)}/{n_done} cases"),
        ("No spurious calls", agg["no_spurious_calls_rate"],     f"{int(agg['no_spurious_calls_rate']*n_done)}/{n_done} cases"),
        ("No missing calls",  agg["no_missing_calls_rate"],      f"{int(agg['no_missing_calls_rate']*n_done)}/{n_done} cases"),
        ("Arg accuracy",      agg["argument_accuracy_mean"],     f"n={agg['argument_accuracy_n']} tool cases"),
        ("Answer grounded",   agg["answer_groundedness_rate"],   f"n={agg['answer_groundedness_n']} keyword cases"),
        ("Avg LLM calls",     None,                              f"{agg['avg_llm_calls']:.1f} per query"),
    ]
    for label, val, detail in metrics:
        if label == "Avg LLM calls":
            v_str = f"{agg['avg_llm_calls']:.1f}"
            color = ""
        else:
            v_str = pct(val)
            v     = val or 0
            color = f"color:{PASS_CLR if v >= 0.9 else (FAIL_CLR if v < 0.7 else WARN_CLR)}"
        tiles_html += f"""
    <div class="tile">
      <div class="k">{label}</div>
      <div class="v" style="{color}">{v_str}</div>
      <div class="d">{detail}</div>
    </div>"""

    # ── per-case rows ─────────────────────────────────────────────────────────
    rows_html = ""
    for c in cases:
        err_badge = ""
        if c.get("run_error"):
            err_badge = f' <span style="color:{FAIL_CLR};font-size:10px">[rate-limited]</span>'
        called   = "|".join(c["tools_called"])  or "(none)"
        expected = "|".join(c["expected_tools"]) or "(none)"
        question = questions.get(c["id"], "")
        answer   = (c.get("answer") or "")[:400]
        q_part   = f'<div><b style="color:var(--ink2)">Q:</b> {question}</div>' if question else ""
        a_part   = f'<div style="margin-top:4px"><b style="color:var(--ink2)">A:</b> {answer}</div>' if answer else ""
        qa_html  = (f'<tr class="qa-row"><td colspan="10"><div class="qa-body">{q_part}{a_part}'
                    f'</div></td></tr>') if (q_part or a_part) else ""
        rows_html += f"""
    <tr class="case-row" onclick="this.nextElementSibling.classList.toggle('open')">
      <td><code>{c['id']}</code></td>
      <td>{c['label']}{err_badge}</td>
      {cell(c['tool_selection_correct'])}
      {cell(c['no_spurious_calls'])}
      {cell(c['no_missing_calls'])}
      {cell(c['argument_accuracy'])}
      {cell(c['answer_grounded'])}
      <td><code style="font-size:10px">{called}</code></td>
      <td><code style="font-size:10px">{expected}</code></td>
      <td class="n">{c['llm_calls']}</td>
    </tr>{qa_html}"""

    # ── category breakdown ────────────────────────────────────────────────────
    cats = {}
    for c in cases:
        cat = c["id"].split("-")[0]
        cats.setdefault(cat, {"total": 0, "pass": 0, "errored": 0})
        cats[cat]["total"] += 1
        if c.get("run_error"):
            cats[cat]["errored"] += 1
        elif c["tool_selection_correct"] and c["no_spurious_calls"] and c["no_missing_calls"]:
            cats[cat]["pass"] += 1

    cat_labels = {"live": "Live status", "metrics": "Historical metrics",
                  "no": "No-tool (policy)", "multi": "Multi-tool", "tricky": "Edge / adversarial"}
    cat_html = ""
    for k, v in cats.items():
        label = cat_labels.get(k, k)
        rate  = v["pass"] / v["total"] if v["total"] else 0
        bar_w = int(rate * 160)
        clr   = PASS_CLR if rate == 1 else (FAIL_CLR if rate < 0.6 else WARN_CLR)
        note  = f'<td style="color:{FAIL_CLR}">{v["errored"]} rate-limited</td>' if v["errored"] else "<td></td>"
        cat_html += f"""
    <tr>
      <td>{label}</td>
      <td class="n">{v['total']}</td>
      <td class="n">{v['pass']}/{v['total']}</td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="width:{bar_w}px;height:8px;background:{clr};border-radius:4px"></div>
          <span style="color:{clr}">{rate:.0%}</span>
        </div>
      </td>
      {note}
    </tr>"""

    # ── run history ───────────────────────────────────────────────────────────
    history_html = ""
    for r in reversed(runs):
        a  = r["aggregate"]
        nd = a["n_cases"]
        nt = a.get("total_cases", nd)
        is_latest = r["run_ts"] == run_ts
        bold = "font-weight:600" if is_latest else ""
        progress_tag = f" ({nd}/{nt})" if nd < nt else ""
        history_html += f"""
    <tr style="{bold}">
      <td>{r['run_ts'].replace('_',' ')}{' ★' if is_latest else ''}{progress_tag}</td>
      <td class="n">{nd}</td>
      <td class="n" style="color:{PASS_CLR if a['tool_selection_accuracy']==1 else FAIL_CLR}">{pct(a['tool_selection_accuracy'])}</td>
      <td class="n" style="color:{PASS_CLR if a['no_spurious_calls_rate']==1 else FAIL_CLR}">{pct(a['no_spurious_calls_rate'])}</td>
      <td class="n" style="color:{PASS_CLR if a['no_missing_calls_rate']==1 else FAIL_CLR}">{pct(a['no_missing_calls_rate'])}</td>
      <td class="n">{pct(a['argument_accuracy_mean'])}</td>
      <td class="n">{pct(a['answer_groundedness_rate'])}</td>
      <td class="n">{a['avg_llm_calls']:.1f}</td>
      <td class="n">{a['total_elapsed_sec']:.0f}s</td>
    </tr>"""

    subtitle = (f"{n_done}/{n_total} cases so far &mdash; in progress"
                if in_progress
                else f"{n_done} cases, {agg['total_elapsed_sec']:.0f}s wall time")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{auto_refresh}
<title>DispatchDesk tool-calling evals</title>
<style>
:root {{
  color-scheme: light;
  --page: #f6f5f2; --surface: #fcfcfb; --line: #e4e3de; --grid: #ecebe7;
  --ink: #0b0b0b; --ink2: #52514e; --ink3: #8a8983;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --good: #1b7f4b; --bad: #c0392b;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    color-scheme: dark;
    --page: #121211; --surface: #1a1a19; --line: #2e2e2b; --grid: #262624;
    --ink: #fff; --ink2: #c3c2b7; --ink3: #8a8983;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --good: #4cc38a; --bad: #e66767;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, sans-serif; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px 80px; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
h2 {{ font-size: 16px; margin: 0; }}
.sub {{ color: var(--ink2); margin: 2px 0 0; font-size: 13px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px; margin-top: 20px; }}
.progress-card {{ background: #fff8e1; border: 1px solid #ffe082; border-radius: 12px; padding: 14px 18px; margin-top: 20px; }}
@media (prefers-color-scheme: dark) {{
  .progress-card {{ background: #2a2500; border-color: #5a4500; }}
}}
.progress-track {{ height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
.progress-bar  {{ height: 8px; background: var(--s1); border-radius: 4px; transition: width 0.3s; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 16px; }}
.tile {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }}
.tile .k {{ font-size: 12px; color: var(--ink2); }}
.tile .v {{ font-size: 26px; font-weight: 600; margin-top: 2px; }}
.tile .d {{ font-size: 12px; color: var(--ink2); }}
.scroll {{ overflow-x: auto; margin-top: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
th {{ color: var(--ink2); font-weight: 600; position: sticky; top: 0; background: var(--surface); z-index: 1; }}
td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
code {{ font-size: 11px; background: var(--page); padding: 1px 4px; border-radius: 4px; }}
details summary {{ cursor: pointer; font-weight: 600; font-size: 14px; color: var(--ink2); }}
.case-row {{ cursor: pointer; }}
.case-row:hover {{ background: var(--grid); }}
.qa-row {{ display: none; }}
.qa-row.open {{ display: table-row; }}
.qa-body {{ padding: 8px 10px; font-size: 12px; line-height: 1.6; background: var(--grid); border-radius: 6px; white-space: pre-wrap; word-break: break-word; }}
</style>
</head>
<body>
<main>
  <h1>DispatchDesk tool-calling evals</h1>
  <p class="sub">25 cases &mdash; live status, historical metrics, no-tool (policy), multi-tool, and adversarial edge cases.
  LLM calls are real (Groq); tool execution is mocked. Latest run: {run_ts.replace('_', ' ')} UTC &mdash; {subtitle}.</p>
  {progress_html}
  <div class="tiles">{tiles_html}
  </div>

  <div class="card">
    <h2>Category breakdown &mdash; latest run</h2>
    <p class="sub">Tool selection + no spurious + no missing all PASS = category pass.</p>
    <div class="scroll">
      <table>
        <thead><tr><th>Category</th><th class="n">Cases</th><th class="n">Passed</th><th>Rate</th><th>Notes</th></tr></thead>
        <tbody>{cat_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Per-case results &mdash; latest run</h2>
    <p class="sub">Click any row to expand the question and model answer.</p>
    <div class="scroll" style="max-height:540px">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Label</th>
            <th>Tool sel.</th><th>No spur.</th><th>No miss.</th>
            <th class="n">Arg acc.</th><th>Grounded</th>
            <th>Called</th><th>Expected</th><th class="n">LLM calls</th>
          </tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <details open>
      <summary>Run history &mdash; {len(runs)} run{'s' if len(runs) != 1 else ''}</summary>
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>Run (UTC)</th><th class="n">Cases</th>
              <th class="n">Tool sel.</th><th class="n">No spur.</th><th class="n">No miss.</th>
              <th class="n">Arg acc.</th><th class="n">Grounded</th>
              <th class="n">Avg LLM</th><th class="n">Wall time</th>
            </tr>
          </thead>
          <tbody>{history_html}
          </tbody>
        </table>
      </div>
    </details>
  </div>
</main>
</body>
</html>"""


def main() -> None:
    runs = load_runs()
    html = build_html(runs)
    OUT.write_text(html)
    latest = runs[-1]
    n_done  = latest["aggregate"]["n_cases"]
    n_total = latest["aggregate"].get("total_cases", n_done)
    status  = f"{n_done}/{n_total} in progress" if n_done < n_total else f"{n_done} complete"
    print(f"Written → {OUT}  ({len(runs)} run(s), latest: {status})")


if __name__ == "__main__":
    main()
