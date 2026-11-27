"""
Worker pool — manages a fixed set of worker slots for the task queue.

Each worker is identified by an ID and has a status (idle / busy).
The pool tracks which workers are available and hands them out on request.
"""

from typing import Optional


class WorkerPool:
    """Fixed-size pool of worker slots."""

    def __init__(self, size: int = 4):
        self._size = size
        self._workers = {f"w-{i}": "idle" for i in range(size)}
        self._idle_count = size
        self._busy_count = 0
        self._total_acquisitions = 0

    def acquire_worker(self) -> Optional[str]:
        """Get an idle worker. Returns worker_id or None if all busy."""
        if self._idle_count <= 0:
            return None

        for wid, status in self._workers.items():
            if status == "idle":
                self._workers[wid] = "busy"
                self._idle_count -= 1
                self._busy_count += 1
                self._total_acquisitions += 1
                return wid
        return None

    def release_worker(self, worker_id: str):
        """Mark a worker as idle."""
        if worker_id in self._workers:
            self._workers[worker_id] = "idle"
            self._busy_count -= 1

    @property
    def size(self) -> int:
        return self._size

    @property
    def available(self) -> int:
        """How many workers are idle."""
        return self._idle_count

    @property
    def busy(self) -> int:
        return self._busy_count

    def actual_idle(self) -> int:
        """Count workers with status 'idle' directly."""
        return sum(1 for s in self._workers.values() if s == "idle")
