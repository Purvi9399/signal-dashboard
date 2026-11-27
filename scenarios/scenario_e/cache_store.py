"""
In-memory cache store — the storage layer.

Provides a simple key-value store with metadata tracking. The TTL and LRU
eviction logic lives in separate modules that compose with this store.
"""

import time
from typing import Any, Optional
from dataclasses import dataclass, field


@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)
    access_count: int = 0
    ttl_s: Optional[float] = None

    @property
    def is_expired(self) -> bool:
        if self.ttl_s is None:
            return False
        return (time.monotonic() - self.created_at) > self.ttl_s

    def touch(self):
        self.last_accessed = time.monotonic()
        self.access_count += 1


class CacheStore:
    """Simple key-value cache store."""

    def __init__(self, max_size: int = 100):
        self._max_size = max_size
        self._data: dict[str, CacheEntry] = {}

    def put(self, key: str, value: Any, ttl_s: Optional[float] = None) -> CacheEntry:
        entry = CacheEntry(key=key, value=value, ttl_s=ttl_s)
        self._data[key] = entry
        return entry

    def get(self, key: str) -> Optional[CacheEntry]:
        entry = self._data.get(key)
        if entry is not None:
            entry.touch()
        return entry

    def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    def has(self, key: str) -> bool:
        return key in self._data

    def size(self) -> int:
        return len(self._data)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def entries(self) -> list[CacheEntry]:
        return list(self._data.values())

    @property
    def max_size(self) -> int:
        return self._max_size
