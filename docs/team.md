# DispatchDesk team

| Team member | Responsibility |
| --- | --- |
| Sunny Gupta | Remaining project work with Priya Ranjan and Sharad |
| Vishnu Mohan | RAG pipeline and 6-pager with Ravisekhar |
| Ravisekhar R | RAG pipeline and 6-pager with Vishnu |
| Priya Ranjan | Remaining project work with Sunny and Sharad |
| Sharad Nailwal | Remaining project work with Sunny and Priya Ranjan |

Sunny, Priya Ranjan, and Sharad share the work outside RAG and the 6-pager, including the application, scenario data, deployment, and the remaining planned tools, memory, guardrails, and observability. Individual task assignments are agreed within that group.

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
