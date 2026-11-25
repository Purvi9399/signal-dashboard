"""Offline benchmark: map N RawEvents (no network).

Run:
  python -m export.benchmarks.bench_upload_map
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from export.mapping import map_raw_event_dict
from export.validate_ingest import validate_event_rows

ROOT = Path(__file__).resolve().parents[3]  # build/
SAMPLE = (
    ROOT
    / "live_tests"
    / "antigravity_hooks_live"
    / "session_10prompts_20260910_151413"
    / "live_capture_output.jsonl"
)
RESULTS = Path(__file__).resolve().parent / "RESULTS.md"


def _load_events(limit: int) -> list:
    events = []
    with SAMPLE.open("r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            events.append(json.loads(text))
            if len(events) >= limit:
                break
    return events


def _bench(n: int) -> dict:
    events = _load_events(n)
    # Repeat source events if file shorter than n
    while len(events) < n:
        events.extend(events[: n - len(events)])
    events = events[:n]
    t0 = time.perf_counter()
    rows = [
        map_raw_event_dict(
            ev,
            tenant_id="00000000-0000-0000-0000-000000000001",
            device_id="bench-device",
            ingest_batch_id="00000000-0000-0000-0000-000000000099",
        )
        for ev in events
    ]
    validate_event_rows(rows)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "n": n,
        "elapsed_ms": round(elapsed_ms, 3),
        "per_event_ms": round(elapsed_ms / max(n, 1), 4),
    }


def main() -> int:
    if not SAMPLE.is_file():
        print(f"missing sample: {SAMPLE}")
        return 1
    rows = [_bench(20), _bench(100)]
    lines = [
        "# Upload map benchmark",
        "",
        "Machine-local timing for RawEvent → `source_events` row mapping + validation.",
        "No network. Re-run: `python -m export.benchmarks.bench_upload_map`",
        "",
        "| N | elapsed_ms | per_event_ms |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['n']} | {r['elapsed_ms']} | {r['per_event_ms']} |")
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(RESULTS.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
