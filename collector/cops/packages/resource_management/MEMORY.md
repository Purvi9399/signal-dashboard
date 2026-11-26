# Resource Management: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["resource_management"]`: it writes
`usage_baseline` and reads `usage_baseline`, scoped to the agent. Workload Optimization also reads
`usage_baseline` (unchanged).

## What it writes

`graph.validate_memory_update` writes one `usage_baseline` record for a substantive finding that is not NORMAL
(VERIFIED when the finding verified, else UNVALIDATED), and an UNVALIDATED one for a rejected candidate. Besides
the common payload, `cop_specific.memory_payload` adds:

- `answers`: the five answers;
- `counted`: the hot and elevated measures, as `metric@state`;
- `baselines`: each judged measure's baseline version, as `metric@version/self_inclusive`;
- `permitted`: RS-1's quota status (WITHIN_QUOTA, ABOVE_QUOTA, NOT_EVALUATED);
- `attribution`: RS-4's attribution status;
- `precedents_read`: the validated records this decision listed.

NORMAL (the quiet label) and anything not substantive (INCONCLUSIVE, including NORMAL withheld for want of a
quota) write nothing. INDETERMINATE is substantive and not the quiet label, so it is written, as it was before
this package (`graph.validate_memory_update` is unchanged; U-RS7).

## What it reads

`assess_resource_questions` reads `usage_baseline` through `_memory_lookup`. VALIDATED records are listed in
RS-5 `basis.precedents` with `use: comparison only, never proof`. They change no answer, label, confidence,
baseline or quota (scenario memory_precedent: the decision with and without the record is identical).
VERIFIED and UNVALIDATED records are not precedent.

The record name says baseline, but no record is read as a baseline: the baselines are `signal_metrics.estate`
of the fetched estate. A record is a past decision, never a measure.

## Isolation and evolution

Tenant and agent scoping are the gateway's. A record becomes VALIDATED only through `record_feedback` by an
independent reviewer; this COP never validates its own records. Nothing here changes a quota, a threshold or a
baseline.
