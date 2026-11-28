# Context & Memory: memory

Context & Memory reuses `memory.MemoryGateway` and its store. There is no second memory database, no new memory
store and no duplicate persistence pipeline.

## Policy

`memory.POLICIES["context_memory"]`, unchanged: writes context_integrity_finding; reads context_integrity_finding;
agent scope. Retrieval does not use Context & Memory's own reads: each request runs `MemoryGateway.read` as the
requesting COP, under that COP's own reads and scope (D-CM12).

## What this COP writes

No memory record (D-CM20). `cop_specific.memory_write_policy: none` makes `graph.validate_memory_update` skip the
write. Records of type context_integrity_finding written under the previous contract stay readable, owned and
unchanged.

## What retrieval never does

- rewrite a record, change its status, timestamps, payload or provenance;
- create, merge or delete a record (duplicates are listed DUPLICATE_OF, each with its own status);
- validate a record (only `MemoryGateway.record_feedback` from an independent reviewer does);
- treat a record as verification of the COP that produced it (VS-01 counts only another COP's established record,
  U-CM9);
- serve a requester its own output of this same run and case (OWN_CURRENT_OUTPUT, D-CM22);
- change another COP's decision.

The harness checks the store before and after every package scenario (`store_unchanged`) and the runtime
scenarios for records owned by this COP (`no_memory_written`).

## Gateway additions

- `MemoryGateway.read(..., include_excluded=False)`: with the flag, adds `excluded_records` (id, type, owner,
  status, expires_at, reason EXPIRED, REJECTED or UNKNOWN_STATUS) for records the same rules withhold. They are
  still never returned as records. Default behaviour is unchanged.
- `MemoryGateway.log(...)`: one `cop_memory_access_log` row for an operation outside read and write. Every
  retrieval writes one row: cop context_memory, operation retrieve, record_id the bundle id, outcome the
  retrieval status, reason "for <requester>: returned [...]; excluded n; not entitled [...]".

## Routing (D-CM18)

Every COP's memory reads in the runtime go through `retrieve_memory`:

- `graph.retrieve_memory`, the working memory view each COP receives;
- `tools._memory_lookup`, used by the package tools (Incident Response, Recovery & Resilience, Anomaly Detection,
  Behavior Analytics and others).

Both keep their shape and add the bundle. Records set apart (insufficient provenance, instruction-shaped, future,
duplicate) are no longer in the established or pending buckets. A gateway denial behaves as before.
