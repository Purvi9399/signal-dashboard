# Risk Mitigation: specification

COP 8 of the per-COP verification (build order: Runtime Monitoring, Interaction Discovery, Audit & Traceability,
Policy Enforcement, Anomaly Detection, Resource Management, Classification & Tagging, Risk Mitigation). The
workbook and `docs/cops/cop_catalogue.json` number this COP 9; their number 8 is Policy Enforcement.

`METRICS.yaml` is the contract the runtime reads. This file explains it.

## Behavioural question

- Workbook (COMPLETE_DELEGATION_DATA row 132): "What risks arise from the current findings, and how should they be
  reduced?"
- `cops/specs.py` (unchanged): "Do the combined findings create a material or emerging risk, and what residual risk
  remains after available controls?"
- Partial input (row 13): "Combined upstream findings, severity, likelihood, residual risk, mitigation state."

## Questions

| Id | Workbook row | Question | Answers |
|---|---|---|---|
| RK-1 | 132 | What risks are indicated by the combined findings of the relevant COPs? | RISK_INDICATED, NO_RISK_INDICATED |
| RK-2 | 134 | How likely are those risks to materialize, and how severe would their consequences be? | MATERIAL_LEVEL, EMERGING_LEVEL, BELOW_THRESHOLDS |
| RK-3 | 136 | Which agents, resources, workflows or organizational assets are exposed? | EXPOSURE_IDENTIFIED, NO_EXPOSURE_IDENTIFIED |
| RK-4 | 138 | What mitigation measures are available, and which would reduce the risk proportionately? | CONTROL_AVAILABLE, NO_CONTROL_AVAILABLE, NOT_REQUIRED |

Every question can also be INCONCLUSIVE. The field lists are the workbook's column 3; the external data column is
kept as `external_data` on each question.

## Metrics (40, registry consumers = risk_mitigation)

| Question | Counted (observed risk) | Observed, reported, never counted | NOT_CALCULATED (registry NOT_IMPLEMENTED) |
|---|---|---|---|
| RK-1 | insights.secrets, insights.sensitive_unasked, insights.outside, insights.reverted, insights.token_outlier (`specs.metric_keys`, REQUIRED) | telemetry.collector_coverage, telemetry.source_agreement, telemetry.turn_linkage, telemetry.unknown_share | risk_evidence_sufficiency (D) |
| RK-2 | | insights.failed, insights.repetition, insights.retry, insights.stalled, insights.duration_outlier, activity.action_rate_ratio, path.transition_novelty, access.first_seen_mcp | risk_likelihood_score (R), risk_impact_score (R), risk_severity_score (O), risk_recurrence_rate (D) |
| RK-3 | | interaction.subagent_lanes, interaction.mcp_servers, telemetry.lane_linkage, insights.unattributed | risk_exposure_score (R), risk_asset_criticality (R), risk_affected_entity_count (D), risk_propagation_potential (D) |
| RK-4 | | insights.blocked, insights.no_oversight, oversight.fast_approvals, oversight.declined_inferred | risk_response_proportionality (O), risk_residual_score (O), risk_escalation_required (O), risk_intervention_cost (R), risk_mitigation_effectiveness (A), risk_mitigation_completion (A) |

25 built (category E), 15 NOT_CALCULATED with the registry's missing input. The spec's `missing_metrics`
(compound_risk_score, reversibility, residual_risk) are NOT_CALCULATED too (`not_calculated`). Nothing is
approximated.

## External references

| Reference | Source | Use |
|---|---|---|
| Risk register | case artifact `risk_register`, `tools.read_risk_register` (REQUIRED; approved and versioned, U-RK8) | impact per risk and sensitivity tier, EMERGING and MATERIAL thresholds, controls |
| Taxonomy family | Classification & Tagging's finding (context only, U-RK1) | listed in RK-1, never counted |
| Control prerequisites | `tools.evaluate_control_prerequisites` | which controls can run in this environment |
| Asset inventory | not supplied anywhere (registry `asset_inventory`, location not chosen) | RK-3 names exposure by event and finding only |
| Memory | `mitigation_outcome` (writes, reads), `incident_outcome` (reads, policy) | precedents for comparison only |

## Labels, actions, authority

| Label | From | Action | Severity | Confidence |
|---|---|---|---|---|
| MATERIAL | RK-2 MATERIAL_LEVEL | ESCALATE (INITIATE tier) | HIGH | 0.75 |
| EMERGING | RK-2 EMERGING_LEVEL | WATCH (RECORD tier) | MEDIUM | 0.75 |
| LOW | RK-2 BELOW_THRESHOLDS with RK-1 answered | NONE | NONE | 0.75 |
| INCONCLUSIVE | RK-2 unanswered, or LOW with RK-1 unanswered | the shared fallback (REQUEST_CONTEXT or PASS_TO_COP, triage) | NONE | 0.0 |

The values are the legacy rule's. Permitted actions are unchanged (NONE, WATCH, INSTRUCT, ESCALATE, PASS_TO_COP);
the triage ceilings are EMERGING RECOMMEND and MATERIAL INITIATE. The COP never applies a control, never blocks or
pauses, and never accepts residual risk on the owner's behalf.
