# Incident Response: connections

## Upstream (current cycle)

`specs.CONSUMES["incident_response"]` is unchanged: Runtime Monitoring, Anomaly Detection, Policy Enforcement,
Interaction Discovery, Workflow Orchestration, Recovery & Resilience, Classification & Tagging and Resource
Management. IR-1 `basis.upstream` lists each finding with:
- present, label, finding id, verified, status and confidence;
- the producer's own proposed action (`action`) and the action its authority check let through (`final_action`);
- lineage, source items, class (EVIDENCE_QUALITY, INCIDENT, FINDING, ABNORMAL, CONTEXT, QUIET, UNDETERMINED,
  NOT_VERIFIED, ABSENT) and why.

| Producer | Effect on the answers (harness scenario) |
|---|---|
| Policy Enforcement VIOLATION, proposes BLOCK | INCIDENT HIGH, ONGOING, INTERVENTION, REQUEST_APPROVAL (verified_violation_intervention) |
| Policy Enforcement APPROVAL_REQUIRED | WATCH LOW (approval_required_without_violation). On the exposure's own row it is correlated and adds nothing (correlated_findings_counted_once). Its own approval workflow is untouched |
| Anomaly Detection ANOMALOUS, ALERT | WATCH MEDIUM ALERT (isolated_anomaly_watch). With an exposure and a source item of its own: INCIDENT HIGH, ESCALATE (escalation_exposure_with_anomaly) |
| Anomaly Detection unverified | nothing (false_positive_resource_spike) |
| Recovery & Resilience FAILED_RECOVERY / PARTIAL_RECOVERY / RECOVERED | INCIDENT HIGH ESCALATE / WATCH LOW / NO_INCIDENT (never RESOLVED) |
| Runtime Monitoring INSUFFICIENT | with no ground, INCONCLUSIVE (incomplete_telemetry_insufficient) |
| Runtime Monitoring DEGRADED | a ground stands; confidence falls (partial_evidence_reduces_certainty) |
| Interaction Discovery, Resource Management, Classification & Tagging | context only; the same answers without them (false_positive_new_route, ct_context_only) |

## Downstream

| Consumer | Edge | Reads | Effect |
|---|---|---|---|
| Risk Mitigation | current cycle (`specs.CONSUMES`) | the label. INCIDENT is a risk-bearing signal when independent; WATCH is an advisory condition (P-RK2) | unchanged contract (downstream_contracts) |
| Governance Intelligence | current cycle (`specs.CONSUMES`) | the label, from the decision store | unchanged |
| Notified | `cop_specific.recipients` | Risk Mitigation and Governance Intelligence for WATCH and INCIDENT | |

## Previous cycle

Risk Mitigation to Incident Response is feedback (`specs.FEEDBACK`, verified only). It is context and is never
counted (D-IR15). Incident Response runs before Risk Mitigation, so there is no circular reasoning: Risk
Mitigation counts this COP's INCIDENT, and this COP never counts Risk Mitigation's band.

## Topology document

`docs/cops/cops_topology.json` now carries this COP's edges exactly (D-IR18):
- Interaction Discovery to Incident Response and Resource Management to Incident Response (added);
- Incident Response to Governance Intelligence (added);
- `reads_decisions_from` = `specs.CONSUMES`, and `downstream` = Risk Mitigation and Governance Intelligence.

No other COP's edges changed, except that Interaction Discovery's and Resource Management's `downstream` lists
now include Incident Response.

The workbook also names Recovery & Resilience as a downstream consumer (agents-harness row 10). That edge is not in
`specs.CONSUMES` (U-IR5).
