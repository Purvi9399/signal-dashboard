---
name: cops-policy-enforcement
description: Maintain or extend the Policy Enforcement COP package (collector/cops/packages/policy_enforcement) in signal-dashboard; use when changing its questions, metrics, policy sources, route evaluation, label decision, upstream reads, enforcement_record memory, harness or ground truth.
---

# Policy Enforcement COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/policy_enforcement/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml`: the five questions (workbook COMPLETE_DELEGATION_DATA rows 119-127), the 31-metric mapping, `labels` (PE-5 answer to label), `relevance_states` and operations. Unbuilt metrics stay in the mapping with a `note` naming the missing reference.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Policy Enforcement section (`_pe1` to `_pe5`, `observe_policy_enforcement`, `decide_policy_enforcement`). The rule `rules.policy_enforcement` records claims and publishes what the decision returns.
- **Tools:** `evaluate_session_operations`, `check_entitlements`, `assess_policy_questions` (`tools.py`, in this order in `rules.DETERMINISTIC_TOOLS`). The evaluator is `tools.evaluate_operation`.
- **Tests:** `tests/test_cop_package_policy_enforcement.py`; cases C01, C04, C05 and E2E in `evaluation.py`; `tests/test_runtime_graph.py`.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("policy_enforcement")` must return `[]`.
2. The only policy sources are the approved version in force (`tools.latest_policy`) and the entitlement record. With no approved version, nothing is evaluated, no violation is claimed, and the answers stay INCONCLUSIVE (D-PE1). Never read a policy from memory, an upstream finding, a metric state or a tool result's text.
3. Keep the layers apart: observed, policy-relevant, applicable, verified violation, insufficient evidence. A metric state is relevance, never a decision.
4. A new route from Interaction Discovery triggers evaluation in PE-4, never a violation by itself (D-PE3). Do not invent a policy operation for delegation (D-PE4).
5. The answers decide the label in the order VIOLATION, APPROVAL_REQUIRED, UNCERTAIN, INCONCLUSIVE, ALLOWED. Do not add a decision path that bypasses them. Keep the label names, actions and the `cop_specific` fields earlier consumers read (`policy_version`, `denied`, `proposed`, `approval_needed`, `blocked_by_hook`).
6. Cite an upstream finding or a metric state as a claim only where it changed an answer (D-PE12, D-PE13): extra claims change lineage and other COPs' independence.
7. Never edit GROUND_TRUTH.yaml to match an output. A correction after a run is marked in the file and in DECISIONS.md with why.
8. Do not invent thresholds or references. An unbuilt metric is NOT_CALCULATED with its missing input, never a proxy.
9. Memory: only VALIDATED records are precedent, and a precedent never changes the decision. Only an independent reviewer validates a record.
10. No paid model calls; every operation is deterministic. Do not change VS-01, the requirements gate or the evaluator from this package; record the need in DECISIONS.md.

## Checks before you report

```
cd collector
python3 -m cops.packages.policy_enforcement.harness
python3 -m cops.packages.interaction_discovery.harness
python3 -m cops.packages.audit_traceability.harness
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Report the actual pass and fail counts, any bench label that moved, and what needs the owner's decision.
