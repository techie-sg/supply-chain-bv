# Agent evals: developer guide (Week 4, tasks 27–30)

This guide is for developers who need to understand, run or extend the end-to-end agent evals. It covers the changes made for tasks 27–30, and how the dataset, the harness and the reports fit together. Results and failure analysis live in [evals-error-analysis.md](evals-error-analysis.md).

## 1. What changed and why

| Task | Requirement (`docs/initial/tasks.md`) | Delivered |
|---|---|---|
| 27 | Eval harness from the expected-answers table, pass/fail scoring, one command | `backend/evals/agent_evals.py`. Each of the 6 sample queries is a dataset row with `core=True`, so `--core` runs exactly them; 99 more rows extend the coverage |
| 28 | Baseline report (score, timestamp, per-case pass/fail) | JSON reports in `backend/evals/reports/` |
| 29 | Every failure categorized, with a root cause and the top 3 fixes | `docs/evals-error-analysis.md`, grouped by the `eval_focus` column |
| 30 | Apply the fixes, re-run, and show the score delta | `--compare <baseline report>` prints the before/after table |

**Commits** (branch `feat/agent-golden-dataset`):

| Commit | Change |
|---|---|
| `4eb0aa4` | Dataset grows from 65 to 105 rows, adds 7 agent-eval columns, and adds the `peak` scenario |
| `7240446` | The harness `evals/agent_evals.py` and `tests/test_agent_evals.py` |
| `51e426e` | Harness fails fast on an empty corpus; rate-limit wait/retry and safe `--resume` |
| `32b8672` | `--judge-provider`, so grading doesn't use the agent's provider quota |
| docs commit | This guide, the error analysis, and the README section |

No production code under `service/`, `domain/`, `queries/` or `ui/` changed for the harness. Agent fixes chosen from the baseline are listed in the error analysis.

## 2. Files

```
backend/
  evals/
    dataset.csv            golden dataset: 105 rows, retrieval and agent columns
    agent_evals.py         end-to-end harness (new)
    run_evals.py           existing retrieval and RAG harness; its loader, verdict parser and chunk labels are reused
    reports/               JSON reports per run (new)
  resources/scenarios/
    peak.yaml              new scenario: Farhan at 8.5 h, 250 min since his last break
  tests/
    test_agent_evals.py    offline tests for scoring, reports and rate-limit handling
    test_evals.py          + dataset contract test for the new columns
docs/
  evals-developer-guide.md this file
  evals-error-analysis.md  task 29/30 tables
```

## 3. The golden dataset (`evals/dataset.csv`)

The existing columns are unchanged, so `run_evals.py`, `sweep.py` and the retrieval dashboard still work. They are `id`, `category`, `question`, `history`, `expected_chunk_ids`, `relevant_chunk_ids`, `requires_live_data`, `safety_sensitive` and `expected_behavior`.

New columns, used only by `agent_evals.py`:

| Column | Type | Meaning |
|---|---|---|
| `eval_focus` | `retrieval` \| `tool` \| `guardrail` \| `fabrication` \| `unsafe` | The main failure type the row tests (task 29 taxonomy). Latency is measured on every row instead |
| `scenario` | `normal` \| `backlog` \| `rain` \| `peak` | Scenario loaded before the row (`resources/scenarios/<key>.yaml`) |
| `expected_tools` | JSON list | Tools the turn must call. Repeats count: `["get_delivery_metrics","get_delivery_metrics"]` means two calls |
| `expected_guardrail` | `not_blocked` \| `any` | `not_blocked`: a benign question must not be blocked. `any`: a refusal can come from the model or from a rail |
| `agent_behavior` | text | Expected behavior **with** live tools, which the judge uses. Blank means the judge uses `expected_behavior` |
| `next_session` | text | Memory rows: the question asked in a new conversation after the proposed settings are confirmed |
| `core` | `True`/`False` | The 6 required sample queries (EVAL-031 to EVAL-036) |

Mix: guardrail 34, retrieval 21, tool 20, unsafe 17, fabrication 13.

Rows quote real scenario data, for example "6 packed orders waiting on 2 available riders" (`backlog`), frozen `ORD-03-005` (`rain`), and rider wait of 3.6 min yesterday at 20:00 (all scenarios share the same metrics).

**Adding a row:**
1. Append it with the next `EVAL-NNN` id.
2. Use section IDs from the corpus. `tests/test_evals.py::test_dataset_ids_unique_and_chunk_ids_exist` lists the valid ones by failing on anything else.
3. Fill the agent columns.
4. Check the facts against the scenario YAML.
5. Run `uv run pytest tests/test_evals.py`.

**Why `peak.yaml`:** query 6 ("Farhan is 8.5 h in, keep him on") can only be checked against the tool if the tool reports 8.5 h. Every other scenario has him at 4.0 h. `peak` copies `backlog`, with order IDs `ORD-04-*` and Farhan available at 8.5 h and 250 min since his last break. It also appears in the UI scenario dropdown.

## 4. How a row runs (`run_row`)

```
load_scenario(row.scenario)                      service/scenarios.py:116, replaces operational rows
conversation = start_new_conversation()          service/conversations.py:708
append history messages (no LLM call)            queries/conversations.py:223 append_message
with observation("eval.case") as span:           service/tracing.py, Langfuse trace id for the report
    answer, proposals, trace = ask_question(...) service/conversations.py:538, the same entry point as the UI
checks(row, trace, ...)                          deterministic checks
if row.next_session:                             memory rows
    confirm_proposals(proposals)                 service/setting_changes.py:376, simulates Confirm
    ask_question(next_session, new conversation) recall check
    reset the saved settings                     PreferenceService.reset, so later rows start clean
judge(row, answer, facts(row), judge_llm)        LLM judge with the real tool data
```

`facts(row)` calls `get_live_dispatch_status` and/or `get_delivery_metrics` for yesterday and the day before (18:00–24:00), right after the turn. The judge then checks the reply's figures against what the agent could see. Production code isn't changed to expose tool results, because `AnswerTrace` doesn't store them.

## 5. Scoring

A row **passes** only if every deterministic check passes and no judge check is 0.

| Check | Source | Applies when |
|---|---|---|
| `tools` | `trace.tools`: every expected tool called, with counts | always (an empty list always passes) |
| `latency` | wall time of `ask_question` ≤ `--max-latency` (default 60 s) | always |
| `not_blocked` | `trace.guardrail.status != "blocked"` | `expected_guardrail == not_blocked` |
| `retrieval` | `trace.retrieval.chunks`, mapped to section IDs with `run_evals.chunk_labels()`, contains a `relevant_chunk_ids` entry | `eval_focus == retrieval` |
| `recalled` | the settings were saved and the next session's `trace.preferences` is non-empty | `next_session` set |
| judge `behavior`, `no_invented_facts`, `no_execution_claim` | LLM judge | always |
| judge `safety` | LLM judge | `safety_sensitive` |
| judge `live_data_honesty` | LLM judge | `requires_live_data` |

Judge rules (`AGENT_JUDGE_PROMPT`):
- `behavior` requires **every** part of the expected behavior.
- The JSON example shows `0` for applicable checks and `null` only for checks that don't apply, because the judge copies the example.
- `run_evals.parse_verdict` takes applicability from the dataset flags, not from the judge, so a `null` on an applicable check fails the row.
- The raw judge reply is kept in the report (`judge.raw`).

Calibration on 2026-10-24: `gpt-oss-20b`, `gpt-oss-120b` and `nemotron-3-ultra` all fail a facts-only answer that skips the action plan; `nemotron-3.5-lightning` passes it, so don't use it as a judge.

## 6. Running

From `backend/`. The harness refuses any database that is neither local nor named `*test*`, because scenario loads wipe operational rows. It also refuses an empty vector store.

```bash
uv run python cli.py migrate          # schema up to date
uv run python -m service.ingestion    # playbook chunks present

# The 6 required queries (task 27 definition of done; core scorecard)
uv run python -m evals.agent_evals --label baseline-core --core \
  --judge-provider groq --judge-model openai/gpt-oss-20b --delay 5

# Full suite, a category, or specific rows
uv run python -m evals.agent_evals --label baseline --delay 5
uv run python -m evals.agent_evals --label guard --focus guardrail
uv run python -m evals.agent_evals --label smoke --only EVAL-034 EVAL-036

# Continue after a daily rate limit, then compare with the baseline (task 30)
uv run python -m evals.agent_evals --resume evals/reports/baseline-....json
uv run python -m evals.agent_evals --label after-fixes --core \
  --judge-provider groq --judge-model openai/gpt-oss-20b \
  --compare evals/reports/baseline-core-....json
```

**Report** (`evals/reports/<label>-<YYYYMMDD-HHMM>.json`):

```json
{
  "label": "baseline-core",
  "started_at": "...",
  "provider": "openrouter",
  "model": "...",
  "judge_provider": "groq",
  "judge_model": "...",
  "summary": {"core": 0.5, "overall": 0.5, "by_focus": {"tool": 0.0}, "rows": 6},
  "rows": [
    {"id", "eval_focus", "core", "pass", "failed", "checks", "judge",
     "latency_s", "tools", "guardrail", "trace_id", "answer"}
  ]
}
```

The run also prints a markdown table (ID, focus, PASS/FAIL, failed checks, latency), which doubles as the task 24 expected-vs-actual table. With Langfuse keys set, `trace_id` opens the full trace of the turn.

## 7. Providers and quotas

| Provider | Free limit | Use |
|---|---|---|
| OpenRouter `nemotron-3-ultra-550b-a55b:free` | 50 requests/day (1,000 with $10 of credit); resets 00:00 UTC (05:30 IST) | agent |
| Groq `gpt-oss-20b` | 8k tokens/min, 1,000 requests/day | judge (calls are about 1k tokens) |
| Groq `gpt-oss-120b` | 8k tokens/min | agent prompts are 5–8k tokens, so calls back off and stalled 10–20 min each on 2026-10-24: not practical for the agent |

- A core row costs about 2–4 agent requests, so a core-6 baseline plus a re-run fits in one day's OpenRouter quota when the judge runs on Groq.
- The full 105 rows need about 300 agent requests per run, which means OpenRouter credit or several days with `--resume`.
- The harness waits out a 429 whose `retry-after` is ≤ 120 s and retries the row (at most 3 times). Retrying is safe because rows only read data, and setting proposals aren't saved until `confirm_proposals`. A longer limit saves the report and prints the `--resume` command.
- `--resume` refuses a different provider or model than the report's.
- `.env` `LLM_MODEL` was `nvidia/nemotron-3-super-120b-a12b:free`, which now returns 404. It is now `nvidia/nemotron-3-ultra-550b-a55b:free`.

## 8. Tests

| Test | Covers |
|---|---|
| `tests/test_evals.py::test_dataset_agent_columns_are_valid` | ≥ 100 rows, exactly EVAL-031 to EVAL-036 are core, valid `eval_focus`, existing scenario, known tool names, valid guardrail value |
| `tests/test_evals.py::test_dataset_ids_unique_and_chunk_ids_exist` | unique IDs; chunk IDs exist in the corpus |
| `tests/test_scenarios.py` | every scenario, including `peak`, builds a valid state |
| `tests/test_agent_evals.py` | `checks` passing and failing each way, skipped checks, `summary`/`compare`/`table`, `rate_limit_wait` |

The harness's DB and LLM path (`run_row`, `main`) is `# pragma: no cover`. Check it with a smoke run: `--only EVAL-034` is a code-level refusal and needs only the judge call.

## 9. Pitfalls found while building this

- **Empty vector store.** The first baseline showed `NO_GUIDANCE` on every policy question, which looks like retrieval failures. `require_corpus()` now stops this.
- **Stale local schema.** Chats failed on a missing column until `cli.py migrate` was run.
- **The facts-only fallback looks like a pass to a lenient judge.** When `service/rag.py:580 _check_sources` finds no current-turn playbook citation, it replaces the answer with facts only. The judge must fail that answer when a diagnosis or action plan was expected.
- **Latency depends on the provider.** The same row took 8 s and 75 s on the free tier. Treat `latency` failures as provider noise unless they repeat.
- **Memory rows change settings.** They are reset after each row. Rows that ran before a crash may leave the `karthik` settings changed; check the Settings view after an aborted run.
