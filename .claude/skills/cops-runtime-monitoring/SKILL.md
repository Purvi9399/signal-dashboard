---
name: cops-runtime-monitoring
description: Maintain or extend the Runtime Monitoring COP package (collector/cops/packages/runtime_monitoring) in signal-dashboard; use when changing its questions, metrics, evidence checks, harness or ground truth.
---

# Runtime Monitoring COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/runtime_monitoring/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `collector/cops/packages/runtime_monitoring/METRICS.yaml`, with the questions, requirements, checks, outcomes, limits and operations.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md` and `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Assessment:** `collector/cops/runtime/evidence_assessment.py`. It is generic, and its questions are loaded from the package.
- **Answers:** `collector/cops/runtime/cop_package.py` (`observe_runtime_monitoring`, `assess_runtime_monitoring`, `validate`).
- **COP label:** threshold label in `collector/cops/runtime/rules.py` `runtime_monitoring` (via `collector/cops/interpret.py`), bounded by the answers in `cop_package.aggregate_runtime_monitoring` through `rules._apply_questions`. The answers reach the rule as the tool `assess_runtime_evidence` (`tools.py`).
- **Tests:** `tests/test_cop_package_runtime_monitoring.py` and `tests/test_evidence_assessment.py`.

## Rules

1. Change a question, requirement or outcome in METRICS.yaml first, then the code. Run `cop_package.validate("runtime_monitoring")`; it must return `[]`.
2. Never edit GROUND_TRUTH.yaml to match an output. Truth is written from what a scenario's rows contain.
   - Every expectation must pass. A miss is a code defect to fix. If it cannot be fixed without data or an approval you do not have, report it and add it to DECISIONS.md; never add an allowance to the test.
   - A new scenario must say in its `world` text when and why it was added.
3. Do not invent thresholds or limits.
   - Use the approved states in `metrics_config.json` `thresholds`.
   - Leave `evidence_limits` absent unless the owner approves values.
   - Harness limits are test values and are labelled as such.
4. Keep presence, reliability and sufficiency separate. Never add a combined score.
5. A check that cannot run is NOT_EXECUTED or EVIDENCE_MISSING. It is never passed. A raw_ref is authentic only if it resolves in the supplied `source_events`.
6. Do not change the aggregation classes or ceilings, the action, or memory behaviour without the owner's approval (DECISIONS.md M10, D1). A question never raises the label to INSUFFICIENT.
7. No paid model calls. Every operation is deterministic. A model operation needs approval and may never verify itself.
8. Signal metric functions in `collector/signal_metrics.py` must not move. The activation mapping hashes their line numbers. Add code at the end of the file.

## Checks before you report

```
cd collector
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
flake8 --max-line-length 120 --extend-ignore E401 cops/runtime/cop_package.py cops/runtime/evidence_assessment.py cops/packages
```

Report the actual pass and fail counts, the known gaps that remain, and anything that needs the owner's decision.
