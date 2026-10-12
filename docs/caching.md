# Caching (Week 3, tasks 22, 23, 25)

Two in-memory caches, both per process. Live dispatch status, order details and LLM answers are **never** cached. Personalization and preferences enter the prompt after retrieval, so they never affect a cache key.

## What is cached

| Cache | Key | Value | TTL |
|---|---|---|---|
| Retrieval (`service/rag.py`) | sha256 of `(embedding_model, task_adapters, top_k, normalized retrieval query)` | the chunk list from `queries.vector_store.retrieve` (`chunk_id`, `content`, `similarity`), stored as a tuple | `RETRIEVAL_CACHE_TTL_S`, default 600 s; 0 turns it off |
| Historical metrics (`service/tools.py`, `get_delivery_metrics`) | sha256 of `(store_id, date, start_hour, end_hour)` | the tool's result dict | `METRICS_CACHE_TTL_S`, default 86400 s for a finished period; 60 s while the period still includes the current hour |

Both are capped by `*_CACHE_MAX_ENTRIES` (default 256); the oldest entry is evicted first.

### Retrieval key details
- The query text is `retrieval_query(question, history)`. Follow-ups already include the last exchange, so the key is history-aware (and follow-ups hit less often). Do not key on the bare question.
- Normalization: strip, casefold, collapse whitespace, so "Why slow in rain?" and " why SLOW in  rain? " share an entry.
- Model, adapter flag and `top_k` are in the key so a different embedding setup never reuses vectors.
- Keys are hashes, so raw question text never appears in a key (relevant once Redis is used).
- A hit skips both the Jina `embed_query` call and the pgvector query. Empty results are never cached.

### Metrics rules
- Only successful results are cached; errors (invalid period, unknown store, no data) are always recomputed.
- `cache_hit` is **not** part of the result sent to the model or of the cached value; it is recorded on the trace only.

## Trace and badges
- `AnswerTrace.retrieval = {cache: hit|miss|off, chunks: [chunk_id], ms}` is shown in the agent-trace panel, e.g. `Retrieval: cache hit · 3 chunks · 0.4 ms` or `cache miss (vector DB) · 3 chunks · 31 ms`.
- `ToolCallTrace.cache_hit` and `latency_ms` (metrics tool only) render a green `cache hit · 2 ms` / grey `cache miss · 38 ms` badge on the tool line. Live-status entries are unchanged.
- `RAGService` publishes the retrieval trace through a context variable that `service/conversations._answer` reads once after `answer_question` returns, so the `answer_question` signature did not change.
- Logs: `RAG answer completed` has `retrieval_cache=hit|miss|off`; `Tool call` has `cache_hit` and `duration_ms`.
- Older stored messages without these keys display exactly as before.

## Code map
- `service/cache.py`: `TTLCache` (lock, injectable clock, eviction, `hits`/`misses`, `stats()`), `cache_key`, lazy singletons `retrieval_cache()` / `metrics_cache()`, `reset_caches()`. A `Cache` Protocol is the seam for Redis.
- `config.py`: `retrieval_cache_ttl_s`, `retrieval_cache_max_entries`, `metrics_cache_ttl_s`, `metrics_cache_max_entries`.
- `domain/chat.py`: `RetrievalTrace`; optional `ToolCallTrace.cache_hit/latency_ms`.
- `ui/chat.py`, `ui/gradio_app.css`: retrieval line and badges.
- Tests: `tests/test_cache.py`, `test_rag.py`, `test_tools.py`, `test_gradio_app.py`. `tests/conftest.py` resets the caches around every test.

## Known limits
- **Staleness after re-ingest:** ingestion is a separate process and cannot clear the app's memory. Old chunks can be served for up to the retrieval TTL (or until restart).
- **Per process:** each worker has its own cache; hit rates drop with more workers and a restart empties it.
- **Redis later:** implement the `Cache` Protocol (`enabled`, `get`, `set(key, value, ttl)`), serialize values as JSON, and add a corpus version to the retrieval key (or flush on ingest) so re-ingestion invalidates it.
- The metrics TTL treats "period end is in the past (Asia/Kolkata)" as final; synthetic data that is edited after the fact stays cached up to 24 h.
- Not built: semantic (similarity-based) caching, which adds a second vector index, a tunable threshold and harder invalidation.

## Verify
1. `cd backend && uv run pytest`
2. Run the app and ask the same playbook question twice: the trace shows `cache miss (vector DB)` then `cache hit` with lower ms.
3. Ask the same historical-metrics question twice (a finished period): `cache miss` then `cache hit` badge.
4. `RETRIEVAL_CACHE_TTL_S=5`, wait 5 s, ask again: miss.

## Latency evidence (task 23)
Not recorded yet. Capture the `ms` values from the trace (or the `duration_ms` log lines) for a miss and a hit on the same question and add them here.
