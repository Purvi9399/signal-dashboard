# Recovery & Resilience: connections

## Current cycle (specs.CONSUMES, unchanged)

| Edge | Use |
|---|---|
| Behavior Analytics → Recovery & Resilience | context: listed with its label and source items, never a ground (D-RR8) |
| Recovery & Resilience → Incident Response | IR reads FAILED_RECOVERY and IRREVERSIBLE as incident grounds, PARTIAL_RECOVERY as a finding and RECOVERED as quiet (Incident Response METRICS.yaml upstream). INCONCLUSIVE is not verified substantive and grounds nothing |
| Recovery & Resilience → Governance Intelligence | GI reads this COP's decisions across sessions (unpackaged) |

## Previous cycle (specs.FEEDBACK, added in R-RR1)

| Edge | Use |
|---|---|
| Incident Response → Recovery & Resilience | verified only (`FEEDBACK_VERIFIED_ONLY`); context only, read after the four answers and never by them |

`docs/cops/cops_topology.json` carries these edges, the feedback one as a `feedback` link. Its
`reads_decisions_from` for this COP names behavior_analytics, as specs.CONSUMES does.

## The Incident Response ↔ Recovery & Resilience dependency

The workbook asks this COP to read Incident Response's findings:
- COMPLETE_DELEGATION_DATA row 197: "Upstream Incident Response findings ...";
- agents-harness row 17: "Incident/action outcome";
- Incident Response's agents-harness row 10 names Recovery & Resilience downstream.

Incident Response consumes this COP in the current cycle, so a current-cycle edge back would close the cycle
IR → RR → IR. The harness checks this (`current_edge_back_would_cycle`). purvi approved the previous-cycle edge
P-RR1 on 2026-11-27 (A-RR1). It works as follows.

**Delivery** (shared `graph.prior_feedback`, unchanged): in cycle n, Incident Response's finding from an earlier
cycle reaches this COP only if it is verified, names its run_id and that run_id is not cycle n's. The consumption
row records it with cycle "previous" and its source_run_id.

**Reading** (`cop_package.rr_earlier_incident`, called by `rules._rr_questions` after the answers):

| Status | When |
|---|---|
| NOT_DELIVERED | nothing came on the edge (none, unverified, current cycle, no run_id; the feedback row gives the reason) |
| REJECTED_UNVERIFIED | not a verified SUBSTANTIVE finding |
| REJECTED_NO_CYCLE | no run_id |
| REJECTED_NO_TIMESTAMP | no usable decided_at |
| REJECTED_WRONG_SESSION | another session, and no recovery record's disruption_refs names the finding |
| REJECTED_STALE | decided before this session's first undisputed disruption: it cannot name it |
| LISTED_NO_RESTORATION | no restoration time to contextualize |
| REJECTED_ORDER | decided at or after the latest restoration time |
| ACCEPTED_AS_CONTEXT | otherwise |

An accepted finding is recorded in `cop_specific.earlier_incident` and `recovery_status.earlier_incident`, with its
finding_id, run_id, decided_at, session_id, association (SESSION or INCIDENT), label, grounds and containment plan
status. It never grounds FAILED_RECOVERY, IRREVERSIBLE, PARTIAL_RECOVERY or RECOVERED. It changes no answer, label,
severity, confidence or action. The memory payload holds only its finding_id.

**Safeguards**
- Incident Response's earlier finding is never modified: only identity fields are copied.
- An earlier incident grounded on this COP's own earlier reading (Incident Response's failed_recovery or
  partial_recovery ground) is flagged `rests_on_this_cop` and never read as confirmation.
- Incident Response in cycle n reads this COP's cycle-n finding, as before.
- Harness: `same_without_feedback` in every p1 scenario, `every_cycle_same_without_feedback` and
  `ir_same_with_and_without_rr_feedback` over three cycles, `earlier_ir_finding_unmodified`,
  `feedback_edge_cycle_safe`.

An Incident Response INCIDENT is still not evidence that recovery failed (`incident_does_not_establish_failure`), and
a recovery finding changes neither its inputs nor Incident Response's evidence (`rr_inputs_unchanged`,
`rr_same_without_ir`).

## Downstream contract

The published finding carries:
- the label, severity, confidence, proposed action and the four question answers;
- `recovery_status` (intervention and recovery states kept apart);
- `legacy_reading`.

Recipients for a substantive reading are `specs.consumers("recovery_resilience")`: Incident Response and Governance
Intelligence.
