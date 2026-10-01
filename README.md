# DispatchDesk

A dispatch copilot for dark-store managers, served as one Gradio application:

- **Assistant** is the default manager workspace: ask for dispatch playbook guidance on delays, SLA changes, and safe batching. Answers use retrieved documents, with Jina embeddings, PostgreSQL/pgvector retrieval, and a Groq-hosted LLM.
- **Demo tools** is a separate view for selecting and loading synthetic situations and inspecting searchable orders, riders, zones, and hourly metrics. These controls support demonstrations without occupying the manager’s workspace.

Background: [requirements](docs/initial/requirements.md), [task plan](docs/initial/tasks.md), [6-pager](docs/6-pager.md), [PR/FAQ](docs/pr-faq.md).

## Folder structure

```text
supply-chain-bv/
├── .github/workflows/        # CI: lint, type check, tests + 90% coverage gate, PR description check
├── backend/                  # All application code (a uv project; run commands from here)
│   ├── .env.example          # Template for backend/.env (copy and fill in)
│   ├── ui/                   # Gradio application and responsive styles
│   │   ├── gradio_app.py     #   Scenario controls, data tables, chat
│   │   └── gradio_app.css    #   Workspace styling
│   ├── config.py             # Typed settings loaded from the environment / backend/.env
│   ├── constants.py          # Shared provider and chunking defaults
│   ├── service/              # Business logic
│   │   ├── scenarios.py      #   Load a scenario YAML into the database
│   │   ├── scenario_data/    #   Scenario definitions (normal, backlog, rain)
│   │   ├── embedding_service.py      # Abstract embedding contract
│   │   ├── jina_embedding_service.py # Jina passage/query embeddings
│   │   ├── llm_service.py    #   Abstract language-model contract
│   │   ├── groq_service.py   #   Groq response generation
│   │   ├── chunking.py       #   Markdown-section and fixed-size strategies
│   │   ├── corpus.py         #   CorpusService: file loading and chunk metadata
│   │   ├── factory.py        #   Configured provider and strategy selection
│   │   ├── rag.py            #   RAGService: retrieval and grounded answers
│   │   ├── ingestion.py      #   IngestionService and corpus-ingestion CLI
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
└── docs/                     # Product docs, team docs, scenario docs, Postman collection
    └── initial/              #   Original brief and sample data (not read by the app)
```

## Prerequisites

- **Python 3.13** (pinned in `backend/.python-version`)
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)**, which manages the virtualenv and dependencies
- **Postgres** (with pgvector) for scenario data and RAG storage.
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
| `DATABASE_URL` (or `DB_URL`) | Scenario loading, RAG storage, migrations | Postgres URL, e.g. `postgresql+psycopg://user:pass@localhost:5432/dispatchdesk` |
| `JINA_API_KEY` | RAG | Jina embeddings API key |
| `GROQ_API_KEY` | RAG | Groq API key for the chat model |
| `PORT` | Railway | HTTP port supplied by Railway; defaults to `7860` locally |

`backend/.env` is git-ignored, so never commit it. Real environment variables override values in `.env`.

## Running

All commands run from `backend/`.

### 1. Database

Migrations are run manually; the app never runs them on startup.

```bash
uv run alembic upgrade head
```

### 2. Gradio workspace

```bash
uv run python -m ui.gradio_app
```

Open http://localhost:7860. The **Assistant** view opens first and automatically
reconnects to the snapshot already saved in the database. Ask a dispatch question
directly; the store and snapshot timestamp appear above the conversation.
The interface follows your system's light or dark appearance.

Use **Demo tools** to preview tables or load a different simulated situation.
**Load scenario** replaces the operational rows and clears the conversation.
Previewing data or switching between views preserves the conversation.
**Back to assistant** returns to the manager workspace.
Chat receives the question and retrieved playbook passages. Scenario rows are
available in Demo tools and are not supplied to the embedding or language-model services.

See [docs/scenarios.md](docs/scenarios.md) for scenario details.

### 3. RAG assistant

The assistant uses the same `DATABASE_URL` and the `app.documents` / `app.document_chunks` tables defined by the Alembic migrations. Retrieval queries pgvector directly; no REST API client or RPC endpoint is required.

Load the corpus into PostgreSQL. Rerunning unchanged files upserts their existing document and chunk rows in one transaction.

```bash
uv run python -m service.ingestion
```

Ingestion accepts any nonempty corpus. The current Markdown-section strategy produces 37 chunks, but this is not a runtime requirement. Reingesting the same document also removes surplus chunks from its previous ingestion.

Embeddings use `jina-embeddings-v5-text-nano`: `retrieval.passage` for corpus ingestion and `retrieval.query` for questions. The embedding client logs the API-reported token usage. Re-run ingestion when changing the embedding model or retrieval task so stored vectors match the query setup.

For an interactive walkthrough, open [`backend/notebooks/simple_rag.ipynb`](backend/notebooks/simple_rag.ipynb) with a Python kernel using the backend dependencies. It loads the corpus, creates Jina embeddings, upserts documents and chunks through the existing PostgreSQL queries, and retrieves guidance. The notebook's database-write cell runs when you execute it.

The Gradio workspace includes chat alongside the scenario controls.

### Provider services and chunking

`RAGService` depends on the abstract `EmbeddingService` and `LLMService` contracts.
`IngestionService` accepts an embedding service and a `CorpusService`, whose
`ChunkingStrategy` is supplied independently. Providers own their API calls;
database operations remain in `queries/`. The UI and CLI compose services through
`service/factory.py`, using the Pydantic settings below.

| Setting | Default | Purpose |
|---|---|---|
| `EMBEDDING_PROVIDER` | `jina` | Embedding implementation; currently Jina is supported |
| `EMBEDDING_MODEL` | `jina-embeddings-v5-text-nano` | Model used for both documents and queries |
| `LLM_PROVIDER` | `groq` | Response provider; currently Groq is supported |
| `LLM_MODEL` | `openai/gpt-oss-20b` | Groq chat model |
| `CHUNKING_STRATEGY` | `markdown_sections` | `markdown_sections` or `fixed_size` |
| `CHUNK_SIZE` | `1600` | Fixed-size window length in characters |
| `CHUNK_OVERLAP` | `200` | Fixed-size overlap in characters; must be smaller than the window |

For example, `CHUNKING_STRATEGY=fixed_size` selects overlapping character windows
without changing ingestion code. To add an embedding provider, implement
`embed_documents()` and `embed_query()` in an `EmbeddingService` subclass and add
its construction to the factory. To add an LLM provider, implement
`LLMService.generate()`; to add a chunker, implement `ChunkingStrategy.split()`.
Tests and custom callers can inject implementations directly, bypassing factories.
Re-run corpus ingestion after changing the embedding model or chunking strategy
before using retrieval with the new configuration.

### Railway

| Service setting | Value |
|---|---|
| Root directory | `/backend` |
| Builder | Railpack |
| Custom start command | `python -m ui.gradio_app` |
| Healthcheck path | `/` |
| Watch paths | `/backend/**` |
| Public domain target port | Match `PORT` (keep `8080` if `PORT=8080`) |

Gradio is a runtime dependency. The app binds to `0.0.0.0` and Railway's `PORT`
and serves the UI directly. Configure `DATABASE_URL`, `JINA_API_KEY`, and
`GROQ_API_KEY` as Railway service variables. Migrations remain manual;
deployment does not run migrations or load a scenario automatically.

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
- Gradio callbacks call `service/` functions; the UI contains no database queries.
- Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`), then mirror runtime ones in `requirements.txt`.
- Pull requests need a description of at least 20 characters.
