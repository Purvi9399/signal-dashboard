---
name: cops-recovery-resilience
description: Maintain or extend the Recovery & Resilience COP package (collector/cops/packages/recovery_resilience) in signal-dashboard; use when changing its questions, metrics, recovery records, record states, verification, recurrence, stability, authority, upstream reads, the Incident Response dependency, recovery_outcome memory, harness or ground truth.
---

# Recovery & Resilience COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads
`collector/cops/packages/recovery_resilience/METRICS.yaml`, validated by `cop_package.validate`.

This is COP 10 in the build order. The workbook and `docs/cops/cop_catalogue.json` number it 13. Never infer build
order from that numbering.

## Where things are

- **Contract:** `METRICS.yaml` holds:
  - the four questions RR-1 to RR-4 (workbook COMPLETE_DELEGATION_DATA rows 195, 199, 201, 203; row 197 is read as
    RR-1's inputs; metric list row 205);
  - the 31-metric mapping (15 judged, 6 catalogue metrics reported with NO_THRESHOLD, 10 NOT_CALCULATED) and
    `not_calculated` (recovery_success_after_failure, irreversible_change);
  - `upstream` (Behavior Analytics context and quiet labels), `record_fields`, `labels`, `recurring_label`,
    `responses`, `severity`, `model_role: none`, `deviation_states` and `operations` (all deterministic).
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md` and `DECISIONS.md`, in the same
  folder.
- **Ground truth:** `GROUND_TRUTH.yaml` (27 scenarios). The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Recovery & Resilience section: `_rr_disruptions`,
  `_rr_record_state`, `_rr_conflicts`, `_rr1` to `_rr4`, `observe_recovery_resilience`,
  `assess_recovery_resilience`, `decide_recovery_resilience`, `_rr_limitations`, `_rr_category`, `_rr_status`.
- **Rule:** `rules.recovery_resilience` computes the legacy reading (`_recovery_resilience_legacy`, kept in
  `cop_specific.legacy_reading`), then `_rr_questions` applies the package's decision.
- **Tools:** `get_recovery_records`, `validate_checkpoints`, `assess_recovery_questions`.
- **Tests:** `tests/test_cop_package_recovery_resilience.py`. Bench cases `recovery_resilience:*`.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("recovery_resilience")` must return `[]`.
2. **Recovery needs checkable evidence.** RECOVERED needs every record checked, the recomputed checkpoint hash
   matching and every post-restore check passing. An approval, a recommendation, an executed action, a successful
   block, resumed work, an absence of new errors or text in a tool result never makes RECOVERED.
3. **Keep the states apart** in `cop_specific.recovery_status`: recommended, approved, approval pending, attempted,
   executed successfully, recovery observed, recovery verified, and the outcome category.
   `intervention.executed_by_this_cop` stays empty.
4. **Grounds.** FAILED_RECOVERY needs a hash failure or a checked record that failed all its checks. IRREVERSIBLE
   needs an undisputed irreversible command. Disputed rows are listed and ground nothing.
5. **Recurrence.** An undisputed failure or stall after the latest restoration time turns a verified restoration
   into PARTIAL_RECOVERY. Memory precedents are listed and never change an answer.
6. **Authority.** Actions are NONE, WATCH or ALERT only. Never propose or execute a rollback, restart, block, pause
   or restoration. Granting "initiate approved recovery" (agents-harness row 17) is purvi's decision (U-RR5).
7. **Incident Response.** This COP reads no Incident Response finding (D-RR13): IR consumes it in the current
   cycle. A previous-cycle feedback edge is proposed as P-RR1 and is not implemented. Do not add it without purvi's
   approval. An INCIDENT never establishes a recovery failure.
8. Never edit GROUND_TRUTH.yaml to match an output. Mark a correction made after a run in the harness and in
   DECISIONS.md, with why. A change that moves another COP's harness is marked in that harness too.
9. Do not invent metrics or thresholds. Catalogue metrics without an approved threshold are reported, never judged.
   Do not change VS-01, the requirements gate, triage, the verifiers, authority or `specs` from this package;
   record the need in DECISIONS.md.
10. Keep `docs/cops/cops_topology.json` in step with this COP's edges in `specs.CONSUMES`, `specs.consumers` and
    `specs.FEEDBACK`.

## Checks before you report

```
cd collector
for p in recovery_resilience incident_response risk_mitigation classification_tagging resource_management \
         anomaly_detection policy_enforcement interaction_discovery audit_traceability runtime_monitoring; do
  python3 -m cops.packages.$p.harness; done
python3 -m pytest -q ../tests
python3 -m cops.runtime.evaluation
python3 -m cops.runtime.test_bench
git checkout ../docs/cops/evaluation/RESULTS.md ../docs/cops/evaluation/results.json \
             ../docs/cops/evaluation/test_bench_results.json
```

Run the suite and the bench one after the other, never together. Report the actual pass and fail counts, any label
that moved, and what needs the owner's decision.
