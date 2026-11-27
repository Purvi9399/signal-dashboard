# Risk Mitigation: connections

## Upstream (current cycle)

`specs.SPEC_CONSUMES["risk_mitigation"]`, unchanged: Anomaly Detection, Interaction Discovery, Policy
Enforcement, Resource Management, Incident Response. Since revision 1 (purvi, U-RK1)
`specs.TOPOLOGY_CONSUMES["risk_mitigation"]` adds Classification & Tagging, context only. The graph stays acyclic:
Classification & Tagging runs before Incident Response, which runs before Risk Mitigation
(`specs.topological_order`). `docs/cops/cops_topology.json` carries the six inputs (D-RK11, D-RK18). Each finding
is listed in RK-1 `basis.upstream` with present, label, finding id, verified, status, class (RISK, QUIET,
UNDETERMINED, CONTEXT, NOT_VERIFIED, ABSENT), lineage, source items, counted and why.

| Producer | Risk-bearing labels | Usually |
|---|---|---|
| Policy Enforcement | VIOLATION (APPROVAL_REQUIRED is a condition, never counted: P-RK2) | independent when it rests on a decision this COP does not observe (a denied or approval-pending proposed operation); correlated when its decision is about a row this COP already observes (an approval missing on the secret read), or in MCP sessions, where its lineage is Interaction Discovery's |
| Interaction Discovery | NEW_ROUTE, PROPAGATING | always correlated: its lineage holds insights.outside, one of this COP's counted metrics |
| Anomaly Detection | ANOMALOUS | unverified without a model when a deviation is seen; NORMAL is quiet |
| Resource Management | EXCESSIVE, UNAUTHORIZED | correlated on a token spike (insights.token_outlier); independent on a quota exceeded (the quota record is distinct evidence); INEFFICIENT is quiet here (waste, not risk) |
| Incident Response | INCIDENT (WATCH is an advisory condition, never counted: P-RK2) | correlated when its lineage holds insights.secrets, or when it rests on the same row or on an upstream finding already counted |
| Classification & Tagging | none: context only (U-RK1) | listed in RK-1 `basis.context`; a family is a kind, not a risk |

Downstream effect of the new edge: VS-01 (`support._risk`) builds one supporting component per consumed COP, so it
has an `upstream:classification_tagging` component. Since P-RK3 (D-RK21) it is not applicable evidence: neutral,
excluded from weights, so a family neither supports nor opposes a reading (scenarios preventive_control_succeeded,
context_only_neutral_in_vs01). Other COPs' VS-01 profiles are unchanged.

## Downstream

| Consumer | Edge | Reads | Effect |
|---|---|---|---|
| Incident Response | previous-cycle feedback (`specs.FEEDBACK`, verified only) | `cop_specific.earlier_risk_assessment` in `rules.incident_response` | context; its label is the same with and without it (scenario downstream_incident_response_feedback) |
| Notified | `cop_specific.recipients` | Incident Response for EMERGING and MATERIAL | |

Risk Mitigation has no current-cycle consumer. In `cops_topology.json` the Risk Mitigation to Incident Response
link is now marked `feedback` (D-RK11).
