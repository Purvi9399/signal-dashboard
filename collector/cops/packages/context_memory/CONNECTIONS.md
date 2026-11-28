# Context & Memory: connections

## Current cycle (specs.CONSUMES, unchanged)

| Edge | Use |
|---|---|
| Runtime Monitoring → Context & Memory | context only; the shared precheck withholds the quiet label when telemetry is INSUFFICIENT (D-CM16) |
| Context & Memory → Behavior Analytics | Behavior Analytics receives this COP's finding (a retrieval label) and its bundle. BA-4 classes the new labels as CONTEXT: neither altered nor quiet, so BA-4 reads NO_ALTERATION_SHOWN. BA's code and ground truth are unchanged (P-CM1) |

No feedback edge. No edge is added: the topology, `specs.FEEDBACK` and `topological_order` are unchanged, and no
cycle is introduced (harness `topology`).

## Retrieval contract for every COP (D-CM18)

Other COPs do not consume this COP's finding to get memory. They request memory through the retrieval contract
(`graph.retrieve_memory`, `tools._memory_lookup`) and receive a structured bundle under their own policy. They keep
their own interpretation:

| Consumer | What it receives | Tested |
|---|---|---|
| Incident Response | validated earlier incident_outcome records (and anomaly_observation, enforcement_record, recovery_outcome) | `runtime_ir_consumes` |
| Recovery & Resilience | validated earlier recovery_outcome records | `runtime_rr_consumes` |
| Anomaly Detection | its anomaly_observation and behaviour_baseline records | `runtime_ad_consumes` |
| Behavior Analytics | behaviour_baseline, context_integrity_finding and workflow_failure records, as context (D-BA10) | `runtime_ba_precedent`, `runtime_poisoned_store` |

Current-cycle and historical records stay distinct (`cycle`). A requester's own earlier output is marked
OWN_EARLIER_OUTPUT and never corroborates its current finding.

## Consumers of the previous contract

| Consumer | Previous dependency | Now |
|---|---|---|
| Behavior Analytics BA-4 and legacy rule | CONTEXT_LOSS or CONTRADICTORY: altered context, DRIFTING | not reachable from a real Context & Memory finding; DRIFTING still reachable from Workflow Orchestration. No adapter (D-CM6). Migration proposed: P-CM1 |
| Evaluation C09, D-CM, E2E, E2E-full; bench context_memory cases | the previous labels and snapshot tools | migrated (D-CM19); the previous expectations are kept verbatim in DECISIONS.md |

## Workbook recipients not wired

agents-harness row 8 (Runtime, Incident, Policy, Governance Intelligence) and many-to-many row 9 (Workflow
Orchestration, Recovery & Resilience). Every COP already reads its memory through this contract; explicit edges
would change the topology (P-CM2).

`docs/cops/cops_topology.json` carries this COP's question, labels, the Runtime Monitoring link and
`reads_decisions_from: [runtime_monitoring]`.
