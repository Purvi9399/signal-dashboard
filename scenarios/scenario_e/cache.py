"""
Cache — the public API that composes CacheStore, TTLIndex, and LRUEvictor.

This is the entry point for the caching system. It handles put/get/delete
and coordinates TTL registration and LRU eviction.
"""

import time
from typing import Any, Optional

from cache_store import CacheStore
from ttl_index import TTLIndex
from lru_evictor import LRUEvictor


class Cache:
    """In-memory cache with TTL and LRU eviction."""

    def __init__(self, max_size: int = 100, default_ttl_s: Optional[float] = None):
        self._store = CacheStore(max_size=max_size)
        self._ttl_index = TTLIndex(self._store)
        self._evictor = LRUEvictor(self._store, self._ttl_index)
        self._default_ttl_s = default_ttl_s
        self._hits = 0
        self._misses = 0

    def put(self, key: str, value: Any, ttl_s: Optional[float] = None):
        """Store a value in the cache."""
        ttl = ttl_s if ttl_s is not None else self._default_ttl_s

        # Evict if needed before adding
        self._evictor.evict_if_needed()

        entry = self._store.put(key, value, ttl_s=ttl)

        if ttl is not None:
            self._ttl_index.register(key, ttl)

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value. Returns None if missing or expired."""
        # Reap expired entries first
        self._ttl_index.reap_expired()

        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if entry.is_expired:
            self._store.delete(key)
            self._misses += 1
            return None

        self._hits += 1
        return entry.value

    def delete(self, key: str) -> bool:
        """Explicitly remove an entry."""
        self._ttl_index.unregister(key)
        return self._store.delete(key)

    def size(self) -> int:
        return self._store.size()

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict:
        return {
            "size": self._store.size(),
            "max_size": self._store.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 3),
            "evictions": self._evictor.total_evictions,
            "ttl_pending": self._ttl_index.pending_count(),
        }
