#!/usr/bin/env python3
"""
Sync field_capability.json from the Supabase field_capability table.

signal_curate.py never talks to Supabase for this data -- it only reads the
local field_capability.json cache beside it (see load_field_capability()),
so curation runs stay usable offline against local collector files even
when Supabase is unreachable or credentials aren't set. Run this script by
hand, or on a schedule, whenever the field_capability table changes.

    python3 sync_field_capability.py

If Supabase can't be reached, the existing field_capability.json is left
untouched -- curate() keeps running on the last-known-good cache rather
than losing its diagnostics or blocking on the network.

Env:
    SUPABASE_URL, SUPABASE_KEY          (service role key)
    SUPABASE_FIELD_CAPABILITY_TABLE     default: field_capability
"""

import json
import os
import sys
from datetime import datetime, timezone


def main():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY")
    # Imported here, not at module scope, so the env-var check above always
    # runs first -- missing config fails with the clear message above rather
    # than a ModuleNotFoundError if the supabase package isn't installed.
    from supabase import create_client
    sb = create_client(url, key)
    table = os.getenv("SUPABASE_FIELD_CAPABILITY_TABLE", "field_capability")

    rows, page, size = [], 0, 1000
    try:
        while True:
            # Real column names in the live schema are field_name and
            # expected_collector, not field/collector -- confirmed against
            # information_schema.columns, not assumed.
            r = (sb.table(table).select("tool,field_name,expected_collector,status")
                   .order("tool")
                   .range(page * size, page * size + size - 1).execute())
            batch = r.data or []
            rows.extend(batch)
            if len(batch) < size:
                break
            page += 1
    except Exception as e:
        sys.exit(f"could not read {table} from Supabase ({e}); leaving the "
                  f"existing field_capability.json untouched")

    # NOT_APPLICABLE is never diagnosed per-row -- see the comment above
    # FIELD_CAPABILITY's use in signal_curate.py:curate(). Storing a status
    # for every field that structurally can't apply to a given
    # observable_type would balloon the local cache for no benefit.
    rows = [r for r in rows if r.get("status") != "NOT_APPLICABLE"]

    # Normalize to the field/collector key names load_field_capability()
    # and field_capability.json already use locally, so nothing downstream
    # needs to change just because the DB's column names differ.
    rows = [{"tool": r["tool"], "field": r["field_name"],
             "collector": r["expected_collector"], "status": r["status"]}
            for r in rows]

    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, "field_capability.json")
    with open(out_path, "w") as f:
        json.dump({
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "source": f"supabase:{table}",
            "rows": rows,
        }, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"{len(rows)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
