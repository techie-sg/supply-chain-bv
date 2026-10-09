# DispatchDesk

[Live demo: Open DispatchDesk](https://supply-chain-bv-production.up.railway.app/)

A dispatch assistant that answers questions using a simulated operating playbook and saved dispatch snapshots. The application provides Gradio chat with stored conversation history, per-manager settings, Jina embeddings, PostgreSQL/pgvector retrieval, Groq answer generation, and read-only dispatch tools.

Demo tools load **normal**, **backlog**, and **rain** starting snapshots and inspect orders, riders, hourly metrics, and zones. Current data comes from PostgreSQL; Refresh reads saved changes. Other scenarios preview their YAML definitions. Loading a scenario replaces operational rows and clears chat.

Chat uses the question, stored conversation history, retrieved policy passages, and the manager's settings and remembered context. Groq can request current dispatch data or historical metrics through local tools. Data is read when a tool is called. Conversations are saved in PostgreSQL; the URL identifies the selected chat so a refresh reopens it. Without a chat ID, the latest chat opens.

Settings (alert thresholds, batching, incentive cap, greeting) can be edited in **Settings** or proposed through chat. Chat proposals are saved only after the manager confirms them. Design: [docs/memory.md](docs/memory.md).

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

To use an existing local PostgreSQL database without changing `.env`, export its URL before running the CLI:

```bash
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/dispatchdesk'
```

The local server needs the pgvector extension. The same migrations and commands work with local PostgreSQL.

Run from `backend/`:

```bash
uv run python cli.py migrate
uv run --group ingestion python cli.py ingest
uv run python cli.py app
```

Open http://localhost:7860. `pyproject.toml` and `uv.lock` are the dependency source of truth. PDF ingestion uses the `ingestion` group; ordinary app and cron runs exclude Docling and PyTorch. Linux ingestion uses CPU-only PyTorch wheels. The CLI runs only the selected command. Application startup does not migrate, ingest documents, or load a scenario. Use `uv run python cli.py --help` to list commands.

To change the listening port, set `PORT` in the process environment:

```bash
PORT=8080 uv run python cli.py app
```

`PORT` defaults to `7860`; adding it to `.env` does not change the listening port.

## RAG

The corpus is in [`backend/resources/corpus/`](backend/resources/corpus/README.md). Default Markdown section chunking produces 37 chunks from seven operational documents. Ingestion upserts document and chunk rows in one transaction. The corpus README and prompt files are excluded from ingestion. Moving resources preserves their bytes, hashes, document IDs, chunk labels and stored source identities; existing indexed data needs no migration or re-ingestion for this move.

Defaults are `jina-embeddings-v5-text-nano` for embeddings and Groq's [`openai/gpt-oss-120b`](https://console.groq.com/docs/model/openai/gpt-oss-120b) for answers. Provider and chunking options are listed in [`backend/.env.example`](backend/.env.example). Reingest after changing the embedding model or chunking strategy.

Assistant instructions live in [dispatch_manager_system.md](backend/resources/prompts/dispatch_manager_system.md), which the RAG service loads directly for each answer.

The [RAG notebook](backend/notebooks/simple_rag.ipynb) demonstrates chunking, embedding, storage, retrieval, and a conversation with a follow-up. Select `backend/.venv/bin/python` as its kernel. The storage cell writes document data; provider cells make API calls.

## Dispatch tools

Two read-only tools use the existing Groq tool-calling loop and run in the application process. Their contract is in [docs/tools.md](docs/tools.md):

| Tool | Returns |
| --- | --- |
| `get_live_dispatch_status(store_id)` | Open queue (counts by status, oldest ages, orders), riders with hours and breaks, zones, rain flag, and the snapshot's `as_of` time with a `stale` flag (older than 5 minutes) |
| `get_delivery_metrics(store_id, date, start_hour, end_hour)` | Hourly orders, 10-minute SLA, pick-pack, rider-wait and ride minutes, riders online, rain flag, and an order-weighted period summary |

`service/tools.py` validates tool arguments and computes results; `queries/tools.py` performs the database reads, filtering by store and the requested period. The configured `LLM_MODEL` and normal Groq retry and fallback behavior apply to all chat requests.

To see it in the chat, load a scenario, ask "Orders are backing up right now, what's going on and what should I do first?", and open the **Agent trace** under the answer. It records executed dispatch and setting tools, their data timestamps and errors, and the manager's customized settings. The trace is saved with the answer and survives a refresh. If no snapshot is loaded, the tool returns `NO_SNAPSHOT`.

Live data is a saved snapshot. Its original `as_of` time is preserved and it is flagged stale after five minutes. Historical comparisons report missing hours and calculate summaries from the available rows.

## Evals

[`backend/evals/dataset.csv`](backend/evals/dataset.csv) holds 65 questions. Each row lists `relevant_chunk_ids` (must be retrieved), `expected_chunk_ids` (all useful chunks), live-data and safety flags, and the expected behavior. `history` holds prior turns as JSON for follow-up questions. Run from `backend/` after ingestion:

```bash
uv run --group eval --group ingestion python -m evals.run_evals            # retrieval only
uv run --group eval --group ingestion python -m evals.run_evals --judge    # also generate and grade answers
```

The harness evaluates retrieval and bare RAG; it does not exercise conversation preferences, handover, memory or dispatch tools. Its scores do not validate the full product workflow.

Retrieval metrics: `hit@k` (any relevant chunk in top k), `recall@k` (share of expected chunks in top k), and `mrr`. `--judge` asks an LLM to grade each answer 1/0 on `behavior`, `no_invented_facts`, `no_execution_claim`, `safety` (safety rows only) and `live_data_honesty` (live-data rows only); `pass` requires every applicable check. Results are printed overall and by category, and per-question rows go to `evals/results.csv`, with `answer_model` and `judge_model` columns when `--judge` is used. Use `--judge-model` to grade with a different model than the one answering, and `--category` to run a subset, and `--delay` (seconds between questions) to stay under provider rate limits. `tests/test_evals.py` checks that every chunk ID in the dataset exists in the corpus.

## Railway

| Setting | Value |
| --- | --- |
| Root directory | `/backend` |
| Builder | Railpack |
| Start command | `python cli.py app` |
| Pre-deploy command | `python cli.py migrate` |
| Healthcheck path | `/` |
| Watch paths | `/backend/**` |
| Variable `PORT` | `8080` |
| Domain target port | `8080` |

Set `DATABASE_URL`, `JINA_API_KEY`, and `GROQ_API_KEY` on the application service in its production environment, then deploy the variable changes. Gradio binds to `0.0.0.0:$PORT`. The `/` healthcheck verifies that the homepage responds; it does not check database connectivity or AI credentials. The pre-deploy command applies pending migrations before the app starts; migrations can also be run locally with `uv run python cli.py migrate`.

Install dependencies during the build. Use the `python` commands above at runtime so app startup and cron runs do not trigger `uv run` dependency synchronization. Installer progress is written to stderr, which Railway can display as errors even when installation succeeds. Application logs use JSON on stdout with an explicit severity; failed CLI jobs log an error and exit with status 1.

### Scheduled conversation summaries

Idle-conversation summarization runs through the `summaries` CLI command. In chat, **Summary** beside the composer opens the saved summary and its **Create summary** or **Update summary** action. Gradio keeps existing after-answer folding, but does not start a scheduler. Create a separate Railway cron service from the same repository with root `/backend`:

| Setting | Value |
| --- | --- |
| Start command | `python cli.py summaries` |
| Cron schedule | `*/5 * * * *` |
| Variables | `DATABASE_URL`, `GROQ_API_KEY` |
| Healthcheck and public domain | None |

Keep the web service's start command as `python cli.py app`. The cron command runs one batch of up to ten conversations idle for at least fifteen minutes, then exits. Railway schedules use UTC. Locally, run the same task with `uv run python cli.py summaries`.

### Daily review

The daily review (dreaming) runs from cron once a day; admins can also run it from **Run review now** in Demo tools. Create a second cron service from the same repository with root `/backend`:

| Setting | Value |
| --- | --- |
| Start command | `python cli.py review` |
| Cron schedule | `0 18 * * *` (18:00 UTC = 23:30 IST) |
| Variables | `DATABASE_URL`, `GROQ_API_KEY` |
| Healthcheck and public domain | None |

Each run also rebuilds every manager's memory digest (shown in Settings → Memory) from the last 7 days of chat summaries; see [memory.md](docs/memory.md), section 6.

Locally: `uv run python cli.py review`.

### Alert checks

Alerts are checked in code against each manager's settings (see [alerts.md](docs/alerts.md)). Open pages check their own manager every 30 seconds; this cron job checks every manager, so pop-ups and the daily counts are recorded even when no page is open. Both share the same cooldown, so a breach is recorded once. Create a third cron service from the same repository with root `/backend`:

| Setting | Value |
| --- | --- |
| Start command | `python cli.py alerts --runs 5 --every 60` |
| Cron schedule | `*/5 * * * *` |
| Variables | `DATABASE_URL` |
| Healthcheck and public domain | None |

Railway runs cron jobs at most every five minutes, so each run checks five times, one minute apart, and exits after about four minutes, before the next run starts (Railway skips a run while the previous one is still active). That gives one check a minute. To run it as an always-on worker instead, use `python cli.py alerts --runs 0` in a normal service. The job needs no Groq key: it calls no model.

Locally: `uv run python cli.py alerts` checks once; `uv run python cli.py alerts --runs 0` keeps checking every minute until stopped.

## Code layout

| Path | Purpose |
| --- | --- |
| `backend/ui/` | Chat, sidebar, scenario, summary and settings views; browser scripts in `assets/` |
| `backend/resources/` | Prompts, policy corpus and scenario fixtures |
| `backend/resources.py` | Backend-relative resource paths |
| `backend/service/` | RAG, ingestion, provider services, chunking, scenarios, conversations, preferences, summaries, and local tools |
| `backend/queries/` | Database operations |
| `backend/domain/` | Data contracts |
| `backend/database/` | SQLAlchemy models and sessions |
| `backend/alembic/` | Migrations |
| `backend/config.py` | Pydantic settings |
| `backend/cli.py` | App, migrations, ingestion, summaries and daily review; owns database resources |
| `backend/service/factory.py` | Provider and chunking composition |

`ui/gradio_app.py` composes the interface explicitly at launch. Services own validation and workflow decisions; `queries/` owns persistence. Suggestion acceptance and handover replacement use scoped atomic transactions. App and job entry points reuse one engine and dispose it on exit; `NullPool` remains unchanged.

## Development checks

Run from `backend/`:

```bash
uv sync --locked --group ingestion --group eval
uv run --group ingestion --group eval ruff check .
uv run --group ingestion --group eval ruff format --check .
uv run --group ingestion --group eval mypy . --exclude alembic/versions
uv run --group ingestion --group eval pytest --cov=. --cov-report=term-missing
```

CI requires 90% coverage. Vector-store integration tests need `TEST_DATABASE_URL` pointing to a disposable PostgreSQL/pgvector database; they clear its document tables. Without that variable, those tests are skipped.

See the [team](docs/team.md), [scenario guide](docs/scenarios.md), and [original requirements](docs/initial/requirements.md).
