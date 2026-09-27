# DispatchDesk team and technical plan

## Team coordination

Tasks will be taken up by groups of 2–3 people. The people responsible for each task, including its reviewer, will be decided as work progresses and recorded here before that task starts. No permanent workstream ownership has been assigned yet.

| Task(s) | People working on it | Reviewer | Status |
| --- | --- | --- | --- |
| Week 1 tasks 1–11 | To be assigned task by task | To be assigned | Not recorded |

Each team member should confirm they have read [`requirements.md`](../requirements.md) before taking a task. Record confirmations when names are known.

| Team member | Requirements read (date) |
| --- | --- |
| To be added | Pending |

## Agreed stack

| Component | Choice | Week 1 use |
| --- | --- | --- |
| Backend | Python + Flask | API and orchestration for retrieval and chat |
| Frontend | Gradio | Manager chat UI |
| Database and vector search | PostgreSQL + pgvector | Store corpus chunks, metadata, and embeddings |
| Chat model | Groq-hosted `openai/gpt-oss-120b` | Generate answers from retrieved passages |
| Python package manager | `uv` | Manage dependencies and run commands |
| Embedding model | Candidate: `BAAI/bge-small-en-v1.5` via `sentence-transformers` | Local English-language embeddings; confirm with the Week 1 retrieval test |

`BAAI/bge-small-en-v1.5` is a practical starting point for the English SOP corpus because it can run locally and is designed for text retrieval. Embed the documents and queries with the same model. Test the required rain-delay question and inspect whether the delay-diagnosis and rain-playbook passages appear in the top three results before treating the choice as final. If the corpus or manager queries need multiple languages, revisit the model choice. See the [model card](https://huggingface.co/BAAI/bge-small-en-v1.5) and [Groq model documentation](https://console.groq.com/docs/model/openai/gpt-oss-120b).

## Week 1 scope

The Week 1 prototype retrieves policy and playbook passages and explains them in Gradio. Live dispatch tools, persistent manager preferences, and the formal guardrail layer are scheduled for later weeks in [`tasks.md`](../tasks.md).

Until live tools are connected, the assistant must not claim to know the current queue, rider state, SLA, or ETA. Operational actions are proposals only, and the assistant must not recommend unsafe rider behavior. A Week 1 demo question should use a clearly labeled example scenario or ask for general policy guidance.

## Working agreements to finish during setup

- Record the repository URL and branch workflow in the project README.
- Record each task's assigned 2–3 people and reviewer in the table above when work begins.
- Confirm each member's requirements review in the table above.
- Evaluate the embedding candidate using the Week 1 retrieval acceptance test before locking the model and pgvector dimension.
