---
name: cops-audit-traceability
description: Maintain or extend the Audit & Traceability COP package (collector/cops/packages/audit_traceability) in signal-dashboard; use when changing its questions, metrics, label decision, upstream reads, trace_gap memory, harness or ground truth.
---

# Audit & Traceability COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/audit_traceability/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml` in the package: the five questions (workbook COMPLETE_DELEGATION_DATA row 101), all 29 registry metrics with their question, `unreliable_states` and operations. Unbuilt metrics stay in the mapping with a `note` naming the missing input.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Audit & Traceability section (`observe_audit_traceability`, `decide_audit_traceability`, `at_gap_codes`). The rule `rules.audit_traceability` only records claims and publishes what the decision returns.
- **Tools:** `reconstruct_trace`, `check_decision_linkage`, `assess_audit_questions` (`tools.py`, in this order in `rules.DETERMINISTIC_TOOLS`).
- **Downstream:** `rules.governance_architecture` reads the finding into `gap_levels.audit`.
- **Tests:** `tests/test_cop_package_audit_traceability.py`; the label meanings are also covered in `tests/test_runtime_root_causes.py`.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("audit_traceability")` must return `[]`.
2. The labels keep the meanings approved on 2026-11-04: UNATTRIBUTABLE means the actor cannot be established; BROKEN_TRACE means an essential causal or provenance link is broken; PARTIAL means incomplete but reconstructable. Severity never chooses the label. Do not add a decision path that bypasses the five answers.
3. Never edit GROUND_TRUTH.yaml to match an output. A new scenario says in its `world` when and why it was added. Every expectation must pass; a miss is a defect or a DECISIONS.md entry, never an allowance.
4. Do not invent thresholds or limits. Metric states come from `metrics_config.json`; `evidence_limits` stays absent unless the owner approves values; harness limits are labelled test values.
5. A check that cannot run, or a metric that is not built, is listed as unassessed with its reason. It is never a pass and never a failure. Do not substitute a proxy for an unbuilt metric.
6. Memory (`trace_gap`) is read as recurring or pending only. It never changes an answer or the label. Only an independent reviewer validates a record.
7. No paid model calls; every operation is deterministic. Governance Architecture is a model COP, so the harness runs its deterministic rule on the published finding.
8. Record every interpretation choice, with alternatives and open questions, in DECISIONS.md.

## Checks before you report

```
cd collector
python3 -m cops.packages.audit_traceability.harness
python3 -m cops.packages.interaction_discovery.harness
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Report the actual pass and fail counts, any bench label that moved, and what needs the owner's decision.
