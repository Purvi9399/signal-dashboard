# Behavior Analytics: decisions

Proposed on 2026-11-27 for purvi's review. Nothing here changes a label name, the behavioural question,
`specs.metric_keys`, `specs.CONSUMES`, `specs.FEEDBACK`, the metrics engine, VS-01, the requirements gate, triage,
the verifiers, authority, the memory policy or another COP's semantics. The shared changes are:
- the new question tool (`tools.assess_behaviour_questions`);
- this package's place in `evidence_assessment.PACKAGED`;
- the package's answers applied in `rules.behavior_analytics` (`_ba_questions`), with the legacy reading kept as the
  computational baseline;
- `rules._pattern_persistence` (this COP's own wrapper) names whether Anomaly Detection's earlier finding is about
  this same session (D-BA9).

Numbering: COP 11 in the build order (purvi, 2026-11-27). The workbook and `docs/cops/cop_catalogue.json` number
Behavior Analytics 12. The build order is recorded apart from that numbering (project memory `cops-build-order`).

## Acceptance

| # | Date | By | Record |
|---|---|---|---|
| A-BA0 | 2026-11-27 | purvi | Behavior Analytics approved as COP 11, after its identity, canonical questions and contracts were confirmed in the workbook and registry, with the same package-by-package process as COPs 1 to 10. Stop for review before COP 12 |

## Identity confirmed

| Source | What it says |
|---|---|
| `cops/specs.py` SPECS | key `behavior_analytics`; question "What behavioural pattern best characterizes this execution or sequence of executions?"; metric_keys insights.repetition, insights.retry, insights.stalled, insights.failed, oversight.fast_approvals; missing_metrics loop_score_per_lane, exploration_rate, planning_deviation, recovery_pattern; labels STABLE, LOOPING, DRIFTING, OVER_EXPLORING, POOR_RECOVERY; permitted_actions NONE, WATCH; downstream anomaly_detection, recovery_resilience |
| `specs.SPEC_CONSUMES`, `specs.FEEDBACK` | consumes runtime_monitoring, context_memory, workflow_orchestration (current cycle); reads anomaly_detection's earlier-cycle finding (feedback, verified only); consumed by anomaly_detection and recovery_resilience |
| `profiles.DETERMINISTIC_COPS` | decided by rules, no model |
| `rules.behavior_analytics` | the legacy rule, wrapped by `_with_pattern_persistence` (METRICS.yaml sources.rule). `cops/interpret.py` has no method for this COP |
| `memory.POLICIES` | writes behaviour_baseline; reads behaviour_baseline, context_integrity_finding, workflow_failure; agent scope |
| Workbook COMPLETE_DELEGATION_DATA rows 181 to 190 | behavioural question row 181 ("What behavioural patterns characterize the agent over time?"); four question texts (rows 181, 183, 185, 187); metric list row 190 (37) |
| Workbook agents-harness row 16 | inputs, tools, authority ("Observe, classify, record, pass. No enforcement from behavioral characterization alone"), validation ("Avoid overgeneralizing from sparse history"), orchestration, downstream (Anomaly Detection, Governance Intelligence, Risk Mitigation) |
| Workbook Sheet1 rows 20, 74, 91 | patterns (loops, escalation, poor recovery, unnecessary exploration, task drift); "Rules. Examines execution sequences for loops, drift and poor recovery"; tools compute_sequence_statistics, compare_behaviour_history |
| Workbook many-to-many mapping row 17 | "What behavioural patterns are emerging over time?"; action WATCH; feeds Anomaly Detection, Workflow Orchestration, Governance Intelligence |
| `metric_registry.json` | 37 metrics with consumer behavior_analytics, matching COPS_METRIC_MAPPING.md (sha256 5bd3c16f...) |

## Made in this package

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D-BA1 | Four questions, BA-1 to BA-4, from the four question texts (rows 181, 183, 185, 187) | a fifth question | the workbook has four |
| D-BA2 | Each of the 37 metrics is assigned to the question its registry meaning answers (METRICS.yaml mapping). Sequences and execution behaviour (BA-1): the five pattern metrics but approvals, reverted, blocked, lanes, MCP servers, linkage and the sequence catalogue metrics. Comparison with history and peers (BA-2): the estate- and tool-relative metrics. Persistence (BA-3): approval metrics and the trend metrics. Variation (BA-4): model, task and context variation | per-COP only | each question needs its own requirements |
| D-BA3 | Event identity. An event is one tool_use_id (`signal_metrics.calls`, the approved action definition). insights.retry and insights.failed count rows, so the same failed call seen by several collectors counts more than once. BA-1 recounts both over distinct, undisputed events (one row per tool_use_id; rows with collectors_agree false left out) and judges the recount against the same approved thresholds (`signal_metrics.judge`). insights.repetition already counts distinct calls | trust the row counts | purvi's item 5: repeated telemetry of one event is not independent confirmation. The metric definitions and thresholds are unchanged; only the events they count are deduplicated. U-BA1 asks whether the metrics themselves should dedupe |
| D-BA4 | BA-1, in the legacy rule's order: recounted retry and failed both in a deviation state is RETRY_AFTER_FAILURE; recounted retry, or repetition, in a deviation state is REPEATED_ACTIONS; no pattern metric deviating but a workload metric (token, duration or action-rate outlier) deviating is WORKLOAD_ONLY; pattern metrics judged and none deviating is NO_PATTERN. Nothing judged is INCONCLUSIVE. A raw metric that deviates only because of rows the collectors dispute is INCONCLUSIVE (CONFLICTING_SOURCES); one that deviates only because of duplicate rows is not a pattern (DUPLICATE_TELEMETRY recorded). A stall or fast approvals in a deviation state are reported and give no label (BA has no stall or approval label; STALL_NOT_LABELLED) | a label for stalls | the legacy rule judges them and labels nothing; STALLED is Workflow Orchestration's |
| D-BA5 | BA-2. OUTSIDE_USUAL_RANGE when an approved estate- or tool-relative metric (token, duration, action-rate outliers, transition novelty, first-seen MCP server, work outside the workspace) is in a deviation state; WITHIN_USUAL_RANGE when at least one is judged and none deviates; INCONCLUSIVE when none can be judged. The agent's own earlier values (compare_behaviour_history: count, median, max) and the number of its earlier sessions against min_sessions_for_baseline are reported. No peer comparison | a historical deviation score | no score is defined (registry NOT_IMPLEMENTED) |
| D-BA6 | Temporal definitions, from what the runtime already uses. Same session: the same session_id. Distinct sessions: different session_ids. The agent's own history: `case.earlier_sessions()` (same tool, started earlier) of the same operator (`case.agent_scope`). Consecutive observations: those sessions in start order. A session without a start timestamp cannot be ordered: it is listed (UNORDERED_HISTORY) and not used; `earlier_sessions` would otherwise sort it first. A current session without timestamps leaves BA-3 INCONCLUSIVE (MISSING_TIMESTAMPS). No window is applied: all ordered history is read | a recurrence window | purvi's item 6: no invented windows |
| D-BA7 | BA-3, per metric behind the current reading (each earlier session's insights.retry and insights.failed recounted by event as in D-BA3); the metrics read are those behind the current reading (the pattern metrics deviating after the recount, fast approvals in a deviation state, or for WORKLOAD_ONLY the workload metrics deviating). An earlier observation is a state other than UNKNOWN (NOT_APPLICABLE is observed, not deviating). RECURRING: at least one earlier ordered session in a deviation state. WORSENING: recurring, the approved state ranks (NORMAL 0 to CRITICAL 3) of the judged earlier sessions and this one never decrease in start order, and this one is above the first. ISOLATED: no earlier deviation and at least min_sessions_for_baseline earlier observed sessions. Otherwise INCONCLUSIVE (INSUFFICIENT_HISTORY). Across metrics: WORSENING if any, else RECURRING if any, else ISOLATED if all, else INCONCLUSIVE. NO_CURRENT_PATTERN when nothing deviates now. No pattern is called persistent: persistence needs an approved window and threshold (row 185 column 5; PERSISTENCE_NOT_ASSESSED) | a slope threshold on the trend metrics | the trend slopes have no approved threshold and are reported only. Ranks and "more than once" use the approved states only. min_sessions_for_baseline is the approved config value. U-BA3 |
| D-BA8 | BA-4. CONTEXT_ALTERED when a verified substantive Context & Memory finding reads CONTEXT_LOSS or CONTRADICTORY, or Workflow Orchestration reads DRIFTING or UNEXPECTED_BRANCH (the legacy rule's authorised integrity reads); otherwise NO_ALTERATION_SHOWN. An unverified finding is listed and alters nothing. behavior_model_variation is reported | INCONCLUSIVE when those COPs are absent | the legacy rule reads absence as no alteration shown; the absence is a limitation (UPSTREAM_ABSENT) |
| D-BA9 | Anomaly Detection's earlier-cycle finding (feedback) is context for persistence and never an occurrence: Anomaly Detection reads the same metrics. `pattern_persistence` keeps its statuses; when the earlier finding names this same session it reads SAME_EVENTS instead of RECURRING, and `counted_as_occurrence` is always false. BA-3 counts only this agent's own metric history | count it as a second observation | purvi's item 5: correlated readings of the same event are not independent |
| D-BA10 | Memory. The shared step writes behaviour_baseline for a verified substantive non-quiet reading (STABLE is quiet and not written, as today), with the four answers, the scope, the recounted events and the metric states. Validated behaviour_baseline, context_integrity_finding and workflow_failure records are listed as context. A precedent never changes an answer and is never counted in BA-3: it is this COP's own earlier classification | count precedents | purvi's item 7: history must not validate its own classifications |
| D-BA11 | The label, as METRICS.yaml labels: RETRY_AFTER_FAILURE POOR_RECOVERY; REPEATED_ACTIONS LOOPING; else CONTEXT_ALTERED DRIFTING; else STABLE; BA-1 unanswered INCONCLUSIVE. BA-2 and BA-3 give the scope it is published with (`behaviour_status.scope`: this execution only, recurring across distinct sessions, worsening across consecutive sessions, or undetermined). One anomalous event is never published as a recurring or worsening tendency | change the label from BA-3 | the labels name patterns; their time scope is what the workbook asks next (rows 183, 185). purvi's item 4 |
| D-BA12 | Severity, confidence and action are the legacy rule's: POOR_RECOVERY MEDIUM 0.8, LOOPING from the worst deviating state 0.75, DRIFTING LOW 0.6, STABLE NONE 0.75, INCONCLUSIVE 0.0; scaled by the upstream evidence confidence; WATCH for patterns and INCONCLUSIVE, NONE for STABLE | scale by persistence | no approved calibration (UNCALIBRATED_CONFIDENCE, U-BA6) |
| D-BA13 | Authority: NONE or WATCH only. Nothing is enforced from a characterization (agents-harness row 16) | none | authority is unchanged |
| D-BA14 | Downstream. Anomaly Detection reads LOOPING and POOR_RECOVERY and marks them correlated with its own metrics (`independent_upstream`); Recovery & Resilience reads this COP as context only and never changes its label because of a pattern (D-RR8, accepted). The workbook also names Governance Intelligence, Risk Mitigation and Workflow Orchestration downstream; none is a runtime edge (P-BA1) | wire them | adding Risk Mitigation's input would change an accepted COP; Workflow Orchestration is upstream of this COP, so an edge back would close a cycle |
| D-BA15 | The legacy reading stays the computational baseline in `cop_specific.legacy_reading`. When the package reads differently, PREVIOUS_READING_DIFFERS names both readings | drop it | as the accepted packages |
| D-BA16 | No model is used: every answer rests on approved metric states, event identities and timestamps | a model for pattern characterization | profiles.DETERMINISTIC_COPS and Sheet1 row 74 ("Rules"); no question needs contextual judgement the approved evidence does not settle |

## Proposed for approval (not implemented)

| # | Proposal | Why it is not implemented |
|---|---|---|
| P-BA1 | The workbook's further downstream recipients: Governance Intelligence and Risk Mitigation (agents-harness row 16), Workflow Orchestration and Governance Intelligence (many-to-many row 17). Risk Mitigation would be a new current-cycle input of an accepted COP; Workflow Orchestration consumes nothing from this COP and is its upstream, so only a previous-cycle edge would be cycle safe; Governance Intelligence is unpackaged | each changes another COP's inputs or adds a feedback relationship |

## Unresolved (for purvi)

| # | Question | Today | Options |
|---|---|---|---|
| U-BA1 | insights.retry and insights.failed count rows, not events (D-BA3) | the package recounts by tool_use_id | dedupe in signal_metrics (shared, changes every COP reading them); keep the package recount |
| U-BA2 | No stall or approval label (D-BA4) | reported, STABLE | keep; route to Workflow Orchestration |
| U-BA3 | Recurrence and worsening use approved states and all ordered history, with no window (D-BA7) | as described | approve; supply a window and persistence threshold (row 185 column 5) |
| U-BA4 | Peer and cohort comparison (row 183 column 5) | not made | supply cohorts and comparable task categories |
| U-BA5 | STABLE is not written to memory, so no stable baseline accumulates (D-BA10) | not written | write STABLE as a baseline (remember_quiet) |
| U-BA6 | Confidence is the legacy rule's constants | kept, with UNCALIBRATED_CONFIDENCE | calibrate |
| U-BA7 | OVER_EXPLORING stays unreachable (no exploration rate) | unreachable | define exploration_rate |
| U-BA8 | P-BA1 | not wired | approve one or more edges |
| U-BA9 | behavior_investigation_prioritya looks like a typo of behavior_investigation_priority | kept as written | correct the registry |

## Corrections after the first run (350 of 358)

Ground truth was not edited. Each correction is marked in the harness.

| # | What | Why |
|---|---|---|
| C-BA1 | `trend_metrics_reported_not_judged` required all three trend metrics to read NOT_JUDGED | behavior_retry_trend is UNOBSERVED in that estate (the catalogue needs a value in more earlier sessions; insights.retry is NOT_APPLICABLE where nothing failed). The ground truth asks that the trends are reported and never judged: each is listed with its verdict and none is WITHIN or DEVIATES |
| C-BA2 | `ad_marks_correlated` read Anomaly Detection's `upstream_used` | AD carries `upstream_used` only when its model interprets; with no model AD is INCONCLUSIVE (llm_unavailable). AD-1's basis records how AD counted this COP's finding ("correlated, not counted: shares ['insights.retry']"); the check reads that |
| — | harness slips: the registry entries are keyed `id`; `graph.run_cop` returns the COP result under `result` | not corrections of meaning |

## Documentation corrected (no runtime change)

`docs/cops/cops_topology.json`: `reads_decisions_from` for this COP corrected from [] to runtime_monitoring,
context_memory, workflow_orchestration (specs.CONSUMES); a runtime_monitoring → behavior_analytics link and an
anomaly_detection → behavior_analytics `feedback` link added (specs.CONSUMES and specs.FEEDBACK).
