"""
TTL (Time-To-Live) index — tracks expiration times for cache entries.

Maintains a sorted index of (expiry_time, key) so expired entries can be
reaped efficiently. Works with CacheStore and LRUEvictor.
"""

import time
import bisect
from typing import Optional

from cache_store import CacheStore


class TTLIndex:
    """Sorted index of cache entry expiration times."""

    def __init__(self, store: CacheStore):
        self._store = store
        # Sorted list of (expiry_time, key)
        self._expirations: list[tuple[float, str]] = []

    def register(self, key: str, ttl_s: float):
        """Register a key's expiration time."""
        expiry = time.monotonic() + ttl_s
        bisect.insort(self._expirations, (expiry, key))

    def unregister(self, key: str):
        """Remove a key from the TTL index."""
        self._expirations = [(t, k) for t, k in self._expirations if k != key]

    def reap_expired(self) -> int:
        """Remove all expired entries from the store. Returns count reaped."""
        now = time.monotonic()
        reaped = 0
        new_expirations = []

        for expiry, key in self._expirations:
            if expiry <= now:
                self._store.delete(key)
                reaped += 1
            else:
                new_expirations.append((expiry, key))

        self._expirations = new_expirations
        return reaped

    def next_expiry(self) -> Optional[float]:
        """Seconds until the next entry expires, or None."""
        if not self._expirations:
            return None
        return max(0, self._expirations[0][0] - time.monotonic())

    def pending_count(self) -> int:
        """How many entries have registered TTLs."""
        return len(self._expirations)
