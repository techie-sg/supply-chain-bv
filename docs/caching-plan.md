# Caching Plan: Tasks 22, 23, and Cache Badges (Task 25)

*Week 3 — DispatchDesk*

---

## 1. Caching Strategies for Agentic RAGs

In an agentic RAG system there are several layers where you can cache. Below are the two relevant ones for this project.

> **Note on Layer A (Query Embedding Cache):** A common third layer caches the embedding vector itself, keyed on the query text. However, if Layer B is keyed on query text (our approach), a repeated identical question hits Layer B first and the embedding step is never reached — making Layer A redundant for this pipeline. Layer A is only useful when the embedding is needed before the cache lookup (e.g., Layer B keyed on vector hash), or when the same embedding feeds multiple downstream tasks. We skip it here.

### Layer B — Retrieval Cache (Vector Search Results)
**What it caches:** The top-k document chunks returned by the pgvector cosine search for a given query embedding.

**Why it helps:** The corpus changes only when you re-ingest. Until that happens, the same embedding always returns the same chunks. The Postgres round-trip is ~10–30 ms; on repeated queries in the same session it adds up.

**Key:** Hash of the serialized embedding vector (or the query text, which is easier to hash).  
**TTL:** Medium-to-long (e.g., 1 hour). You'd bust it manually if you re-ingest.  
**Gotcha:** The retrieval query can include conversation history (see `retrieval_query()` in `service/rag.py:24`). For history-aware follow-ups the cache key must include the history, which reduces hit rate. Simpler to cache only standalone queries — or accept the lower hit rate and key on the full retrieval-query string.

**Example:**

| | First call | Second call (same question) |
|---|---|---|
| **Input** | query text `"why did SLA drop last night?"` + `top_k=3` | same question, same top_k |
| **Cache key** | `("why did sla drop last night?", 3)` | `("why did sla drop last night?", 3)` |
| **Cache value stored** | `[{"chunk_id": "sop#4", "content": "During peak hours...", "similarity": 0.91}, ...]` (3 chunks) | — already in cache |
| **What happens** | MISS → pgvector cosine search runs (~20 ms) → stores the 3 chunks | HIT → returns stored chunks immediately (~0 ms), Postgres is never queried |

---

### Layer C — Tool Result Cache (Historical Metrics)
**What it caches:** The return value of `get_delivery_metrics(store_id, date, start_hour, end_hour)`.

**Why it helps:** Historical data never changes. The current implementation hits the database for every single call, including repeated identical queries in the same session (e.g., two questions about "last Tuesday 8–10 pm").

**Key:** `(store_id, date, start_hour, end_hour)` — exact tuple.  
**TTL:** Very long or infinite (data is immutable).  
**Evidence for Task 22:** This is the clearest "cache miss → cache hit" demo: ask the same historical-metrics question twice; the second call should show a cache hit badge and near-zero latency.

**Example:**

| | First call | Second call (same period) |
|---|---|---|
| **Input** | `get_delivery_metrics("store_bng_01", "2026-10-08", 20, 22)` | `get_delivery_metrics("store_bng_01", "2026-10-08", 20, 22)` |
| **Cache key** | `("store_bng_01", "2026-10-08", 20, 22)` | `("store_bng_01", "2026-10-08", 20, 22)` |
| **Cache value stored** | Full metrics dict: `{"hourly": [...], "period_summary": {...}, "cache_hit": False, ...}` | — already in cache |
| **What happens** | MISS → SQL query runs (~15 ms) → stores result dict, `cache_hit=False` returned to UI | HIT → returns stored dict immediately (~0 ms), `cache_hit=True` returned to UI → green badge shown |

---

## 2. What's Best for This Use Case

| Layer | Cache it? | TTL | Reason |
|---|---|---|---|
| A — Query Embedding | **No** | — | Redundant when Layer B is keyed on query text; B hits before A is ever needed |
| B — Retrieval (vector search) | **Yes** | 60 min | Corpus is static between ingestion runs |
| C — Historical Metrics Tool | **Yes** | Infinite (or 24 h) | Historical data never mutates |

**Important limitation — exact-match only:** Both caches use the query string as the key. A differently-worded question (`"what caused deliveries to slow down yesterday?"` vs `"why did SLA drop last night?"`) is a complete miss even though both retrieve the same chunks. Handling semantically similar queries requires a **semantic cache** (ANN search over previously cached query embeddings, e.g. `GPTCache`) — meaningful added complexity that is out of scope for this demo.

**Chosen library: `cachetools`**

The app is deployed on Railway as a single Gradio container. Gradio launches a single-process uvicorn server by default (one worker unless you explicitly set `--workers N`). With one process, every request shares the same in-process memory — `cachetools` works without any extra infrastructure.

**Why not Redis or Memcached?**  
Redis/Memcached are the right choice when: (a) you run multiple workers or Railway replicas and need a shared cache, or (b) you need the cache to survive a redeploy/restart. Neither applies here:

- Single Railway service, single Gradio worker — no cross-process sharing needed.
- Cache entries (an embedding vector, a DB query result) are cheap to rebuild on a cold start — a warm cache across deploys isn't worth the complexity.
- Redis on Railway is a paid add-on, adds a second service to manage, and requires connection handling and error paths in the code.

If the project moves to multiple workers (e.g., `gunicorn -w 4`), the switch to Redis would be straightforward — the `CacheStore` wrapper abstracts the backing store, so only its constructor changes.

---

## 3. Implementation Plan

### 3.1 Files to Create

| File | Purpose |
|---|---|
| `backend/service/cache.py` | A thin `CacheStore` wrapper around `cachetools` that tracks hit/miss counts and exposes them for metrics (Task 23) |

### 3.2 Files to Modify

| File | Change |
|---|---|
| `backend/domain/chat.py` | Add `cache_hit: bool \| None` field to `ToolCallTrace` typed dict |
| `backend/service/rag.py` | Add retrieval cache (Layer B) |
| `backend/service/tools.py` | Add metrics cache (Layer C); propagate `cache_hit` into `ToolCallTrace` |
| `backend/ui/chat.py` | Render a cache hit/miss badge in `_tool_line()` (Task 25 partial) |
| `backend/config.py` | Add `cache_ttl_retrieval_sec` setting so the retrieval TTL is configurable via `.env` |

### 3.3 Step-by-Step Tasks

---

#### Task 22a — `service/cache.py`: `CacheStore`

Create a minimal wrapper:

```python
# pseudo-code
class CacheStore:
    def __init__(self, maxsize, ttl):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self.hits = 0
        self.misses = 0

    def get(self, key) -> tuple[Any, bool]:
        """Returns (value, cache_hit). cache_hit=False means miss."""
        ...

    def set(self, key, value) -> None: ...

    def stats(self) -> dict:
        """hit_count, miss_count, hit_rate, size"""
        ...
```

A module-level singleton per cache type (retrieval, metrics) is the simplest pattern. Singletons are reset on process restart, which is fine for a demo.

---

#### Task 22b — Retrieval cache in `service/rag.py`

In `RAGService.answer_question`:
- Build the query text first (already done via `retrieval_query()`).
- Check the retrieval cache keyed on `(query_text, top_k)` before embedding or querying Postgres.
- On miss: embed and retrieve as today, then store the chunks.
- On hit: skip both the Jina API call and the pgvector search entirely.

```python
query_text = retrieval_query(question, history)
chunks, ret_hit = RETRIEVAL_CACHE.get((query_text, top_k))
if not ret_hit:
    embedding = self.embedding_service.embed_query(query_text)
    chunks = retrieve(query_embedding=embedding, match_count=top_k)
    RETRIEVAL_CACHE.set((query_text, top_k), chunks)
```

Log `retrieval_cache_hit` in the existing `logger.info("RAG answer completed", ...)` call.

---

#### Task 22c — Metrics tool cache in `service/tools.py`

In `get_delivery_metrics`:
- Before calling `read_delivery_metrics(...)`, check the metrics cache keyed on `(store_id, date, start_hour, end_hour)`.
- On miss: read from DB and store.
- Return a `_cache_hit` field in the response dict so `_record_call` can pick it up.

The existing `_invoke` helper already `json.dumps` the result — the `cache_hit` field just rides along in the dict. `_record_call` reads `data.get("cache_hit")` and stores it in the `ToolCallTrace`.

---

#### Task 22d — `domain/chat.py`: update `ToolCallTrace`

```python
class ToolCallTrace(TypedDict, total=False):
    tool: str
    arguments: dict
    error: str | None
    as_of: str | None
    stale: bool
    cache_hit: bool | None   # new
```

---

#### Task 22e — `_record_call` in `service/tools.py`

Read `data.get("cache_hit")` and add it to the trace entry.

```python
trace.append({
    ...
    "cache_hit": data.get("cache_hit"),   # present only for metrics tool
})
```

---

#### Task 23 — Latency measurement and reporting

The timing is already done in `_record_call` (`perf_counter` wraps the tool call). Add:

1. A `latency_ms` field to `ToolCallTrace` (alongside `cache_hit`).
2. Log it in the existing `logger.info("Tool call", ...)` call.
3. Add a `cache_stats()` function to `service/cache.py` that returns a summary dict — used by the metrics dashboard in Task 31 and for the committed evidence file.

**Evidence file:** After running the same historical-metrics query twice, commit `docs/cache-latency.md` with before/after numbers (the log lines make this easy to capture).

---

#### Task 25 (cache badge) — `ui/chat.py`

In `_tool_line(call)`:

```python
if call.get("cache_hit") is True:
    text += ' <span class="badge cache-hit">cache hit</span>'
elif call.get("cache_hit") is False:
    text += ' <span class="badge cache-miss">cache miss</span>'
```

The badge rides inside the existing `<li>` in `_trace_html`. No new Gradio components needed — just HTML/CSS inside the existing expandable agent-trace panel.

Add the badge styles to the existing theme CSS (two rules: `.cache-hit` green, `.cache-miss` grey).

---

### 3.4 Tests to Add / Update

| Test file | What to test |
|---|---|
| `tests/test_rag.py` | Cache hit on second call with same query; cache miss on first; `retrieval_cache_hit` logged correctly; differently-worded question produces a miss |
| `tests/test_tools.py` | `get_delivery_metrics` returns `cache_hit=False` on first call, `cache_hit=True` on second identical call |
| `tests/test_gradio_app.py` or `tests/test_chat_ui.py` | `_tool_line` renders `cache-hit` badge when `cache_hit=True`, `cache-miss` badge when `False`, and neither when `None` |

---

### 3.5 Definition of Done

**Task 22:**
- [ ] `service/cache.py` exists with `CacheStore` and per-type singletons
- [ ] Repeated identical historical-metrics call hits cache on second invocation
- [ ] Repeated identical RAG question uses cached embedding + chunks on second call
- [ ] `cache_hit` field is present in `ToolCallTrace` and propagated to the trace panel
- [ ] Log shows `cache_hit=False` then `cache_hit=True` for back-to-back calls

**Task 23:**
- [ ] `latency_ms` is logged per tool call
- [ ] `cache_stats()` can be called to get aggregate hit rate
- [ ] `docs/cache-latency.md` committed with before/after numbers for a cached vs. uncached metrics call

**Task 25 (cache badge):**
- [ ] Agent-trace panel shows a green "cache hit" badge on repeated historical-metrics queries
- [ ] Shows a grey "cache miss" badge on first call
- [ ] Screenshot committed as evidence

---

## 4. Open Questions / Decisions Needed

1. **Retrieval cache key includes conversation history?**  
   Pro: higher accuracy. Con: much lower hit rate.  
   *Recommendation: Key on query text only (no history), accept the lower hit rate for follow-ups. History-aware queries are already context-enriched by the retrieval query function.*

2. **Cache TTL for retrieval — 1 hour or per-process (no TTL)?**  
   A per-process `LRUCache` (no TTL) would have the highest hit rate within a single demo session.  
   *Recommendation: `LRUCache(maxsize=256)` for retrieval — TTL adds little value when the process restarts frequently anyway.*

3. **Where to surface latency numbers in the UI?**  
   The badge can show `cache hit · 2 ms` vs `cache miss · 38 ms` if we add the latency to `ToolCallTrace`.  
   *Recommendation: Add it — it makes Task 23's demo more vivid.*

4. **Single `cache.py` module vs. cache logic inline in each service?**  
   *Recommendation: Single `service/cache.py` — keeps the `stats()` aggregation in one place, and the TTL config in one place.*
