# Recovery & Resilience: connections

## Current cycle (specs.CONSUMES, unchanged)

| Edge | Use |
|---|---|
| Behavior Analytics → Recovery & Resilience | context: listed with its label and source items, never a ground (D-RR8) |
| Recovery & Resilience → Incident Response | IR reads FAILED_RECOVERY and IRREVERSIBLE as incident grounds, PARTIAL_RECOVERY as a finding and RECOVERED as quiet (Incident Response METRICS.yaml upstream). INCONCLUSIVE is not verified substantive and grounds nothing |
| Recovery & Resilience → Governance Intelligence | GI reads this COP's decisions across sessions (unpackaged) |

`docs/cops/cops_topology.json` carries these edges. Its `reads_decisions_from` for this COP now names
behavior_analytics, as specs.CONSUMES does.

## The Incident Response ↔ Recovery & Resilience dependency

The workbook asks this COP to read Incident Response's findings:
- COMPLETE_DELEGATION_DATA row 197: "Upstream Incident Response findings ...";
- agents-harness row 17: "Incident/action outcome";
- Incident Response's agents-harness row 10 names Recovery & Resilience downstream.

Incident Response already consumes this COP in the current cycle, so a current-cycle edge back would close the
cycle IR → RR → IR. The harness checks this (`current_edge_back_would_cycle`).

What is implemented (D-RR13):
- This COP reads no Incident Response finding, in this cycle or an earlier one.
- RR-1 reads the session's own timestamped rows and the recovery records, which carry their own action, approval
  and execution times.
- An Incident Response INCIDENT is not evidence that recovery failed (harness `incident_does_not_establish_failure`).
- A recovery finding changes neither its inputs nor Incident Response's evidence (`rr_inputs_unchanged`,
  `rr_same_without_ir`).

### P-RR1: the proposed cycle-safe design (for approval, not implemented)

The proposal is a previous-cycle feedback edge, `specs.FEEDBACK["recovery_resilience"] = ("incident_response",)`,
verified only, as Risk Mitigation → Incident Response already is. It would work as follows:
- In cycle n, RR-1 would read Incident Response's verified finding from cycle n-1 (`graph` feedback, with its run_id
  and decided_at).
- That finding would be context only. It would name the disruption and its containment plan. It would be used only
  when its decided_at is earlier than the recovery record's restoration time.
- It would never ground FAILED_RECOVERY or IRREVERSIBLE and never change an answer.
- Incident Response in cycle n then reads this COP's cycle-n finding, as today. Incident Response's cycle n-1 finding
  is never modified.

Why it is cycle safe:
- it adds no current-cycle edge;
- it passes the `specs` assertion (the producer is consumed elsewhere and is not a current-cycle input of the
  consumer), which the harness checks as `proposed_feedback_edge_cycle_safe`.

It is a new feedback relationship, so it waits for purvi's decision (U-RR8). Incident Response's
`deferred_downstream` entry (U-IR5) names the same edge.

## Downstream contract

The published finding carries:
- the label, severity, confidence, proposed action and the four question answers;
- `recovery_status` (intervention and recovery states kept apart);
- `legacy_reading`.

Recipients for a substantive reading are `specs.consumers("recovery_resilience")`: Incident Response and Governance
Intelligence.
