# Resource Management: interpretation

## Pipeline

1. `signal_metrics.compute(..., catalogue=True)` computes every metric (no change to the engine). The baselines
   are `signal_metrics.estate` of the fetched estate, the session included.
2. `evidence_assessment.assess` gives each question its presence, reliability and sufficiency.
3. `tools.t_get_quota` reads the quota record; `tools.t_compute_consumption` computes the usage rates (unchanged).
4. `tools.t_assess_resource_questions` calls `cop_package.assess_resource_management`, which answers each
   question (`_rs1` to `_rs5`) from the metric values and states, the quota record, Interaction Discovery's
   verified finding, Runtime Monitoring's verified label and usage_baseline memory, and records
   `rs_baseline_comparison` (leave-one-out, used, and the self-inclusive reading before U-RS1, side by side).
5. `cop_package.decide_resource_management` turns the answers into the label, confidence, severity, action,
   recipients, drivers, contrary evidence and limitations.
6. `rules.resource_management` records one claim per answer, attention metric states, the quota comparison
   (`compute_consumption` tokens), attribution (`_attribute_consumption`) and the Workload Optimization advisory.

Every step is deterministic. No model is called.

## Observation

A judged measure has a verdict (`cop_package._ad_measure`, shared with Anomaly Detection): DEVIATES (EXCEPTION
or CRITICAL), WITHIN (with `warning` at WARNING), NO_BASELINE (the leave-one-out baseline is below
`min_sessions_for_baseline` or has no median), UNOBSERVED (the basis is absent, for example no row carries
total_tokens), DOES_NOT_APPLY.

| Question | Answer when |
|---|---|
| RS-1 | ABOVE_PERMITTED: tokens > token_budget (whatever the baseline says). ABOVE_EXPECTED: token outlier DEVIATES. ELEVATED: WARNING. WITHIN_LIMITS: within, and within the quota. WITHIN_EXPECTED: within, quota not evaluated. BASELINE_INSUFFICIENT: no baseline. INCONCLUSIVE: tokens not measured |
| RS-2 | OUTSIDE_THRESHOLDS: a measure DEVIATES. ELEVATED: a WARNING. WITHIN_THRESHOLDS: both within. BASELINE_INSUFFICIENT: every open measure lacks a baseline. INCONCLUSIVE otherwise |
| RS-3 | WASTE_OBSERVED: insights.stalled DEVIATES. ELEVATED: WARNING. NO_WASTE_COUNTED: within. Repetition, retry and failure are recorded in `not_counted`; the capacity effect is NOT_EVALUATED |
| RS-4 | SINGLE_AGENT: one lane and no MCP server. ATTRIBUTED: a verified Interaction Discovery finding, ID-2 answered FULLY_RECONSTRUCTED, label not PATH_PARTIAL, every delegation link RECORDED, and a RECORDED link for every subagent lane. A finding with no ID-2 answer verifies no relationship and never attributes (R-RS1). UNATTRIBUTED otherwise, with the reason |
| RS-5 | see below |

## How the label is decided

RS-5's answer gives the label (`labels` in METRICS.yaml). Positive evidence first, as the legacy rule judged
whichever measures could be judged:

| Order | RS-5 | When | Label | Confidence | Action | Severity |
|---|---|---|---|---|---|---|
| 1 | INTERVENTION_REQUIRED | RS-1 ABOVE_PERMITTED | UNAUTHORIZED | the reading underneath: min(0.9, 0.6 + 0.1 n) with n hot or elevated measures, 0.8 with none | ALERT | HIGH |
| 2 | ATTENTION | RS-1, RS-2 or RS-3 above or elevated | EXCESSIVE if a burn measure is hot, else INEFFICIENT | min(0.9, 0.6 + 0.1 n) | ALERT when two measures are hot or one is CRITICAL, else WATCH | highest of the hot states, else of the elevated |
| 3 | UNDETERMINED | Runtime Monitoring verified INSUFFICIENT | INDETERMINATE | 0.2 | WATCH | NONE |
| 4 | UNDETERMINED | a question INCONCLUSIVE or BASELINE_INSUFFICIENT | INDETERMINATE | 0.2 | WATCH | NONE |
| 5 | NO_INTERVENTION | otherwise | NORMAL (withheld without a quota by `requirements.py`) | 0.8 | NONE | NONE |

Every confidence is multiplied by min(1, Runtime Monitoring evidence level / 0.9) and rounded to three places.
An UNAUTHORIZED, EXCESSIVE or INEFFICIENT reading on a session with subagent lanes or MCP servers that RS-4
leaves UNATTRIBUTED is discounted by 0.6/0.9, as before. Recipients: Risk Mitigation when a burn measure is hot,
Workload Optimization when a waste measure is hot (the legacy `downstream`).

## Deterministic and model operations

All deterministic (METRICS.yaml `operations`). The workbook marks no RS question as needing judgement that the
approved thresholds and the quota cannot settle; whether a high workload is legitimate would need a workload
record, which does not exist (limitation `workload_record`), not a model.
