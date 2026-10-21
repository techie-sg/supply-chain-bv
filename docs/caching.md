# Caching (Week 3, tasks 22, 23, 25)

Two in-memory caches, both per process. Live dispatch status, order details and LLM answers are **never** cached. Personalization and preferences are not included in cache keys. They are available to the model, which chooses the policy search query.

## What is cached

| Cache | Key | Value | TTL |
|---|---|---|---|
| Retrieval (`service/rag.py`) | sha256 of `(embedding_model, task_adapters, top_k, normalized retrieval query)` | the chunk list from `queries.vector_store.retrieve` (`chunk_id`, `content`, `similarity`), stored as a tuple | `RETRIEVAL_CACHE_TTL_S`, default 600 s; 0 turns it off |
| Historical metrics (`service/tools.py`, `get_delivery_metrics`) | sha256 of `(store_id, date, start_hour, end_hour)` | the tool's result dict | `METRICS_CACHE_TTL_S`, default 86400 s for a finished period; 60 s while the period still includes the current hour |

Both are capped by `*_CACHE_MAX_ENTRIES` (default 256); the oldest entry is evicted first.

### Retrieval key details
- The query text is the self-contained `query` supplied by the model to `search_playbook`. The model resolves follow-ups from history and can search using facts fetched by dispatch tools. No embedding or vector search happens until the tool is called. `retrieval_query(question, history)` remains the fixed-query baseline for offline retrieval evaluations.
- Normalization: strip, casefold, collapse whitespace, so "Why slow in rain?" and " why SLOW in  rain? " share an entry.
- Model, adapter flag and `top_k` are in the key so a different embedding setup never reuses vectors.
- Keys are hashes, so raw question text never appears in a key (relevant once Redis is used).
- A hit skips both the Jina `embed_query` call and the pgvector query. Empty results are never cached.

### Metrics rules
- Only successful results are cached; errors (invalid period, unknown store, no data) are always recomputed.
- `cache_hit` is **not** part of the result sent to the model or of the cached value; it is recorded on the trace only.
- Loading or reloading a scenario retires the app process's metrics cache after the database reset succeeds. The next metrics request reads the new rows. In-flight requests cannot repopulate the replacement cache with old results. Policy retrieval entries remain available.

## Trace and badges
- `AnswerTrace.retrieval = {cache: hit|miss|off, chunks: [chunk_id], ms}` is shown in the agent-trace panel, e.g. `Retrieval: cache hit · 3 chunks · 0.4 ms` or `cache miss (vector DB) · 3 chunks · 31 ms`.
- `ToolCallTrace.cache_hit` and `latency_ms` (metrics tool only) render a green `cache hit · 2 ms` / grey `cache miss · 38 ms` badge on the tool line. Live-status entries are unchanged.
- `search_playbook` appears in the tool trace alongside dispatch and setting calls. Failed searches record an error and let the model continue with other tools.
- `RAGService` publishes the retrieval trace through a context variable that `service/conversations._answer` reads once after `answer_question` returns. Several searches in one answer combine unique chunk IDs and elapsed time; any cache miss makes the combined cache status a miss. Answers that do not search have no retrieval trace.
- Logs: `RAG answer completed` has `retrieval_cache=hit|miss|off`; `Tool call` has `cache_hit` and `duration_ms`.
- Older stored messages without these keys display exactly as before.

## Code map
- `service/cache.py`: `TTLCache` (lock, injectable clock, eviction, `hits`/`misses`, `stats()`), `cache_key`, lazy singletons `retrieval_cache()` / `metrics_cache()`, `invalidate_metrics_cache()` for scenario resets, `reset_caches()` for both caches. A `Cache` Protocol is the seam for Redis.
- `config.py`: `retrieval_cache_ttl_s`, `retrieval_cache_max_entries`, `metrics_cache_ttl_s`, `metrics_cache_max_entries`.
- `domain/chat.py`: `RetrievalTrace`; optional `ToolCallTrace.cache_hit/latency_ms`.
- `ui/chat.py`, `ui/gradio_app.css`: retrieval line and badges.
- Tests: `tests/test_cache.py`, `test_rag.py`, `test_tools.py`, `test_gradio_app.py`. `tests/conftest.py` resets the caches around every test.

## Known limits
- **Staleness after re-ingest:** ingestion is a separate process and cannot clear the app's memory. Old chunks can be served for up to the retrieval TTL (or until restart).
- **Per process:** each worker has its own cache; hit rates drop with more workers and a restart empties it.
- **Redis later:** implement the `Cache` Protocol (`enabled`, `get`, `set(key, value, ttl)`), serialize values as JSON, and add a corpus version to the retrieval key (or flush on ingest) so re-ingestion invalidates it.
- The metrics TTL treats "period end is in the past (Asia/Kolkata)" as final. Scenario loads clear the app process's metrics cache; manual database edits or resets from another process can stay cached up to 24 h.
- Not built: semantic (similarity-based) caching, which adds a second vector index, a tunable threshold and harder invalidation.

## Verify
1. `cd backend && uv run pytest`
2. Run the app and ask the same playbook question twice: the trace shows `cache miss (vector DB)` then `cache hit` with lower ms.
3. Ask the same historical-metrics question twice (a finished period): `cache miss` then `cache hit` badge.
4. `RETRIEVAL_CACHE_TTL_S=5`, wait 5 s, ask again: miss.

## Latency evidence (task 23)
Measured 2026-10-15 with `cd backend && uv run python -m evals.cache_latency --queries 65 --date 2026-10-07` (raw numbers in `backend/evals/cache-latency.csv`). Local pgvector, Jina `jina-embeddings-v5-text-nano`, default TTLs, single process. Retrieval: 65 corpus questions, each asked once cold then 3 more times warm (62 cold calls observed, 186 warm calls); metrics: one 8–10pm period asked once cold then 3 more times.

| Path | Cold (miss) p50/p75 | Warm (hit) p50/p75 | Speedup p50/p75 | Hit rate |
|---|---|---|---|---|
| Retrieval (Jina embed + pgvector) | 523.33 / 527.12 ms | 0.0 / 0.01 ms | 114,165x / 104,333x | 75% (186 of 248) |
| `get_delivery_metrics` | 7.25 / 7.25 ms | 0.01 / 0.02 ms | 1,074x / 472x | 75% (3 of 4) |
| `get_live_dispatch_status` (never cached) | 25.39 / 25.39 ms | 14.8 / 16.05 ms | 1.7x / 1.6x | n/a |

- The 75% hit rate is set by the script (1 miss then 3 repeats). It is not a production hit rate, which depends on how often managers repeat questions.
- Retrieval dominates: nearly all of the ~520 ms is the Jina network call (~500 ms in the logs); the database part is small.
- Retrieval timing is stable (p50 ≈ p75), indicating consistent network latency. Warm cache latency is sub-millisecond.
- The live-status first call is slower than subsequent calls due to connection warm-up, not cache effect.
- Timings are from one local run, not repeated across runs.
