# Audit & Traceability: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["audit_traceability"]`: it writes
and reads `trace_gap`, scoped to the tenant.

## What it writes

`graph.validate_memory_update` writes one `trace_gap` record for a finding that is not COMPLETE: VERIFIED when the
finding verified, else UNVALIDATED. Besides the common payload it carries `gaps`: one code per answer that is not
clean, as `AT-n:OUTCOME` (`cop_package.at_gap_codes`). These come from `cop_specific.memory_payload`. Before this
package the payload held no gap, so a later case could not tell which gap was recorded.

A COMPLETE finding writes nothing (the existing rule for every COP's quiet label).

## What it reads

`assess_audit_questions` reads `trace_gap`. In AT-5:

- gap codes of this session found in a VALIDATED record are listed as recurring, and the finding cites the
  record as a memory precedent, which SC-01 checks is still VALIDATED;
- codes found only in a VERIFIED record are listed as pending;
- UNVALIDATED records are counted only.

Memory never changes an answer or the label: a recurring gap is still the gap it is.

## Isolation and evolution

Tenant scoping is the gateway's. A record becomes VALIDATED only through `record_feedback` by an independent
reviewer. Nothing here changes a threshold or a baseline.
