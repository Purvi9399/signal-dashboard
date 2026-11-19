# Runtime Monitoring: memory

The existing memory gateway is used unchanged (`collector/cops/runtime/memory.py`, `MemoryGateway`, `POLICIES["runtime_monitoring"]`).

## Three kinds of state

| Kind | Where it lives | Lifetime | Who writes it |
|---|---|---|---|
| Current-cycle state | LangGraph state of one COP run (`graph.COPState`), checkpointed by the case graph | one run (`run_id`) | the graph nodes |
| Historical evidence | `cop_memory_records`, status UNVALIDATED or VERIFIED | 90 days by default (`DEFAULT_RETENTION_DAYS`) | `graph.validate_memory_update`, only for a non-quiet label |
| Verified learning | the same records, status VALIDATED | as above | `MemoryGateway.record_feedback` from an independent reviewer only (`independent_reviewer`: never a COP, the gateway, the runtime or `auto`) |

## What Runtime Monitoring writes

- **Record type:** `collector_reliability` only, with tenant scope.
- **When:** only for a substantive label other than SUFFICIENT. In practice that is DEGRADED or INSUFFICIENT.
- **Status:** VERIFIED when the finding verified, UNVALIDATED otherwise. A COP cannot write VALIDATED or REJECTED.
- **Payload:** label, finding status, confidence, rationale, lineage, session id and up to ten claims. The lineage names metrics and tools (`tool:<name>`), never this run's tool call ids, so the same logical finding has the same record id.
- **Provenance:** `case_id`, `finding_id`, `tenant_id`, `session_id`, `verified`, `verifier_registry`, `contract_version`, `synthetic`, and the cycle and sources: `run_id`, `evaluated_at`, `metric_version`, the full `lineage` with tool call ids, and `sources` (metrics, tools, collectors).
- **Re-run:** the gateway keeps the stored record (status, created_at and feedback stand) and appends the re-observation, with its `run_id`, to `provenance.re_observed`. A changed finding is a different record.
- **Gate:** verifier MU-01 checks ownership, scope, provenance and instruction-shaped text before the write.

## What it reads

- **What:** `collector_reliability` of its own tenant, in three buckets: established (VALIDATED), verified pending, and unverified. Expired records are excluded.
- **Effect:** the read happens in `graph.retrieve_memory`. `rules.runtime_monitoring` does not use it, so memory cannot change Runtime Monitoring's decision. A test checks this.

## Isolation

- A record of another tenant is never visible, not even as a count.
- A request naming another tenant is denied and logged.
- Every read, write and denial is recorded in `cop_memory_access_log`.

## No self-evolution

- Memory never changes a threshold, a limit, a question or the label rule.
- `MemoryGateway.propose_baseline_update` returns a proposal with `applied: False`, which needs human approval. Runtime Monitoring does not call it.

## Unverified memory

A record Runtime Monitoring reads, UNVALIDATED or VERIFIED, never changes its label, thresholds, `expected_collectors` or limits (`test_unverified_memory_never_changes_the_rule_or_collector_expectations`).

## Shared change (see DECISIONS.md D4, D5)

The payload and provenance change is in `graph.validate_memory_update`, used by all 18 COPs. Every COP's record ids now differ from those written before.
