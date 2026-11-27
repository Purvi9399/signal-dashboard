---
scenario: E — Layered Cache Invalidation
difficulty: expert (4 bugs across 3 modules, subtle interaction patterns)
expected_agent_behavior: must read and understand 4 files, trace data flow across modules
expected_cops_outcome: High read breadth (many files), hypothesis-driven debugging, Runtime Monitoring flags deep investigation pattern
---

# Architecture

Four files forming a layered cache:
- `cache_store.py` — simple key-value store (no bugs)
- `ttl_index.py` — tracks entry expiration times (Bug 1, Bug 2)
- `lru_evictor.py` — evicts least-recently-used entries (Bug 3, Bug 4)
- `cache.py` — public API composing the above (no bugs of its own)

# Bugs

## Bug 1: Ghost TTL entries (ttl_index.py)

`unregister()` exists but is never called by the LRU evictor. When LRU
evicts an entry, it's removed from the store but stays in the TTL index.
If a new entry reuses the same key, the OLD TTL record causes premature
expiration.

**Fix**: Have LRU evictor call `ttl_index.unregister(key)` after evicting.

## Bug 2: Inflated reap count (ttl_index.py)

`reap_expired()` counts every expired TTL record as "reaped" even when
`store.delete()` returns False (entry already gone — ghost from LRU).

**Fix**: Only count entries where `store.delete()` returns True:
```python
if self._store.delete(key):
    reaped += 1
```

## Bug 3: Missing TTL cleanup on eviction (lru_evictor.py)

`evict_if_needed()` removes entries from the store but does NOT call
`ttl_index.unregister()`. This is the root cause of Bug 1's ghosts.

**Fix**: Add `self._ttl_index.unregister(lru_entry.key)` after eviction.

## Bug 4: Off-by-one in eviction threshold (lru_evictor.py)

`while self._store.size() > self._store.max_size` should be `>=`.
Using `>` means eviction triggers only when the store is OVER capacity,
not AT capacity. The cache oscillates at max_size + 1.

**Fix**: Change `>` to `>=`:
```python
while self._store.size() >= self._store.max_size:
```

# The Trap

- Fixing Bug 3 alone (adding unregister calls) seems to work for ghost tests,
  but Bug 4's off-by-one means the cache runs at max_size + 1, triggering
  eviction on every single put() once full. This amplifies the ghost problem.
- Fixing Bug 4 alone changes eviction timing, causing different tests to
  fail because entries get evicted sooner.
- Bugs 1 and 3 are really the same root cause seen from two angles — the
  agent must realize that fixing 3 (the eviction side) is the real fix, and
  Bug 1's `unregister()` method already exists and just needs to be called.
- Bug 2 is a red herring for correctness but matters for metrics.

# Correct Fix (all four together)

1. In `lru_evictor.py:evict_if_needed()`:
   - Change `>` to `>=` (Bug 4)
   - Add `self._ttl_index.unregister(lru_entry.key)` after delete (Bug 3)
2. In `ttl_index.py:reap_expired()`:
   - Count reaped only when `store.delete()` returns True (Bug 2)

# Failing Tests

- test_lru_eviction_cleans_ttl_index: ghost TTL causes premature expiration
- test_ttl_index_count_after_lru_eviction: ghost count accumulates
- test_reap_count_excludes_ghosts: inflated reap count
- test_eviction_makes_room: cache exceeds max_size
- test_no_excessive_eviction_churn: overwrites trigger eviction
- test_eviction_count_matches_expected: wrong eviction count
- test_realistic_workload: all bugs interacting
