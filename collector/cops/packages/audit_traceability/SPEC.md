# Audit & Traceability

METRICS.yaml is the contract the runtime reads. This file explains it.

## Identity

| | |
|---|---|
| Key | `audit_traceability` |
| Classification | deterministic (one of the eight) |
| Behavioural question | Can every consequential finding, decision and action be attributed and reconstructed from trustworthy evidence? (`specs.py`) |
| Workbook question | Can we reconstruct what happened, who was responsible and why decisions were made? |
| Labels | COMPLETE, PARTIAL, BROKEN_TRACE, UNATTRIBUTABLE, or INCONCLUSIVE |
| Permitted actions | NONE, WATCH, ALERT |
| Consumes | Runtime Monitoring, Interaction Discovery, Policy Enforcement (`specs.CONSUMES`) |
| Consumed by | Governance Architecture |

## Purpose

Say whether the record of a session lets an auditor establish who acted, rebuild what happened, trust the
telemetry it rests on, and trace every governance decision to evidence the record can produce. It judges the
record, not the behaviour in it.

## Authority

It observes and alerts. It cannot amend an audit record (`triage.py`). Every action it proposes goes through
triage and the authority check; none executes.

## Inputs and outputs

- Rows: the case session's observables. Metrics: `signal_metrics.compute(..., catalogue=True)`.
- Tools, in order (`rules.DETERMINISTIC_TOOLS`): `reconstruct_trace`, `check_decision_linkage`,
  `assess_audit_questions` (the five answers, read against the trace, the upstream decisions' evidence links,
  validated trace_gap memory and the upstream findings).
- Output: the finding, with `cop_specific.questions` (each answer, explanation, metrics, basis and evidence refs),
  `drivers`, `also_held`, `contrary`, `trace` and `decision_linkage`; and the common decision record
  (`cops/runtime/decision.py`).

## The five questions

Source: the workbook, sheet COMPLETE_DELEGATION_DATA, row 101. Fields are the workbook's, as column names; the
workbook gives none for AT-3 and AT-4. The registry assigns 29 metrics to the COP but not to questions, so each
metric's question is assigned from its registry definition. Nine are not built and are OPTIONAL, reported
NOT_CALCULATED.

### AT-1 Can every relevant action be attributed to the correct agent, operator, tool or system?

`insights.unattributed`, `audit_actor_attribution_rate` (required); `audit_action_attribution_rate`,
`interaction.mcp_servers` (optional). Read with `reconstruct_trace`'s actor facts.
ACTOR_NOT_ESTABLISHED when an action or disk change carries no session or operator; CAUSE_NOT_ATTRIBUTED when
the actor is known but a disk change is attributed to no step; otherwise ATTRIBUTED. When only disk-change
attribution is unassessed (no file watcher) and actions carry no operator, the question is still answered
ACTOR_NOT_ESTABLISHED (D4). `audit_actor_attribution_rate` is reported, not judged: a row without an operator
belongs to its session's operator (D3).

### AT-2 Can the complete execution sequence be reconstructed from available traces and observables?

`audit_trace_completeness`, `audit_execution_reconstruction_rate`, `interaction.subagent_lanes` (required);
`telemetry.turn_linkage`, `telemetry.lane_linkage` (when applicable); `path.transition_novelty`,
`audit_reconstruction_confidence` (optional). SEQUENCE_BROKEN when a subagent lane has no parent step, or a
verified Interaction Discovery answered ID-2 NOT_RECONSTRUCTABLE. RECONSTRUCTED_WITH_GAPS when steps cite no
observable, cannot be placed in a turn and lane, have no turn id, a lane has no handoff, Runtime Monitoring RM-3
is PARTIALLY_LINKED or UNLINKED, or ID-2 is PARTIALLY_RECONSTRUCTED. Otherwise FULLY_RECONSTRUCTED. An upstream
answer that the record contradicts is kept as contrary evidence.

### AT-3 Are telemetry sources, timestamps, turn links and execution-lane relationships sufficiently reliable?

`telemetry.collector_coverage`, `telemetry.unknown_share` (required); `telemetry.source_agreement`,
`telemetry.turn_linkage`, `telemetry.lane_linkage` (when applicable); `audit_timestamp_integrity`,
`audit_event_ordering_consistency` (optional). Reads the CONFLICTING_SOURCES, INCOMPARABLE_TIMESTAMPS and
TIMESTAMP_ORDER checks. UNRELIABLE when source agreement is EXCEPTION or CRITICAL. DEGRADED when source agreement
is WARNING, an expected collector is missing, unknown share is not NORMAL, turn or lane linkage is in an
attention state, a timestamp check failed, rows are out of sequence order in time, or a verified Runtime
Monitoring is not SUFFICIENT. A check that could not run is listed as unassessed (D2).

### AT-4 Can each governance finding, decision and intervention be traced back to its supporting evidence?

`audit_metric_evidence_linkage` (when applicable); `audit_decision_evidence_linkage`,
`audit_intervention_traceability`, `audit_policy_version_traceability`, `audit_verifier_coverage`,
`audit_evidence_sufficiency` (optional, not built). Read with `check_decision_linkage`. NOT_TRACEABLE when an
upstream decision cites an observable or step the case does not hold; PARTIALLY_TRACEABLE when metric evidence
refs do not resolve; otherwise TRACEABLE. With no upstream decision, that is stated as a limitation.

### AT-5 Are missing, conflicting or unverifiable records preventing a defensible audit of the event?

`audit_missing_event_rate`, `audit_conflicting_record_rate`, `audit_unverifiable_record_rate`,
`audit_source_provenance_completeness`, `audit_raw_reference_resolution_rate`, `audit_evidence_integrity`,
`audit_evidence_retention_coverage` (all optional; the last three not built). Depends on AT-1 to AT-4.
NOT_DEFENSIBLE when an earlier answer is broken or unattributable, or a verified upstream COP is
BLOCKED_BY_EVIDENCE. IMPAIRED when an earlier answer has gaps or is unanswered, or on its own causes: conflicting
action records, calls with no result, inferred rows naming no source, rows naming no collector, or Interaction
Discovery ID-5 RECONSTRUCTION_IMPAIRED. Validated trace_gap records with the same gap codes are listed as
recurring.
