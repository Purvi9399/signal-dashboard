"""Tests for the layered cache system. Run: pytest test_cache.py -v"""

import time
import pytest

from cache import Cache
from cache_store import CacheStore
from ttl_index import TTLIndex
from lru_evictor import LRUEvictor


# ---------------------------------------------------------------------------
# Helper to advance monotonic time in tests
# ---------------------------------------------------------------------------
class FakeClock:
    """Patches time.monotonic for deterministic TTL testing."""
    def __init__(self, start=1000.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake)
    return fake


# ---------------------------------------------------------------------------
# Basic tests (these pass)
# ---------------------------------------------------------------------------

class TestBasicCache:
    def test_put_and_get(self):
        cache = Cache(max_size=10)
        cache.put("k1", "value1")
        assert cache.get("k1") == "value1"

    def test_missing_key(self):
        cache = Cache(max_size=10)
        assert cache.get("nonexistent") is None

    def test_delete(self):
        cache = Cache(max_size=10)
        cache.put("k1", "value1")
        cache.delete("k1")
        assert cache.get("k1") is None

    def test_overwrite(self):
        cache = Cache(max_size=10)
        cache.put("k1", "v1")
        cache.put("k1", "v2")
        assert cache.get("k1") == "v2"


class TestTTLBasic:
    def test_entry_expires(self, clock):
        cache = Cache(max_size=10)
        cache.put("k1", "value", ttl_s=5.0)
        clock.advance(6.0)
        assert cache.get("k1") is None

    def test_entry_alive_before_ttl(self, clock):
        cache = Cache(max_size=10)
        cache.put("k1", "value", ttl_s=5.0)
        clock.advance(3.0)
        assert cache.get("k1") == "value"


# ---------------------------------------------------------------------------
# Tests that expose Bug 3 + Bug 1: ghost TTL entries after LRU eviction
# ---------------------------------------------------------------------------

class TestGhostEntries:
    def test_lru_eviction_cleans_ttl_index(self, clock):
        """When LRU evicts an entry, its TTL record must be removed.

        BUG 3: LRU evictor does not call ttl_index.unregister(),
        leaving ghost entries. When a new entry reuses the same key,
        the OLD TTL record causes premature expiration.
        """
        cache = Cache(max_size=3)

        # Fill the cache
        cache.put("a", 1, ttl_s=100.0)
        clock.advance(0.1)
        cache.put("b", 2, ttl_s=100.0)
        clock.advance(0.1)
        cache.put("c", 3, ttl_s=100.0)
        clock.advance(0.1)

        # Access "b" and "c" to make "a" the LRU
        cache.get("b")
        cache.get("c")
        clock.advance(0.1)

        # Adding "d" should evict "a" (LRU)
        cache.put("d", 4, ttl_s=100.0)

        # Now reuse key "a" with a fresh TTL
        clock.advance(0.1)
        cache.put("a", 10, ttl_s=100.0)

        # Advance time past the ORIGINAL "a"'s TTL but NOT the new one's
        clock.advance(99.5)

        # The new "a" should still be alive (its TTL is 100s from now)
        result = cache.get("a")
        assert result == 10, (
            f"New 'a' was prematurely expired by ghost TTL record. "
            f"Got {result} instead of 10"
        )

    def test_ttl_index_count_after_lru_eviction(self, clock):
        """TTL index should not accumulate ghost entries.

        BUG 1 + BUG 3: Ghost entries pile up in the TTL index because
        LRU eviction doesn't clean them. The pending_count grows
        unboundedly even though the store has bounded size.
        """
        cache = Cache(max_size=3)

        # Fill cache, then replace entries repeatedly
        for i in range(20):
            cache.put(f"key-{i}", i, ttl_s=50.0)
            clock.advance(0.1)

        stats = cache.stats
        # Store should have at most 3 entries
        assert stats["size"] <= 3

        # TTL index should track only live entries (at most 3)
        # BUG: It tracks all 20 (17 ghosts + 3 live)
        assert stats["ttl_pending"] <= stats["size"] + 1, (
            f"TTL index has {stats['ttl_pending']} entries but store "
            f"has only {stats['size']} — {stats['ttl_pending'] - stats['size']} ghosts"
        )


class TestReapAccuracy:
    def test_reap_count_excludes_ghosts(self, clock):
        """reap_expired should only count entries it actually removed.

        BUG 2: reap_expired counts ghost entries (already evicted by LRU)
        as reaped, inflating the count.
        """
        store = CacheStore(max_size=3)
        ttl_index = TTLIndex(store)

        # Add entries with short TTL
        store.put("x", 1, ttl_s=2.0)
        ttl_index.register("x", 2.0)
        store.put("y", 2, ttl_s=2.0)
        ttl_index.register("y", 2.0)

        # Manually remove "x" from store (simulating LRU eviction)
        # but leave it in the TTL index (the bug)
        store.delete("x")

        clock.advance(3.0)

        reaped = ttl_index.reap_expired()
        # Only "y" was actually in the store to be reaped
        assert reaped == 1, (
            f"reap_expired returned {reaped} but only 1 entry was "
            f"actually in the store"
        )


# ---------------------------------------------------------------------------
# Tests that expose Bug 4: off-by-one in LRU eviction
# ---------------------------------------------------------------------------

class TestEvictionBehavior:
    def test_eviction_makes_room(self, clock):
        """After eviction, the cache should have room for a new entry
        WITHOUT exceeding max_size.

        BUG 4: The while condition uses > instead of >=, so eviction
        only triggers when the store is OVER max_size. This means:
        1. put() adds entry → size becomes max_size + 1
        2. next put() triggers eviction → size goes to max_size
        3. put() adds entry → size becomes max_size + 1 again
        The cache oscillates at max_size + 1.
        """
        cache = Cache(max_size=3)

        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)  # Should evict one, keeping size at 3

        assert cache.size() <= 3, (
            f"Cache size is {cache.size()} after adding 4 entries "
            f"to a max_size=3 cache"
        )

    def test_no_excessive_eviction_churn(self, clock):
        """Putting N entries into a cache of size N should not trigger eviction.

        BUG 4: Because eviction doesn't make room properly, adding the
        Nth entry (at capacity) triggers eviction of the oldest entry,
        then the new entry makes it N again, then the next put evicts again.
        """
        cache = Cache(max_size=5)
        for i in range(5):
            cache.put(f"k{i}", i)
            clock.advance(0.1)

        evictions_before = cache.stats["evictions"]

        # Overwriting existing keys should NEVER trigger eviction
        for i in range(5):
            cache.put(f"k{i}", i * 10)

        evictions_after = cache.stats["evictions"]
        assert evictions_after == evictions_before, (
            f"Overwriting existing keys triggered {evictions_after - evictions_before} evictions"
        )

    def test_eviction_count_matches_expected(self, clock):
        """Adding max_size + N entries should evict exactly N entries.

        BUG 4: Off-by-one means more evictions than expected.
        """
        max_size = 5
        cache = Cache(max_size=max_size)

        # Fill the cache
        for i in range(max_size):
            cache.put(f"fill-{i}", i)
            clock.advance(0.1)

        # Add 3 more — should evict exactly 3
        for i in range(3):
            cache.put(f"extra-{i}", i)
            clock.advance(0.1)

        assert cache.stats["evictions"] == 3, (
            f"Expected 3 evictions, got {cache.stats['evictions']}"
        )
        assert cache.size() == max_size, (
            f"Expected cache size {max_size}, got {cache.size()}"
        )


# ---------------------------------------------------------------------------
# Integration test — all bugs interacting
# ---------------------------------------------------------------------------

class TestCacheIntegration:
    def test_realistic_workload(self, clock):
        """Simulate a real agent session caching tool results.

        This exercises all four bugs:
        - Bug 1: Ghost TTL entries from LRU eviction
        - Bug 2: Inflated reap count
        - Bug 3: Missing TTL cleanup on eviction
        - Bug 4: Off-by-one eviction
        """
        cache = Cache(max_size=5, default_ttl_s=30.0)

        # Phase 1: Fill cache with tool results
        tools = ["read_file", "search", "list_dir", "grep", "git_log"]
        for i, tool in enumerate(tools):
            cache.put(f"result:{tool}", {"data": f"output_{i}"})
            clock.advance(1.0)

        assert cache.size() == 5

        # Phase 2: Agent switches context — new tool calls evict old ones
        new_tools = ["write_file", "run_tests", "compile"]
        for tool in new_tools:
            cache.put(f"result:{tool}", {"data": f"new_{tool}"})
            clock.advance(1.0)

        # Phase 3: Agent circles back to earlier tools (cache miss expected)
        # Re-caches them with fresh TTL
        for tool in ["read_file", "search"]:
            cache.put(f"result:{tool}", {"data": f"refreshed_{tool}"})
            clock.advance(1.0)

        # Phase 4: Time passes near the original TTL
        clock.advance(25.0)

        # The refreshed entries should still be alive (their TTL is 30s
        # from when they were re-added, ~10s ago)
        read_result = cache.get("result:read_file")
        assert read_result is not None, (
            "Refreshed read_file result was prematurely expired"
        )
        assert read_result["data"] == "refreshed_read_file"

        search_result = cache.get("result:search")
        assert search_result is not None, (
            "Refreshed search result was prematurely expired"
        )

        # Stats should be consistent
        stats = cache.stats
        assert stats["ttl_pending"] <= stats["size"] + 1, (
            f"Ghost entries detected: {stats['ttl_pending']} TTL records "
            f"for {stats['size']} entries"
        )
