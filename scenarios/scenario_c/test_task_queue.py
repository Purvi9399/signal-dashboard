"""Tests for the async task queue system. Run: pytest test_task_queue.py -v"""

import pytest

from task_queue import TaskQueue, Task
from worker_pool import WorkerPool
from result_collector import ResultCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

def failing_task():
    raise ValueError("tool call failed")

def identity(x):
    return x


# ---------------------------------------------------------------------------
# Basic tests (these pass)
# ---------------------------------------------------------------------------

class TestBasicQueue:
    def test_single_task(self):
        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        task = Task(task_id="t-1", fn=add, args=(3, 4))
        assert queue.submit(task) is True
        queue.drain()
        result = collector.get("t-1")
        assert result is not None
        assert result["value"] == 7

    def test_multiple_tasks(self):
        pool = WorkerPool(size=4)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        for i in range(4):
            queue.submit(Task(task_id=f"t-{i}", fn=add, args=(i, 1)))

        queue.drain()
        assert collector.count() == 4
        assert collector.get("t-2")["value"] == 3

    def test_priority_ordering(self):
        pool = WorkerPool(size=1)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        order = []
        def track(label):
            order.append(label)
            return label

        # First task runs immediately (worker available)
        queue.submit(Task(task_id="first", fn=track, args=("first",)))
        # These two queue up; high priority should run before low
        queue.submit(Task(task_id="low", fn=track, args=("low",), priority=1))
        queue.submit(Task(task_id="high", fn=track, args=("high",), priority=10))
        queue.drain()

        # first runs immediately, then high (priority 10), then low (priority 1)
        assert order == ["first", "high", "low"]


# ---------------------------------------------------------------------------
# Tests that expose Bug 1: stale _completed set blocks retries
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    def test_retry_produces_new_result(self):
        """Retrying a task should run it again and produce a new result.

        BUG 1: submit_retry doesn't clear task_id from _completed.
        The retried task gets added to _pending, but when _dispatch runs,
        it completes the task, adds it to _completed again — that works.
        But the real issue: _on_complete adds to _completed, then removes
        from _in_flight, then calls _dispatch. If a retry was queued,
        _dispatch will see the task_id in _completed (from line above)
        and... actually the current _dispatch doesn't check _completed.
        Let me restructure the bug.
        """
        call_count = 0
        def counting_fn():
            nonlocal call_count
            call_count += 1
            return call_count

        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        task = Task(task_id="retry-1", fn=counting_fn)
        queue.submit(task)
        queue.drain()
        assert collector.get("retry-1")["value"] == 1

        # Retry the same task
        retry_task = Task(task_id="retry-1", fn=counting_fn)
        queue.submit_retry(retry_task)
        queue.drain()

        result = collector.get("retry-1")
        assert result is not None, "Retry result is missing"
        assert result["value"] == 2, (
            f"Retry should have produced value=2, got {result['value']}. "
            f"Task was not re-executed (stale _completed entry)."
        )

    def test_multiple_retries(self):
        """A task retried multiple times must run each time.

        BUG 1: _completed accumulates stale task_ids. After first completion,
        the task_id stays in _completed. submit_retry doesn't clear it.
        """
        results = []
        def append_fn(val):
            results.append(val)
            return val

        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        for attempt in range(5):
            task = Task(task_id="multi-retry", fn=append_fn, args=(attempt,))
            if attempt == 0:
                queue.submit(task)
            else:
                queue.submit_retry(task)
            queue.drain()

        assert len(results) == 5, (
            f"Expected 5 executions, got {len(results)}: {results}"
        )
        assert results == [0, 1, 2, 3, 4]

    def test_retry_clears_old_result(self):
        """After retry, the collector should have the NEW result, not the old."""
        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        queue.submit(Task(task_id="t-1", fn=identity, args=("old",)))
        queue.drain()
        assert collector.get("t-1")["value"] == "old"

        queue.submit_retry(Task(task_id="t-1", fn=identity, args=("new",)))
        queue.drain()
        assert collector.get("t-1")["value"] == "new", (
            "Retry did not update the result"
        )


# ---------------------------------------------------------------------------
# Tests that expose Bug 2: worker pool idle count drift
# ---------------------------------------------------------------------------

class TestWorkerPoolCounting:
    def test_idle_count_after_release(self):
        """After acquire + release, idle count should be restored.

        BUG 2: release_worker does not increment _idle_count.
        """
        pool = WorkerPool(size=4)
        assert pool.available == 4

        wid = pool.acquire_worker()
        assert pool.available == 3

        pool.release_worker(wid)
        assert pool.available == 4, (
            f"After release, available should be 4 but is {pool.available}. "
            f"Actual idle workers: {pool.actual_idle()}"
        )

    def test_idle_count_after_many_cycles(self):
        """Idle count should stay consistent through many acquire/release cycles.

        BUG 2: _idle_count drifts down with each cycle. After N cycles,
        _idle_count is size - N (clamped at 0), while actual idle count
        is still correct.
        """
        pool = WorkerPool(size=2)

        for _ in range(10):
            wid = pool.acquire_worker()
            assert wid is not None, (
                f"acquire_worker returned None — idle_count drifted to "
                f"{pool.available}, but actual idle: {pool.actual_idle()}"
            )
            pool.release_worker(wid)

        assert pool.available == 2, (
            f"After 10 acquire/release cycles, available={pool.available}, "
            f"expected 2. Counter drifted."
        )

    def test_all_workers_usable_after_cycling(self):
        """All workers should be acquirable after full cycling.

        BUG 2: After cycling all workers, _idle_count is 0 even though
        all workers are idle. No more workers can be acquired.
        """
        pool = WorkerPool(size=4)

        # Use all workers once
        workers = []
        for _ in range(4):
            w = pool.acquire_worker()
            assert w is not None
            workers.append(w)

        for w in workers:
            pool.release_worker(w)

        # Now try to use them all again
        workers2 = []
        for i in range(4):
            w = pool.acquire_worker()
            assert w is not None, (
                f"Could only acquire {i} workers on second round. "
                f"available={pool.available}, actual_idle={pool.actual_idle()}"
            )
            workers2.append(w)

        assert len(workers2) == 4

    def test_queue_stalls_after_worker_cycling(self):
        """Tasks should keep running even after workers have been recycled.

        BUG 2: After the first batch of tasks, _idle_count is 0.
        The second batch can't acquire workers and queues forever.
        """
        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        # First batch — uses both workers
        queue.submit(Task(task_id="batch1-a", fn=add, args=(1, 1)))
        queue.submit(Task(task_id="batch1-b", fn=add, args=(2, 2)))
        queue.drain()
        assert collector.count() == 2

        # Second batch — workers should be available again
        queue.submit(Task(task_id="batch2-a", fn=add, args=(3, 3)))
        queue.submit(Task(task_id="batch2-b", fn=add, args=(4, 4)))
        queue.drain()

        assert collector.count() == 4, (
            f"Only {collector.count()} results — second batch stalled. "
            f"Pool available={pool.available}, actual_idle={pool.actual_idle()}"
        )


# ---------------------------------------------------------------------------
# Tests that expose Bug 3: shallow merge in ResultCollector
# ---------------------------------------------------------------------------

class TestResultMerging:
    def test_merge_preserves_existing_keys(self):
        """Merging partial results should not overwrite existing nested data.

        BUG 3: dict.update() replaces the entire nested dict. Merging
        {"files": {"b.py": "x"}} into {"files": {"a.py": "x"}} loses a.py.
        """
        collector = ResultCollector()

        # First partial: file reads
        collector.collect("scan", {"files": {"a.py": "read"}, "status": "partial"})

        # Second partial: more file reads
        collector.merge_results("scan", {"files": {"b.py": "read"}})

        result = collector.get("scan")["value"]
        assert "a.py" in result["files"], (
            f"'a.py' was lost during merge. files={result['files']}"
        )
        assert "b.py" in result["files"], (
            f"'b.py' was not added during merge. files={result['files']}"
        )

    def test_merge_nested_dicts(self):
        """Deep nested structures should merge correctly.

        BUG 3: Only the top-level keys of the partial dict survive;
        nested dicts are replaced wholesale.
        """
        collector = ResultCollector()

        collector.collect("analysis", {
            "findings": {"bugs": ["null-ref"], "warnings": ["unused-var"]},
            "metrics": {"lines": 100}
        })

        collector.merge_results("analysis", {
            "findings": {"bugs": ["buffer-overflow"]},
            "metrics": {"functions": 10}
        })

        result = collector.get("analysis")["value"]

        # findings.bugs should have BOTH entries
        assert "null-ref" in result["findings"].get("bugs", []), (
            f"Original bug was lost: {result['findings']}"
        )
        # findings.warnings should survive
        assert "warnings" in result["findings"], (
            f"warnings key was lost during merge: {result['findings']}"
        )
        # metrics should have both keys
        assert result["metrics"].get("lines") == 100, (
            f"metrics.lines was lost: {result['metrics']}"
        )
        assert result["metrics"].get("functions") == 10, (
            f"metrics.functions was not added: {result['metrics']}"
        )

    def test_merge_multiple_partials(self):
        """Sequential merges should accumulate, not replace."""
        collector = ResultCollector()

        collector.collect("multi", {"sections": {"intro": "done"}})

        for section in ["methods", "results", "discussion"]:
            collector.merge_results("multi", {"sections": {section: "done"}})

        result = collector.get("multi")["value"]
        expected_sections = {"intro", "methods", "results", "discussion"}
        actual_sections = set(result["sections"].keys())

        assert actual_sections == expected_sections, (
            f"Expected {expected_sections}, got {actual_sections}. "
            f"Merge lost sections."
        )


# ---------------------------------------------------------------------------
# End-to-end test — all bugs together
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_pipeline_with_retries_and_merges(self):
        """A realistic pipeline: submit, retry failures, merge partial results.

        Exercises:
        - Bug 1: Retry doesn't re-execute (stale _completed)
        - Bug 2: Workers become unavailable after cycling
        - Bug 3: Merged results lose data
        """
        pool = WorkerPool(size=2)
        collector = ResultCollector()
        queue = TaskQueue(pool, collector)

        # Phase 1: Initial tasks
        queue.submit(Task(task_id="read-1", fn=identity,
                          args=({"files": {"main.py": "ok"}},)))
        queue.submit(Task(task_id="read-2", fn=identity,
                          args=({"files": {"utils.py": "ok"}},)))
        queue.drain()
        assert collector.count() == 2

        # Phase 2: More tasks (workers must be reusable)
        queue.submit(Task(task_id="read-3", fn=identity,
                          args=({"files": {"test.py": "ok"}},)))
        queue.submit(Task(task_id="read-4", fn=identity,
                          args=({"files": {"config.py": "ok"}},)))
        queue.drain()
        assert collector.count() == 4, (
            f"Second batch failed: {collector.count()} results, "
            f"pool available={pool.available}"
        )

        # Phase 3: Merge partial results
        collector.merge_results("read-1", {"files": {"main_test.py": "ok"}})
        r1 = collector.get("read-1")["value"]
        assert "main.py" in r1["files"] and "main_test.py" in r1["files"], (
            f"Merge lost data: {r1['files']}"
        )

        # Phase 4: Retry a task
        attempt = [0]
        def improved_read():
            attempt[0] += 1
            return {"files": {"main.py": "v2"}, "attempt": attempt[0]}

        queue.submit_retry(Task(task_id="read-1", fn=improved_read))
        queue.drain()

        r1_new = collector.get("read-1")
        assert r1_new is not None, "Retry result missing"
        assert r1_new["value"]["attempt"] == 2, (
            f"Retry did not re-execute: {r1_new['value']}"
        )
