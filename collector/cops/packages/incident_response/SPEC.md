# Incident Response: specification

COP 9 in the build order (Runtime Monitoring, Interaction Discovery, Audit & Traceability, Policy Enforcement,
Anomaly Detection, Resource Management, Classification & Tagging, Risk Mitigation, Incident Response). The
workbook and `docs/cops/cop_catalogue.json` number this COP 6. The build order is recorded apart from that
numbering.

`METRICS.yaml` is the contract the runtime reads. This file explains it.

## Behavioural question

- `cops/specs.py` (unchanged): "Do the current findings constitute an actionable incident, and what should happen
  now?"
- Workbook COMPLETE_DELEGATION_DATA rows 94 to 102 give the five questions below. Row 104 lists the 20 built
  metrics.

## Questions

| Id | Question | Answers |
|---|---|---|
| IR-1 | Do the available findings indicate an actionable incident? | INCIDENT_INDICATED, FINDING_WITHOUT_INCIDENT, NO_INCIDENT_INDICATED |
| IR-2 | What is the severity, scope and potential impact of the incident? | SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, NOTHING_TO_RATE |
| IR-3 | Is the incident ongoing, contained, resolved or still uncertain? | ONGOING, CONTAINED, RESOLVED (never reached, D-IR8), NO_INCIDENT_STATE |
| IR-4 | Do upstream COP findings provide sufficient evidence to justify intervention? | INTERVENTION_JUSTIFIED, ATTENTION_JUSTIFIED, NOT_JUSTIFIED |
| IR-5 | What immediate response is warranted, and does it require escalation or human approval? | REQUEST_APPROVAL, ESCALATE_TO_PERSON, ALERT, WATCH, NO_RESPONSE |

Every question can also be INCONCLUSIVE. For IR-3 that means "still uncertain". IR-2 and IR-3 depend on IR-1,
IR-4 on IR-1 and IR-3, and IR-5 on IR-2 and IR-4 (`METRICS.yaml depends_on`).

## Metrics (34, registry consumers = incident_response)

| Question | Built (category E) | NOT_CALCULATED (registry NOT_IMPLEMENTED) |
|---|---|---|
| IR-1 | insights.secrets, insights.sensitive_unasked, insights.blocked (REQUIRED: `specs.metric_keys`), insights.outside | incident_trigger_score |
| IR-2 | interaction.subagent_lanes, interaction.mcp_servers, access.first_seen_mcp, insights.unattributed | incident_severity_score, incident_scope, incident_impact_score |
| IR-3 | insights.failed, insights.reverted, insights.retry, insights.stalled | incident_active_duration, incident_containment_status, incident_resolution_status |
| IR-4 | telemetry.collector_coverage, telemetry.source_agreement, telemetry.turn_linkage, telemetry.lane_linkage, telemetry.unknown_share | incident_evidence_sufficiency, incident_upstream_agreement |
| IR-5 | insights.no_oversight, oversight.fast_approvals, oversight.declined_inferred | incident_response_urgency, incident_escalation_required, incident_human_approval_required, incident_intervention_success, incident_residual_risk |

That is 20 built metrics and 14 NOT_CALCULATED, each with the registry's missing input. The spec's
`missing_metrics` (time_to_harm, intervention_available, affected_entities) are NOT_CALCULATED too
(`not_calculated`). Nothing is approximated. Only the three `specs.metric_keys` ground anything. The other built
metrics are reported in each question's basis as scope, state, telemetry or oversight context.

## Upstream findings read

These are `specs.CONSUMES["incident_response"]`, unchanged. `METRICS.yaml upstream` gives each label's class.

| Producer | Incident ground | Finding without incident | Quiet | Otherwise |
|---|---|---|---|---|
| Runtime Monitoring | | | SUFFICIENT | evidence quality only: INSUFFICIENT (with no ground, IR-1 INCONCLUSIVE); DEGRADED lowers confidence |
| Policy Enforcement | VIOLATION | APPROVAL_REQUIRED (D-IR3) | ALLOWED | |
| Recovery & Resilience | FAILED_RECOVERY, IRREVERSIBLE | PARTIAL_RECOVERY (D-IR3) | RECOVERED | |
| Anomaly Detection | | ANOMALOUS | NORMAL | INDETERMINATE: undetermined, a limitation |
| Interaction Discovery, Workflow Orchestration, Resource Management | | | | context, never a ground (D-IR2) |
| Classification & Tagging | | | UNCLASSIFIED | context only (D-IR16) |

Only a verified substantive finding counts. An unverified finding grounds nothing. When it carries an
incident-relevant label, it is a reason for INCONCLUSIVE.

## Labels, severity, actions, authority

| Label | From | Severity (IR-2) | Action (IR-5) | Confidence |
|---|---|---|---|---|
| INCIDENT | IR-1 INCIDENT_INDICATED | HIGH or MEDIUM | REQUEST_APPROVAL, ESCALATE or ALERT | the legacy rule's: the lowest confidence of the verified RM, AD, PE and ID findings used, else 0.8 x the evidence level |
| WATCH | IR-1 FINDING_WITHOUT_INCIDENT | MEDIUM (anomaly with AD ALERT) or LOW | ALERT or WATCH | the same |
| NO_INCIDENT | IR-1 NO_INCIDENT_INDICATED | NONE | NONE | the same |
| INCONCLUSIVE | IR-1 INCONCLUSIVE, or an intervention incident the model did not confirm (D-IR11) | NONE | WATCH (the triage fallback for INCONCLUSIVE applies) | 0.0 |

Permitted actions are unchanged (NONE, WATCH, ALERT, INSTRUCT, REQUEST_APPROVAL, PAUSE, BLOCK, ESCALATE). The
package proposes only NONE, WATCH, ALERT, REQUEST_APPROVAL and ESCALATE. It never proposes PAUSE, BLOCK or INSTRUCT
(D-IR12). A block is requested through REQUEST_APPROVAL, and it executes only after a person approves it. In dry
run, nothing executes. The triage ceilings are unchanged: WATCH has the ALERT ceiling and INCIDENT has the
INTERVENE ceiling.

## Five readings kept apart (`cop_specific.incident_status.category`)

| Category | When |
|---|---|
| OBSERVED_ABNORMAL | WATCH on a verified anomaly only. Unusual is not wrong |
| VERIFIED_FINDING | WATCH on a verified governance finding (APPROVAL_REQUIRED, PARTIAL_RECOVERY) or a deny rule that fired |
| INCIDENT_ATTENTION | INCIDENT with IR-4 ATTENTION_JUSTIFIED |
| INCIDENT_INTERVENTION | INCIDENT with IR-4 INTERVENTION_JUSTIFIED, the model's plan confirmed |
| INCIDENT_UNCONFIRMED | IR-1 indicates an intervention incident, but the model did not confirm a plan: the label is INCONCLUSIVE and the answers are kept |
| INSUFFICIENT_EVIDENCE | IR-1 INCONCLUSIVE |
| NONE | NO_INCIDENT |
