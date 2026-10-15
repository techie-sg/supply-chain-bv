"""Small in-memory TTL caches for retrieval and historical metrics."""

import hashlib
import json
from collections.abc import Callable, Hashable
from threading import Lock
from time import monotonic
from typing import Any, Protocol

from config import get_settings


class Cache(Protocol):
    """What callers need; a Redis-backed class can replace the in-memory one."""

    @property
    def enabled(self) -> bool: ...

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl: float | None = None) -> None: ...


class TTLCache:
    """Thread-safe, oldest-entry eviction. Values are never None.

    shortcut: per-process only, so ingestion (a separate process) cannot clear it;
    the TTL bounds staleness. Use Redis with a corpus version in the key to share
    or invalidate across processes.
    """

    def __init__(
        self,
        ttl: float,
        max_entries: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0 and self.max_entries > 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > self._clock():
                self.hits += 1
                return entry[1]
            self._entries.pop(key, None)
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = self.ttl if ttl is None else ttl
        if not self.enabled or ttl <= 0:
            return
        with self._lock:
            self._entries.pop(key, None)
            while len(self._entries) >= self.max_entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = self.misses = 0

    def stats(self) -> dict[str, float]:
        with self._lock:
            lookups = self.hits + self.misses
            return {
                "hit_count": self.hits,
                "miss_count": self.misses,
                "hit_rate": round(self.hits / lookups, 3) if lookups else 0.0,
                "size": len(self._entries),
            }


def cache_key(*parts: Any) -> str:
    """Hash so raw question text is never used as a key (Redis keys, logs)."""
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


_retrieval: TTLCache | None = None
_metrics: TTLCache | None = None


def retrieval_cache() -> TTLCache:
    global _retrieval
    if _retrieval is None:
        settings = get_settings()
        _retrieval = TTLCache(
            settings.retrieval_cache_ttl_s,
            settings.retrieval_cache_max_entries,
        )
    return _retrieval


def metrics_cache() -> TTLCache:
    global _metrics
    if _metrics is None:
        settings = get_settings()
        _metrics = TTLCache(
            settings.metrics_cache_ttl_s,
            settings.metrics_cache_max_entries,
        )
    return _metrics


def reset_caches() -> None:
    """Drop both singletons (tests, or after changing cache settings)."""
    global _retrieval, _metrics
    _retrieval = _metrics = None
