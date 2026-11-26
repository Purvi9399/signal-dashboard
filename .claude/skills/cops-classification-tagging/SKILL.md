---
name: cops-classification-tagging
description: Maintain or extend the Classification & Tagging COP package (collector/cops/packages/classification_tagging) in signal-dashboard; use when changing its questions, metrics, families, taxonomy reads, model read-back, upstream reads, entity linkage, classification_example memory, harness or ground truth.
---

# Classification & Tagging COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/classification_tagging/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml`: the five questions CT-1 to CT-5 (workbook COMPLETE_DELEGATION_DATA rows 107-115, metric list row 117), the 26-metric mapping, `families` (the four counted metrics), `precedence`, `labels`, `deviation_states`, `not_calculated` and operations (only `interpret_ambiguous_event` is `by: llm`).
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py` (scripted models only, `fixtures.Script`).
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Classification & Tagging section (`_ct_family`, `_ct4`, `_ct5`, `_ct_upstream`, `_ct_lane_links`, `_ct_entities`, `observe_classification_tagging`, `assess_classification_tagging`, `decide_classification_tagging`, `interpreted_classification_tagging`).
- **Rule:** `rules.classification_tagging` keeps the legacy reading as the precedence baseline and applies the decision (`_ct_questions`).
- **Model read-back:** `graph.INTERPRETED["classification_tagging"]`.
- **Tools:** `query_taxonomy`, `inspect_event_metadata`, `assess_classification_questions` (`tools.py`, in this order in `rules.DETERMINISTIC_TOOLS`).
- **Upstream edges:** `specs.TOPOLOGY_CONSUMES["classification_tagging"]` (D-CT9).
- **Tests:** `tests/test_cop_package_classification_tagging.py`; evaluation case C08; bench cases for this COP.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("classification_tagging")` must return `[]`.
2. Only the four `specs.metric_keys` indicate a family, at EXCEPTION or CRITICAL (D-CT2). Do not change `specs.metric_keys` or `cops/interpret.py`.
3. A candidate is a family that is indicated and defined in the approved taxonomy. One candidate is decided by the package without a model. Two or more are AMBIGUOUS and go to the model; its verified label stands only if it is a candidate (D-CT5). Never let the precedence order decide an ambiguous event.
4. No family indicated with a family question unanswered is UNDETERMINED (INCONCLUSIVE), not UNCLASSIFIED (D-CT7).
5. Upstream findings are context. They never indicate a family or change a confidence; list each with verification status and whether it is correlated (D-CT10).
6. Link a subagent lane only through a verified Interaction Discovery finding with ID-2 FULLY_RECONSTRUCTED and a RECORDED link for the lane (D-CT11).
7. The action is always NONE. Kind only, not severity or response.
8. Never edit GROUND_TRUTH.yaml to match an output. A correction after a run is marked in the file and in DECISIONS.md with why.
9. Do not invent families, rules, thresholds or metrics. An unbuilt metric is NOT_CALCULATED with its missing input.
10. Memory: only VALIDATED classification_example records are listed, and they change nothing.
11. No paid model calls in tests or the harness. Do not change VS-01, the requirements gate, triage, the verifiers or the model prompt from this package; record the need in DECISIONS.md.

## Checks before you report

```
cd collector
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
