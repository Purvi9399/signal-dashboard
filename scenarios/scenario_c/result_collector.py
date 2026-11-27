"""
Result collector — aggregates results from completed tasks.

Supports both simple results and partial results that need merging
(e.g., a tool call that returns part of a large response).
"""

from typing import Any, Optional
from copy import deepcopy


class ResultCollector:
    """Collects and stores task results."""

    def __init__(self):
        self._results: dict[str, dict] = {}
        self._errors: dict[str, Any] = {}

    def collect(self, task_id: str, result: Any, is_error: bool = False):
        """Store a task result."""
        if is_error:
            self._errors[task_id] = result
        else:
            self._results[task_id] = {"value": result, "collected": True}

    def merge_results(self, task_id: str, partial: dict):
        """Merge a partial result into an existing result.

        Used when a tool call returns results incrementally (e.g., streaming
        file reads, paginated API responses).
        """
        existing = self._results.get(task_id)
        if existing is None:
            self._results[task_id] = {"value": partial, "collected": True}
            return

        if isinstance(existing["value"], dict):
            existing["value"].update(partial)
        else:
            existing["value"] = partial

    def get(self, task_id: str) -> Optional[dict]:
        return self._results.get(task_id)

    def get_error(self, task_id: str) -> Optional[Any]:
        return self._errors.get(task_id)

    def remove(self, task_id: str):
        """Remove a result (used before retries)."""
        self._results.pop(task_id, None)
        self._errors.pop(task_id, None)

    def count(self) -> int:
        return len(self._results)

    def error_count(self) -> int:
        return len(self._errors)

    def all_results(self) -> dict:
        return dict(self._results)

    def all_errors(self) -> dict:
        return dict(self._errors)

    def reset(self):
        self._results.clear()
        self._errors.clear()
