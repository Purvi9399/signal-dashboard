---
name: cops-anomaly-detection
description: Maintain or extend the Anomaly Detection COP package (collector/cops/packages/anomaly_detection) in signal-dashboard; use when changing its questions, metrics, baselines, label decision, the model's interpretation of deviations, upstream reads, anomaly_observation memory, harness or ground truth.
---

# Anomaly Detection COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/anomaly_detection/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml`: the five questions (workbook COMPLETE_DELEGATION_DATA rows 31-35), the 16-metric mapping, `labels` (AD-5 answer to label), `interpretations` (model label to AD-5 answer), `deviation_states` and operations. The two unbuilt metrics stay in the mapping with a `note` naming the missing input.
- **Explanations:** `SPEC.md` (baselines, questions), `INTERPRETATION.md` (label table, verification boundary), `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`, with `post_run_corrections`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Anomaly Detection section (`_ad_baselines`, `_ad_observe`, `_ad5`, `observe_anomaly_detection`, `decide_anomaly_detection`, `interpreted_anomaly_detection`). `rules._ad_questions` publishes the package's decision; `graph.evidence_precheck` skips the model when the package decided (`DECIDED_WITHOUT_MODEL`); `graph.independent_verify` reads a verified model label back as AD-5 (`graph.INTERPRETED`).
- **Tool:** `assess_anomaly_questions` (`tools.py`), the only entry in `rules.DETERMINISTIC_TOOLS["anomaly_detection"]`, and in `tools.UNSEQUENCED`.
- **Tests:** `tests/test_cop_package_anomaly_detection.py`; cases C02, C03, C13, C14, C16 and E2E in `evaluation.py`; the bench.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("anomaly_detection")` must return `[]`.
2. AD-1 to AD-4 are deterministic. The model is consulted only when one of them deviates, and only to say whether the deviation is meaningful (AD-5). Never let the model reach ANOMALOUS where no question deviates, or NORMAL over an unanswered question.
3. A deviation is EXCEPTION or CRITICAL against a sufficient baseline. WARNING is recorded, not counted. Do not add a cut-off; the thresholds are metrics_config.json's.
4. Baselines come from `signal_metrics.estate`; read them, do not recompute or invent another comparison group. A missing baseline is BASELINE_INSUFFICIENT with its size and minimum, never a deviation and never normal.
5. Unusual is not prohibited. No answer names a policy or a violation; permission is Policy Enforcement's.
6. Keep the label names, actions and the legacy threshold reading (`cops/interpret.py:anomaly_detection`, recorded as the baseline beside a model finding). Do not weaken DS-03 or VS-01 from this package; record the need in DECISIONS.md (U-AD2, U-AD3).
7. Tests that need a model call on Anomaly Detection must give it a deviation (`evaluation.spike_session`, `_retried`, `sensitive_retried_session`). A model NORMAL over a deviation is rejected by the verifiers.
8. A new baseline tool for an LLM COP that has recorded live replies goes in `tools.UNSEQUENCED`, or the replies stop replaying.
9. Never edit GROUND_TRUTH.yaml to match an output. A correction after a run is marked in the file and in DECISIONS.md with why.
10. Memory: only VALIDATED anomaly_observation records are precedent, listed for comparison, never deciding. Only an independent reviewer validates a record.
11. No paid model calls. Model replies in tests and the harness are scripted and simulated.

## Checks before you report

```
cd collector
python3 -m cops.packages.anomaly_detection.harness
python3 -m cops.packages.policy_enforcement.harness
python3 -m cops.packages.interaction_discovery.harness
python3 -m cops.packages.audit_traceability.harness
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Report the actual pass and fail counts, any bench label that moved, and what needs the owner's decision.
