# DispatchDesk policy corpus

Seven PDF documents provide simulated dispatch policy for the RAG assistant. The PDFs are the source documents; ingestion extracts their text with [Docling](https://github.com/docling-project/docling). They are based on the [sample dispatch playbook](../../../../docs/initial/sample_data/dispatch_operations_playbook.pdf); thresholds are illustrative demo rules.

## Documents

| Document ID | File | Sections | Coverage |
| --- | --- | --- | --- |
| DD-SOP-001 | [Dispatch SOP](01-dispatch-sop.pdf) | 4 | Queue triage, priorities, proposals, preferences |
| DD-DIAG-001 | [Delay diagnosis](02-delay-diagnosis-guide.pdf) | 8 | Delivery stages, SLA interpretation, live and historical diagnosis |
| DD-BATCH-001 | [Batching and cold chain](03-batching-and-cold-chain.pdf) | 5 | Order count, geography, detour, items, frozen goods, age |
| DD-WEATHER-001 | [Rain and surge](04-rain-and-surge-playbook.pdf) | 5 | Capacity options, surge declarations, safety |
| DD-COMMS-001 | [Customer communication](05-customer-communication.pdf) | 4 | Honest estimates, delay notices, message drafts |
| DD-RIDER-001 | [Rider safety and hours](06-rider-safety-and-working-hours.pdf) | 5 | Safety, penalties, shifts, breaks, refusals |
| DD-QUICKREF-001 | [Quick reference](07-quick-reference.pdf) | 6 | Symptoms, first actions, escalation |

## Ingestion and retrieval

Run `uv run python -m service.ingestion` from `backend/`. `DoclingPdfParser` converts each PDF to Markdown with Docling's layout model (OCR off, since the PDFs are born-digital); the model downloads on first use. The configured default strategy then splits at section headings and produces **37 chunks**. The title and header fields before the first section supply metadata. Only `*.pdf` files are ingested, so this README is excluded. An optional fixed-size strategy produces a different count; 37 is not a runtime limit.

`CorpusService` attaches document ID, title, version, section, source path, and the PDF's file hash. Jina embeds the text; SQLAlchemy queries upsert it into `app.documents` and `app.document_chunks`. Stored chunk numbers are zero-based, and retrieved citations use the policy `doc_id#chunk_number`, such as `DD-BATCH-001#0`.

The assistant retrieves the nearest three passages by default using pgvector cosine distance. Follow-up retrieval includes the recent exchange, and Groq receives the full session history, retrieved passages, and the [system prompt](../prompts/dispatch_manager_system.md).

## Scope and sources

The corpus supplies rules, not live queue counts, rider states, weather observations, or ETAs. The current chat pipeline has no operational tools and does not receive scenario database rows. Tools and persistent store preferences described in the policy documents are intended interfaces for later project stages.

Hard safety and hours constraints take priority. Explicit batching, cold-chain, and communication rules constrain proposals. Future stored preferences may make these rules stricter; they cannot relax them. Assistant behavior is defined in [dispatch_manager_system.md](../prompts/dispatch_manager_system.md), which `RAGService` loads directly. Rules moved out of the corpus are included in that prompt. A separate guardrail layer is planned.

The normal, backlog, and rain [scenario files](../../scenario_data/) seed operational tables. Rain YAML includes synthetic candidate-route adjacency, detour assumptions, and illustrative ETA inputs. These are not verified map results, are not persisted in the four operational tables, and are not available to chat. A general zone adjacency map, recorded incentive cap, and store closing time remain undefined.

## Retrieval review

For the rain-delay question, inspect whether the top three passages include both delay-diagnosis and rain-playbook guidance. Other useful checks cover frozen-item batching, unsafe rider pressure, mandatory breaks, and the eight-minute queue boundary. A similarity score alone does not establish that every rule needed for an answer was retrieved.

## Recorded policy decisions

| Date | Decision | Affects |
| --- | --- | --- |
| 2026-09-28 | Batching age limit: do not newly batch at or beyond 480 seconds; delay notice also prepared at 480 seconds or later | DD-BATCH-001, DD-COMMS-001 |
| 2026-09-28 | Detour cap: require both ≤ ~0.8 km and ≤ ~2 min; reject if either exceeds or is unknown | DD-BATCH-001 |
| 2026-09-28 | Rain alone does not declare a surge; the manager must declare it | DD-WEATHER-001 |
| 2026-09-29 | Restored playbook content missing from v1.0 (SLA cliff, causes by stage, rain expectations, rider protections, incentive sign-off); added quick reference; moved assistant-behavior rules to the system prompt | All |

When policy changes, replace its PDF and record the decision here. Keep the document title, the `Document ID:` and `Version:` fields, and one heading per section, since chunking and metadata depend on Docling recovering them. After replacing a PDF, confirm the chunk count and section names from `CorpusService.load()`, then reingest and check affected retrieval queries. Keep policy thresholds consistent with the system prompt and any future code checks.
