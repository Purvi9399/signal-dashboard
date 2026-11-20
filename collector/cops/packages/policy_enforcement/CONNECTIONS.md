# Policy Enforcement: connections

## Upstream

Read only when verified and substantive (`rules.verified`).

| Producer | Reads | Effect |
|---|---|---|
| Runtime Monitoring | label | INSUFFICIENT makes PE-5 INCONCLUSIVE (and `graph._gate_required` withholds ALLOWED); recorded in `upstream_used` |
| Interaction Discovery | the ID-3 answer's `new_routes` and `new_mcp_servers` | the routes PE-4 evaluates; a NEW_ROUTE label is never a violation by itself |
| Agent Lifecycle Management, Anomaly Detection | not read by this package | listed in `specs.CONSUMES`; VS-01 counts them as context |

## Downstream

| Consumer | Reads | Effect |
|---|---|---|
| Incident Response | verified VIOLATION and its proposed action (`rules.incident_response`) | a pending BLOCK becomes REQUEST_APPROVAL there; any other non-quiet label is an upstream claim |
| Audit & Traceability | the decision's evidence refs (`tools.t_check_decision_linkage`, AT-4) | resolvable refs keep AT-4 TRACEABLE (scenario downstream_audit_traceability) |
| Risk Mitigation | the verified finding and its lineage (`independent_upstream`) | an independent signal when lineage does not overlap |
| Knowledge & Policy Management | the finding | notified on rule conflicts and operations with no rule |
| Verifier PE-01 | `cop_specific.proposed` | re-evaluates BLOCK and REQUEST_APPROVAL targets against the policy |

## Tested

`tests/test_cop_package_policy_enforcement.py` and the harness. Interaction Discovery's harness checks that Policy
Enforcement reads `insights.outside` as policy-relevant without a policy and claims no violation
(D19 in its DECISIONS.md). Cases C01, C04, C05, C15, E2E and the bench keep their Policy Enforcement expectations.
