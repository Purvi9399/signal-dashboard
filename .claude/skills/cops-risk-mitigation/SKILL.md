---
name: cops-risk-mitigation
description: Maintain or extend the Risk Mitigation COP package (collector/cops/packages/risk_mitigation) in signal-dashboard; use when changing its questions, metrics, register formula, signal independence, controls, authority, upstream reads, mitigation_outcome memory, Incident Response feedback, harness or ground truth.
---

# Risk Mitigation COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/risk_mitigation/METRICS.yaml`, validated by `cop_package.validate`.

This is COP 8 in the per-COP build order. The workbook and `docs/cops/cop_catalogue.json` number it 9.

## Where things are

- **Contract:** `METRICS.yaml`: the four questions RK-1 to RK-4 (workbook COMPLETE_DELEGATION_DATA rows 132, 134, 136, 138; metric list row 140), the 40-metric mapping (15 NOT_CALCULATED with notes), `not_calculated` (the 3 spec missing_metrics), `upstream` (risk-bearing and quiet labels per consumed COP), `labels`, `decision` (action, severity, confidence per label), `deviation_states`, operations (all deterministic).
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Risk Mitigation section (`rk_signal_split`, `_rk_upstream`, `_rk1` to `_rk4`, `observe_risk_mitigation`, `assess_risk_mitigation`, `decide_risk_mitigation`, `_rk_limitations`, `_rk_status`).
- **Rule:** `rules.risk_mitigation` runs `_risk_mitigation_legacy` (the register reading, kept as the computational baseline) and then `_rk_questions`, which applies the decision on every path, including a missing register.
- **Tools:** `read_risk_register`, `calculate_risk_components` (signals from `cop_package.rk_signal_split`), `assess_risk_questions` (in `tools.UNSEQUENCED`); `evaluate_control_prerequisites` is called inside the assess tool.
- **Verifier recomputation:** `support._risk_recomputation` (VS-01) recomputes the formula from `calculate_risk_components`. Do not change it; keep the tool's signals equal to the package's.
- **Tests:** `tests/test_cop_package_risk_mitigation.py`; evaluation case C05; bench cases `risk_mitigation:*` and fleet F2.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("risk_mitigation")` must return `[]`.
2. Keep the register formula: impact (highest applicable register impact at the session's highest sensitivity tier) x (independent signals + 1 for an undisputed irreversible operation); thresholds from the register only. Never supply a threshold, impact or likelihood.
3. A signal is a verified substantive consumed finding with a risk-bearing label (`METRICS.yaml upstream`) whose lineage is independent (`rules.independent_upstream`). Quiet, undetermined and unverified readings are never signals. A row with `collectors_agree` false never counts as an irreversible operation.
4. Keep observed, inferred, verified risk and justified intervention apart (`risk_status`). Observed risk alone is not verified risk. An anomaly, new route or policy concern is not by itself grounds for mitigation.
5. Controls are recommended, never applied. The action comes from `METRICS.yaml decision` and stays within `specs.permitted_actions`, the triage ceiling and `authority.check`. Never propose BLOCK, PAUSE or INSTRUCT from this package. Never accept residual risk.
6. A LOW band with RK-1 unanswered is INCONCLUSIVE. A missing register or thresholds keeps MISSING_DEPENDENCY.
7. Upstream findings and memory precedents never change a band except as counted signals under rule 3. Memory is comparison only, validated records only.
8. Never edit GROUND_TRUTH.yaml to match an output. A correction after a run is marked in the harness and in DECISIONS.md with why.
9. Do not invent metrics. An unbuilt metric stays NOT_CALCULATED with its missing input. Residual risk stays NOT_ESTABLISHED until mitigation effectiveness is approved.
10. No paid model calls in tests or the harness. Do not change VS-01, the requirements gate, triage, the verifiers, `specs.metric_keys` or `cops/interpret.py` from this package; record the need in DECISIONS.md.
11. Keep `docs/cops/cops_topology.json` in step with this COP's edges in `specs.CONSUMES` and `specs.FEEDBACK`.

## Checks before you report

```
cd collector
python3 -m cops.packages.risk_mitigation.harness
python3 -m cops.packages.classification_tagging.harness
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
