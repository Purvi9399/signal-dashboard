# Context & Memory: connections

## Current cycle (specs.CONSUMES; reconciled by P-CM1, purvi 2026-11-28)

| Edge | Use |
|---|---|
| Runtime Monitoring → Context & Memory | context only; the shared precheck withholds the quiet label when telemetry is INSUFFICIENT (D-CM16) |

Context & Memory has no current-cycle consumer. The Context & Memory → Behavior Analytics edge existed only for the
retired CONTEXT_LOSS / CONTRADICTORY detector and is removed (P-CM1, A-CM1); no COP consumes this COP's finding. No
feedback edge, no edge added, no cycle (harness `topology`, superseded checks topology_reconciled and no_cycle).

In a case run, this COP makes one request per other COP of the case that has a memory policy, each under that COP's
own policy (D-CM21), and never serves a requester its own output of this same run (D-CM22). Its finding describes
what was retrievable; nothing reads it as evidence.

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
OWN_EARLIER_OUTPUT and never corroborates its current finding: in VS-01 (`support._memory`) only an established
record owned by another COP can support (U-CM9; harness `vs01_own_output`, `vs01_other_cop_record`).

## Consumers of the previous contract

| Consumer | Previous dependency | Now |
|---|---|---|
| Behavior Analytics BA-4 and legacy rule | CONTEXT_LOSS or CONTRADICTORY: altered context, DRIFTING | dependency removed (P-CM1): BA-4 and the legacy rule read Workflow Orchestration only; DRIFTING still reachable from Workflow Orchestration (BA harness drifting_from_workflow). No adapter (D-CM6) |
| Evaluation C09, D-CM, E2E, E2E-full; bench context_memory cases | the previous labels and snapshot tools | migrated (D-CM19); the previous expectations are kept verbatim in DECISIONS.md. The bench cases are deterministic cases since U-CM5 (`bench_cases.DET_CASES`) |

## Workbook recipients not wired

agents-harness row 8 (Runtime, Incident, Policy, Governance Intelligence) and many-to-many row 9 (Workflow
Orchestration, Recovery & Resilience). Every COP already reads its memory through this contract; explicit edges
would change the topology (P-CM2).

`docs/cops/cops_topology.json` carries this COP's question, labels, the Runtime Monitoring link,
`reads_decisions_from: [runtime_monitoring]` and `downstream: []`.
