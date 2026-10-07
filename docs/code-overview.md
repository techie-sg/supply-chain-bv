# DispatchDesk: Code Overview, Interactions, and Next Steps

Snapshot of `agent_tools` (2026-10-07), on top of `main` at `5cc2baf`. Since the previous snapshot (`evals/expand-dataset-and-metrics` at `7ea9ed2`, 2026-10-06), `main` gained stored conversations, per-manager preferences with a Settings tab, titles, timestamps and rolling summaries (PRs #21 to #25; design in [memory.md](memory.md)). This branch adds the live-data tools, an MCP server, a tool-calling agent in the chat, and an agent-trace panel. Those changes are not committed yet. The memory design is in [memory.md](memory.md) and the tool contract is in [tools.md](tools.md).

## 1. What it is

A dispatch copilot for dark-store managers (persona: Karthik, Koramangala). Today it is **RAG chat over a PDF policy playbook, with live-data tools when a scenario is loaded; stored conversations and per-manager settings; a demo workspace for synthetic operational scenarios; and an offline eval harness**. A live demo runs on Railway (linked from the README). Per [requirements.md](initial/requirements.md), the target also includes guardrails, caching and observability. See section 8 for the gap.

## 2. Tech stack

| Area | Choice |
| --- | --- |
| Language / packaging | Python 3.13, `uv` (`package = false`, locked sync) |
| UI | Gradio 6.29 (`gr.Blocks`, custom dark theme and CSS), bound to `0.0.0.0:$PORT` (default 7860) |
| DB | PostgreSQL + `pgvector`, SQLAlchemy 2.1 ORM, `psycopg` 3, Alembic migrations |
| Document parsing | **New:** Docling 2.133+ (layout model, OCR off) converts corpus PDFs to Markdown. Needs `torch`/`torchvision`; on Linux, `uv` pulls CPU-only wheels from the PyTorch CPU index |
| Embeddings | Jina `jina-embeddings-v5-text-nano` (768-D) via plain `requests` POST. **New:** optional task adapters (`EMBEDDING_TASK_ADAPTERS`, default `false`) |
| LLM | Groq `openai/gpt-oss-20b` via `langchain-groq` (`temperature=0`) for plain answers, titles and summaries. The tool-calling agent posts to Groq's chat-completions API directly with `requests` (`reasoning_effort` low) |
| Tools / MCP | `mcp` 2.3 SDK (`MCPServer`). `mcp_server/server.py` is a local stdio server named `dispatchdesk-ops`; the agent starts it as a subprocess and talks to it with `mcp.client.Client` |
| LangChain use | `langchain-core` only for `Document` and message types, plus `ChatGroq` |
| Config / validation | `pydantic` v2, `pydantic-settings` (env, then `backend/.env`; secrets are `SecretStr`) |
| Scenario data | YAML (`normal`, `backlog`, `rain`) validated by Pydantic |
| Logging | `structlog` JSON to stdout (the MCP server logs to stderr, because stdout carries its protocol), stdlib logs routed through the same formatter |
| Quality | `ruff` (with COM812), `mypy`, `pytest` + `pytest-cov`, 90% coverage gate (212 passed, 27 skipped locally) |
| Evals | `numpy` (already installed) for the in-memory retrieval sweep |
| CI | GitHub Actions: lint, test (pgvector 17 service container, PR coverage comment), PR-description check |
| Deploy | Railway (Railpack, root `/backend`, start `python -m ui.gradio_app`, healthcheck `/`) |

## 3. Layout and layering

```
backend/
  config.py, constants.py, logging_config.py
  ui/            gradio_app.py (+ .css), settings.py     presentation, callbacks, Settings tab
  mcp_server/    server.py                               MCP stdio server for the two tools
  service/       rag, ingestion, corpus, chunking, document_parser,
                 factory, groq/jina/llm/embedding services, scenarios
                 conversations (chat history, titles), preferences,
                 summaries + scheduler (rolling summary, idle job),
                 tools (live status, metrics), agent (tool-calling loop, MCP client)
                 rag_data/corpus/*.pdf, rag_data/prompts (system, title, summary)
                 scenario_data/*.yaml
  queries/       vector_store, scenarios, conversations, preferences   all SQL lives here
  domain/        chat, scenario, memory, tools          data contracts (tools: inputs and outputs)
  database/      models.py, session.py                  ORM + transactional session
  alembic/       0001 six tables ... 0004 drops match function,
                 0005 conversations, 0006 preferences, 0007 titles,
                 0008 summary timestamp
  evals/         dataset.csv (65 rows), run_evals.py, sweep.py,
                 sweep-results.csv, evals-embedding-chunking.md,
                 .embed_cache.json (gitignored)
  tests/         one test module per service, plus test_evals.py,
                 test_agent.py, test_tools.py, test_mcp_server.py
```

Dependency direction is `ui -> service -> queries -> database`, with `domain` and `config` shared. `mcp_server` sits beside `service` and calls `service.tools`; the agent reaches it only over stdio. Providers sit behind small ABCs (`EmbeddingService`, `LLMService`, `ChunkingStrategy`, and now `DocumentParser`). `service/factory.py` picks embedding (model and, **new**, task adapters), LLM, and chunking from settings; the parser is passed in directly (`DoclingPdfParser()` in ingestion, the notebook, and evals).

## 4. Data model (schema `app`)

- **Memory (3 tables, migrations 0005 to 0008):** `conversations` (messages as a JSON list, title, rolling summary) plus `preference_definitions` (the 9-item catalogue) and `store_preferences`. Full columns are in [memory.md](memory.md).
- **Operational (4 tables, from the original workbook):** `zones`, `riders`, `orders`, `hourly_metrics`. Composite PKs (`scenario_key` + id), FKs with `RESTRICT`, `CHECK` constraints, and indexes on scenario/store/status.
- **RAG (2 tables):** `documents` (uuid, `file_hash` bytea(32), `version`, JSONB `metadata`; unique on `(file_hash, version)`) and `document_chunks` (`(document_id, chunk_id)` PK, `content`, unconstrained `Vector()` embedding, cascade delete).
- Migration 0003 creates `app.match_document_chunks(vector(768), int)`; 0004 drops it. Retrieval goes through the ORM only.

## 5. Interactions (end to end)

### 5.1 Ingestion (`uv run python -m service.ingestion`)
1. `configure_logging()`, then `get_settings()`.
2. `CorpusService.load()` globs `corpus/*{parser.suffix}` (`*.pdf`), so the corpus README is excluded by suffix rather than by name.
3. **New:** `DoclingPdfParser.to_markdown()` builds the Docling converter lazily on first use (model download on first run, OCR off), exports Markdown without HTML/underscore escaping, and promotes the first `##` to `#` when no `#` exists, because Docling exports the title as level two.
4. Newlines are normalized. `title` comes from the first `#` heading. `Document ID` and `Version` are now matched **by value format** (`DD-XXX-001`, `1.1 (2026-09-29)`), not up to end of line, because PDF extraction can join header fields onto one line. The SHA-256 is of the **PDF bytes**.
5. The chunking strategy (`markdown_sections` splits on `##`, or `fixed_size` with overlap) yields `(label, body)`; each chunk becomes `"<title> > <section>\n\n<body>"`. Duplicate chunk labels raise. The PDF corpus still produces **37 chunks** with the same section names as the old Markdown.
6. `JinaEmbeddingService.embed_documents()` makes one batched POST (60 s timeout) and validates count and index order. **New:** with `EMBEDDING_TASK_ADAPTERS=true` it sends `task=retrieval.passage` (queries send `retrieval.query`). Without it, no `task` is sent, so queries and passages are embedded the same way. Toggling the setting changes the vector space, so it needs a re-ingest.
7. `queries.vector_store.insert_chunks()` runs one transaction per file: upsert `documents` on `(file_hash, version)`, then **delete other `documents` rows with the same `file_name` or the same `metadata.doc_id`** (chunks cascade). Matching on `doc_id` is what cleans up the old `.md` rows after the switch to `.pdf`. Then delete trailing chunks and upsert chunks on the PK.

### 5.2 Chat (question to answer)
1. Gradio `submit`/Enter stores the message in a hidden textbox and shows a busy state, then `respond_to_pending` calls `chat()` (which builds a `request_id`, converts Gradio history to `ChatMessage`s).
2. `chat()` calls `ask_question_traced()`, which stores the manager's message, then `ConversationService` calls `_answer()` with the stored history and, if present, the conversation summary. History is always stored first, so a failed answer never loses the question.
3. `_answer()` checks `current_scenario()`:
   - **No scenario loaded:** `service.rag.answer_question()` builds a `RAGService` from the factory and answers from the playbook alone.
   - **Scenario loaded:** `prepare_message()` does the retrieval and builds the user message (settings block, summary, retrieved context, question), then `run_agent()` handles the tools (section 5.6).
4. The text to embed comes from `retrieval_query(question, history)`, now a module-level function shared with the evals. With history it is the last two messages plus "Follow-up question: ..."; behavior is unchanged.
5. `retrieve()` runs pgvector cosine distance, `top_k=3`, returning `doc_id#chunk` ids, content, and similarity.
6. Empty result returns a fixed "could not find guidance" message. Otherwise the context is wrapped (`<context>` when there is history) and sent with the system prompt (`dispatch_manager_system.md`, re-read each call) and history to `GroqService.generate()`.
7. **Prompt section "Scope":** the model only handles dispatch and store operations. For unrelated questions (trivia, coding, personal) it replies with a fixed redirect line. Greetings, thanks, and dispatch follow-ups stay in scope. This is enforced only by the prompt; retrieval still runs first and the top 3 chunks are still sent.
8. On provider/DB errors the UI keeps the draft and history, logs with the request id, and shows a `gr.Error`.
9. After the answer, background calls write the conversation title (once, after the first answer) and fold older messages into the summary when limits are exceeded. An idle-chat job started with the app summarizes chats left for 30 minutes.

### 5.3 Demo tools (scenarios)
- `scenario_names()` lists YAML files. **Preview** calls `scenario_details()` which builds rows in memory only. **Load** calls `load_scenario()`: validate whole YAML (`ScenarioData`, cross-reference checks), build ORM rows with `as_of = now` (Asia/Kolkata), `TRUNCATE` the four tables, insert in FK order, all in one transaction, then read back via `current_scenario()`.
- `app.load` and **Refresh** call `current_scenario()` to restore the saved snapshot into four Dataframes. Loading a scenario also clears chat.
- With a scenario loaded, chat reads this data through the tools (section 5.6). Without one it does not.

### 5.4 Evals (`uv run python -m evals.run_evals [--judge]`, this branch)
1. `load_dataset()` reads `evals/dataset.csv`: 65 questions across 16 categories (batching, rider safety, diagnosis, staffing, live data, adversarial, authorization, out-of-domain, multi-turn, ...). Each row has `relevant_chunk_ids` (must retrieve), `expected_chunk_ids` (all useful), optional JSON `history`, and `requires_live_data` / `safety_sensitive` flags.
2. `chunk_labels()` reloads the corpus (through `load_chunks()` and a per-run `CachedParser`, so Docling parses each PDF once) and maps stored `DOC#index` ids to the **set** of `DOC#section-slug` ids each chunk covers. **New:** section chunks map to their own section. Fixed-size chunks map to every section they overlap by at least 100 characters (`chunk_sections()`, `MIN_OVERLAP`), or the whole section/chunk if it's shorter. Before this, fixed-size chunks were labelled `DOC#chunk-n` and always scored 0.
3. For each row, `retrieval_query()` + `retrieve()` give the top 5; `retrieval_scores()` computes `hit@1/3/5`, `recall@1/3/5`, and `mrr`. It now accepts a ranked list of section-id sets, so one chunk can match several sections.
4. With `--judge`, `RAGService` answers and a Groq judge (`--judge-model` can differ from the answer model) grades `behavior`, `no_invented_facts`, `no_execution_claim`, plus `safety` and `live_data_honesty` where applicable. Empty answers fail without a judge call. `pass` needs every applicable check.
5. `summarize()` prints overall and per-category means (skipping missing values); per-row results go to `evals/results.csv`. `--category` and `--delay` filter and throttle (Groq rate limits).
6. Baselines on the local pgvector DB (sections, v5-nano, no task):
   - Markdown corpus, 2026-10-05: hit@1 0.46, hit@3 0.68, hit@5 0.79, recall@5 0.69, MRR 0.59.
   - **PDF corpus, 2026-10-06:** hit@1 0.397, hit@3 0.683, hit@5 0.762, recall@1 0.317, recall@3 0.574, recall@5 0.647, MRR 0.542.
   - Judged (answers `gpt-oss-20b`, judge `gpt-oss-120b`), PDF corpus: pass 0.677 (safety 0.421), down from 0.80 on the Markdown corpus.

### 5.5 Embedding sweep (`uv run python -m evals.sweep`, new)
1. Retrieval only. It never calls Groq, and it searches **in memory** (numpy cosine), so the database is untouched. It matches pgvector within one question (hit@1 0.413 vs 0.397, MRR 0.547 vs 0.542).
2. Grid (192 rows): chunking (`sections`, `fixed-{400,800,1600,3200}` characters with 0 or 1/8 overlap) × v5-nano with and without task adapters, plus models v5-text-small, v4 and v3 on sections, with and without adapters. Each one is also scored at Matryoshka dims 512/256/128 and as sign-only binary, computed locally from the float vectors. Every row reports per-k metrics plus **equal-context** metrics (`hit@budget`, `recall@budget`): the top-ranked chunks that fit in 4,000 characters (`BUDGET`, `budget_scores()`), so chunk sizes are compared on what actually reaches the LLM.
3. Jina embeddings are cached in `evals/.embed_cache.json`, keyed by model, task and text, so re-runs and crashes don't re-bill.
4. It writes `evals/sweep-results.csv`. Results, commands and all eval queries are in [evals-embedding-chunking.md](../backend/evals/evals-embedding-chunking.md).
5. Key results: task adapters lift every setup (v5-nano + sections: hit@3 0.683 → 0.825, MRR 0.547 → 0.664). Bigger models are within noise of v5-nano once adapters are on. Truncating or binarizing vectors loses quality. fixed-1600-0 + adapters leads per 3 chunks (MRR 0.734), but only because each chunk covers about 2.3 sections and twice the text. At equal context, sections + adapters wins (recall@budget 0.771 vs 0.651), so section chunking stays.

### 5.6 Tools, MCP and the agent (new)
1. **Tools** (`service/tools.py`, contract in [tools.md](tools.md)) are read-only functions over the scenario tables. `get_live_dispatch_status(store_id)` returns the queue (counts by status, oldest packed and oldest frozen-packed age, up to 50 orders), riders, zones, a summary, `as_of`, `data_age_sec`, `stale` (older than 300 s) and `conditions.is_raining` read from the scenario definition. `get_delivery_metrics(store_id, date, start_hour, end_hour)` returns hourly rows and an order-weighted period summary. Errors are `{error: {code, message, details}}` (`UNKNOWN_STORE`, `NO_SNAPSHOT`, `INVALID_PERIOD`, `NO_METRICS_FOR_PERIOD`). A stale snapshot logs a `Stale live data` warning.
2. **MCP server** (`mcp_server/server.py`, run with `python -m mcp_server.server`) registers both tools with the spec's descriptions and read-only annotations, takes input schemas from `domain/tools.py`, and builds `outputSchema` from the output models there. Success returns `structuredContent`; tool errors are results with `isError` set and the error JSON as text; a crash becomes `DATA_UNAVAILABLE` with no internals. Each call is logged (tool, arguments, duration, error) to stderr.
3. **Agent** (`service/agent.py`): `run_agent()` starts the server through `ToolClient` (one subprocess per question, shared by every call in the loop, on its own event loop in a thread), fetches the tool list from it, and loops up to 5 times: post to Groq with the tool definitions, run any tool calls through MCP (5 s timeout each), feed results back. A tokens-per-minute 429 is retried after the wait Groq asks for (up to 4 attempts). An empty final answer raises an error instead of being stored.
4. **Trace:** each answer carries a trace of tool calls (name, arguments, `as_of`, stale flag, error) and the manager's customized settings. It is stored on the assistant message (`trace`, display only, never sent to the model) and shown as a collapsed "Agent trace" panel under the bubble, also after a refresh.

## 6. What is good

- Clean layering, constructor-injected services, and ABC seams that make providers swappable and tests easy. The new `DocumentParser` seam lets tests use a `TextParser` on `.txt` files without Docling.
- The PDF switch kept chunk count, section names, doc ids, and versions identical, and `test_corpus.py` pins them, so retrieval and citations are unchanged.
- The `doc_id` cleanup in `insert_chunks` handles the `.md` to `.pdf` rename with an integration test (`test_renamed_source_replaces_rows_with_the_same_document_id`).
- Metadata regexes are tolerant of PDF line joins and covered by a dedicated test.
- CPU-only torch on Linux avoids pulling CUDA/NVIDIA wheels into CI and Railway.
- Secrets handled with `SecretStr`, `require()` never echoes values, `hide_parameters=True` on the engine, raw SQL parameters not logged.
- Whole-scenario validation before any write; atomic replace; DB `CHECK`/FK constraints mirror the Pydantic rules.
- The eval harness shares `retrieval_query()` with production, so it measures the real retrieval path, and the dataset is checked against the corpus in `test_evals.py`.
- The tools return computed figures and an `as_of` time, and the error messages tell the model what to do next. The MCP server is read-only, so the agent can only propose actions.
- The sweep reuses the harness's dataset, scoring and corpus code, so its numbers are comparable with `run_evals`. Task adapters were added behind a default-off setting, so production behavior is unchanged until it's switched on.

## 7. Review findings, ordered by importance

### 7.1 Status of earlier findings

| # | Finding | Status |
| --- | --- | --- |
| F1 | Stale chunks survive corpus edits | **Fixed.** Old rows are deleted by `file_name`, and now also by `doc_id` |
| F2 | Unauthenticated public app can wipe shared scenario data | Open. More pressing now that the README links the live demo |
| F3 | Migration 0003 unused and not portable | **Mostly fixed.** 0004 drops the function, and the Supabase grants/`NOTIFY` are gone. 0003 still creates it on a fresh upgrade (harmless) |
| F4 | No relevance threshold | Open. The new Scope prompt covers off-topic questions at the model level only |
| F5 | `requirements.txt` duplicates `pyproject.toml`, unpinned | Open, and the gap has grown: `requirements.txt` has no `docling`/`torch` |
| F6 | Per-request service wiring, prompt re-read, `NullPool` | Open |
| F7 | Provider/strategy settings are free `str` | Open |
| F8 | Follow-up retrieval concatenates raw messages | Open. Logic moved into `retrieval_query()`, behavior unchanged |
| F9 | Hardcoded timezone/store, shallow healthcheck, no contextvars | Open |

### 7.2 New findings from the merge and recent changes

| # | Finding | Why it matters | Suggested fix |
| --- | --- | --- | --- |
| N1 | **Fixed (`b93d9e9`).** After the merge, `evals/run_evals.py` called `CorpusService` without the new required `parser` argument, so `chunk_labels()` raised `TypeError` and two `test_evals.py` tests failed. Git showed no textual conflict. It now passes `DoclingPdfParser()` and uses `BACKEND_DIR` as `repo_root`, matching ingestion. | Eval harness and CI were red. | Done. |
| N2 | **Docling and torch ship in the app image.** They are main dependencies, but only ingestion, the notebook, and evals import them; the Gradio app never parses PDFs. | Much larger Railway image and slower builds for no runtime benefit. | Move `docling`, `torch`, `torchvision` to an `ingest` dependency group and install it only where ingestion runs. |
| N3 | **Unit tests run the real Docling pipeline.** `test_current_pdf_corpus_retains_existing_chunks_and_metadata` and both `chunk_labels()` tests in `test_evals.py` parse all seven PDFs, and the layout model downloads on first use. `test_evals.py` alone now takes about 120 s locally. | Slow tests and a network dependency in CI; a model-host outage fails the build. | Parse once per session (fixture or cached `chunk_labels`), and cache the Docling/Hugging Face model directory in CI. |
| N4 | **Out-of-domain handling is prompt-only.** Retrieval still runs and three unrelated chunks are sent; the refusal depends on the model following the Scope section. | The two `out_of_domain` eval rows are the only check; no unit test covers it. | Combine with F4: a similarity floor returns the fixed reply before calling the LLM. Track the OOD rows' pass rate in evals. |
| N5 | **Cleanup by `doc_id` assumes ids are unique per file.** Two PDFs sharing a `Document ID` (copy-paste error) would delete each other on alternate ingests, and the last one wins silently. | Silent loss of a policy document from retrieval. | Fail `CorpusService.load()` when two sources share a `doc_id`. |
| N6 | **Done. The eval baseline was re-run on the PDF corpus.** Retrieval fell about 5 points (hit@1 0.46 → 0.40, MRR 0.59 → 0.54); see 5.4. | The old baseline overstated quality. | Compare against the PDF baseline from now on. The cause (Docling extraction vs chunk text) is not yet isolated. |

### 7.3 New findings from the embedding evals

| # | Finding | Why it matters | Suggested fix |
| --- | --- | --- | --- |
| N7 | **The app embeds without Jina task adapters.** The default sends no `task`, so questions and passages use the same embedding. | Leaves about +0.14 hit@3 and +0.12 MRR unused, at no cost. | Set `EMBEDDING_TASK_ADAPTERS=true` in `.env` and Railway, then re-ingest. Make it the default once the judged run confirms it. |
| N8 | **Nothing records which settings produced the stored vectors.** Changing `EMBEDDING_MODEL`, `EMBEDDING_TASK_ADAPTERS` or chunking without a re-ingest silently mixes vector spaces. This happened during the evals: the DB briefly held fixed-1600 + task vectors while `.env` said sections, no task. | Retrieval quietly degrades, with no error. | Store model, task and chunking in `documents.metadata` at ingest; log a warning or fail at query time on a mismatch. |
| N9 | **Groq's free tier can't finish one full judged run per day.** The 200k tokens/day limit on `gpt-oss-20b` ran out after one full run plus 28 questions; bigger chunks use more tokens per answer. | Judged comparisons take days, or are skipped. | Resume with `--answers` across days, judge a subset of categories, or use a paid tier. Retrieval-only evals don't need Groq. |
| N10 | **Empty answers recur.** EVAL-033 and EVAL-036 came back empty in the PDF baseline judged run and count as fails. The same happened in the agent chat: Groq returned `finish_reason=length` after 2,048 reasoning tokens and no text. | Inflates the failure rate, hides real behavior, and showed a blank bubble in the app. | The agent now raises an error on an empty answer and uses low reasoning effort, which was not enough. Shorten the prompt, raise `max_completion_tokens` (it also counts toward the per-minute limit), and log the raw response for empty outputs in the evals. |
| N11 | **The judged pass rate fell from 0.80 to 0.677**, with safety the weakest (0.42). Two changes landed together, the PDF corpus and the Scope prompt, so the cause isn't known. | It may be a regression in safety answers. | Re-run the judge with the old prompt on the PDF corpus to separate the two causes. |
| N12 | **Addressed. Per-k metrics favoured large chunks.** A fixed-1600 chunk covers 2.3 sections on average, so its top 3 was credited with ~6.5 sections vs 3, and even random hit@3 is 0.213 vs 0.089. This made fixed-1600 look better than sections. | It would have led to the wrong chunking choice. | Done: the sweep now reports equal-context `hit@budget`/`recall@budget` (4,000 chars), which shows sections + adapters ahead. Compare chunk sizes on these, not on per-k metrics. |

### 7.4 New findings from the tools and agent

| # | Finding | Why it matters | Suggested fix |
| --- | --- | --- | --- |
| A1 | **One agent question is about 10k prompt tokens across two Groq calls; the free tier allows 8,000 tokens per minute.** The system prompt is about 2,700 tokens and is sent twice; tool definitions 540, settings and context 910, live status 1,400. | Back-to-back questions get a 429 and wait up to about 20 s; a long question can fail. | A shorter agent system prompt (keep the safety rules), then re-run the evals. Trimming tool output alone saves about 1k, which is not enough. Or use Groq's dev tier. |
| A2 | **The MCP server starts for every question**, including playbook-only ones in a scenario, because the model needs the tool list first. | Adds about a second per question. | Keep one long-lived session, or start the server only when the question looks operational. |
| A3 | `service/tools.py` builds its DB engine at import, so every server start does too. `current_scenario()` also reads all four scenario tables on every question just to learn whether a scenario is loaded. | Unneeded work per question; hard to test. | Create the engine lazily; add a cheap "which scenario is loaded" query. |
| A4 | **Snapshots go stale after 5 minutes** (`as_of` is the load time), so in a demo the agent will say the data may be out of date. | Correct by the spec, but noisy in a demo. | Reload before the demo, or raise `STALE_AFTER_SEC`. |
| A5 | **The agent is not covered by the evals yet.** The tool descriptions and agent path changed the answers, but `run_evals` calls `RAGService` directly. | The `live_data` rows no longer reflect what the chat does. | Add an agent mode to the harness, with a fixed scenario loaded. |
| A6 | The guardrail layer does not exist. The agent can quote a figure that came from the model's own wording (it once cited a field that does not exist and an unsupported "typical threshold"). | Violates the no-invented-numbers rule. | Task 20: check each number and ETA in an answer against the tool results. |

## 8. Gap versus requirements (what is still to build)

Mapped to [tasks.md](initial/tasks.md):

| Week | Item | Status |
| --- | --- | --- |
| 1 | Docs, corpus (now PDF), ingestion, retrieval, prompt, Gradio UI, deploy | Done |
| 2 | Tools (`get_live_dispatch_status`, `get_delivery_metrics`), MCP exposure, memory schema and recall, agent-trace panel | **Built, not committed.** Spec ([tools.md](tools.md)), both tools, MCP server and client, agent in the chat, trace panel. Memory: conversations, 9-item settings catalogue, Settings tab, summaries. Still open: the saved two-session recall transcript (task 17), handover notes, alert evaluation, and the prompt size problem (A1) |
| 3 | Guardrail layer, caching with TTL, cache/guardrail/freshness badges | Not started (prompt-level rules, now including scope) |
| 4 | Trace ids, eval harness, error analysis, dashboard, edge cases | **Eval harness in progress** on this branch: 65-row dataset, retrieval metrics, LLM judge, and (new) the retrieval-only embedding/chunking sweep is done. The judged comparison of the top setups is blocked on the Groq quota (N9). Trace ids and dashboard not started |

## 9. Recommended next steps

1. **Switch on task adapters (N7):** set `EMBEDDING_TASK_ADAPTERS=true` and re-ingest; record the ingest settings (N8) at the same time.
2. **Finish the judged comparison:** sections + task at the app's `top_k=3` vs `top_k=6` (more context without bigger chunks), resuming with `--answers` around the Groq limit (N9). Fix empty answers first (N10), and separate the cause of the pass-rate drop (N11).
3. **Decide F2** now that the live demo is linked publicly.
4. **Trim the deploy (N2, F5):** move Docling/torch to an ingest-only group and drop or regenerate `requirements.txt`.
5. **Speed up tests (N3):** parse the PDF corpus once per test session and cache the model in CI.
6. **Relevance floor (F4, N4):** use the eval results, including the out-of-domain rows, to pick a similarity threshold that short-circuits to the fixed reply.
7. **Tools and MCP (tasks 12-15): built.** Next: shorten the agent prompt (A1), make the engine lazy (A3), and add an agent mode to the evals (A5).
8. **Memory (16-17): mostly built.** Save the two-session recall transcript, then handover notes, alert evaluation from the tool output, and the suggestions design in [memory.md](memory.md).
9. **Guardrails (19-21), not started:** a post-generation check that every number or ETA in the answer traces to a tool result; the `safety` and `adversarial` eval rows already exercise refusals.
10. **Observability (26):** bind a trace id per request via `structlog.contextvars` and log retrieval, tool calls, and guardrail hits under it.
11. Housekeeping: N5, F6, F7.
