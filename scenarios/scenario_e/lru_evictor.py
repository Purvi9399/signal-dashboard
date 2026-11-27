"""
LRU (Least Recently Used) evictor — evicts the least recently accessed
entries when the cache store exceeds its max size.

Works with CacheStore and TTLIndex.
"""

from cache_store import CacheStore


class LRUEvictor:
    """Evicts least recently used entries when the cache is full."""

    def __init__(self, store: CacheStore, ttl_index=None):
        self._store = store
        self._ttl_index = ttl_index
        self._eviction_count = 0
        self._eviction_log: list[str] = []

    def evict_if_needed(self) -> list[str]:
        """Evict LRU entries if the store exceeds max size.

        Returns list of evicted keys.
        """
        evicted = []

        while self._store.size() > self._store.max_size:
            entries = self._store.entries()
            if not entries:
                break
            lru_entry = min(entries, key=lambda e: e.last_accessed)

            self._store.delete(lru_entry.key)
            evicted.append(lru_entry.key)
            self._eviction_count += 1
            self._eviction_log.append(lru_entry.key)

        return evicted

    @property
    def total_evictions(self) -> int:
        return self._eviction_count

    @property
    def recent_evictions(self) -> list[str]:
        return list(self._eviction_log[-20:])
