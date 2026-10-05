"""Offline benchmark: construct N MetricEvent and COPDecision objects.

Run:
  python -m cops.benchmarks.bench_contracts
"""

from __future__ import annotations

import time
from pathlib import Path

from cops.contracts import (
    ActionType,
    COPDecision,
    CopType,
    MetricEvent,
    MetricState,
    confidence_is_valid,
    is_incident_input,
)

RESULTS = Path(__file__).resolve().parent / "RESULTS.md"


def _make_event(index: int) -> MetricEvent:
    return MetricEvent(
        metric_id=f"metric_{index}",
        metric_name="interaction_path_novelty",
        session_id="session_42",
        value=0.92,
        threshold=0.75,
        state=MetricState.EXCEPTION,
        evidence_refs=[f"evt_{index}"],
    )


def _make_decision(index: int) -> COPDecision:
    return COPDecision(
        cop=CopType.CLASSIFICATION,
        session_id="session_42",
        interpretation="tag",
        confidence=0.9,
        action=ActionType.NONE,
        action_reason="tags only",
        metric_id=f"metric_{index}",
    )


def _invariants_ok(n: int, events: list, decisions: list) -> bool:
    if len(events) != n:
        return False
    if not all(confidence_is_valid(item.confidence) for item in decisions):
        return False
    return is_incident_input(decisions)


def _bench(n: int) -> dict:
    t0 = time.perf_counter()
    events = [_make_event(i) for i in range(n)]
    decisions = [_make_decision(i) for i in range(n)]
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if not _invariants_ok(n, events, decisions):
        raise RuntimeError("benchmark invariant failed")
    return {
        "n": n,
        "elapsed_ms": round(elapsed_ms, 3),
        "per_object_ms": round(elapsed_ms / max(n, 1), 4),
    }


def main() -> int:
    rows = [_bench(20), _bench(100)]
    lines = [
        "# COPS contract construction benchmark",
        "",
        "Machine-local timing for MetricEvent and COPDecision construction.",
        "No network. Re-run: `python -m cops.benchmarks.bench_contracts`",
        "",
        "| N | elapsed_ms | per_object_ms |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['n']} | {row['elapsed_ms']} | {row['per_object_ms']} |"
        )
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(RESULTS.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
