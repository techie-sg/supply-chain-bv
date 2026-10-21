# DispatchDesk team

## Ownership

| Group | Team members | Responsibilities |
| --- | --- | --- |
| RAG, evaluations and observability | Vishnu Mohan, Ravisekhar R | Own the policy corpus, chunking, embeddings, ingestion and retrieval; the two read-only dispatch tools; retrieval and answer evaluations; instrumentation and caching; the 6-pager. |
| Setup, integration and reliability | Sunny Gupta, Priya Ranjan, Sharad Nailwal | Own repository setup, dependencies, environment configuration, database models and migrations, scenario loading, Gradio integration, Railway deployment and CI; conversation history, settings, personalization, summaries, handover and dreaming; reliability fixes, guardrails, the README and PR/FAQ. |

Both groups coordinate tool contracts, integration and reviews. Sharad is
handling guardrails and has completed the reported bug fix, as of the
18 October 2026 update.

## Current priorities

| Workstream | Owners | Focus |
| --- | --- | --- |
| Evaluations | Vishnu Mohan, Ravisekhar R | Compare embedding models and chunking strategies against a shared dataset; review retrieval quality and grounded answers. |
| Observability and caching | Vishnu Mohan, Ravisekhar R | Instrument retrieval and provider calls, measure latency and cache behavior, and integrate results with the application. |
| Reliability and integration | Sunny Gupta, Priya Ranjan, Sharad Nailwal | Resolve failures across chat, tools, settings, memory and handover; verify the complete demo flow. |
| Guardrails | Sharad Nailwal, with Sunny Gupta and Priya Ranjan | Validate tool inputs, enforce setting approvals and manager isolation, and handle untrusted input. |

## Technology stack

| Component | Implementation |
| --- | --- |
| Application | Python 3.13 and Gradio |
| Database and retrieval | PostgreSQL, SQLAlchemy and pgvector cosine search |
| Migrations | Alembic through `python cli.py migrate`, locally or as the Railway pre-deploy step |
| Embeddings | Jina API, `jina-embeddings-v5-text-nano` |
| Answer generation | OpenRouter, `nvidia/nemotron-3-super-120b-a12b:free` |
| Chunking | Markdown sections by default; optional fixed-size character windows |
| Scenarios | YAML starting snapshots: normal, backlog and rain; current data stored in PostgreSQL |
| Dependencies | uv, with versions locked in `backend/uv.lock` |
| Deployment | Railway with Railpack; Gradio serves the application directly |

## Product scope

DispatchDesk combines playbook retrieval with saved conversation history and
read-only tools for operational snapshots and hourly metrics. Manager settings,
personalization, summaries, alerts, handover and daily review support the chat
workflow. Settings proposals require manager confirmation. Operational tools
read data and do not execute dispatch actions.

See the [README](../README.md) for setup and implemented behavior,
[requirements](initial/requirements.md) for the product requirements, and
[initial task plan](initial/tasks.md) for the original project milestones.
