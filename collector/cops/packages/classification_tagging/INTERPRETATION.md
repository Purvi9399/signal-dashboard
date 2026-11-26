# Classification & Tagging: interpretation

## Flow

1. `signal_metrics` computes the 26 metrics (unchanged).
2. `tools.t_query_taxonomy` reads the approved taxonomy; `tools.t_inspect_event_metadata` the indicator rows
   (unchanged).
3. `tools.t_assess_classification_questions` calls `cop_package.assess_classification_tagging`:
   `evidence_assessment.assess` per question, then `observe_classification_tagging` (`_ct_family` for CT-1 to
   CT-3, `_ct4`, `_ct5` with `_ct_entities` and `_ct_upstream`).
4. `cop_package.decide_classification_tagging` turns the answers into the label, confidence, severity, action,
   recipients, drivers, contrary evidence and limitations.
5. `rules.classification_tagging` keeps the legacy reading as the computational baseline and applies the decision
   (`_ct_questions`). When CT-5 is decided, the finding carries `decided_without_model` and the graph publishes it
   without calling the model (`graph.evidence_precheck`, as Anomaly Detection).
6. When CT-5 is AMBIGUOUS, the model investigates (profile prompt and tools unchanged), the shared verifiers check
   its finding, and `interpreted_classification_tagging` (registered in `graph.INTERPRETED`) reads the verified
   label back into CT-5.

## Observation

Each measure has a verdict (`cop_package._ad_measure`, shared): DEVIATES (EXCEPTION or CRITICAL), WITHIN (WARNING
recorded as `warning`), UNOBSERVED (the basis is absent), DOES_NOT_APPLY.

## Answers

| Q | Answers |
|---|---|
| CT-1 to CT-3 | FAMILY_INDICATED: a counted metric DEVIATES. NOT_INDICATED: every counted metric WITHIN or DOES_NOT_APPLY. INCONCLUSIVE: none indicates and one is unobserved |
| CT-4 | CONCERNS_OBSERVED: an assigned measure DEVIATES, or a verified Anomaly Detection or Resource Management finding reads above its quiet label. NONE_OBSERVED otherwise. Never counted |
| CT-5 | CLASSIFIED: exactly one candidate family (indicated and defined in the approved taxonomy). AMBIGUOUS: two or more candidates. UNCLASSIFIED: no family indicated and every family question answered with a judged measure. UNDETERMINED: no approved taxonomy; or no candidate while a family is indicated but undefined, a family question is unanswered, or nothing could be judged |

## Label

| CT-5 | Label | Confidence | Decided by |
|---|---|---|---|
| CLASSIFIED | the candidate family | min(0.9, 0.65 + 0.1 n), n = its indicating measures | package |
| UNCLASSIFIED | UNCLASSIFIED | 0.7 | package |
| UNDETERMINED | INCONCLUSIVE | 0.0 | package |
| AMBIGUOUS | the model's verified label if it is a candidate, else INCONCLUSIVE (rejected candidate kept) | the model's, after verification | model, then package read-back |

Confidence is scaled by Runtime Monitoring's evidence level (`rules.evidence_conf`); Runtime Monitoring is not
in this COP's upstream, so the scale is 1. Severity is the legacy SEV of the indicating states. The action is
always NONE. A classified finding notifies Governance Intelligence.

## Observed, inferred, verified, insufficient

CT-5 `basis.evidence_status` records them apart:

- `observed`: the indicating measures, as `metric@state`;
- `inferred`: the family and what inferred it ("package rule (one candidate family)" or "model interpretation",
  with the candidates);
- `insufficient`: why nothing could be classified.

Whether the classification is verified is the finding's `verified` (the shared verifiers). A model
interpretation is recorded under `basis.interpretation` with its verification status.

## Where the model is needed, and why

Only for AMBIGUOUS. When two families are indicated, the legacy rule picks by a fixed order (BOUNDARY before
INTEGRITY before OVERSIGHT). Evaluation case C08 shows that order is wrong for an event it describes: a fake
credential in a test fixture inside the workspace indicates BOUNDARY (sensitive_unasked) and INTEGRITY
(secrets), and the accepted ground truth is INTEGRITY. Which family describes such an event is a reading of the
taxonomy's definitions against the event's metadata, which no approved rule settles. The order's reading stays
the computational baseline (RP-01 and DS-03 compare against it).

The model's answer is constrained. It stands only when:

- the shared verifiers accept it;
- it is one of the candidates.

A quiet reading over an indicated family is rejected by DS-03. A family that no indicator supports is rejected
by the read-back (INTERPRETATION_OUTSIDE_CANDIDATES).

## Unchanged

- `cops/interpret.py` and `specs.metric_keys`.
- The taxonomy gate in `requirements.py`.
- VS-01, triage, verifiers.
- The model prompt and tools.
