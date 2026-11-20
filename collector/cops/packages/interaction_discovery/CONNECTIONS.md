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
| Agent Lifecycle Management | label NEW_ROUTE | a connectivity change input; used for a new route, not used otherwise (tested 2026-11-20) |
| Resource Management | label, `cop_specific.edges`, and the ID-2 answer | attributes consumption to subagents and MCP servers only when ID-2 is FULLY_RECONSTRUCTED, so since U5 only when every subagent link records `parent_agent_id` (changed in this package; before, only the label PATH_PARTIAL stopped it) |
| Risk Mitigation | the verified finding and its lineage (`independent_upstream`) | counted as an independent signal when its lineage does not overlap Risk Mitigation's metrics |
| Incident Response | any verified finding (`rules.incident_response`) | recorded as used with its label |
| Audit & Traceability | the ID-2 answer (`cop_package._at2`) | NOT_RECONSTRUCTABLE breaks AT-2, PARTIALLY_RECONSTRUCTED is a gap in it. A late handoff makes AT PARTIAL, the same session with ordered handoffs COMPLETE (tested 2026-11-20) |

## Tested

`tests/test_cop_package_interaction_discovery.py` and the harness: Runtime Monitoring's RM-3 changes the label;
Policy Enforcement cites the NEW_ROUTE finding by id; Resource Management attributes through the finding when
ID-2 is complete, and refuses when it is not (`tests/test_cop_influence.py`); Audit & Traceability's AT-2 and
label follow ID-2; Agent Lifecycle Management uses the finding only when it is NEW_ROUTE.

## What a consumer receives

The published finding: label, status, verified, confidence, claims, lineage, `cop_specific.questions` with the
five answers and their bases, `drivers`, `also_held`, `contrary`, `limitations`, `edges`, `new_routes`. Resource
Management and Audit & Traceability read the question findings; the others read the label.
