# DispatchDesk

[Live demo: Open DispatchDesk](https://supply-chain-bv-production.up.railway.app/)

A dispatch assistant that answers questions using a simulated operating playbook and, when a scenario is loaded, live (simulated) dispatch data. The application provides Gradio chat with stored conversation history, per-manager settings, Jina embeddings, PostgreSQL/pgvector retrieval, Groq answer generation, and read-only dispatch tools served over MCP.

Demo tools load **normal**, **backlog**, and **rain** starting snapshots and inspect orders, riders, hourly metrics, and zones. Current data comes from PostgreSQL; Refresh reads saved changes. Other scenarios preview their YAML definitions. Loading a scenario replaces operational rows and clears chat.

Chat uses the question, the stored conversation history, the manager's settings, and retrieved policy passages. Conversations are saved in PostgreSQL, so a page refresh or restart resumes the latest chat; long chats are summarized so prompts stay small. With a scenario loaded, the assistant can also call two read-only tools for the live queue and rider status and for historical hourly metrics. Each answer has a collapsed **Agent trace** panel listing the tool calls (with the data's "as of" time) and the settings applied. Without a scenario, answers come from the playbook alone. Guardrail checks, caching, and action execution are planned work; the assistant only proposes actions.

Settings (alert thresholds, batching, incentive cap, greeting) are changed only in the **Settings** tab. Design: [docs/memory.md](docs/memory.md).

## Setup

Requires Python 3.13, uv, PostgreSQL with pgvector, and Jina/Groq API keys.

From the repository root:

```bash
cd backend
uv sync --locked
cp .env.example .env
```

Set these values in `backend/.env` or the process environment. Environment variables take precedence; `.env` is ignored by Git.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URL; `DB_URL` is also accepted |
| `JINA_API_KEY` | Jina embeddings |
| `GROQ_API_KEY` | Groq answer generation |

Example database URL: `postgresql+psycopg://user:password@localhost:5432/dispatchdesk`.

Run from `backend/`:

```bash
uv run alembic upgrade head
uv run python -m service.ingestion
uv run python -m ui.gradio_app
```

Open http://localhost:7860. Load a scenario in **Demo tools** to give the assistant live data. Migrations (0001 to 0008) are manual and run from your local machine. Application startup does not migrate, ingest documents, or load a scenario.

To change the listening port, set `PORT` in the process environment:

```bash
PORT=8080 uv run python -m ui.gradio_app
```

`PORT` defaults to `7860`; adding it to `.env` does not change the listening port.

## RAG

The corpus is in [`backend/service/rag_data/corpus/`](backend/service/rag_data/corpus/README.md). Default Markdown section chunking produces 37 chunks from seven operational documents. Ingestion upserts document and chunk rows in one transaction. The corpus README and prompt files are excluded from ingestion.

Defaults are `jina-embeddings-v5-text-nano` for embeddings and Groq's `openai/gpt-oss-20b` for answers. Provider and chunking options are listed in [`backend/.env.example`](backend/.env.example). Reingest after changing the embedding model or chunking strategy.

Assistant instructions live in [dispatch_manager_system.md](backend/service/rag_data/prompts/dispatch_manager_system.md), which the RAG service loads directly for each answer.

The [RAG notebook](backend/notebooks/simple_rag.ipynb) demonstrates chunking, embedding, storage, retrieval, and a conversation with a follow-up. Select `backend/.venv/bin/python` as its kernel. The storage cell writes document data; provider cells make API calls.

## Tools and MCP

Two read-only tools are served by a local MCP server (`dispatchdesk-ops`, [`backend/mcp_server/server.py`](backend/mcp_server/server.py)) and described in [docs/tools.md](docs/tools.md):

| Tool | Returns |
| --- | --- |
| `get_live_dispatch_status(store_id)` | Open queue (counts by status, oldest ages, orders), riders with hours and breaks, zones, rain flag, and the snapshot's `as_of` time with a `stale` flag (older than 5 minutes) |
| `get_delivery_metrics(store_id, date, start_hour, end_hour)` | Hourly orders, 10-minute SLA, pick-pack, rider-wait and ride minutes, riders online, rain flag, and an order-weighted period summary |

The chat starts the server for each question as a subprocess over stdio (no network port), so it needs no separate process. It uses `DATABASE_URL` from the environment or `backend/.env`. To check it on its own, run `uv run pytest tests/test_mcp_server.py tests/test_agent.py`, or start it with `uv run python -m mcp_server.server`; it speaks MCP on stdin and stdout, and its logs go to stderr.

To see it in the chat, load a scenario, ask "Orders are backing up right now, what's going on and what should I do first?", and open the **Agent trace** under the answer. The app log shows a `Tool call` line from the agent and one from the server for each call.

Groq's free tier allows 8,000 tokens per minute, and one question that uses the tools sends about 10,000 prompt tokens across two calls. Expect a wait of up to about 20 seconds on a second question within a minute; the app retries automatically.

## Evals

[`backend/evals/dataset.csv`](backend/evals/dataset.csv) holds 65 questions. Each row lists `relevant_chunk_ids` (must be retrieved), `expected_chunk_ids` (all useful chunks), live-data and safety flags, and the expected behavior. `history` holds prior turns as JSON for follow-up questions. Run from `backend/` after ingestion:

```bash
uv run python -m evals.run_evals            # retrieval only
uv run python -m evals.run_evals --judge    # also generate and grade answers
```

Retrieval metrics: `hit@k` (any relevant chunk in top k), `recall@k` (share of expected chunks in top k), and `mrr`. `--judge` asks an LLM to grade each answer 1/0 on `behavior`, `no_invented_facts`, `no_execution_claim`, `safety` (safety rows only) and `live_data_honesty` (live-data rows only); `pass` requires every applicable check. Results are printed overall and by category, and per-question rows go to `evals/results.csv`, with `answer_model` and `judge_model` columns when `--judge` is used. Use `--judge-model` to grade with a different model than the one answering, and `--category` to run a subset, and `--delay` (seconds between questions) to stay under provider rate limits. `tests/test_evals.py` checks that every chunk ID in the dataset exists in the corpus.

## Railway

| Setting | Value |
| --- | --- |
| Root directory | `/backend` |
| Builder | Railpack |
| Start command | `python -m ui.gradio_app` |
| Healthcheck path | `/` |
| Watch paths | `/backend/**` |
| Variable `PORT` | `8080` |
| Domain target port | `8080` |

Set `DATABASE_URL`, `JINA_API_KEY`, and `GROQ_API_KEY` on the application service in its production environment, then deploy the variable changes. Gradio binds to `0.0.0.0:$PORT`. The `/` healthcheck verifies that the homepage responds; it does not check database connectivity or AI credentials. Schema changes require local migrations before deployment.

## Code layout

| Path | Purpose |
| --- | --- |
| `backend/ui/` | Gradio callbacks, the Settings tab, styles, and favicon |
| `backend/service/` | RAG, ingestion, provider services, chunking, scenarios, conversations, preferences, summaries, tools, and the tool-calling agent |
| `backend/mcp_server/` | MCP server for the dispatch tools |
| `backend/queries/` | Database operations |
| `backend/domain/` | Data contracts |
| `backend/database/` | SQLAlchemy models and sessions |
| `backend/alembic/` | Migrations |
| `backend/config.py` | Pydantic settings |
| `backend/service/factory.py` | Provider and chunking composition |

## Development checks

Run from `backend/`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy . --exclude alembic/versions
uv run pytest --cov=. --cov-report=term-missing
```

CI requires 90% coverage. Vector-store integration tests need `TEST_DATABASE_URL` pointing to a disposable PostgreSQL/pgvector database; they clear its document tables. Without that variable, those tests are skipped.

See the [code overview](docs/code-overview.md), [tool spec](docs/tools.md), [memory design](docs/memory.md), [team](docs/team.md), [scenario guide](docs/scenarios.md), and [original requirements](docs/initial/requirements.md).
