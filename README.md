# DispatchDesk

A dispatch copilot for dark-store managers. It has two parts:

- **Flask API**, which serves synthetic dispatch scenarios (orders, riders, zones, hourly metrics) from Postgres.
- **RAG assistant**, which answers operational questions ("why are deliveries slipping in the rain?") using the dispatch playbook corpus. It uses Jina embeddings, a PostgreSQL/pgvector store and a Groq-hosted LLM, with a Gradio chat UI.

Background: [requirements](docs/initial/requirements.md), [task plan](docs/initial/tasks.md), [6-pager](docs/6-pager.md), [PR/FAQ](docs/pr-faq.md).

## Folder structure

```text
supply-chain-bv/
├── .github/workflows/        # CI: lint, type check, tests + 90% coverage gate, PR description check
├── backend/                  # All application code (a uv project; run commands from here)
│   ├── .env.example          # Template for backend/.env (copy and fill in)
│   ├── app.py                # Flask entry point; registers the blueprints
│   ├── gradio_app.py         # Gradio chat UI for the RAG assistant
│   ├── config.py             # Typed settings loaded from the environment / backend/.env
│   ├── blueprints/           # HTTP routes (Flask blueprints)
│   │   ├── health.py         #   GET /
│   │   └── scenarios.py      #   /api/scenarios endpoints
│   ├── service/              # Business logic
│   │   ├── scenarios.py      #   Load a scenario YAML into the database
│   │   ├── scenario_export.py#   Export a scenario as an Excel workbook
│   │   ├── scenario_data/    #   Scenario definitions (normal, backlog, rain)
│   │   ├── chunker.py        #   Split corpus Markdown into section chunks
│   │   ├── embedder.py       #   Jina embeddings client
│   │   ├── llm.py            #   Groq chat model
│   │   ├── rag.py            #   Retrieve → build context → answer
│   │   ├── ingestion.py      #   One-off job: chunk, embed and upsert the corpus
│   │   └── rag_data/
│   │       ├── corpus/       #   Operational playbook documents (the knowledge base)
│   │       └── prompts/      #   System prompt for the assistant
│   ├── queries/              # SQLAlchemy document upsert and pgvector search
│   ├── domain/               # Pydantic models that validate scenario files
│   ├── database/             # SQLAlchemy models and session handling
│   ├── alembic/              # Database migrations
│   ├── tests/                # Pytest suite (external services are faked)
│   ├── pyproject.toml        # Dependencies and tool config
│   ├── uv.lock               # Locked dependency versions
│   └── requirements.txt      # Runtime dependencies for pip-based deploys
├── docs/                     # Product docs, team docs, scenario docs, Postman collection
│   ├── initial/              #   Original brief and sample data (not read by the app)
│   └── postman/              #   Importable API collection
└── notebooks/                # Early Chroma-based RAG prototype (exploration only)
```

## Prerequisites

- **Python 3.13** (pinned in `backend/.python-version`)
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)**, which manages the virtualenv and dependencies
- **Postgres** (with pgvector) for the scenario API and RAG storage.
- Accounts and keys for **Jina** and **Groq** for the RAG assistant

## Setup

```bash
git clone https://github.com/techie-sg/supply-chain-bv.git
cd supply-chain-bv/backend
cp .env.example .env    # then fill in the values
uv sync --locked        # creates backend/.venv with runtime + dev dependencies
```

### Environment variables (`backend/.env`)

| Variable | Needed for | Description |
|---|---|---|
| `DATABASE_URL` (or `DB_URL`) | Scenario API, RAG storage, migrations | Postgres URL, e.g. `postgresql+psycopg://user:pass@localhost:5432/dispatchdesk` |
| `JINA_API_KEY` | RAG | Jina embeddings API key |
| `GROQ_API_KEY` | RAG | Groq API key for the chat model |

`backend/.env` is git-ignored, so never commit it. Real environment variables override values in `.env`.

## Running

All commands run from `backend/`.

### 1. Database (scenario API)

Migrations are run manually; the app never runs them on startup.

```bash
uv run alembic upgrade head
```

### 2. Flask API

```bash
uv run flask --app app run --port 8080
```

Production-style:

```bash
uv run gunicorn -b 0.0.0.0:8080 app:app
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/api/scenarios` | List scenarios |
| POST | `/api/scenarios/<key>/load` | Reset the database to a scenario (`normal`, `backlog`, `rain`) |
| GET | `/api/scenarios/<key>/download` | Download a scenario as `.xlsx` |

See [docs/scenarios.md](docs/scenarios.md) for scenario details and [the Postman collection](docs/postman/DispatchDesk.postman_collection.json) to try the endpoints.

### 3. RAG assistant

The assistant uses the same `DATABASE_URL` and the `app.documents` / `app.document_chunks` tables defined by the Alembic migrations. Retrieval queries pgvector directly; no REST API client or RPC endpoint is required.

Load the corpus into PostgreSQL. Rerunning unchanged files upserts their existing document and chunk rows in one transaction.

```bash
uv run python -m service.ingestion
```

The first run downloads the `BAAI/bge-small-en-v1.5` tokenizer from Hugging Face to check chunk sizes. The job expects exactly 37 chunks. If you add or remove `##` sections in the corpus, update `EXPECTED_CHUNKS` in `service/ingestion.py`.

Start the chat UI, then open http://localhost:7860:

```bash
uv run python gradio_app.py
```

## Development

These are the same checks CI runs on every push:

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run mypy . --exclude alembic/versions
```

```bash
uv run pytest --cov=. --cov-report=term-missing
```

CI fails if coverage drops below 90%. Tests fake Jina and Groq, so no API keys are needed. Vector-store integration tests use a dedicated PostgreSQL database with pgvector; CI provisions it automatically. Locally, set `TEST_DATABASE_URL` to a disposable test database to run those tests. They clear the document tables in that test database; without this variable, those tests are skipped.

Conventions:
- New HTTP routes go in `blueprints/` and business logic in `service/`.
- Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`), then mirror runtime ones in `requirements.txt`.
- Pull requests need a description of at least 20 characters.
