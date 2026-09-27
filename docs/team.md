# DispatchDesk team and stack

Tasks will be handled by groups of 2–3 people. Assignments will be decided as work progresses and recorded in the [task tracker](task-tracker.md), along with a reviewer and completion evidence. Responsibilities are assigned per task.

Each team member should read the [requirements](requirements.md) and understand Karthik's persona and objective. Add each person's name and dated confirmation below when available.

| Team member | Requirements read on |
| --- | --- |
| Roster pending | Pending |

The agreed technical choices are:

| Component | Choice |
| --- | --- |
| Backend | Python + Flask |
| Frontend | Gradio |
| Database and vector search | PostgreSQL + pgvector |
| Chat model | Groq-hosted `openai/gpt-oss-120b` |
| Package manager | `uv` |
| Embedding model | Pending validation; recommended candidate: `BAAI/bge-small-en-v1.5` via `sentence-transformers` |

The embedding candidate is intended for an English corpus and can run locally. Confirm the choice through retrieval evaluation before finalizing it. The [model card](https://huggingface.co/BAAI/bge-small-en-v1.5) documents its use; [Groq's model documentation](https://console.groq.com/docs/model/openai/gpt-oss-120b) provides the chat model identifier.

The [source task plan](tasks.md) defines milestones and acceptance criteria. The [working tracker](task-tracker.md) records assignments, progress, and review status.
