"""Offline benchmark: ContextMemoryCOP.evaluate for N metrics.

Run:
  python -m cops.benchmarks.bench_context_memory
"""

from __future__ import annotations

import time

from cops.context_memory import ContextMemoryCOP
from cops.contracts import ActionType, MetricEvent, MetricState


# Build one context_ok MetricEvent for timing runs.
# Uses a complete feature set so the bench path is the success case.
def _event(index: int) -> MetricEvent:
    return MetricEvent(
        metric_id=f"metric_{index}",
        metric_name="context_consistency",
        session_id="session_42",
        value=True,
        threshold=True,
        state=MetricState.NORMAL,
        features={
            "compaction_count": 0,
            "context_loss_after_compaction": False,
            "session_reset": False,
            "precedent_match": False,
            "context_consistency": True,
        },
    )


# Check count and that healthy rows stay on NONE.
# Stops a silent logic change from looking like a faster bench.
def _invariants_ok(n: int, decisions: list) -> bool:
    if len(decisions) != n:
        return False
    return all(item.action == ActionType.NONE for item in decisions)


# Time N evaluate calls and return elapsed stats.
# Independent of git; results are printed, not stored.
def _bench(n: int) -> dict:
    cop = ContextMemoryCOP()
    events = [_event(i) for i in range(n)]
    t0 = time.perf_counter()
    decisions = [cop.evaluate(event) for event in events]
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if not _invariants_ok(n, decisions):
        raise RuntimeError("benchmark invariant failed")
    return {
        "n": n,
        "elapsed_ms": round(elapsed_ms, 3),
        "per_event_ms": round(elapsed_ms / max(n, 1), 4),
    }


# Run N=20 and N=100 and print a table.
# Reviewers use this instead of a committed RESULTS file.
def main() -> int:
    rows = [_bench(20), _bench(100)]
    print("Context and Memory evaluate benchmark (not stored in git)")
    print("| N | elapsed_ms | per_event_ms |")
    print("|---|---|---|")
    for row in rows:
        print(f"| {row['n']} | {row['elapsed_ms']} | {row['per_event_ms']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
