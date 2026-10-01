# DispatchDesk

A Gradio assistant for dispatch managers. Answers use the dispatch playbook, Jina embeddings, PostgreSQL/pgvector retrieval, and Groq. Conversations retain history within the current session.

Demo tools load **normal**, **backlog**, and **rain** scenarios and display orders, riders, zones, and hourly metrics. The current scenario is read from the database; Refresh shows saved changes. Other scenarios preview their YAML starting data. Loading a scenario replaces operational data and clears chat. Scenario rows are not sent to the chat model.

## Local setup

Requires Python 3.13, uv, PostgreSQL with pgvector, and Jina/Groq API keys.

```bash
cd backend
cp .env.example .env
uv sync --locked
```

Set these values in `backend/.env` or your environment:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL; `DB_URL` is also accepted |
| `JINA_API_KEY` | Embeddings |
| `GROQ_API_KEY` | Answer generation |
| `PORT` | HTTP port; defaults to `7860` |

Use a SQLAlchemy connection URL such as `postgresql+psycopg://user:password@localhost:5432/dispatchdesk`. The local `.env` is ignored by Git.

Run from `backend/`:

```bash
uv run alembic upgrade head
uv run python -m service.ingestion
uv run python -m ui.gradio_app
```

Open http://localhost:7860. Migrations are manual; application startup does not migrate, ingest documents, or reset scenarios.

Ingestion upserts documents and chunks from `backend/service/rag_data/corpus/`. Defaults are `jina-embeddings-v5-text-nano`, Markdown section chunking, and Groq's `openai/gpt-oss-20b`. Reingest after changing the embedding model or chunking strategy.

## RAG notebook

Open [simple_rag.ipynb](backend/notebooks/simple_rag.ipynb) using `backend/.venv/bin/python` as the kernel. It walks through chunking, embedding, database storage, retrieval, and a conversation with a follow-up. Running the storage cell writes document data; provider cells make API calls.

## Railway

| Setting | Value |
| --- | --- |
| Root directory | `/backend` |
| Builder | Railpack |
| Start command | `python -m ui.gradio_app` |
| Healthcheck | `/` |
| Watch paths | `/backend/**` |
| Domain target port | Match `PORT` |

Set `DATABASE_URL`, `JINA_API_KEY`, and `GROQ_API_KEY` as service variables. The application binds to `0.0.0.0` and the configured port. Apply migrations locally before deploying schema changes.

## Code layout

- `backend/ui/`: Gradio callbacks and styles.
- `backend/service/`: scenarios, ingestion, RAG, provider services, and chunking strategies.
- `backend/queries/`: database operations.
- `backend/domain/`: data contracts.
- `backend/database/` and `backend/alembic/`: SQLAlchemy models and migrations.

Provider and chunking implementations are composed in `service/factory.py`; settings are in `config.py`.

## Checks

Run from `backend/`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy . --exclude alembic/versions
uv run pytest --cov=. --cov-report=term-missing
```

Vector-store integration tests require `TEST_DATABASE_URL` pointing to a disposable PostgreSQL/pgvector database. They clear document tables in that database; otherwise they are skipped. CI requires 90% coverage.

See [requirements](docs/initial/requirements.md) and [scenario documentation](docs/scenarios.md).
