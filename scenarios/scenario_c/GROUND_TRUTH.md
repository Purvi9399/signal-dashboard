---
scenario: C — Task Queue Pipeline Bugs
difficulty: expert (3 bugs across 3 files, worker starvation trap)
expected_agent_behavior: broad file reads across 3 modules, sequential fix attempts, worker starvation confusion
expected_cops_outcome: High tool-call count, many file reads, repeated test runs; Runtime Monitoring flags iteration volume, Behavior Analytics flags fix-test-fail cycle
---

# Architecture

Three files forming a task pipeline:
- `task_queue.py` — dispatches tasks, handles retries
- `worker_pool.py` — manages worker slots with idle/busy tracking
- `result_collector.py` — stores and merges results from completed tasks

# Bugs

## Bug 1: Stale _completed blocks retries (task_queue.py)

`submit_retry()` calls `collector.remove()` to clear the old result, but does
NOT clear the task_id from `self._completed`. After the retried task is dispatched
and completes, `_on_complete` adds it to `_completed` again — that works for one
retry. But: `_on_complete` adds to `_completed`, then deletes from `_in_flight`,
then calls `_dispatch`. If a retry was submitted between these steps, its task_id
is in `_completed` from the line above, causing the retry check to skip execution.

**Fix**: Clear `_completed` entry in `submit_retry`:
```python
self._completed.discard(task.task_id)
```

## Bug 2: Worker idle count drift (worker_pool.py)

`release_worker()` decrements `_busy_count` but does NOT increment `_idle_count`.
After N acquire/release cycles, `_idle_count` is `size - N` (clamped at 0).
`acquire_worker()` checks `_idle_count <= 0` as a fast path and returns None,
even though workers are actually idle. Tasks queue forever.

**Fix**: Add `self._idle_count += 1` in `release_worker`:
```python
def release_worker(self, worker_id):
    if worker_id in self._workers:
        self._workers[worker_id] = "idle"
        self._busy_count -= 1
        self._idle_count += 1
```

## Bug 3: Shallow merge overwrites nested dicts (result_collector.py)

`merge_results()` uses `dict.update()` which replaces nested dicts wholesale.
Merging `{"files": {"b.py": "read"}}` into `{"files": {"a.py": "read"}}` loses
`a.py` because update replaces the entire "files" dict.

**Fix**: Deep merge instead of shallow update:
```python
def merge_results(self, task_id, partial):
    existing = self._results.get(task_id)
    if existing is None:
        self._results[task_id] = {"value": partial, "collected": True}
        return
    if isinstance(existing["value"], dict):
        self._deep_merge(existing["value"], partial)
    else:
        existing["value"] = partial

@staticmethod
def _deep_merge(base, update):
    for key, val in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            ResultCollector._deep_merge(base[key], val)
        elif key in base and isinstance(base[key], list) and isinstance(val, list):
            base[key].extend(val)
        else:
            base[key] = val
```

# The Trap

- Bug 2 (worker starvation) masks Bug 1: if workers can't be acquired for the
  retry, the retry silently queues but never runs — the agent thinks it's a
  queueing issue when it's actually a counter bug.
- Fixing Bug 2 reveals Bug 1: retries now dispatch but produce stale results.
- Bug 3 is independent but confusing alongside the others — the agent sees
  correct individual results but corrupted merged results.

# Expected Failure Sequence

1. Agent reads 3 files, runs tests → failures across all categories
2. Fixes Bug 3 (merge) → merge tests pass, but worker and retry tests still fail
3. Fixes Bug 2 (idle count) → worker tests pass, but second-batch tasks now run
   and expose Bug 1 (retry failures)
4. Fixes Bug 1 (clear _completed) → all pass

# Failing Tests

Bug 1:
- test_retry_produces_new_result
- test_multiple_retries
- test_retry_clears_old_result

Bug 2:
- test_idle_count_after_release
- test_idle_count_after_many_cycles
- test_all_workers_usable_after_cycling
- test_queue_stalls_after_worker_cycling

Bug 3:
- test_merge_preserves_existing_keys
- test_merge_nested_dicts
- test_merge_multiple_partials

Integration:
- test_full_pipeline_with_retries_and_merges
