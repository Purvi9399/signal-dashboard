# COPS architecture: catalogue, mappings, graph and gaps

> **Superseded in part.** This snapshot predates the LangGraph runtime. For the runtime's architecture, verifier registry, memory and evaluation, see [`RUNTIME.md`](RUNTIME.md). The edge discrepancies in section 3 are now resolved by `specs.CONSUMES`, which is the single edge source; `downstream_cops` is derived from it, and `specs.edge_report()` lists the remaining differences against the old hand-written lists.

Generated read-only from `Purvi9399/signal-dashboard` at `f4f131b`. Structure is read from code; rule text names the function it was read from. Anything the code does not define is marked **UNDEFINED**. Machine-readable versions: `cop_catalogue.json`, `metric_cop_mappings.json`, `cop_graph.json`.

Status terms. **Specified**: has a `COPReadSpec` in `collector/cops/specs.py`. **Implemented**: has a `RuleInterpreter` method (`collector/cops/interpret.py`). **Runnable**: in `specs.ORDER` (`COPRunner.__init__` drops anything else). **Active**: `active=True`. **Tested**: a test in `tests/test_cops.py` asserts its output. **Live**: unverified for all 18 (no database read).

### Workflow Orchestration (`workflow_orchestration`)

**Behavioural question (spec, verbatim):** Is the task progressing coherently toward its intended state?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `insights.stalled`, `insights.repetition`, `oversight.declined_inferred`, `interaction.subagent_lanes`; context lane_path; reads decisions of none
- **Outputs:** NORMAL, DRIFTING, STALLED, EXCESSIVE_DELEGATION, UNEXPECTED_BRANCH; record `models.COPDecision -> cop_decisions`; hand-offs behavior_analytics, incident_response
- **Actions:** NONE, WATCH, ALERT, INSTRUCT, PASS_TO_COP; tiers OBSERVE, RECORD, ALERT, NOTIFY, RECOMMEND; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): task_progress, intended_state, task_drift, unresolved_items, stalled_duration_per_turn, excessive_delegation_ratio; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Runtime Monitoring (`runtime_monitoring`)

**Behavioural question (spec, verbatim):** Is the evidence required for this governance decision present, reliable and sufficiently complete?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** Reports on collection itself; a gap is its subject, not its obstacle (evidence_map.COP_MATERIALITY); Empty result means 'could not see', not 'nothing happened' (interpret.RuleInterpreter.runtime_monitoring)
- **Inputs:** metrics `telemetry.collector_coverage`, `telemetry.source_agreement`, `telemetry.turn_linkage`, `telemetry.lane_linkage`, `telemetry.unknown_share`; context collector_breakdown; reads decisions of none
- **Outputs:** SUFFICIENT, DEGRADED, INSUFFICIENT; record `models.COPDecision -> cop_decisions`; hand-offs audit_traceability, incident_response
- **Actions:** NONE, WATCH, ALERT, REQUEST_CONTEXT, PASS_TO_COP; tiers OBSERVE, RECORD, ALERT, NOTIFY, REQUEST; harness: harness_configs.RUNTIME_MONITORING
- **Tests:** `test_normal_session_is_quiet`, `test_partial_instrumentation_degrades_but_does_not_alarm`, `test_thin_evidence_is_insufficient_and_lowers_confidence`
- **Known gaps:** missing metrics (named in spec, computed nowhere): collector_heartbeat, event_freshness_live, missing_event_rate, collector_errors

### Anomaly Detection (`anomaly_detection`)

**Behavioural question (spec, verbatim):** Is the observed behaviour materially different from what is expected in this context?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** 'unusual is not prohibited, permission is Policy Enforcement's question' (interpret.RuleInterpreter.anomaly_detection)
- **Inputs:** metrics `insights.token_outlier`, `insights.duration_outlier`, `path.transition_novelty`, `activity.action_rate_ratio`, `access.first_seen_mcp`, `insights.retry`, `insights.repetition`; context lane_path, evidence_rows; reads decisions of runtime_monitoring
- **Outputs:** NORMAL, ANOMALOUS, INDETERMINATE; record `models.COPDecision -> cop_decisions`; hand-offs classification_tagging, policy_enforcement, risk_mitigation, incident_response
- **Actions:** NONE, WATCH, ALERT, REQUEST_CONTEXT, PASS_TO_COP; tiers OBSERVE, RECORD, ALERT, NOTIFY, REQUEST; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** `test_normal_session_is_quiet`, `test_spike_is_anomalous_but_not_an_incident`, `test_novelty_pulls_only_the_lane`, `test_harness_result_carries_action_and_existing_fields`, `test_incident_response_reads_upstream_action_on_the_harness_path`
- **Known gaps:** missing metrics (named in spec, computed nowhere): tool_use_deviation_per_agent, resource_access_novelty_files, token_expansion_per_turn; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Context & Memory (`context_memory`)

**Behavioural question (spec, verbatim):** Is the current context internally consistent, relevant to the task, and supported by useful precedent?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics none; context none; reads decisions of none
- **Outputs:** CONTEXT_OK, STALE, CONTRADICTORY, CONTEXT_LOSS, PRECEDENT_FOUND; record `models.COPDecision -> cop_decisions`; hand-offs behavior_analytics
- **Actions:** NONE, WATCH; tiers OBSERVE, RECORD; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): compaction_count, context_loss_after_compaction, session_reset, precedent_match, context_consistency; reads no computed metric; cannot run on today's data; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Interaction Discovery (`interaction_discovery`)

**Behavioural question (spec, verbatim):** Who or what is interacting with whom, through what route, and how is the activity propagating?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** 'Structure only. Whether a route was permitted belongs to Policy Enforcement' (interpret._interaction_discovery)
- **Inputs:** metrics `interaction.subagent_lanes`, `interaction.mcp_servers`, `access.first_seen_mcp`, `telemetry.lane_linkage`, `insights.outside`; context lane_path, evidence_rows; reads decisions of runtime_monitoring
- **Outputs:** CONTAINED, PROPAGATING, NEW_ROUTE, PATH_PARTIAL; record `models.COPDecision -> cop_decisions`; hand-offs policy_enforcement, risk_mitigation, audit_traceability
- **Actions:** NONE, WATCH, PASS_TO_COP; tiers OBSERVE, RECORD, NOTIFY; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** test_incident_response_reads_upstream_action_on_the_harness_path
- **Known gaps:** missing metrics (named in spec, computed nowhere): edge_table, new_edges, fan_in, fan_out, max_delegation_depth, cross_agent_path_general_to_coding; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Incident Response (`incident_response`)

**Behavioural question (spec, verbatim):** Do the current findings constitute an actionable incident, and what should happen now?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** Only COP with intervening actions; PAUSE/BLOCK recorded, never executed (harness_configs.OTHERS, authority.check)
- **Inputs:** metrics `insights.secrets`, `insights.sensitive_unasked`, `insights.blocked`; context evidence_rows; reads decisions of runtime_monitoring, anomaly_detection, policy_enforcement, interaction_discovery
- **Outputs:** NO_INCIDENT, WATCH, INCIDENT; record `models.COPDecision -> cop_decisions`; hand-offs none
- **Actions:** NONE, WATCH, ALERT, INSTRUCT, REQUEST_APPROVAL, PAUSE, BLOCK, ESCALATE; tiers OBSERVE, RECORD, ALERT, REQUEST, RECOMMEND, INITIATE, INTERVENE; never automatic: PAUSE, BLOCK; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** `test_normal_session_is_quiet`, `test_partial_instrumentation_degrades_but_does_not_alarm`, `test_spike_is_anomalous_but_not_an_incident`, `test_anomaly_plus_secret_escalates`, `test_thin_evidence_is_insufficient_and_lowers_confidence`, `test_incident_response_reads_upstream_action_on_the_harness_path`
- **Known gaps:** missing metrics (named in spec, computed nowhere): time_to_harm, intervention_available, affected_entities; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Classification & Tagging (`classification_tagging`)

**Behavioural question (spec, verbatim):** What kind of governance event is this, and which entities should remain associated with it?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** 'Kind only. Not how serious, not what to do'; permits only NONE (interpret._classification_tagging)
- **Inputs:** metrics `insights.secrets`, `insights.sensitive_unasked`, `insights.outside`, `insights.blocked`; context none; reads decisions of none
- **Outputs:** UNCLASSIFIED, BOUNDARY, OVERSIGHT, LOOPS, OUTCOME, INTEGRITY, COST; record `models.COPDecision -> cop_decisions`; hand-offs governance_intelligence
- **Actions:** NONE; tiers OBSERVE; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): actor_type, resource_type, incident_family, classification_confidence; has an interpreter but is not in specs.ORDER: COPRunner silently drops it, even with --activate; labels LOOPS, OUTCOME, COST never produced by the rule interpreter; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Policy Enforcement (`policy_enforcement`)

**Behavioural question (spec, verbatim):** Does the observed behaviour violate an explicit permission, policy, entitlement, boundary, or approval requirement?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** 'Was it permitted? Not: was it unusual, and not: was it harmful' (interpret._policy_enforcement); No policy store: reads conditions, not evaluated rules; never reads silence as permission
- **Inputs:** metrics `insights.blocked`, `insights.sensitive_unasked`, `insights.outside`, `insights.secrets`, `oversight.declined_inferred`; context matched_policy, lane_path; reads decisions of runtime_monitoring, interaction_discovery
- **Outputs:** ALLOWED, VIOLATION, APPROVAL_REQUIRED, UNCERTAIN; record `models.COPDecision -> cop_decisions`; hand-offs incident_response, risk_mitigation, audit_traceability
- **Actions:** NONE, WATCH, ALERT, REQUEST_APPROVAL, BLOCK, PASS_TO_COP; tiers OBSERVE, RECORD, ALERT, NOTIFY, REQUEST, INTERVENE; never automatic: BLOCK; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** test_incident_response_reads_upstream_action_on_the_harness_path
- **Known gaps:** missing metrics (named in spec, computed nowhere): entitlement_mismatch, indirect_privilege_use, policy_precedence, missing_required_approval; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Risk Mitigation (`risk_mitigation`)

**Behavioural question (spec, verbatim):** Do the combined findings create a material or emerging risk, and what residual risk remains after available controls?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `insights.secrets`, `insights.sensitive_unasked`, `insights.outside`, `insights.reverted`, `insights.token_outlier`; context none; reads decisions of none
- **Outputs:** LOW, EMERGING, MATERIAL; record `models.COPDecision -> cop_decisions`; hand-offs incident_response
- **Actions:** NONE, WATCH, INSTRUCT, ESCALATE, PASS_TO_COP; tiers OBSERVE, RECORD, NOTIFY, RECOMMEND, INITIATE; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): compound_risk_score, reversibility, residual_risk; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Resource Management (`resource_management`)

**Behavioural question (spec, verbatim):** Is resource consumption within entitlement and acceptable operating bounds?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** Burn and waste kept apart; 'whether the work was permitted is Policy Enforcement's question' (interpret.RuleInterpreter.resource_management); No quota configured: UNAUTHORIZED unreachable until budget_or_quota exists (specs.py comment)
- **Inputs:** metrics `insights.token_outlier`, `insights.duration_outlier`, `insights.stalled`, `activity.action_rate_ratio`; context none; reads decisions of none
- **Outputs:** NORMAL, INEFFICIENT, EXCESSIVE, UNAUTHORIZED, INDETERMINATE; record `models.COPDecision -> cop_decisions`; hand-offs workload_optimization, risk_mitigation
- **Actions:** NONE, WATCH, ALERT; tiers OBSERVE, RECORD, ALERT; harness: harness_configs.RESOURCE_MANAGEMENT
- **Tests:** every runner test session (it is in ACTIVE); harness test
- **Known gaps:** missing metrics (named in spec, computed nowhere): budget_or_quota, cpu_gpu_memory, rate_limit_hits; docs/cops/cops_topology.json says 'defined' but spec is active; UNAUTHORIZED unreachable until budget_or_quota exists

### Governance Intelligence (`governance_intelligence`)

**Behavioural question (spec, verbatim):** What recurring patterns, concentrations or blind spots indicate a broader governance problem?

- **Cadence:** aggregate · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics none; context none; reads decisions of none
- **Outputs:** NO_PATTERN, RECURRING, CONCENTRATED, BLIND_SPOT; record `models.COPDecision -> cop_decisions`; hand-offs governance_architecture, knowledge_policy_management
- **Actions:** NONE, WATCH; tiers OBSERVE, RECORD; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): metric_state_trends_from_metric_events, decision_trends_from_cop_decisions, repeat_actor_concentration; reads no computed metric; cannot run on today's data; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError; cadence 'aggregate': COPRunner only has a per-session path

### Behavior Analytics (`behavior_analytics`)

**Behavioural question (spec, verbatim):** What behavioural pattern best characterizes this execution or sequence of executions?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `insights.repetition`, `insights.retry`, `insights.stalled`, `insights.failed`, `oversight.fast_approvals`; context lane_path; reads decisions of none
- **Outputs:** STABLE, LOOPING, DRIFTING, OVER_EXPLORING, POOR_RECOVERY; record `models.COPDecision -> cop_decisions`; hand-offs anomaly_detection, recovery_resilience
- **Actions:** NONE, WATCH; tiers OBSERVE, RECORD; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): loop_score_per_lane, exploration_rate, planning_deviation, recovery_pattern; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Recovery & Resilience (`recovery_resilience`)

**Behavioural question (spec, verbatim):** After failure or intervention, did the system return to a viable state without compounding the problem?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `insights.reverted`, `insights.retry`, `insights.failed`; context lane_path; reads decisions of none
- **Outputs:** RECOVERED, PARTIAL_RECOVERY, FAILED_RECOVERY, IRREVERSIBLE; record `models.COPDecision -> cop_decisions`; hand-offs incident_response, governance_intelligence
- **Actions:** NONE, WATCH, ALERT; tiers OBSERVE, RECORD, ALERT; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): recovery_success_after_failure, irreversible_change, recovery_time; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Workload Optimization (`workload_optimization`)

**Behavioural question (spec, verbatim):** Is the workload using tools, models, time and resources efficiently for the task being performed?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `insights.repetition`, `insights.duration_outlier`, `insights.token_outlier`, `activity.action_rate_ratio`; context none; reads decisions of none
- **Outputs:** EFFICIENT, REDUNDANT, BOTTLENECK, OVERPROVISIONED, UNDERPROVISIONED; record `models.COPDecision -> cop_decisions`; hand-offs resource_management
- **Actions:** NONE, WATCH, INSTRUCT; tiers OBSERVE, RECORD, RECOMMEND; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): cost_per_task, cache_efficiency, redundant_reads; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Audit & Traceability (`audit_traceability`)

**Behavioural question (spec, verbatim):** Can every consequential finding, decision and action be attributed and reconstructed from trustworthy evidence?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** Owns actions another COP could not judge (interpret._audit_traceability)
- **Inputs:** metrics `insights.unattributed`, `telemetry.turn_linkage`, `telemetry.lane_linkage`, `telemetry.source_agreement`; context evidence_rows; reads decisions of none
- **Outputs:** COMPLETE, PARTIAL, BROKEN_TRACE, UNATTRIBUTABLE; record `models.COPDecision -> cop_decisions`; hand-offs governance_architecture
- **Actions:** NONE, WATCH, ALERT; tiers OBSERVE, RECORD, ALERT; harness: harness_configs.OTHERS (notes only, not wired)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): decision_evidence_coverage, raw_ref_resolvability; has an interpreter but is not in specs.ORDER: COPRunner silently drops it, even with --activate; no HarnessConfig of its own: harness_configs.default_for gives the most restrictive default

### Knowledge & Policy Management (`knowledge_policy_management`)

**Behavioural question (spec, verbatim):** Are the rules, policies and governance knowledge currently being used still current, coherent and internally consistent?

- **Cadence:** aggregate · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics none; context none; reads decisions of none
- **Outputs:** CURRENT, STALE, CONFLICT, UPDATE_REQUIRED; record `models.COPDecision -> cop_decisions`; hand-offs governance_architecture
- **Actions:** NONE, WATCH; tiers OBSERVE, RECORD; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): policy_store, policy_version, rule_conflicts, rule_hit_rate, override_rate; reads no computed metric; cannot run on today's data; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError; cadence 'aggregate': COPRunner only has a per-session path

### Agent Lifecycle Management (`agent_lifecycle_management`)

**Behavioural question (spec, verbatim):** Has an agent's identity, version, configuration, capability or connectivity changed in a way requiring governance attention?

- **Cadence:** session · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `access.first_seen_mcp`; context none; reads decisions of none
- **Outputs:** UNCHANGED, NEW, CHANGED, DRIFTED, INACTIVE, RETIRED; record `models.COPDecision -> cop_decisions`; hand-offs classification_tagging, policy_enforcement
- **Actions:** NONE, WATCH, ALERT; tiers OBSERVE, RECORD, ALERT; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): tool_version_change, model_change, new_machine, first_last_seen, hook_config_removed; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError

### Governance Architecture (`governance_architecture`)

**Behavioural question (spec, verbatim):** Does the current governance architecture adequately cover the estate and the behaviours actually occurring?

- **Cadence:** aggregate · **Spec version:** 1
- **Boundaries:** none stated in code beyond permitted_actions
- **Inputs:** metrics `telemetry.unknown_share`; context none; reads decisions of none
- **Outputs:** ADEQUATE, GAP, STRUCTURAL_CHANGE_RECOMMENDED; record `models.COPDecision -> cop_decisions`; hand-offs none
- **Actions:** NONE, WATCH; tiers OBSERVE, RECORD; harness: harness_configs.default_for (restrictive default)
- **Tests:** none
- **Known gaps:** missing metrics (named in spec, computed nowhere): observable_field_status_per_tool, cops_coverage, unhandled_exceptions, repeated_escalations; no RuleInterpreter method: RuleInterpreter.interpret raises NotImplementedError; cadence 'aggregate': COPRunner only has a per-session path

## 2. Metric-to-COP mappings

26 metric IDs are computed (`signal_metrics.METRICS` + `telemetry.unknown_share` in `signal_metrics.compute`). Specs pair them with COPs 60 times; 37 pairs are confirmed (the COP's interpreter reads the metric). Verifier checks that apply to every confirmed pair: cited metric must be supplied, interpretation and action must be in the spec, hand-offs must be declared (`harness.reverify`, `LLMInterpreter.validate`, `COPRunner.run_cop`). No verifier checks a single pair's contribution: **UNDEFINED** per pair.

| COP | Metric | Role | Interpretation rule | Rule source | Evidence (basis · required fields · materiality) | Thresholds W/E/C |
|---|---|---|---|---|---|---|
| workflow_orchestration | `insights.stalled` | UNDEFINED | **UNDEFINED**: no interpreter | — | wrapper · hung_seconds · command=DEGRADING, timestamp=BLOCKING | 1/1/3 (above) |
| workflow_orchestration | `insights.repetition` | UNDEFINED | **UNDEFINED**: no interpreter | — | args · tool_name, tool_arguments, turn_id · turn_id=BLOCKING, tool_name=BLOCKING, tool_arguments=DEGRADING | 1/3/10 (above) |
| workflow_orchestration | `oversight.declined_inferred` | UNDEFINED | **UNDEFINED**: no interpreter | — | approvals · observable_type, turn_id · **UNDEFINED** | 1/3/10 (above) |
| workflow_orchestration | `interaction.subagent_lanes` | UNDEFINED | **UNDEFINED**: no interpreter | — | always · agent_id · lane_id=DEGRADING, parent_agent_id=DEGRADING | 5/15/50 (above) |
| runtime_monitoring | `telemetry.collector_coverage` | driver | EXCEPTION/CRITICAL -> INSUFFICIENT (ALERT); UNKNOWN -> DEGRADED; features.missing -> request collector_breakdown context | interpret.RuleInterpreter.runtime_monitoring | always · collector, contributing · tool_name=IMMATERIAL, timestamp=IMMATERIAL | 0.99/0.5/0.0 (below) |
| runtime_monitoring | `telemetry.source_agreement` | evidence | WARNING+ -> DEGRADED (WATCH); generic: any hot state counts alike, no metric-specific rule | interpret.RuleInterpreter.runtime_monitoring | always · collectors_agree · **UNDEFINED** | 0.99/0.9/0.5 (below) |
| runtime_monitoring | `telemetry.turn_linkage` | evidence | WARNING+ -> DEGRADED (WATCH); generic: any hot state counts alike, no metric-specific rule | interpret.RuleInterpreter.runtime_monitoring | always · turn_id · turn_id=IMMATERIAL | 0.9/0.6/0.2 (below) |
| runtime_monitoring | `telemetry.lane_linkage` | evidence | WARNING+ -> DEGRADED (WATCH); generic: any hot state counts alike, no metric-specific rule | interpret.RuleInterpreter.runtime_monitoring | always · agent_id, observable_type · lane_id=IMMATERIAL, parent_agent_id=IMMATERIAL | 0.99/0.5/0.0 (below) |
| runtime_monitoring | `telemetry.unknown_share` | driver | EXCEPTION/CRITICAL -> INSUFFICIENT; features.unknown listed as 'cannot judge' | interpret.RuleInterpreter.runtime_monitoring | always · — · **UNDEFINED** | 0.25/0.5/0.75 (above) |
| anomaly_detection | `insights.token_outlier` | driver | EXCEPTION/CRITICAL -> ANOMALOUS; WATCH if single, ALERT if >=2 hot or any CRITICAL; ranked by value/threshold | interpret.RuleInterpreter.anomaly_detection | tokens · total_tokens · total_tokens=BLOCKING, model=DEGRADING, lane_id=DEGRADING | 3/5/15 (above) |
| anomaly_detection | `insights.duration_outlier` | driver | EXCEPTION/CRITICAL -> ANOMALOUS (same ladder) | interpret.RuleInterpreter.anomaly_detection | durations · tool_duration_ms · tool_duration_ms=BLOCKING, tool_name=DEGRADING | 1/1/5 (above) |
| anomaly_detection | `path.transition_novelty` | driver | EXCEPTION/CRITICAL -> REQUEST_CONTEXT lane_path first, then ANOMALOUS with novel transitions named | interpret.RuleInterpreter.anomaly_detection | always · tool_name, command, observable_type · **UNDEFINED** | 0.25/0.5/0.8 (above) |
| anomaly_detection | `activity.action_rate_ratio` | driver | EXCEPTION/CRITICAL -> ANOMALOUS (same ladder) | interpret.RuleInterpreter.anomaly_detection | always · tool_name, occurred_at · timestamp=BLOCKING, tool_name=IMMATERIAL | 3/5/15 (above) |
| anomaly_detection | `access.first_seen_mcp` | driver | EXCEPTION/CRITICAL -> ANOMALOUS and adds policy_enforcement downstream | interpret.RuleInterpreter.anomaly_detection | mcp · mcp_server · mcp_server=BLOCKING | 1/1/3 (above) |
| anomaly_detection | `insights.retry` | supporting | EXCEPTION/CRITICAL named in rationale only; never drives | interpret.RuleInterpreter.anomaly_detection | commands · command, exit_code · **UNDEFINED** | 1/2/5 (above) |
| anomaly_detection | `insights.repetition` | supporting | EXCEPTION/CRITICAL named in rationale only; never drives | interpret.RuleInterpreter.anomaly_detection | args · tool_name, tool_arguments, turn_id · turn_id=BLOCKING, tool_name=BLOCKING, tool_arguments=DEGRADING | 1/3/10 (above) |
| interaction_discovery | `interaction.subagent_lanes` | driver | EXCEPTION/CRITICAL -> PROPAGATING (PASS_TO_COP) | interpret._interaction_discovery | always · agent_id · lane_id=DEGRADING, parent_agent_id=DEGRADING | 5/15/50 (above) |
| interaction_discovery | `interaction.mcp_servers` | evidence | counted as judged; no rule fires on it | interpret._interaction_discovery | mcp · mcp_server · **UNDEFINED** | 3/6/12 (above) |
| interaction_discovery | `access.first_seen_mcp` | driver | EXCEPTION/CRITICAL -> NEW_ROUTE (PASS_TO_COP policy_enforcement, audit_traceability) | interpret._interaction_discovery | mcp · mcp_server · mcp_server=BLOCKING | 1/1/3 (above) |
| interaction_discovery | `telemetry.lane_linkage` | driver | EXCEPTION/CRITICAL -> PATH_PARTIAL (WATCH), adds audit_traceability; checked first | interpret._interaction_discovery | always · agent_id, observable_type · lane_id=IMMATERIAL, parent_agent_id=IMMATERIAL | 0.99/0.5/0.0 (below) |
| interaction_discovery | `insights.outside` | driver | EXCEPTION/CRITICAL -> PROPAGATING (PASS_TO_COP policy_enforcement, risk_mitigation) | interpret._interaction_discovery | paths · file_path, outside_workspace · file_path=DEGRADING, workspace=BLOCKING | 1/1/10 (above) |
| incident_response | `insights.secrets` | driver | EXCEPTION/CRITICAL = exposure -> INCIDENT; ESCALATE if anomaly_detection ANOMALOUS, else ALERT | interpret.RuleInterpreter.incident_response | content · secret_detected · file_path=DEGRADING, content_hash=DEGRADING | 1/1/3 (above) |
| incident_response | `insights.sensitive_unasked` | driver | same as insights.secrets | interpret.RuleInterpreter.incident_response | approvals · sensitivity_tier, permission_decision · file_path=DEGRADING, sensitivity_tier=DEGRADING, permission_decision=DEGRADING | 1/1/5 (above) |
| incident_response | `insights.blocked` | supporting | WARNING+ -> WATCH ('a deny rule fired; the control worked') | interpret.RuleInterpreter.incident_response | our_rules · policy_rule_matched · **UNDEFINED** | 1/1/5 (above) |
| classification_tagging | `insights.secrets` | driver | EXCEPTION/CRITICAL -> INTEGRITY | interpret._classification_tagging | content · secret_detected · file_path=DEGRADING, content_hash=DEGRADING | 1/1/3 (above) |
| classification_tagging | `insights.sensitive_unasked` | driver | EXCEPTION/CRITICAL -> BOUNDARY | interpret._classification_tagging | approvals · sensitivity_tier, permission_decision · file_path=DEGRADING, sensitivity_tier=DEGRADING, permission_decision=DEGRADING | 1/1/5 (above) |
| classification_tagging | `insights.outside` | driver | EXCEPTION/CRITICAL -> BOUNDARY (ranked first) | interpret._classification_tagging | paths · file_path, outside_workspace · file_path=DEGRADING, workspace=BLOCKING | 1/1/10 (above) |
| classification_tagging | `insights.blocked` | driver | EXCEPTION/CRITICAL -> OVERSIGHT | interpret._classification_tagging | our_rules · policy_rule_matched · **UNDEFINED** | 1/1/5 (above) |
| policy_enforcement | `insights.blocked` | driver | EXCEPTION/CRITICAL -> VIOLATION (ALERT) | interpret._policy_enforcement | our_rules · policy_rule_matched · **UNDEFINED** | 1/1/5 (above) |
| policy_enforcement | `insights.sensitive_unasked` | driver | EXCEPTION/CRITICAL without a violation metric -> APPROVAL_REQUIRED (REQUEST_APPROVAL) | interpret._policy_enforcement | approvals · sensitivity_tier, permission_decision · file_path=BLOCKING, sensitivity_tier=BLOCKING, permission_decision=DEGRADING | 1/1/5 (above) |
| policy_enforcement | `insights.outside` | driver | EXCEPTION/CRITICAL -> VIOLATION (ALERT) | interpret._policy_enforcement | paths · file_path, outside_workspace · file_path=BLOCKING, workspace=BLOCKING | 1/1/10 (above) |
| policy_enforcement | `insights.secrets` | driver | EXCEPTION/CRITICAL -> VIOLATION (ALERT), adds risk_mitigation | interpret._policy_enforcement | content · secret_detected · file_path=DEGRADING, content_hash=DEGRADING | 1/1/3 (above) |
| policy_enforcement | `oversight.declined_inferred` | driver | EXCEPTION/CRITICAL without a violation metric -> APPROVAL_REQUIRED (REQUEST_APPROVAL) | interpret._policy_enforcement | approvals · observable_type, turn_id · **UNDEFINED** | 1/3/10 (above) |
| risk_mitigation | `insights.secrets` | UNDEFINED | **UNDEFINED**: no interpreter | — | content · secret_detected · file_path=DEGRADING, content_hash=DEGRADING | 1/1/3 (above) |
| risk_mitigation | `insights.sensitive_unasked` | UNDEFINED | **UNDEFINED**: no interpreter | — | approvals · sensitivity_tier, permission_decision · file_path=BLOCKING, sensitivity_tier=BLOCKING, permission_decision=DEGRADING | 1/1/5 (above) |
| risk_mitigation | `insights.outside` | UNDEFINED | **UNDEFINED**: no interpreter | — | paths · file_path, outside_workspace · file_path=BLOCKING, workspace=BLOCKING | 1/1/10 (above) |
| risk_mitigation | `insights.reverted` | UNDEFINED | **UNDEFINED**: no interpreter | — | hashes · content_hash, reverted_to_earlier · **UNDEFINED** | 1/3/10 (above) |
| risk_mitigation | `insights.token_outlier` | UNDEFINED | **UNDEFINED**: no interpreter | — | tokens · total_tokens · total_tokens=BLOCKING, model=DEGRADING, lane_id=DEGRADING | 3/5/15 (above) |
| resource_management | `insights.token_outlier` | driver (burn) | EXCEPTION/CRITICAL -> EXCESSIVE, adds risk_mitigation | interpret.RuleInterpreter.resource_management | tokens · total_tokens · total_tokens=BLOCKING, model=DEGRADING, lane_id=DEGRADING | 3/5/15 (above) |
| resource_management | `insights.duration_outlier` | driver (waste) | EXCEPTION/CRITICAL -> INEFFICIENT, adds workload_optimization | interpret.RuleInterpreter.resource_management | durations · tool_duration_ms · tool_duration_ms=BLOCKING, tool_name=DEGRADING | 1/1/5 (above) |
| resource_management | `insights.stalled` | driver (waste) | EXCEPTION/CRITICAL -> INEFFICIENT, adds workload_optimization; WARNING alone -> INEFFICIENT (WATCH) | interpret.RuleInterpreter.resource_management | wrapper · hung_seconds · command=DEGRADING, timestamp=BLOCKING | 1/1/3 (above) |
| resource_management | `activity.action_rate_ratio` | driver (burn) | EXCEPTION/CRITICAL -> EXCESSIVE, adds risk_mitigation | interpret.RuleInterpreter.resource_management | always · tool_name, occurred_at · timestamp=BLOCKING, tool_name=IMMATERIAL | 3/5/15 (above) |
| behavior_analytics | `insights.repetition` | UNDEFINED | **UNDEFINED**: no interpreter | — | args · tool_name, tool_arguments, turn_id · turn_id=BLOCKING, tool_name=BLOCKING, tool_arguments=DEGRADING | 1/3/10 (above) |
| behavior_analytics | `insights.retry` | UNDEFINED | **UNDEFINED**: no interpreter | — | commands · command, exit_code · **UNDEFINED** | 1/2/5 (above) |
| behavior_analytics | `insights.stalled` | UNDEFINED | **UNDEFINED**: no interpreter | — | wrapper · hung_seconds · command=DEGRADING, timestamp=BLOCKING | 1/1/3 (above) |
| behavior_analytics | `insights.failed` | UNDEFINED | **UNDEFINED**: no interpreter | — | outcomes · success, exit_code · **UNDEFINED** | 1/5/20 (above) |
| behavior_analytics | `oversight.fast_approvals` | UNDEFINED | **UNDEFINED**: no interpreter | — | approvals · observable_type, occurred_at · **UNDEFINED** | 1/3/10 (above) |
| recovery_resilience | `insights.reverted` | UNDEFINED | **UNDEFINED**: no interpreter | — | hashes · content_hash, reverted_to_earlier · **UNDEFINED** | 1/3/10 (above) |
| recovery_resilience | `insights.retry` | UNDEFINED | **UNDEFINED**: no interpreter | — | commands · command, exit_code · **UNDEFINED** | 1/2/5 (above) |
| recovery_resilience | `insights.failed` | UNDEFINED | **UNDEFINED**: no interpreter | — | outcomes · success, exit_code · **UNDEFINED** | 1/5/20 (above) |
| workload_optimization | `insights.repetition` | UNDEFINED | **UNDEFINED**: no interpreter | — | args · tool_name, tool_arguments, turn_id · turn_id=BLOCKING, tool_name=BLOCKING, tool_arguments=DEGRADING | 1/3/10 (above) |
| workload_optimization | `insights.duration_outlier` | UNDEFINED | **UNDEFINED**: no interpreter | — | durations · tool_duration_ms · tool_duration_ms=BLOCKING, tool_name=DEGRADING | 1/1/5 (above) |
| workload_optimization | `insights.token_outlier` | UNDEFINED | **UNDEFINED**: no interpreter | — | tokens · total_tokens · total_tokens=BLOCKING, model=DEGRADING, lane_id=DEGRADING | 3/5/15 (above) |
| workload_optimization | `activity.action_rate_ratio` | UNDEFINED | **UNDEFINED**: no interpreter | — | always · tool_name, occurred_at · timestamp=BLOCKING, tool_name=IMMATERIAL | 3/5/15 (above) |
| audit_traceability | `insights.unattributed` | driver | CRITICAL -> UNATTRIBUTABLE; EXCEPTION -> BROKEN_TRACE; WARNING -> PARTIAL | interpret._audit_traceability | fs · file_path, attribution · **UNDEFINED** | 1/5/25 (above) |
| audit_traceability | `telemetry.turn_linkage` | driver | same severity ladder; generic: any hot state counts alike, no metric-specific rule | interpret._audit_traceability | always · turn_id · turn_id=IMMATERIAL | 0.9/0.6/0.2 (below) |
| audit_traceability | `telemetry.lane_linkage` | driver | same severity ladder; generic: any hot state counts alike, no metric-specific rule | interpret._audit_traceability | always · agent_id, observable_type · lane_id=DEGRADING, parent_agent_id=IMMATERIAL | 0.99/0.5/0.0 (below) |
| audit_traceability | `telemetry.source_agreement` | driver | same severity ladder; generic: any hot state counts alike, no metric-specific rule | interpret._audit_traceability | always · collectors_agree · **UNDEFINED** | 0.99/0.9/0.5 (below) |
| agent_lifecycle_management | `access.first_seen_mcp` | UNDEFINED | **UNDEFINED**: no interpreter | — | mcp · mcp_server · mcp_server=BLOCKING | 1/1/3 (above) |
| governance_architecture | `telemetry.unknown_share` | UNDEFINED | **UNDEFINED**: no interpreter | — | always · — · **UNDEFINED** | 0.25/0.5/0.75 (above) |

**Metrics no spec reads:** `insights.no_oversight`, `insights.lockfile`

**Pairs only in `docs/cops/cops_topology.json` (not in any spec), 28:** agent_lifecycle_management←`interaction.mcp_servers`; agent_lifecycle_management←`telemetry.collector_coverage`; anomaly_detection←`insights.failed`; audit_traceability←`insights.secrets`; behavior_analytics←`insights.blocked`; behavior_analytics←`insights.reverted`; behavior_analytics←`oversight.declined_inferred`; behavior_analytics←`path.transition_novelty`; classification_tagging←`insights.lockfile`; governance_architecture←`telemetry.collector_coverage`; governance_intelligence←`insights.no_oversight`; governance_intelligence←`insights.reverted`; governance_intelligence←`oversight.fast_approvals`; interaction_discovery←`insights.unattributed`; interaction_discovery←`path.transition_novelty`; knowledge_policy_management←`insights.blocked`; policy_enforcement←`access.first_seen_mcp`; policy_enforcement←`insights.no_oversight`; policy_enforcement←`oversight.fast_approvals`; resource_management←`interaction.subagent_lanes`; risk_mitigation←`insights.lockfile`; risk_mitigation←`insights.no_oversight`; runtime_monitoring←`insights.stalled`; runtime_monitoring←`insights.unattributed`; workflow_orchestration←`insights.duration_outlier`; workflow_orchestration←`insights.failed`; workflow_orchestration←`telemetry.turn_linkage`; workload_optimization←`insights.retry`

**Marked `built` in the topology doc but computed nowhere:** `lifecycle.model_change`, `lifecycle.new_machine`, `lifecycle.version_change`, `telemetry.event_freshness`, `telemetry.evidence_score`

## 3. COP communication graph

Three kinds of defined edge. Only the first two execute.

| From | To | Kind | Executed | Trigger | Purpose | Defined in |
|---|---|---|---|---|---|---|
| workflow_orchestration | behavior_analytics | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| workflow_orchestration | incident_response | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | audit_traceability | handoff | no | evidence INSUFFICIENT or DEGRADED | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | incident_response | handoff | no | evidence INSUFFICIENT | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | anomaly_detection | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | passed in; this interpreter does not read its content | specs.py consumes_decisions_from |
| anomaly_detection | classification_tagging | handoff | no | ANOMALOUS | pass the finding to another governance lens | specs.py downstream_cops |
| anomaly_detection | policy_enforcement | handoff | no | ANOMALOUS with access.first_seen_mcp hot | pass the finding to another governance lens | specs.py downstream_cops |
| anomaly_detection | risk_mitigation | handoff | no | UNDEFINED: interpreter never emits this hand-off | pass the finding to another governance lens | specs.py downstream_cops |
| anomaly_detection | incident_response | handoff | no | ANOMALOUS | pass the finding to another governance lens | specs.py downstream_cops |
| context_memory | behavior_analytics | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | interaction_discovery | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | passed in; this interpreter does not read its content | specs.py consumes_decisions_from |
| interaction_discovery | policy_enforcement | handoff | no | NEW_ROUTE or PROPAGATING | pass the finding to another governance lens | specs.py downstream_cops |
| interaction_discovery | risk_mitigation | handoff | no | PROPAGATING | pass the finding to another governance lens | specs.py downstream_cops |
| interaction_discovery | audit_traceability | handoff | no | PATH_PARTIAL or NEW_ROUTE | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | incident_response | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | incident_response combines findings | specs.py consumes_decisions_from |
| anomaly_detection | incident_response | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | incident_response combines findings | specs.py consumes_decisions_from |
| policy_enforcement | incident_response | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | incident_response combines findings | specs.py consumes_decisions_from |
| interaction_discovery | incident_response | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | incident_response combines findings | specs.py consumes_decisions_from |
| classification_tagging | governance_intelligence | handoff | no | any classified family (not UNCLASSIFIED) | pass the finding to another governance lens | specs.py downstream_cops |
| runtime_monitoring | policy_enforcement | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | passed in; this interpreter does not read its content | specs.py consumes_decisions_from |
| interaction_discovery | policy_enforcement | reads_decision | yes | upstream ran earlier in specs.ORDER this session; downstream interpreter runs if any upstream action != NONE or a metric is in WARNING/EXCEPTION/CRITICAL | passed in; this interpreter does not read its content | specs.py consumes_decisions_from |
| policy_enforcement | incident_response | handoff | no | VIOLATION or APPROVAL_REQUIRED | pass the finding to another governance lens | specs.py downstream_cops |
| policy_enforcement | risk_mitigation | handoff | no | VIOLATION with insights.secrets hot | pass the finding to another governance lens | specs.py downstream_cops |
| policy_enforcement | audit_traceability | handoff | no | VIOLATION, APPROVAL_REQUIRED, or UNCERTAIN with unknown conditions | pass the finding to another governance lens | specs.py downstream_cops |
| risk_mitigation | incident_response | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| resource_management | workload_optimization | handoff | no | waste metric hot (duration_outlier or stalled) | pass the finding to another governance lens | specs.py downstream_cops |
| resource_management | risk_mitigation | handoff | no | EXCESSIVE (burn metric hot) | pass the finding to another governance lens | specs.py downstream_cops |
| governance_intelligence | governance_architecture | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| governance_intelligence | knowledge_policy_management | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| behavior_analytics | anomaly_detection | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| behavior_analytics | recovery_resilience | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| recovery_resilience | incident_response | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| recovery_resilience | governance_intelligence | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| workload_optimization | resource_management | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| audit_traceability | governance_architecture | handoff | no | UNATTRIBUTABLE (severe) or BROKEN_TRACE | pass the finding to another governance lens | specs.py downstream_cops |
| knowledge_policy_management | governance_architecture | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| agent_lifecycle_management | classification_tagging | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |
| agent_lifecycle_management | policy_enforcement | handoff | no | UNDEFINED: sender has no interpreter | pass the finding to another governance lens | specs.py downstream_cops |

**Evidence broadcast** (`runner.COPRunner.run_cop`, `evid`): runtime_monitoring → each of the other 17 COPs, every run; it executes for the runnable ones.

**Proposed edges (not defined):**

- runtime_monitoring → anomaly_detection (handoff): already an executed read (consumes_decisions_from) but not declared in runtime_monitoring.downstream_cops
- runtime_monitoring → interaction_discovery (handoff): already an executed read (consumes_decisions_from) but not declared in runtime_monitoring.downstream_cops
- runtime_monitoring → policy_enforcement (handoff): already an executed read (consumes_decisions_from) but not declared in runtime_monitoring.downstream_cops
- interaction_discovery → incident_response (handoff): already an executed read but not declared in interaction_discovery.downstream_cops
- * → audit_traceability (handoff): decide.finalise Rule 1 adds audit_traceability to any BLOCKED_BY_EVIDENCE decision after harness.reverify ran, so COPs without it in downstream_cops can emit an undeclared edge

## 4. Implementation gap report

Smallest changes to hold the catalogue and relationships as structured data. Nothing below changes how a COP runs.

1. **Keep `COPReadSpec` as the source of truth.** It already holds question, metrics, missing metrics, context, actions, labels, upstream and hand-offs (`collector/cops/models.py`, `specs.py`). No new spec format.
2. **Add one data-only module, `collector/cops/relations.py`:** `PAIR_RULES` (role, rule text, rule source per confirmed pair) and `HANDOFF_TRIGGERS`. These are the two things that exist today only as prose inside interpreter code.
3. **Add a read-only exporter, `collector/cops/export_catalogue.py`:** writes the three JSON files from `SPECS`, `relations.py`, `signal_metrics.METRICS`, `evidence_map`, `metrics_config.json` and `harness_configs`. Status (implemented, runnable, active) is computed, never typed by hand.
4. **Reuse `docs/cops/cops_topology.json`** rather than a fourth format: regenerate its `cops[].status` from the specs (it says `resource_management` is `defined`) and its metric `status` from `signal_metrics.METRIC_IDS` (5 metrics marked `built` are not computed).
5. **One drift test in `tests/test_cops.py`:** every spec pair has a `PAIR_RULES` entry or is explicitly undefined; every `HANDOFF_TRIGGERS` edge is in `spec.downstream_cops`; the exported JSON matches a fresh export.
6. **Storage: no change.** `cop_decisions` (`collector/cops_schema.sql`) already records `downstream_cops`, `upstream_decisions`, `metric_id` and `metrics_read`, which is the runtime trace of these edges. The two count views already proposed in `cops_topology.json` `live` are enough for a demo.

Gaps this report records but does not fix: 23 pairs have no rule (7 COPs have metrics but no interpreter; 3 more read no metric); 20 pairs have no `evidence_map.METRIC_NEEDS` entry; 15 of 30 hand-offs have no trigger; hand-offs are never executed; the harness path passes every earlier decision as upstream, the runner only `consumes_decisions_from`; `decide.finalise` can add an undeclared `audit_traceability` hand-off.
