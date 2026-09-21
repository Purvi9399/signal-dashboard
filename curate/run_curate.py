"""Run adapt → curate → optional observables upsert / file out."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from curate.adapter_source_events import (
    adapt_source_event_rows,
    load_jsonl,
)
from curate.signal_curate import curate


def curate_from_jsonl(path: Path | str) -> Tuple[List[Dict[str, Any]], list, list]:
    loaded = load_jsonl(path)
    return curate(loaded)


def curate_from_source_rows(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], list, list]:
    loaded = adapt_source_event_rows(rows)
    return curate(loaded)


def write_jsonl(path: Path | str, records: Sequence[Dict[str, Any]]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, default=str) + "\n")
            count += 1
    return count


def type_stats(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(r.get("observable_type") or "?" for r in records))


def tool_stats(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(r.get("tool") or "?" for r in records))


def summarize(
    records: Sequence[Dict[str, Any]],
    *,
    loaded_n: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "records": len(records),
        "loaded": loaded_n,
        "tools": tool_stats(records),
        "types": type_stats(records),
    }
