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
| Policy Enforcement | VIOLATION, APPROVAL_REQUIRED | independent when it rests on a decision this COP does not observe (a denied or approval-pending proposed operation); correlated when its decision is about a row this COP already observes (an approval missing on the secret read), or in MCP sessions, where its lineage is Interaction Discovery's |
| Interaction Discovery | NEW_ROUTE, PROPAGATING | always correlated: its lineage holds insights.outside, one of this COP's counted metrics |
| Anomaly Detection | ANOMALOUS | unverified without a model when a deviation is seen; NORMAL is quiet |
| Resource Management | EXCESSIVE, UNAUTHORIZED | correlated on a token spike (insights.token_outlier); independent on a quota exceeded (the quota record is distinct evidence); INEFFICIENT is quiet here (waste, not risk) |
| Incident Response | INCIDENT, WATCH | correlated when its lineage holds insights.secrets, or when it rests on the same row or on an upstream finding already counted |
| Classification & Tagging | none: context only (U-RK1) | listed in RK-1 `basis.context`; a family is a kind, not a risk |

Downstream effect of the new edge: VS-01 (`support._risk`) builds one supporting component per consumed COP, so it
now has an `upstream:classification_tagging` component. VS-01 is unchanged. When the family's lineage does not share
this COP's metrics, the component opposes a LOW reading. Probe: a session with one blocked call and the
synthetic taxonomy reads Classification & Tagging OVERSIGHT (lineage insights.blocked), and Risk Mitigation stays
LOW and verified, but VS-01 moves from SUPPORTED to PROVISIONALLY_SUPPORTED. No harness, evaluation or bench case
moved. P-RK3 in DECISIONS.md proposes making this component neutral for a context-only input.

## Downstream

| Consumer | Edge | Reads | Effect |
|---|---|---|---|
| Incident Response | previous-cycle feedback (`specs.FEEDBACK`, verified only) | `cop_specific.earlier_risk_assessment` in `rules.incident_response` | context; its label is the same with and without it (scenario downstream_incident_response_feedback) |
| Notified | `cop_specific.recipients` | Incident Response for EMERGING and MATERIAL | |

Risk Mitigation has no current-cycle consumer. In `cops_topology.json` the Risk Mitigation to Incident Response
link is now marked `feedback` (D-RK11).
