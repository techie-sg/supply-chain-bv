# DispatchDesk: Code Overview, Interactions, and Next Steps

Snapshot of `main` at `a1a328e`. Checks at review time: `ruff` clean, `mypy` clean (37 files), `pytest` 76 passed / 6 skipped (the skipped ones are the pgvector integration tests that need `TEST_DATABASE_URL`).

## 1. What it is

A dispatch copilot for dark-store managers (persona: Karthik, Koramangala). Today it is **RAG chat over a policy playbook plus a demo workspace for synthetic operational scenarios**. Per [requirements.md](initial/requirements.md), the target is much larger (live-data tools, memory, guardrails, caching, observability, evals). See section 7 for the gap.

## 2. Tech stack

| Area | Choice |
| --- | --- |
| Language / packaging | Python 3.13, `uv` (`package = false`, locked sync) |
| UI | Gradio 6.29 (`gr.Blocks`, custom dark theme and CSS), bound to `0.0.0.0:$PORT` (default 7860) |
| DB | PostgreSQL + `pgvector`, SQLAlchemy 2.1 ORM, `psycopg` 3, Alembic migrations |
| Embeddings | Jina `jina-embeddings-v5-text-nano` via plain `requests` POST |
| LLM | Groq `openai/gpt-oss-20b` via `langchain-groq` (`temperature=0`) |
| LangChain use | `langchain-core` only for `Document` and message types, plus `ChatGroq` |
| Config / validation | `pydantic` v2, `pydantic-settings` (env, then `backend/.env`; secrets are `SecretStr`) |
| Scenario data | YAML (`normal`, `backlog`, `rain`) validated by Pydantic |
| Logging | `structlog` JSON to stdout, stdlib logs routed through the same formatter |
| Quality | `ruff` (with COM812), `mypy`, `pytest` + `pytest-cov`, 90% coverage gate |
| CI | GitHub Actions: lint, test (pgvector 17 service container, PR coverage comment), PR-description check |
| Deploy | Railway (Railpack, root `/backend`, start `python -m ui.gradio_app`, healthcheck `/`) |

## 3. Layout and layering

```
backend/
  config.py, constants.py, logging_config.py
  ui/            gradio_app.py (+ .css)         presentation, callbacks
  service/       rag, ingestion, corpus, chunking, factory,
                 groq/jina/llm/embedding services, scenarios
                 rag_data/{corpus,prompts}  scenario_data/*.yaml
  queries/       vector_store.py, scenarios.py  all SQL lives here
  domain/        chat.py, scenario.py           data contracts
  database/      models.py, session.py          ORM + transactional session
  alembic/       0001 six tables, 0002 uuid default, 0003 match function
  tests/         one test module per service
```

Dependency direction is `ui -> service -> queries -> database`, with `domain` and `config` shared. Providers sit behind small ABCs (`EmbeddingService`, `LLMService`, `ChunkingStrategy`) and are chosen by `service/factory.py` from settings.

## 4. Data model (schema `app`)

- **Operational (4 tables, from the original workbook):** `zones`, `riders`, `orders`, `hourly_metrics`. Composite PKs (`scenario_key` + id), FKs with `RESTRICT`, `CHECK` constraints, and indexes on scenario/store/status.
- **RAG (2 tables):** `documents` (uuid, `file_hash` bytea(32), `version`, JSONB `metadata`; unique on `(file_hash, version)`) and `document_chunks` (`(document_id, chunk_id)` PK, `content`, unconstrained `Vector()` embedding, cascade delete).
- Migration 0003 adds SQL function `app.match_document_chunks(vector(768), int)`. The application does **not** call it (see finding F3).

## 5. Interactions (end to end)

### 5.1 Ingestion (`uv run python -m service.ingestion`)
1. `configure_logging()`, then `get_settings()`.
2. `CorpusService.load()` reads `corpus/*.md` (skips README), normalizes newlines, extracts `title`, `Document ID`, `Version` by regex, computes a SHA-256 of the raw bytes.
3. The chunking strategy (`markdown_sections` splits on `##`, or `fixed_size` with overlap) yields `(label, body)`; each chunk becomes `"<title> > <section>\n\n<body>"`. Duplicate chunk labels raise.
4. `JinaEmbeddingService.embed_documents()` makes one batched POST (60 s timeout) and validates count and index order.
5. `queries.vector_store.insert_chunks()` runs one transaction per call: upsert `documents` on `(file_hash, version)`, delete trailing chunks above the new count, upsert chunks on the PK.

### 5.2 Chat (question to answer)
1. Gradio `submit`/Enter stores the message in a hidden textbox and shows a busy state, then `respond_to_pending` calls `chat()` (which builds a `request_id`, converts Gradio history to `ChatMessage`s).
2. `service.rag.answer_question()` builds a `RAGService` from the factory on every call.
3. With history, the retrieval query is the last two messages plus "Follow-up question: ...". `JinaEmbeddingService.embed_query()` embeds it.
4. `retrieve()` runs pgvector cosine distance, `top_k=3`, returning `doc_id#chunk` ids, content, and similarity.
5. Empty result returns a fixed "could not find guidance" message. Otherwise the context is wrapped (`<context>` when there is history) and sent with the system prompt (`dispatch_manager_system.md`, re-read each call) and history to `GroqService.generate()`.
6. On provider/DB errors the UI keeps the draft and history, logs with the request id, and shows a `gr.Error`.

### 5.3 Demo tools (scenarios)
- `scenario_names()` lists YAML files. **Preview** calls `scenario_details()` which builds rows in memory only. **Load** calls `load_scenario()`: validate whole YAML (`ScenarioData`, cross-reference checks), build ORM rows with `as_of = now` (Asia/Kolkata), `TRUNCATE` the four tables, insert in FK order, all in one transaction, then read back via `current_scenario()`.
- `app.load` and **Refresh** call `current_scenario()` to restore the saved snapshot into four Dataframes. Loading a scenario also clears chat.
- Chat does **not** read this data (stated in the README).

## 6. What is good

- Clean layering, constructor-injected services, and ABC seams that make providers swappable and tests easy.
- Secrets handled with `SecretStr`, `require()` never echoes values, `hide_parameters=True` on the engine, raw SQL parameters not logged.
- Whole-scenario validation before any write; atomic replace; DB `CHECK`/FK constraints mirror the Pydantic rules.
- Ingestion is idempotent per file/version and chunk ids are stable.
- System prompt encodes the safety rules (no invented numbers, propose-never-execute, no rider pressure) and treats retrieved text as data.
- Good CI hygiene: lint, types, coverage gate, PR-body injection-safe env handling, structured JSON logs with durations and token usage.

## 7. Review findings, ordered by importance

| # | Finding | Why it matters | Suggested fix |
| --- | --- | --- | --- |
| F1 | **Stale chunks survive corpus edits.** `documents` is keyed on `(file_hash, version)`. Editing a file changes the hash, so a *new* document row is created and the old row and its chunks remain, still searchable by `retrieve()`. | Retrieval can return outdated policy next to the new one, which is a real risk for a safety-policy assistant. | In `insert_chunks`, delete other `documents` rows with the same `file_name` (cascade removes chunks) inside the same transaction. Add an integration test. |
| F2 | **Unauthenticated public app can wipe shared data.** `replace_scenario` truncates global tables and the Railway app is public with no auth. State is shared by all visitors, so one user's Load resets everyone's view. | Data loss and cross-user interference. | Gate demo tools (auth or env flag), or make scenario state per session/`scenario_key`. |
| F3 | **Migration 0003 is unused and not portable.** It grants to `service_role`/`anon` and runs `NOTIFY pgrst` (Supabase-isms) and hardcodes `vector(768)` while the column is unconstrained. On plain Postgres/Railway it fails if those roles do not exist. The app queries via ORM instead. | A fresh `alembic upgrade head` can break; dead code drifts. | Drop the function (new migration) or guard the grants; if kept, align the dimension with the model. Verify the Jina model's output size. |
| F4 | **No relevance threshold.** `retrieve` always returns top-3, so the "no guidance found" path only fires on an empty table. Off-topic questions get weak context and the model may over-reach. | Hallucination risk; the prompt relies on the model to notice irrelevance. | Filter by minimum similarity (tune on the 6 sample queries) and return the fallback message when below it. |
| F5 | **Dependency drift.** `requirements.txt` duplicates `pyproject.toml` and leaves `langchain-*`/`requests` unpinned. Railpack may install from either. | Production can differ from CI. | Pick one source (uv lock), delete `requirements.txt`, or generate it with `uv export`. |
| F6 | **Per-request wiring and I/O.** Each chat call builds new services and re-reads the prompt; `NullPool` opens a new Postgres connection every query; no embedding cache. | Latency and cost. Fine for a demo; matters for the Week 3 caching/latency goals. | Build the services once at startup, cache the prompt, consider a small pool, add the planned embedding/metrics cache. |
| F7 | **Weak config typing.** `embedding_provider`, `llm_provider`, `chunking_strategy` are free `str`; bad values only fail when first used. | Late failures in deployment. | Use `Literal[...]` types so startup fails fast. |
| F8 | **Retrieval for follow-ups is crude.** Last two raw messages are concatenated into the query, which can drag in assistant text and drift topics. | Lower retrieval precision. | Rewrite the follow-up into a standalone question (cheap LLM call) or embed only the user turns. |
| F9 | **Minor.** Hardcoded `Asia/Kolkata` and `DS-BLR-014`; Railway healthcheck `/` does not test DB or keys; `statement_timeout=5000` is applied to all queries including TRUNCATE; no request/session id propagates into structlog contextvars; the notebook duplicates logic and may rot. | Operability. | Add a `/health` that checks the DB, bind `request_id` with `structlog.contextvars`, document or parametrize timezone/store. |

## 8. Gap versus requirements (what is still to build)

Mapped to [tasks.md](initial/tasks.md):

| Week | Item | Status |
| --- | --- | --- |
| 1 | Docs, corpus, ingestion, retrieval, prompt, Gradio UI, deploy | Done |
| 2 | Tools (`get_live_dispatch_status`, `get_delivery_metrics`), MCP exposure, memory schema and recall, agent-trace panel | **Not started.** Scenario data exists in Postgres but is not wired to the LLM |
| 3 | Guardrail layer, caching with TTL, cache/guardrail/freshness badges | Not started (only prompt-level rules) |
| 4 | Trace ids, eval harness for the 6 sample queries, error analysis, dashboard, edge cases | Not started (structured logs are a foundation) |

## 9. Recommended next steps

1. **Fix F1 and F3 first** (small, correctness and deploy safety). Decide F2 before sharing the Railway URL widely.
2. **Tools (tasks 12-15):** add `queries` functions for live status and metrics, expose them as typed tool functions (return an `as_of` timestamp and clear errors for unknown store/period), then let the LLM call them. Start with direct LangChain tool-calling; add MCP only if the task requires it.
3. **Memory (16-17):** one `store_preferences` table (store, key, value JSON, window, updated_at), read at the start of each answer and passed into the prompt as the "Store preferences" source the prompt already describes.
4. **Eval harness early (27):** turn the six sample queries into pytest cases with scorers now. It also gives you the data to pick the F4 threshold and to detect regressions as tools are added.
5. **Guardrails (19-21):** a post-generation check that every number or ETA in the answer traces to a tool result, plus refusal tests for the two unsafe prompts.
6. **Observability (26):** bind a trace id per request via `structlog.contextvars` and log retrieval, tool calls, and guardrail hits under it.
7. Housekeeping: F5, F6, F7.
