# DispatchDesk team

| Group | Team members | Tasks |
| --- | --- | --- |
| 1 : RAG and 6-pager | Vishnu Mohan, Ravisekhar R | Prepare the policy corpus; implement chunking, Jina embeddings, document ingestion, pgvector retrieval, and Groq answer generation; validate retrieval and grounded responses; write and maintain the 6-pager. |
| 2 : Setup and integration | Sunny Gupta, Priya Ranjan, Sharad Nailwal | Set up the repository, dependencies, and environment configuration; implement database models, migrations, and scenario loading; integrate services with the Gradio UI; configure Railway deployment and CI; write the README and PR/FAQ. Implement the planned dispatch tools/MCP, preference memory, action approval, guardrails, caching, observability, and end-to-end evaluation; prepare the demo. |

Each group shares its listed tasks and coordinates integration and review with the other group.

## Current stack

| Component | Implementation |
| --- | --- |
| Application | Python 3.13 and Gradio |
| Database and retrieval | PostgreSQL, SQLAlchemy, and pgvector cosine search |
| Migrations | Alembic; run manually from a local machine |
| Embeddings | Jina API, `jina-embeddings-v5-text-nano` |
| Answer generation | Groq, `openai/gpt-oss-20b` |
| Chunking | Markdown sections by default; optional fixed-size character windows |
| Scenario definitions | YAML starting snapshots: normal, backlog, rain |
| Dependencies | uv; locked versions in `backend/uv.lock` |
| Deployment | Railway with Railpack; Gradio serves the application directly |

## Current scope

The assistant retrieves playbook passages and answers policy questions with conversation history in the current session. Demo tools load synthetic scenarios into PostgreSQL and inspect the latest saved rows. Operational rows are not part of the chat pipeline.

Operational tools, persistent store preferences, simulated action approval, a separate guardrail layer, caching, and observability dashboards remain planned work. The [requirements](initial/requirements.md) define the intended behavior; the [source task plan](initial/tasks.md) records the project milestones. Setup instructions are in the [README](../README.md).
