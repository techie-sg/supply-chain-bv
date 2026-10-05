# DispatchDesk

[Live demo: Open DispatchDesk](https://supply-chain-bv-production.up.railway.app/)

A dispatch assistant that answers questions using a simulated operating playbook. The current application provides Gradio chat with session history, Jina embeddings, PostgreSQL/pgvector retrieval, and Groq answer generation.

Demo tools load **normal**, **backlog**, and **rain** starting snapshots and inspect orders, riders, hourly metrics, and zones. Current data comes from PostgreSQL; Refresh reads saved changes. Other scenarios preview their YAML definitions. Loading a scenario replaces operational rows and clears chat.

Chat uses the question, conversation history, and retrieved policy passages. Operational scenario rows are not sent to the chat model. Operational tools, persistent preference memory, and action execution are planned work.

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

Open http://localhost:7860. Migrations are manual and run from your local machine. Application startup does not migrate, ingest documents, or load a scenario.

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
| `backend/ui/` | Gradio callbacks, styles, and favicon |
| `backend/service/` | RAG, ingestion, provider services, chunking, and scenarios |
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

See the [team](docs/team.md), [scenario guide](docs/scenarios.md), and [original requirements](docs/initial/requirements.md).
