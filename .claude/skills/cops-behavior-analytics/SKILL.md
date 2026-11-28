---
name: cops-behavior-analytics
description: Maintain or extend the Behavior Analytics COP package (collector/cops/packages/behavior_analytics) in signal-dashboard; use when changing its questions, metrics, event recount, temporal definitions, recurrence or worsening logic, Anomaly Detection feedback, upstream reads, behaviour_baseline memory, downstream consumption, harness or ground truth.
---

# Behavior Analytics COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads
`collector/cops/packages/behavior_analytics/METRICS.yaml`, validated by `cop_package.validate`.

This is COP 11 in the build order. The workbook and `docs/cops/cop_catalogue.json` number it 12. Never infer build
order from that numbering.

## Where things are

- **Contract:** `METRICS.yaml` holds:
  - the four questions BA-1 to BA-4 (workbook COMPLETE_DELEGATION_DATA rows 181, 183, 185, 187; metric list row
    190);
  - the 37-metric mapping (20 judged, 7 catalogue metrics reported with NO_THRESHOLD, 10 NOT_CALCULATED) and
    `not_calculated` (the spec's four missing metrics);
  - `upstream`, `feedback`, `pattern_metrics`, `workload_metrics`, `labels`, `severity`, `confidence`,
    `responses`, `state_rank`, `model_role: none`, `deviation_states` and `operations` (all deterministic).
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md` and `DECISIONS.md`.
- **Ground truth:** `GROUND_TRUTH.yaml` (27 scenarios). The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Behavior Analytics section: `ba_recount`,
  `_ba1` to `_ba4`, `_ba_history_states`, `observe_behavior_analytics`, `assess_behavior_analytics`,
  `decide_behavior_analytics`, `_ba_limitations`.
- **Rule:** `rules.behavior_analytics` computes the legacy reading (`_behavior_analytics_legacy`, kept in
  `cop_specific.legacy_reading`), then `_ba_questions` applies the package's decision. `_with_pattern_persistence`
  records Anomaly Detection's earlier finding (SAME_EVENTS for this same session; never counted).
- **Tool:** `tools.t_assess_behaviour_questions`, last in `DETERMINISTIC_TOOLS["behavior_analytics"]`.

## Rules that must hold

1. retry and failed are judged over distinct, undisputed events (one row per tool_use_id), with the approved
   thresholds. Duplicate telemetry of one event is never repeated behaviour.
2. One anomalous session is ISOLATED at most. RECURRING needs an earlier ordered session of the same agent in a
   deviation state; WORSENING also needs never-decreasing approved state ranks. No window or trend threshold is
   invented; nothing is called persistent (U-BA3).
3. Sessions without a start timestamp are never ordered: the case session's absence leaves BA-3 INCONCLUSIVE; an
   earlier one is listed and not used.
4. Memory precedents and Anomaly Detection's earlier finding are context, never an occurrence.
5. Recovery & Resilience reads this COP as context only (D-RR8). Do not give it decision authority without an
   approved contract.
6. Actions stay NONE or WATCH. Nothing is enforced.
7. Never edit GROUND_TRUTH.yaml to match an output. A correction is marked in the harness and in DECISIONS.md
   (C-BA1, C-BA2). `superseded:` is only for purvi-approved semantics.

## Checks to run after a change

```
cd collector && python3 -m cops.packages.behavior_analytics.harness
python3 -m pytest tests/test_cop_package_behavior_analytics.py tests/test_cop_influence.py -q
```

Then every accepted COP harness, the full suite, the evaluation and the bench, sequentially. After the evaluation
and bench, restore `docs/cops/evaluation/RESULTS.md`, `results.json` and `test_bench_results.json` with git checkout.

## Decisions of 2026-11-28 (purvi; DECISIONS.md A-BA1, U-BA1..U-BA9)

- P-CM1 / A-BA1: no Context & Memory upstream. BA-4 reads Workflow Orchestration only; retired CONTEXT_LOSS /
  CONTRADICTORY are never read. Retrieved memory is context; it never establishes a finding.
- U-BA1 package-level event recount kept; shared metrics in signal_metrics unchanged (impact audited in DECISIONS.md).
- U-BA2 stalls and approval patterns observational. U-BA3 recurrence and worsening unchanged, no invented windows.
- U-BA4 peer cohorts, U-BA7 exploration_rate deferred. U-BA6 confidence uncalibrated.
- U-BA5 a verified STABLE is written as a VERIFIED behaviour_baseline with `memory_use: contextual_baseline`
  (`remember_quiet: verified_only`); an unverified STABLE is not written; only an independent reviewer validates.
- U-BA8 Risk Mitigation and Workflow Orchestration downstream edges deferred; Governance Intelligence recorded as a
  future context-only consumer (CONNECTIONS.md).
- U-BA9 registry id behavior_investigation_priority, source spelling behavior_investigation_prioritya kept as
  `source_spelling`.
