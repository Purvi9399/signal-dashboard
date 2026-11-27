# Risk Mitigation: connections

## Upstream (current cycle)

`specs.SPEC_CONSUMES["risk_mitigation"]`, unchanged: Anomaly Detection, Interaction Discovery, Policy
Enforcement, Resource Management, Incident Response. `docs/cops/cops_topology.json` now carries the same five
inputs (D-RK11). Each finding is listed in RK-1 `basis.upstream` with present, label, finding id, verified,
status, class (RISK, QUIET, UNDETERMINED, NOT_VERIFIED, ABSENT), lineage, counted and why.

| Producer | Risk-bearing labels | Usually |
|---|---|---|
| Policy Enforcement | VIOLATION, APPROVAL_REQUIRED | independent (lineage: tool calls); correlated in MCP sessions, where its lineage is Interaction Discovery's |
| Interaction Discovery | NEW_ROUTE, PROPAGATING | always correlated: its lineage holds insights.outside, one of this COP's counted metrics |
| Anomaly Detection | ANOMALOUS | unverified without a model when a deviation is seen; NORMAL is quiet |
| Resource Management | EXCESSIVE, UNAUTHORIZED | correlated on a token spike (insights.token_outlier); INEFFICIENT is quiet here (waste, not risk) |
| Incident Response | INCIDENT, WATCH | correlated when its lineage holds insights.secrets |

The workbook's RK-1 external data also names Classification & Tagging. It is not a consumed COP in
`specs.py` and was not added: U-RK1.

## Downstream

| Consumer | Edge | Reads | Effect |
|---|---|---|---|
| Incident Response | previous-cycle feedback (`specs.FEEDBACK`, verified only) | `cop_specific.earlier_risk_assessment` in `rules.incident_response` | context; its label is the same with and without it (scenario downstream_incident_response_feedback) |
| Notified | `cop_specific.recipients` | Incident Response for EMERGING and MATERIAL | |

Risk Mitigation has no current-cycle consumer. In `cops_topology.json` the Risk Mitigation to Incident Response
link is now marked `feedback` (D-RK11).
