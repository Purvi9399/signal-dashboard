# Anomaly Detection: interpretation

## Pipeline

1. `signal_metrics.compute(..., catalogue=True)` computes every metric and `signal_metrics.estate` the baselines
   (no change to the engine).
2. `evidence_assessment.assess` gives each question its presence, reliability and sufficiency.
3. `cop_package.observe_anomaly_detection` answers AD-1 to AD-4 from the metric values, the states the approved
   thresholds give them and the estate baselines, and AD-5 from those four answers, Runtime Monitoring and
   validated memory. A question whose blocking reliability check fails or cannot run is INCONCLUSIVE; what it
   observed is kept as `observed_not_judged`.
4. `cop_package.decide_anomaly_detection` gives the package's decision. `rules.anomaly_detection` records it
   beside the threshold reading of `cops/interpret.py:anomaly_detection` (unchanged).
5. If AD-5 was answered (NO_DEVIATION or UNDETERMINED), the decision is the finding: `graph.evidence_precheck`
   stops before the model (stop reason `decided_by_package_without_model`) and the finding is verified like a
   deterministic COP's.
6. Otherwise the model investigates with its own tools, sees the question answers among the baseline tool
   results, and concludes. Its candidate is verified (DS-01 to DS-03, SC, MC, VS-01, unchanged), then
   `cop_package.interpreted_anomaly_detection` reads the verified label back as AD-5's answer.

## How the label is decided

AD-5's answer gives the label (`labels` in METRICS.yaml).

| AD-5 | When | Label | Decided by | Action | Confidence |
|---|---|---|---|---|---|
| NO_DEVIATION | AD-1 to AD-4 all answered without a deviation | NORMAL | package | NONE | 0.8 x evidence |
| UNDETERMINED | no deviation, and Runtime Monitoring INSUFFICIENT or a question BASELINE_INSUFFICIENT or INCONCLUSIVE | INDETERMINATE | package | WATCH | 0.2 x evidence |
| MEANINGFUL_ANOMALY | a deviation, and the model's verified label is ANOMALOUS | ANOMALOUS | model, verified | the model's, through triage | the model's, capped |
| LEGITIMATE_VARIATION | a deviation, every one explained by re-checked context, and the model's verified label is NORMAL | NORMAL | model, verified | the model's | the model's, capped |
| UNDETERMINED | a deviation, and the model's verified label is INDETERMINATE | INDETERMINATE | model, verified | the model's | the model's |
| INCONCLUSIVE | a deviation, and no verified interpretation | INCONCLUSIVE | | WATCH | 0 |

The confidences and actions of the package's labels are `cops/interpret.py:anomaly_detection`'s; "evidence" is
Runtime Monitoring's evidence level over SUFFICIENT's. Recipients: a verified ANOMALOUS notifies Incident Response
and Risk Mitigation.

The model cannot reach ANOMALOUS where no question deviates: it is not called. It cannot reach NORMAL over
insufficient telemetry: the package decides UNDETERMINED first, and DS-01 and the evidence precheck still apply.

## Verification boundary

The verifiers still decide whether a candidate stands. For Anomaly Detection only (D-AD15 to D-AD19):

- **Legitimate variation.** A model NORMAL over a deviation stands only when VS-01 and DS-03, re-checking from the
  case and validated memory, find every deviation explained by checkable context (task-class peers for tokens
  and action rate, or a validated NORMAL precedent naming the measure), and the finding cites it. Otherwise it is
  rejected and AD-5 is INCONCLUSIVE with LEGITIMACY_NOT_ESTABLISHED.
- **False anomaly.** A model ANOMALOUS where checkable context explains every deviation is rejected by VS-01
  (scenario false_anomaly_over_explained_deviation).
- **WARNING.** For the package's NORMAL, a WARNING is partial support and is recorded. VS-01 reads
  PROVISIONALLY_SUPPORTED (below_threshold_warning), and the label stays NORMAL. EXCEPTION and CRITICAL still
  oppose.

| Reading | Outcome |
|---|---|
| deviation, model ANOMALOUS, not all explained | MEANINGFUL_ANOMALY, ANOMALOUS |
| deviation, model NORMAL, every deviation explained and cited | LEGITIMATE_VARIATION, NORMAL |
| deviation, model NORMAL, context missing, insufficient, partial or not cited | INCONCLUSIVE (LEGITIMACY_NOT_ESTABLISHED) |
| deviation, model ANOMALOUS, every deviation explained | INCONCLUSIVE (VS-01 contrary evidence) |
| model INDETERMINATE, or no deviation and evidence short | UNDETERMINED, INDETERMINATE |

## What changed from the existing rule

- With no deviation and enough evidence, NORMAL comes from the five answers and no model is called; before, the
  model was called and the threshold reading was recorded beside it.
- AD-1 is a question of its own: a repetition, retry or failure count in EXCEPTION or CRITICAL is a deviation that
  the model interprets. The threshold reading treated retry and repetition as support only; it is unchanged as
  the recorded baseline.
- NORMAL needs every question answered; a missing baseline or measure gives INDETERMINATE (the threshold reading
  gave NORMAL when one primary metric could be judged).
- A first-seen MCP count for an operator with no earlier session is not novelty (BASELINE_INSUFFICIENT).
- A deviation observed on telemetry whose sources conflict is not judged.
