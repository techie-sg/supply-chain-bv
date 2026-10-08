# DispatchDesk

[Live demo: Open DispatchDesk](https://supply-chain-bv-production.up.railway.app/)

A dispatch assistant that answers questions using a simulated operating playbook. The current application provides Gradio chat with stored conversation history, Jina embeddings, PostgreSQL/pgvector retrieval, and Groq answer generation.

Demo tools load **normal**, **backlog**, and **rain** starting snapshots and inspect orders, riders, hourly metrics, and zones. Current data comes from PostgreSQL; Refresh reads saved changes. Other scenarios preview their YAML definitions. Loading a scenario replaces operational rows and clears chat.

Chat uses the question, the stored conversation history, and retrieved policy passages. Conversations are saved in PostgreSQL; the URL identifies the selected chat so a refresh reopens it. Without a chat ID, the latest chat opens. Operational scenario rows are not sent to the chat model. Operational tools, persistent preference memory, and action execution are planned work.

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
uv run python cli.py ingest
uv run python cli.py app
```

Open http://localhost:7860. The CLI runs only the selected command. Application startup does not migrate, ingest documents, or load a scenario. Use `uv run python cli.py --help` to list commands.

To change the listening port, set `PORT` in the process environment:

```bash
PORT=8080 uv run python cli.py app
```

`PORT` defaults to `7860`; adding it to `.env` does not change the listening port.

## RAG

The corpus is in [`backend/service/rag_data/corpus/`](backend/service/rag_data/corpus/README.md). Default Markdown section chunking produces 37 chunks from seven operational documents. Ingestion upserts document and chunk rows in one transaction. The corpus README and prompt files are excluded from ingestion.

Defaults are `jina-embeddings-v5-text-nano` for embeddings and Groq's `openai/gpt-oss-20b` for answers. Provider and chunking options are listed in [`backend/.env.example`](backend/.env.example). Reingest after changing the embedding model or chunking strategy.

Assistant instructions live in [dispatch_manager_system.md](backend/service/rag_data/prompts/dispatch_manager_system.md), which the RAG service loads directly for each answer.

The [RAG notebook](backend/notebooks/simple_rag.ipynb) demonstrates chunking, embedding, storage, retrieval, and a conversation with a follow-up. Select `backend/.venv/bin/python` as its kernel. The storage cell writes document data; provider cells make API calls.

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

## Code layout

| Path | Purpose |
| --- | --- |
| `backend/ui/` | Gradio callbacks, styles, and favicon |
| `backend/service/` | RAG, ingestion, provider services, chunking, and scenarios |
| `backend/queries/` | Database operations |
| `backend/domain/` | Data contracts |
| `backend/database/` | SQLAlchemy models and sessions |
| `backend/alembic/` | Migrations |
| `backend/config.py` | Pydantic settings |
| `backend/cli.py` | App, migrations, ingestion, and one-shot summary commands |
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

See the [team](docs/team.md), [scenario guide](docs/scenarios.md), and [original requirements](docs/initial/requirements.md).
