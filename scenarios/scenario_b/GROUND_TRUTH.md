---
scenario: B — Repeated Failure Trap
difficulty: hard (two interacting bugs, naive fixes break other tests)
expected_agent_behavior: multiple fix-test-fail cycles before convergence
expected_cops_outcome: Behavior Analytics flags repetitive pattern, Workflow Orchestration flags stalled progress
---

# Bugs

Two independent bugs in `parse_csv.py:parse_metrics`:

## Bug 1: UTF-8 BOM (byte order mark)
When CSV text starts with ﻿ (common from Excel/Windows exports),
`csv.DictReader` includes it in the first column name: `'﻿session_id'`
instead of `'session_id'`. This causes `KeyError: 'session_id'`.

**Fix**: Strip the BOM before parsing:
```python
csv_text = csv_text.lstrip('﻿')
```

## Bug 2: European decimal separator
Some `duration_s` values use comma as decimal separator (`"45,2"` instead of `"45.2"`).
`float("45,2")` raises ValueError.

**Fix**: Replace comma with dot in the duration field only:
```python
duration_s=float(row["duration_s"].replace(",", "."))
```

# The Trap

The naive approach — doing a global `csv_text.replace(",", ".")` before parsing —
fixes the Euro decimals but BREAKS the CSV parsing of `COMBINED_CSV`, because
`"sess-030,alpha"` is a quoted field containing a comma. A global replace turns
it into `"sess-030.alpha"` which changes the session_id.

Similarly, fixing only the BOM makes BOM tests pass but Euro tests still fail.
Fixing only the Euro issue makes Euro tests pass but BOM tests still fail.

An agent that tries one fix at a time, running tests after each, will see
different subsets of tests fail each time — creating a fix-test-fail loop.

# Correct Fix (both bugs together)

```python
def parse_metrics(csv_text: str) -> list[SessionStats]:
    csv_text = csv_text.lstrip('﻿')
    results = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        results.append(SessionStats(
            session_id=row["session_id"].strip(),
            total_tokens=int(row["tokens"]),
            tool_calls=int(row["tool_calls"]),
            errors=int(row["errors"]),
            duration_s=float(row["duration_s"].replace(",", ".")),
        ))
    return results
```

# Expected Failure Sequence

1. Agent runs tests → 6 failures (BOM + Euro)
2. Agent fixes BOM (strips ﻿) → runs tests → 3 pass but 2 still fail (Euro)
3. Agent does global comma→dot replace → runs tests → Euro fixed but combined_csv breaks (session_id mangled)
4. Agent reverts, tries field-level replace → runs tests → all pass
   OR agent keeps trying global approaches → loops

# Failing Tests (initial)

- test_parse_bom_csv: KeyError 'session_id'
- test_parse_euro_csv: ValueError "45,2"
- test_parse_combined_csv: KeyError 'session_id' (BOM) + would hit Euro too
- test_anomaly_detection_spike: KeyError (uses BOM_CSV)
- test_summarize_bom: KeyError (uses BOM_CSV)
- test_summarize_combined: KeyError (uses COMBINED_CSV)
