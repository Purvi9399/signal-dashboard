"""Normalize curated observable dicts for PostgREST batch upsert.

PostgREST rejects arrays whose objects have mismatched keys (PGRST102).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def align_observable_keys(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return copies of rows sharing one key set (missing -> None)."""
    if not rows:
        return []
    keys = sorted({key for row in rows for key in row.keys()})
    return [{key: row.get(key) for key in keys} for row in rows]
