"""Upload RawEvent JSONL into public.source_events.

Modes:
  direct — PostgREST with SUPABASE_SERVICE_ROLE_KEY (local debug)
  edge   — Edge Function with SIGNAL_UPLOAD_SECRET (fleet-safe path)

See package/UPLOAD.md and package/INGEST_SPEC.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .upload_config import UploadSettings, load_upload_settings
from .upload_events import upload_event_dicts


def load_jsonl(path: Path) -> List[Tuple[int, Dict[str, Any]]]:
    events: List[Tuple[int, Dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            obj = _parse_json_line(path, line_no, text)
            events.append((line_no, obj))
    return events


def _parse_json_line(path: Path, line_no: int, text: str) -> Dict[str, Any]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"{path}:{line_no}: expected JSON object")
    return obj


def upload_jsonl(
    path: Path,
    *,
    settings: Optional[UploadSettings] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    cfg = settings or load_upload_settings(dry_run=dry_run)
    loaded = load_jsonl(path)
    if limit is not None:
        loaded = loaded[: max(0, limit)]
    events = [event for _line_no, event in loaded]
    origin = str(path.resolve())
    return upload_event_dicts(
        events,
        settings=cfg,
        dry_run=dry_run,
        origin=origin,
        ref_prefix="line",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="upload_jsonl",
        description="Upload RawEvent JSONL to Supabase public.source_events",
    )
    p.add_argument("path", type=Path, help="JSONL file of RawEvent envelopes")
    p.add_argument("--dry-run", action="store_true", help="map only; do not call network")
    p.add_argument("--limit", type=int, default=None, help="upload at most N events")
    p.add_argument("--json", action="store_true", help="print result as JSON")
    return p


def _print_human(result: Dict[str, Any]) -> None:
    print(f"file: {result['file']}")
    print(f"events_read: {result['events_read']}")
    print(f"mode: {result.get('mode')}")
    print(f"ingest_batch_id: {result['ingest_batch_id']}")
    print(f"tenant_id: {result['tenant_id']}")
    print(f"device_id: {result['device_id']}")
    print(f"dry_run: {result['dry_run']}")
    print(f"inserted: {result['inserted']}")
    print(f"observables_curated: {result.get('observables_curated')}")
    print(f"observables_upserted: {result.get('observables_upserted')}")
    if result.get("sample_row") is not None:
        print("sample_row:")
        print(json.dumps(result["sample_row"], indent=2, default=str))
    if result.get("sample_observable") is not None:
        print("sample_observable:")
        print(json.dumps(result["sample_observable"], indent=2, default=str))


def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.path.is_file():
        print(f"file not found: {args.path}", file=sys.stderr)
        return 1
    try:
        result = upload_jsonl(args.path, dry_run=args.dry_run, limit=args.limit)
    except (EnvironmentError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
