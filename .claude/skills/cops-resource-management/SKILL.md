---
name: cops-resource-management
description: Maintain or extend the Resource Management COP package (collector/cops/packages/resource_management) in signal-dashboard; use when changing its questions, metrics, quota source, baselines, attribution, label decision, upstream reads, usage_baseline memory, harness or ground truth.
---

# Resource Management COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/resource_management/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml`: the five questions RS-1 to RS-5 (workbook COMPLETE_DELEGATION_DATA rows 146-154, metric list row 156), the 31-metric mapping, `labels` (RS-5 answer to label), `burn` and `waste` (the four counted measures), `deviation_states`, `elevated_states` and operations. Unbuilt metrics stay in the mapping with a `note` naming the missing input.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Resource Management section (`_rs_baselines`, `rs_baseline_comparison`, `_rs1` to `_rs5`, `observe_resource_management`, `decide_resource_management`). The rule `rules.resource_management` records claims, applies the attribution discount (`_attribute_consumption`) and publishes what the decision returns.
- **Tools:** `compute_consumption`, `get_quota`, `assess_resource_questions` (`tools.py`, in this order in `rules.DETERMINISTIC_TOOLS`).
- **Tests:** `tests/test_cop_package_resource_management.py`; `tests/test_cop_influence.py`; `tests/test_runtime_graph.py::test_resource_management_alternate_outcomes`; evaluation and bench cases for this COP.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("resource_management")` must return `[]`.
2. The quota record (`tools.get_quota`, `token_budget`) is the only entitlement source. UNAUTHORIZED only when tokens exceed it. High consumption against the estate is EXCESSIVE or INEFFICIENT, never UNAUTHORIZED (D-RS3).
3. Only the four `specs.metric_keys` count toward the label (D-RS2). Do not change `specs.metric_keys` or `cops/interpret.py`: the legacy 4-COP runner reads them.
4. The answers decide the label in the order INTERVENTION_REQUIRED, ATTENTION, UNDETERMINED (telemetry), UNDETERMINED (unanswered), NO_INTERVENTION. Positive evidence stands on partial evidence (D-RS8). Do not add a path that bypasses RS-5.
5. Baselines are self-inclusive (D-RS11). Do not switch to leave-one-out without purvi's approval (U-RS1); the comparison is recorded beside each reading.
6. Attribute consumption only through a verified Interaction Discovery finding with ID-2 FULLY_RECONSTRUCTED and every delegation link RECORDED (D-RS10). Keep the `cop_specific.attribution` field shape and the 0.6/0.9 discount.
7. Never edit GROUND_TRUTH.yaml to match an output. A correction after a run is marked in the file and in DECISIONS.md with why.
8. Do not invent thresholds, quotas or references. An unbuilt metric is NOT_CALCULATED with its missing input, never a proxy.
9. Memory: only VALIDATED usage_baseline records are listed, and a record never changes a decision or a baseline.
10. No paid model calls; every operation is deterministic. Do not change VS-01, the requirements gate or triage from this package; record the need in DECISIONS.md.

## Checks before you report

```
cd collector
python3 -m cops.packages.resource_management.harness
python3 -m cops.packages.anomaly_detection.harness
python3 -m cops.packages.policy_enforcement.harness
python3 -m cops.packages.interaction_discovery.harness
python3 -m cops.packages.audit_traceability.harness
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Report the actual pass and fail counts, any label that moved, and what needs the owner's decision.
