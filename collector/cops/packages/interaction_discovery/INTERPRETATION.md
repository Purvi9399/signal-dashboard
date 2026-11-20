# Interaction Discovery: interpretation

## Pipeline

1. `signal_metrics.compute(..., catalogue=True)` computes every metric (no change to the engine).
2. `evidence_assessment.assess` gives each question its presence, reliability and sufficiency.
3. `cop_package.observe_interaction_discovery` answers each SUFFICIENT question from metric values, approved
   metric states, validated route memory and upstream Runtime Monitoring. Any other question is INCONCLUSIVE
   with the assessment's reasons.
4. `cop_package.decide_interaction_discovery` turns the five answers into the label, confidence, severity,
   action, recipients, drivers and contrary evidence.
5. `rules.interaction_discovery` records one claim per answer (a tool observation SC-01 re-reads), the metric
   values and states, any memory precedent, and the upstream finding used.

Every step is deterministic. No model is called.

## How the label is decided

| Order | Label | When | Action |
|---|---|---|---|
| 1 | NEW_ROUTE | ID-3 is NEW_ROUTES_APPEARED | PASS_TO_COP (Policy Enforcement, Audit & Traceability) |
| 2 | PROPAGATING | ID-4 is BEYOND_APPROVED_BOUNDS: more subagent lanes or MCP servers than approved (D17) | PASS_TO_COP (Policy Enforcement, Risk Mitigation) |
| 3 | PATH_PARTIAL | any of ID-1, ID-2, ID-4, ID-5 is INCONCLUSIVE or partial | WATCH (Audit & Traceability) |
| 4 | INCONCLUSIVE | ID-3 is INCONCLUSIVE | WATCH |
| 5 | CONTAINED | none of the above | NONE |

A new route or propagation beyond bounds is positive evidence and stands on a partial graph. An incomplete graph
stops only CONTAINED (D2). Every lower reading that also holds is kept in `also_held` and in the decision's
contrary evidence. All five answers, with their bases and evidence, stay in the decision record whichever one
sets the label (scenario several_concerns). Missing history leaves novelty undecided, not the graph partial (D4).

The proposed action is the COP's. Triage then selects the final action under its own approved rules: for
PATH_PARTIAL and INCONCLUSIVE with no actionable gap it selects NONE ("no automatic WATCH").

## Confidence and severity

Base confidence per label is carried over from `cops/interpret.py:_interaction_discovery` (0.8, 0.7, 0.6, 0.8;
0.2 when neither ID-1 nor ID-2 could be answered), scaled by Runtime Monitoring's evidence level as before.
Severity: NEW_ROUTE uses `access.first_seen_mcp`'s state when a server is new, else LOW; PROPAGATING the worst
state beyond bounds; PATH_PARTIAL the worst judged state among the partial questions' metrics.

## Uncertainty handling

- A question without sufficient evidence is INCONCLUSIVE with each reason kept.
- Optional measures that could not be observed are listed as unassessed or unidentified, never counted as zero.
- `interaction_unattributed_edges` is not built and is reported NOT_CALCULATED with its missing input.
- `limitations` lists, as structured entries (decision.py, D14), what the answers rest on without having it:
  - INFERRED_RELATIONSHIP: each delegation link inferred from timing, with its reason code
    (LATEST_PRIOR_HANDOFF or FIRST_HANDOFF_FALLBACK) and `verified: false`;
  - MISSING_EVIDENCE: provenance and timestamp checks with no evidence;
  - LIMIT_NOT_APPROVED: depth and breadth, which are measured but have no approved threshold.
  The record adds NOT_CALCULATED entries for metrics that are not built. The entries are published as
  `uncertainty.limitations` and never change the label.
- Unvalidated route records are shown as pending and never treated as known.

## Thresholds

None is introduced. Metric states come from `metrics_config.json`; `propagation_states` (EXCEPTION, CRITICAL) are
the states the legacy rule used for PROPAGATING. "Complete" means a ratio of 1.
