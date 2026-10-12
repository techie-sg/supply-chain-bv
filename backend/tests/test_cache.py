"""In-memory TTL cache behaviour."""

from threading import Thread

from service.cache import TTLCache, cache_key


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_hit_miss_and_stats() -> None:
    cache = TTLCache(ttl=10, max_entries=5)
    assert cache.get("a") is None
    cache.set("a", [1])
    assert cache.get("a") == [1]
    assert cache.stats() == {
        "hit_count": 1,
        "miss_count": 1,
        "hit_rate": 0.5,
        "size": 1,
    }


def test_entry_expires_after_ttl() -> None:
    clock = Clock()
    cache = TTLCache(ttl=10, max_entries=5, clock=clock)
    cache.set("a", 1)
    clock.now = 9.9
    assert cache.get("a") == 1
    clock.now = 10.1
    assert cache.get("a") is None


def test_per_entry_ttl_overrides_default() -> None:
    clock = Clock()
    cache = TTLCache(ttl=100, max_entries=5, clock=clock)
    cache.set("a", 1, ttl=5)
    clock.now = 6
    assert cache.get("a") is None


def test_oldest_entry_evicted_at_capacity() -> None:
    cache = TTLCache(ttl=10, max_entries=2)
    for key in "abc":
        cache.set(key, key)
    assert cache.get("a") is None
    assert cache.get("b") == "b"
    assert cache.get("c") == "c"


def test_zero_ttl_disables_the_cache() -> None:
    cache = TTLCache(ttl=0, max_entries=5)
    cache.set("a", 1)
    assert not cache.enabled
    assert cache.get("a") is None


def test_key_depends_on_every_part_and_hides_the_text() -> None:
    assert cache_key("m", 3, "why?") == cache_key("m", 3, "why?")
    assert cache_key("m", 3, "why?") != cache_key("m", 2, "why?")
    assert cache_key("m", 3, "why?") != cache_key("n", 3, "why?")
    assert "why" not in cache_key("m", 3, "why?")


def test_concurrent_use_does_not_corrupt_the_cache() -> None:
    cache = TTLCache(ttl=10, max_entries=50)

    def work(n: int) -> None:
        for i in range(200):
            cache.set(str(i % 60), n)
            cache.get(str(i % 60))

    threads = [Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert cache.stats()["size"] <= 50
