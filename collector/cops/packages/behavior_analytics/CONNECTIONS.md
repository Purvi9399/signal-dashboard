# Behavior Analytics: connections

## Current cycle (specs.CONSUMES; Context & Memory edge removed by P-CM1, purvi 2026-11-28)

| Edge | Use |
|---|---|
| Runtime Monitoring → Behavior Analytics | evidence quality: scales the confidence (evidence_conf) |
| Workflow Orchestration → Behavior Analytics | BA-4: a verified DRIFTING or UNEXPECTED_BRANCH is altered context (DRIFTING when BA-1 shows no pattern); the only altered-context input (harness drifting_from_workflow, unverified_workflow) |

Removed (A-BA1): Context & Memory → Behavior Analytics. It served only Context & Memory's retired CONTEXT_LOSS /
CONTRADICTORY labels. A Context & Memory finding of any label is not read (harness retired_context_label_ignored).
Memory is still read through the shared retrieval interface (`tools._memory_lookup` → `cop_package.retrieve_memory`):
behaviour_baseline, context_integrity_finding (records written under Context & Memory's previous contract) and
workflow_failure, as context only, never a finding (harness memory_never_a_finding).
| Behavior Analytics → Anomaly Detection | AD reads LOOPING and POOR_RECOVERY and marks them correlated with its own metrics (`independent_upstream`): never independent confirmation |
| Behavior Analytics → Recovery & Resilience | context only (D-RR8, accepted): listed with its label and source items, never a ground. RR's label does not change because a pattern exists |

## Previous cycle (specs.FEEDBACK, unchanged)

| Edge | Use |
|---|---|
| Anomaly Detection → Behavior Analytics | verified only (`FEEDBACK_VERIFIED_ONLY`); context for persistence, never an occurrence (D-BA9). SAME_EVENTS when it is about this same session |

`docs/cops/cops_topology.json` carries these edges: the Runtime Monitoring link and the Anomaly Detection feedback
link, and `reads_decisions_from: [runtime_monitoring, workflow_orchestration]`, matching specs.CONSUMES. The Context &
Memory link was removed with P-CM1.

## Memory written (U-BA5)

LOOPING, POOR_RECOVERY and DRIFTING are written as before. A verified STABLE is written as a VERIFIED
behaviour_baseline record with `memory_use: contextual_baseline`; an unverified STABLE is not written. The COP
cannot validate its own record (feedback from `cop:behavior_analytics` is denied); only an independent reviewer
can, and even a validated record of its own is context for this COP, never an occurrence (D-BA10), and never VS-01
corroboration of its own finding (U-CM9).

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

Each was proposed as P-BA1. Decision U-BA8 (purvi 2026-11-28): the Risk Mitigation and Workflow Orchestration
connections stay deferred and unwired. Governance Intelligence is recorded as a future context-only consumer: when
it is packaged it may read this COP's finding as context, never as a ground, through a new edge decided then.
