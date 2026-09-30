# DispatchDesk

A dispatch copilot for dark-store managers. It has two parts:

- **Flask API**, which serves synthetic dispatch scenarios (orders, riders, zones, hourly metrics) from Postgres.
- **RAG assistant**, which answers operational questions ("why are deliveries slipping in the rain?") using the dispatch playbook corpus. It uses Jina embeddings, a Supabase vector store and a Groq-hosted LLM, with a Gradio chat UI.

Background: [requirements](docs/initial/requirements.md), [task plan](docs/initial/tasks.md), [6-pager](docs/6-pager.md), [PR/FAQ](docs/pr-faq.md).

## Folder structure

```text
supply-chain-bv/
├── .env.example              # Template for .env (copy to .env in this folder)
├── .github/workflows/        # CI: lint, type check, tests + 90% coverage gate, PR description check
├── backend/                  # All application code (a uv project; run commands from here)
│   ├── app.py                # Flask entry point; registers the blueprints
│   ├── gradio_app.py         # Gradio chat UI for the RAG assistant
│   ├── config.py             # Typed settings loaded from the environment / root .env
│   ├── blueprints/           # HTTP routes (Flask blueprints)
│   │   ├── health.py         #   GET /
│   │   └── scenarios.py      #   /api/scenarios endpoints
│   ├── service/              # Business logic
│   │   ├── scenarios.py      #   Load a scenario YAML into the database
│   │   ├── scenario_export.py#   Export a scenario as an Excel workbook
│   │   ├── scenario_data/    #   Scenario definitions (normal, backlog, rain)
│   │   ├── chunker.py        #   Split corpus Markdown into section chunks
│   │   ├── embedder.py       #   Jina embeddings client
│   │   ├── vector_store.py   #   Supabase upsert + similarity search
│   │   ├── llm.py            #   Groq chat model
│   │   ├── rag.py            #   Retrieve → build context → answer
│   │   ├── ingestion.py      #   One-off job: chunk, embed and upsert the corpus
│   │   └── rag_data/
│   │       ├── corpus/       #   Operational playbook documents (the knowledge base)
│   │       └── prompts/      #   System prompt for the assistant
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
- **Postgres** (with pgvector) for the scenario API. Only needed for the database endpoints.
- Accounts and keys for **Jina**, **Groq** and **Supabase** for the RAG assistant

## Setup

```bash
git clone https://github.com/techie-sg/supply-chain-bv.git
cd supply-chain-bv
cp .env.example .env    # then fill in the values
cd backend
uv sync --locked        # creates backend/.venv with runtime + dev dependencies
```

### Environment variables (`.env` in the repository root)

| Variable | Needed for | Description |
|---|---|---|
| `DATABASE_URL` (or `DB_URL`) | Scenario API, migrations | Postgres URL, e.g. `postgresql+psycopg://user:pass@localhost:5432/dispatchdesk` |
| `JINA_API_KEY` | RAG | Jina embeddings API key |
| `GROQ_API_KEY` | RAG | Groq API key for the chat model |
| `SUPABASE_URL` | RAG | Supabase project URL |
| `SUPABASE_KEY` | RAG | Supabase API key |

`.env` is git-ignored, so never commit it. Real environment variables override values in `.env`.

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

The Supabase project must already contain the `document_chunks` table and the `match_document_chunks` similarity-search function. Their SQL is not in this repository; set them up in Supabase first.

Load the corpus into Supabase. Rerun it whenever files in `service/rag_data/corpus/` change; it upserts, so reruns are safe.

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

CI fails if coverage drops below 90%. Tests fake Jina, Groq, Supabase and the database, so they need no API keys or running services.

Conventions:
- New HTTP routes go in `blueprints/` and business logic in `service/`.
- Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`), then mirror runtime ones in `requirements.txt`.
- Pull requests need a description of at least 20 characters.
