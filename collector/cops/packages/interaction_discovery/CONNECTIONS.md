# Interaction Discovery: connections

## Upstream

Runtime Monitoring. Read only when verified and substantive:

- its label scales confidence (`rules.evidence_conf`), and INSUFFICIENT withholds CONTAINED (`graph._gate_required`);
- its label, if not SUFFICIENT, and its RM-3 answer (`cop_specific.evidence_assessment.questions.RM-3.answer`),
  if PARTIALLY_LINKED or UNLINKED, are causes in ID-5;
- it is recorded in `upstream_used` and as an upstream claim.

## Downstream

| Consumer | Reads | Effect |
|---|---|---|
| Policy Enforcement | label NEW_ROUTE (`rules.policy_enforcement`) | a new route becomes an input to evaluate; with no policy store, ALLOWED becomes UNCERTAIN |
| Agent Lifecycle Management | label NEW_ROUTE | a connectivity change input |
| Resource Management | label, `cop_specific.edges`, and the ID-2 answer | attributes consumption to subagents and MCP servers only when ID-2 is FULLY_RECONSTRUCTED (changed in this package; before, only the label PATH_PARTIAL stopped it) |
| Risk Mitigation | the verified finding and its lineage (`independent_upstream`) | counted as an independent signal when its lineage does not overlap Risk Mitigation's metrics |
| Incident Response | any verified finding (`rules.incident_response`) | recorded as used with its label |
| Audit & Traceability | consumes the finding (`specs.CONSUMES`) | not yet read by its rule |

## Tested

`tests/test_cop_package_interaction_discovery.py` and the harness: Runtime Monitoring's RM-3 changes the label;
Policy Enforcement cites the NEW_ROUTE finding by id; Resource Management attributes through the finding when
ID-2 is complete, and refuses when it is not (`tests/test_cop_influence.py`).

## What a consumer receives

The published finding: label, status, verified, confidence, claims, lineage, `cop_specific.questions` with the
five answers and their bases, `drivers`, `also_held`, `contrary`, `edges`, `new_routes`. No consumer other than
Resource Management reads the question findings yet.
