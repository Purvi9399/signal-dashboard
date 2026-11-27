# Behavior Analytics: connections

## Current cycle (specs.CONSUMES, unchanged)

| Edge | Use |
|---|---|
| Runtime Monitoring → Behavior Analytics | evidence quality: scales the confidence (evidence_conf) |
| Context & Memory → Behavior Analytics | BA-4: a verified CONTEXT_LOSS or CONTRADICTORY is altered context (DRIFTING when BA-1 shows no pattern) |
| Workflow Orchestration → Behavior Analytics | BA-4: a verified DRIFTING or UNEXPECTED_BRANCH is altered context |
| Behavior Analytics → Anomaly Detection | AD reads LOOPING and POOR_RECOVERY and marks them correlated with its own metrics (`independent_upstream`): never independent confirmation |
| Behavior Analytics → Recovery & Resilience | context only (D-RR8, accepted): listed with its label and source items, never a ground. RR's label does not change because a pattern exists |

## Previous cycle (specs.FEEDBACK, unchanged)

| Edge | Use |
|---|---|
| Anomaly Detection → Behavior Analytics | verified only (`FEEDBACK_VERIFIED_ONLY`); context for persistence, never an occurrence (D-BA9). SAME_EVENTS when it is about this same session |

`docs/cops/cops_topology.json` now carries these edges: the Runtime Monitoring link and the Anomaly Detection
feedback link were added, and `reads_decisions_from` corrected from [] to the three consumed COPs, matching
specs.CONSUMES. No runtime edge changed.

## Consumption tested

The harness reads the graph's consumption rows: Anomaly Detection and Recovery & Resilience each consume this
COP's finding by its finding_id, not merely find it in a shared record. AD's AD-1 records it as "correlated, not
counted". RR lists it as context with `ground: false`, and RR reads the same label, answers and action with and
without this COP.

## Not wired (P-BA1)

The workbook names further recipients: Governance Intelligence and Risk Mitigation (agents-harness row 16),
Workflow Orchestration and Governance Intelligence (many-to-many row 17). None is a runtime edge:
- Risk Mitigation would gain a new current-cycle input, changing an accepted COP;
- Workflow Orchestration is upstream of this COP, so only a previous-cycle edge would be cycle safe;
- Governance Intelligence is unpackaged.

Each is proposed as P-BA1 (U-BA8).
