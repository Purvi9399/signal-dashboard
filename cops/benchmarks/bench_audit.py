"""Offline benchmark: AuditCOP.evaluate for N metrics.

Run:
  python -m cops.benchmarks.bench_audit
"""

from __future__ import annotations

import time

from cops.audit import AuditCOP
from cops.contracts import ActionType, MetricEvent, MetricState


def _event(index: int) -> MetricEvent:
    return MetricEvent(
        metric_id=f"metric_{index}",
        metric_name="raw_ref",
        session_id="session_42",
        value="source_events:evt_1",
        threshold=None,
        state=MetricState.NORMAL,
        features={
            "raw_ref": "source_events:evt_1",
            "attribution": "agent",
            "unattributed": False,
            "collectors_agree": True,
        },
        evidence_refs=[f"evt_{index}"],
    )


def _invariants_ok(n: int, decisions: list) -> bool:
    if len(decisions) != n:
        return False
    return all(item.action == ActionType.NONE for item in decisions)


def _bench(n: int) -> dict:
    cop = AuditCOP()
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


def main() -> int:
    rows = [_bench(20), _bench(100)]
    print("Audit evaluate benchmark (not stored in git)")
    print("| N | elapsed_ms | per_event_ms |")
    print("|---|---|---|")
    for row in rows:
        print(f"| {row['n']} | {row['elapsed_ms']} | {row['per_event_ms']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
