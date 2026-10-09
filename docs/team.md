# DispatchDesk team

Current workstream ownership confirmed by Sunny on October 5, 2026:

| Workstream | Owners | Scope |
| --- | --- | --- |
| 1. Retrieval evaluations | Ravi and Vishnu | Compare embedding models, chunk sizes and embedding/chunking approaches against a shared evaluation set. |
| 2. Two MCP tools | Ravi and Vishnu | Build the two agreed operational tools and define their input/output contracts for integration. |
| 3. Memory | Sunny, Sharad and Ranjan | Design persistent preferences and episodes, relevant recall, updates/deletion, isolation and concise alerts; preserve existing procedural guidance. |
| 4. Dreaming | Sunny, Sharad and Ranjan | Define and test a bounded reflection/consolidation process over recorded experience, with source evidence and reviewed changes. |

This ownership supersedes the earlier assignment of MCP work to the setup
group. Memory design is a new learning area for Sunny and requires dedicated
study as well as implementation. Detailed subtask allocation within each group
remains to be agreed. The four workstreams are not implemented milestones.

## Existing group responsibilities

| Group | Team members | Tasks |
| --- | --- | --- |
| 1 : RAG and 6-pager | Vishnu Mohan, Ravisekhar R | Prepare the policy corpus; implement chunking, Jina embeddings, document ingestion, pgvector retrieval, and Groq answer generation; validate retrieval and grounded responses; write and maintain the 6-pager. |
| 2 : Setup and integration | Sunny Gupta, Priya Ranjan, Sharad Nailwal | Set up the repository, dependencies, and environment configuration; implement database models, migrations, and scenario loading; integrate services with the Gradio UI; configure Railway deployment and CI; write the README and PR/FAQ. Own memory and dreaming under the current allocation above; coordinate integration of the other group's tools. Action approval, guardrails, caching, observability, end-to-end evaluation and demo scope require separate agreement. |

Each group shares its listed tasks and coordinates integration and review with the other group.

## Current stack

| Component | Implementation |
| --- | --- |
| Application | Python 3.13 and Gradio |
| Database and retrieval | PostgreSQL, SQLAlchemy, and pgvector cosine search |
| Migrations | Alembic; run manually from a local machine |
| Embeddings | Jina API, `jina-embeddings-v5-text-nano` |
| Answer generation | Groq, `openai/gpt-oss-120b` |
| Chunking | Markdown sections by default; optional fixed-size character windows |
| Scenario definitions | YAML starting snapshots: normal, backlog, rain |
| Dependencies | uv; locked versions in `backend/uv.lock` |
| Deployment | Railway with Railpack; Gradio serves the application directly |

## Current scope

The assistant retrieves playbook passages and answers policy questions with conversation history stored in PostgreSQL. Demo tools load synthetic scenarios into PostgreSQL and inspect the latest saved rows. Operational rows are not part of the chat pipeline.

Operational tools, persistent store preferences, simulated action approval, a separate guardrail layer, caching, and observability dashboards remain planned work. The [requirements](initial/requirements.md) define the intended behavior; the [source task plan](initial/tasks.md) records the project milestones. Setup instructions are in the [README](../README.md).
