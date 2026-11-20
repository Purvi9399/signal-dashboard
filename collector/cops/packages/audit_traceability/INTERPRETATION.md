# Audit & Traceability: interpretation

## Pipeline

1. `signal_metrics.compute(..., catalogue=True)` computes every metric (no change to the engine).
2. `evidence_assessment.assess` gives each question its presence, reliability and sufficiency.
3. `cop_package.observe_audit_traceability` answers each SUFFICIENT question from metric values, approved
   metric states, the reliability checks, `reconstruct_trace`, `check_decision_linkage`, validated trace_gap
   memory and the verified upstream findings. Any other question is INCONCLUSIVE with the assessment's reasons.
4. `cop_package.decide_audit_traceability` turns the five answers into the label, confidence, severity, action,
   recipients, drivers and contrary evidence.
5. `rules.audit_traceability` records one claim per answer (a tool observation SC-01 re-reads), the trace facts
   behind them, the metric values and states, any memory precedent, and the upstream findings used.

Every step is deterministic. No model is called.

## How the label is decided

The meanings are the ones approved on 2026-11-04; the questions now supply the evidence for them.

| Order | Label | When | Action |
|---|---|---|---|
| 1 | UNATTRIBUTABLE | AT-1 is ACTOR_NOT_ESTABLISHED | ALERT (Governance Architecture) |
| 2 | BROKEN_TRACE | AT-1 CAUSE_NOT_ATTRIBUTED, AT-2 SEQUENCE_BROKEN, AT-3 UNRELIABLE, AT-4 NOT_TRACEABLE, or an AT-5 cause of its own that breaks the audit | ALERT (Governance Architecture) |
| 3 | INCONCLUSIVE | AT-1 or AT-2 is INCONCLUSIVE | WATCH |
| 4 | PARTIAL | AT-2, AT-3 or AT-4 has gaps or (AT-3, AT-4) is unanswered, or AT-5 has a cause of its own | WATCH |
| 5 | COMPLETE | none of the above | NONE |

A broken link is positive evidence and stands when attribution could not be judged (D1). AT-5's restatement of
an earlier answer never adds a reading of its own. Every lower reading that also holds is kept in `also_held`
and in the decision's contrary evidence. The proposed action is the COP's; triage selects the final one.

## Confidence and severity

Base confidence per label is carried over from `cops/interpret.py:_audit_traceability` (0.85, 0.8, 0.75, 0.85),
scaled by Runtime Monitoring's evidence level as before. Severity is the worst state of the four specs.py
metrics (`insights.unattributed`, `telemetry.turn_linkage`, `telemetry.lane_linkage`,
`telemetry.source_agreement`), at least MEDIUM when an upstream decision's evidence does not resolve, as in the
replaced rule. It is reported beside the label and never chooses it.

## Uncertainty handling

- A question without sufficient evidence is INCONCLUSIVE with each reason kept; the COP then reports
  MISSING_EVIDENCE.
- Optional measures that could not be observed and checks that could not run are listed as unassessed, never
  counted as zero or as a failure.
- Nine metrics are not built and are reported NOT_CALCULATED.
- Unvalidated trace_gap records are shown as pending and never treated as precedent.

## Thresholds

None is introduced. Metric states come from `metrics_config.json`; `unreliable_states` (EXCEPTION, CRITICAL) are
the states the replaced rule used for a source disagreement. Counts of steps, lanes, rows and refs are facts, not
thresholds: one lane without a parent step is a broken link.
