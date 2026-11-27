"""
Async task queue — dispatches jobs to a pool of workers and collects results.

Used by the AI agent orchestrator to parallelize tool calls (file reads,
API calls, code analysis) and collect their outputs for the next reasoning step.

Architecture:
    TaskQueue  →  dispatches to  →  WorkerPool  →  results in  →  ResultCollector
"""

from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from worker_pool import WorkerPool
from result_collector import ResultCollector


@dataclass
class Task:
    task_id: str
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    priority: int = 0
    timeout_s: float = 30.0


class TaskQueue:
    """Task queue that dispatches to a worker pool."""

    def __init__(self, pool: WorkerPool, collector: ResultCollector,
                 max_pending: int = 100):
        self._pool = pool
        self._collector = collector
        self._max_pending = max_pending

        self._pending: list[Task] = []
        self._in_flight: dict[str, str] = {}  # task_id -> worker_id
        self._completed: set[str] = set()
        self._submitted = 0
        self._dropped = 0

    def submit(self, task: Task) -> bool:
        """Submit a task. Returns False if the queue is full."""
        if len(self._pending) >= self._max_pending:
            return False
        self._pending.append(task)
        self._pending.sort(key=lambda t: -t.priority)
        self._submitted += 1
        self._dispatch()
        return True

    def _dispatch(self):
        """Try to dispatch pending tasks to available workers."""
        while self._pending:
            worker_id = self._pool.acquire_worker()
            if worker_id is None:
                break

            task = self._pending.pop(0)

            if task.task_id in self._in_flight:
                self._pool.release_worker(worker_id)
                continue

            self._in_flight[task.task_id] = worker_id
            result = task.fn(*task.args, **task.kwargs)
            self._on_complete(task.task_id, worker_id, result)

    def _on_complete(self, task_id: str, worker_id: str, result):
        """Handle task completion."""
        self._collector.collect(task_id, result)
        self._completed.add(task_id)
        del self._in_flight[task_id]
        self._pool.release_worker(worker_id)
        self._dispatch()

    def submit_retry(self, task: Task) -> bool:
        """Resubmit a task (e.g., after a transient failure)."""
        self._collector.remove(task.task_id)
        return self.submit(task)

    def drain(self, timeout_s: float = 10.0) -> dict:
        """Process all pending tasks synchronously."""
        self._dispatch()
        return self.stats()

    def stats(self) -> dict:
        return {
            "submitted": self._submitted,
            "pending": len(self._pending),
            "in_flight": len(self._in_flight),
            "completed": len(self._completed),
            "collected": self._collector.count(),
            "dropped": self._dropped,
        }
