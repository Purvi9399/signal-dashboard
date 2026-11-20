---
name: cops-interaction-discovery
description: Maintain or extend the Interaction Discovery COP package (collector/cops/packages/interaction_discovery) in signal-dashboard; use when changing its questions, metrics, link provenance, label decision, upstream reads, route_record memory, harness or ground truth.
---

# Interaction Discovery COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads `collector/cops/packages/interaction_discovery/METRICS.yaml`, validated by `cop_package.validate`.

## Where things are

- **Contract:** `METRICS.yaml` in the package: the five questions (workbook COMPLETE_DELEGATION_DATA rows 43-47), the metric mapping, `propagation_states` and operations. `interaction_unattributed_edges` is not built and stays in the mapping with a `note` naming the missing input.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` in the same folder.
- **Ground truth:** `GROUND_TRUTH.yaml`. The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Interaction Discovery section (`interaction_links`, `observe_interaction_discovery`, `decide_interaction_discovery`). The rule `rules.interaction_discovery` only records claims and publishes what the decision returns.
- **Tools:** `build_interaction_graph`, `compare_known_routes`, `assess_interaction_questions` (`tools.py`, in this order in `rules.DETERMINISTIC_TOOLS`).
- **Downstream:** Policy Enforcement and Agent Lifecycle Management read NEW_ROUTE; Resource Management and Audit & Traceability read the ID-2 answer.
- **Tests:** `tests/test_cop_package_interaction_discovery.py` and `tests/test_cop_influence.py`.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("interaction_discovery")` must return `[]`.
2. The five answers decide the label in the order NEW_ROUTE, PROPAGATING, PATH_PARTIAL, INCONCLUSIVE, CONTAINED (D2, D3 and D4, accepted). Do not add a decision path that bypasses the answers.
3. A link inferred by time is not a verified link. Keep each link's basis (RECORDED, INFERRED, INFERRED_AGAINST_TIME_ORDER, UNLINKED) and list inferred links in `limitations`. Only a recorded `parent_agent_id` confirms a delegation; an inferred link leaves ID-2 partial (U5, decided by the owner on 2026-11-20). Do not report an interaction as verified because a relationship can be inferred.
4. Never edit GROUND_TRUTH.yaml to match an output. A new scenario says in its `world` when and why it was added. Every expectation must pass; a miss is a defect or a DECISIONS.md entry, never an allowance.
5. Do not invent thresholds. Depth and breadth have none and are reported, not judged. STALE and INGEST_LATENCY stay unread until `evidence_limits` values are approved.
6. A check that cannot run, or a metric that is not built, is listed as unassessed or as a limitation with its reason. It is never a pass and never a failure. Do not substitute a proxy.
7. Memory (`route_record`): only VALIDATED records make a route known; VERIFIED ones are pending. Only an independent reviewer validates a record.
8. No paid model calls; every operation is deterministic. Do not change `signal_traces.link_lanes` from this package (U6).
9. Record every interpretation choice, with alternatives and open questions, in DECISIONS.md.

## Checks before you report

```
cd collector
python3 -m cops.packages.interaction_discovery.harness
python3 -m cops.packages.audit_traceability.harness
python3 -m cops.packages.runtime_monitoring.harness
python3 -m pytest -q ../tests
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Report the actual pass and fail counts, any bench label that moved, and what needs the owner's decision.
