"""
The eighteen operational COPS, as specifications.

A COP here is a question plus the metrics it reads and what it may do.
There is no analysis code in this file. metric_keys name rows that
signal_metrics.py computes today; missing_metrics name what the question
needs and nothing computes yet, so the gap is visible in code rather than
silently papered over.

The same metric appears under several COPS on purpose. It is computed
once; each COP reads it through its own question.

Only three are active. The rest are defined so their inputs can be
checked, not run.
"""

from .models import COPReadSpec

S = COPReadSpec

SPECS = [
    S(key="workflow_orchestration", name="Workflow Orchestration",
      behavioural_question="Is the task progressing coherently toward its intended state?",
      metric_keys=("insights.stalled", "insights.repetition", "oversight.declined_inferred",
                   "interaction.subagent_lanes"),
      missing_metrics=("task_progress", "intended_state", "task_drift", "unresolved_items",
                       "stalled_duration_per_turn", "excessive_delegation_ratio"),
      context_keys=("lane_path",),
      downstream_cops=("behavior_analytics", "incident_response"),
      permitted_actions=("NONE", "WATCH", "ALERT", "INSTRUCT", "PASS_TO_COP"),
      interpretations=("NORMAL", "DRIFTING", "STALLED", "EXCESSIVE_DELEGATION", "UNEXPECTED_BRANCH")),

    S(key="runtime_monitoring", name="Runtime Monitoring",
      behavioural_question="Is the evidence required for this governance decision present, reliable and sufficiently complete?",
      metric_keys=("telemetry.collector_coverage", "telemetry.source_agreement",
                   "telemetry.turn_linkage", "telemetry.lane_linkage", "telemetry.unknown_share"),
      missing_metrics=("collector_heartbeat", "event_freshness_live", "missing_event_rate",
                       "collector_errors"),
      context_keys=("collector_breakdown",),
      downstream_cops=("audit_traceability", "incident_response"),
      permitted_actions=("NONE", "WATCH", "ALERT", "REQUEST_CONTEXT", "PASS_TO_COP"),
      interpretations=("SUFFICIENT", "DEGRADED", "INSUFFICIENT"),
      active=True),

    S(key="anomaly_detection", name="Anomaly Detection",
      behavioural_question="Is the observed behaviour materially different from what is expected in this context?",
      metric_keys=("insights.token_outlier", "insights.duration_outlier", "path.transition_novelty",
                   "activity.action_rate_ratio", "access.first_seen_mcp", "insights.retry",
                   "insights.repetition"),
      missing_metrics=("tool_use_deviation_per_agent", "resource_access_novelty_files",
                       "token_expansion_per_turn"),
      context_keys=("lane_path", "evidence_rows"),
      downstream_cops=("classification_tagging", "policy_enforcement", "risk_mitigation",
                       "incident_response"),
      permitted_actions=("NONE", "WATCH", "ALERT", "REQUEST_CONTEXT", "PASS_TO_COP"),
      interpretations=("NORMAL", "ANOMALOUS", "INDETERMINATE"),
      consumes_decisions_from=("runtime_monitoring",),
      active=True),

    S(key="context_memory", name="Context & Memory",
      behavioural_question="Is the current context internally consistent, relevant to the task, and supported by useful precedent?",
      metric_keys=(),
      missing_metrics=("compaction_count", "context_loss_after_compaction", "session_reset",
                       "precedent_match", "context_consistency"),
      downstream_cops=("behavior_analytics",),
      permitted_actions=("NONE", "WATCH"),
      interpretations=("CONTEXT_OK", "STALE", "CONTRADICTORY", "CONTEXT_LOSS", "PRECEDENT_FOUND")),

    S(key="interaction_discovery", name="Interaction Discovery",
      behavioural_question="Who or what is interacting with whom, through what route, and how is the activity propagating?",
      metric_keys=("interaction.subagent_lanes", "interaction.mcp_servers", "access.first_seen_mcp",
                   "telemetry.lane_linkage", "insights.outside"),
      missing_metrics=("edge_table", "new_edges", "fan_in", "fan_out", "max_delegation_depth",
                       "cross_agent_path_general_to_coding"),
      context_keys=("lane_path", "evidence_rows"),
      downstream_cops=("policy_enforcement", "risk_mitigation", "audit_traceability"),
      permitted_actions=("NONE", "WATCH", "PASS_TO_COP"),
      interpretations=("CONTAINED", "PROPAGATING", "NEW_ROUTE", "PATH_PARTIAL"),
      consumes_decisions_from=("runtime_monitoring",)),

    S(key="incident_response", name="Incident Response",
      behavioural_question="Do the current findings constitute an actionable incident, and what should happen now?",
      metric_keys=("insights.secrets", "insights.sensitive_unasked", "insights.blocked"),
      missing_metrics=("time_to_harm", "intervention_available", "affected_entities"),
      context_keys=("evidence_rows",),
      downstream_cops=(),
      permitted_actions=("NONE", "WATCH", "ALERT", "INSTRUCT", "REQUEST_APPROVAL",
                         "PAUSE", "BLOCK", "ESCALATE"),
      interpretations=("NO_INCIDENT", "WATCH", "INCIDENT"),
      consumes_decisions_from=("runtime_monitoring", "anomaly_detection",
                               "policy_enforcement", "interaction_discovery"),
      active=True),

    S(key="classification_tagging", name="Classification & Tagging",
      behavioural_question="What kind of governance event is this, and which entities should remain associated with it?",
      metric_keys=("insights.secrets", "insights.sensitive_unasked", "insights.outside", "insights.blocked"),
      missing_metrics=("actor_type", "resource_type", "incident_family", "classification_confidence"),
      downstream_cops=("governance_intelligence",),
      permitted_actions=("NONE",),
      interpretations=("UNCLASSIFIED", "BOUNDARY", "OVERSIGHT", "LOOPS", "OUTCOME", "INTEGRITY", "COST")),

    S(key="policy_enforcement", name="Policy Enforcement",
      behavioural_question="Does the observed behaviour violate an explicit permission, policy, entitlement, boundary, or approval requirement?",
      metric_keys=("insights.blocked", "insights.sensitive_unasked", "insights.outside",
                   "insights.secrets", "oversight.declined_inferred"),
      missing_metrics=("entitlement_mismatch", "indirect_privilege_use", "policy_precedence",
                       "missing_required_approval"),
      context_keys=("matched_policy", "lane_path"),
      downstream_cops=("incident_response", "risk_mitigation", "audit_traceability"),
      permitted_actions=("NONE", "WATCH", "ALERT", "REQUEST_APPROVAL", "BLOCK", "PASS_TO_COP"),
      interpretations=("ALLOWED", "VIOLATION", "APPROVAL_REQUIRED", "UNCERTAIN"),
      consumes_decisions_from=("runtime_monitoring", "interaction_discovery")),

    S(key="risk_mitigation", name="Risk Mitigation",
      behavioural_question="Do the combined findings create a material or emerging risk, and what residual risk remains after available controls?",
      metric_keys=("insights.secrets", "insights.sensitive_unasked", "insights.outside",
                   "insights.reverted", "insights.token_outlier"),
      missing_metrics=("compound_risk_score", "reversibility", "residual_risk"),
      downstream_cops=("incident_response",),
      permitted_actions=("NONE", "WATCH", "INSTRUCT", "ESCALATE", "PASS_TO_COP"),
      interpretations=("LOW", "EMERGING", "MATERIAL")),

    S(key="resource_management", name="Resource Management",
      behavioural_question="Is resource consumption within entitlement and acceptable operating bounds?",
      metric_keys=("insights.token_outlier", "insights.duration_outlier", "insights.stalled",
                   "activity.action_rate_ratio"),
      missing_metrics=("budget_or_quota", "cpu_gpu_memory", "rate_limit_hits"),
      downstream_cops=("workload_optimization", "risk_mitigation"),
      permitted_actions=("NONE", "WATCH", "ALERT"),
      interpretations=("NORMAL", "INEFFICIENT", "EXCESSIVE", "UNAUTHORIZED")),

    S(key="governance_intelligence", name="Governance Intelligence",
      behavioural_question="What recurring patterns, concentrations or blind spots indicate a broader governance problem?",
      metric_keys=(),
      missing_metrics=("metric_state_trends_from_metric_events", "decision_trends_from_cop_decisions",
                       "repeat_actor_concentration"),
      downstream_cops=("governance_architecture", "knowledge_policy_management"),
      permitted_actions=("NONE", "WATCH"),
      interpretations=("NO_PATTERN", "RECURRING", "CONCENTRATED", "BLIND_SPOT"),
      cadence="aggregate"),

    S(key="behavior_analytics", name="Behavior Analytics",
      behavioural_question="What behavioural pattern best characterizes this execution or sequence of executions?",
      metric_keys=("insights.repetition", "insights.retry", "insights.stalled", "insights.failed",
                   "oversight.fast_approvals"),
      missing_metrics=("loop_score_per_lane", "exploration_rate", "planning_deviation",
                       "recovery_pattern"),
      context_keys=("lane_path",),
      downstream_cops=("anomaly_detection", "recovery_resilience"),
      permitted_actions=("NONE", "WATCH"),
      interpretations=("STABLE", "LOOPING", "DRIFTING", "OVER_EXPLORING", "POOR_RECOVERY")),

    S(key="recovery_resilience", name="Recovery & Resilience",
      behavioural_question="After failure or intervention, did the system return to a viable state without compounding the problem?",
      metric_keys=("insights.reverted", "insights.retry", "insights.failed"),
      missing_metrics=("recovery_success_after_failure", "irreversible_change", "recovery_time"),
      context_keys=("lane_path",),
      downstream_cops=("incident_response", "governance_intelligence"),
      permitted_actions=("NONE", "WATCH", "ALERT"),
      interpretations=("RECOVERED", "PARTIAL_RECOVERY", "FAILED_RECOVERY", "IRREVERSIBLE")),

    S(key="workload_optimization", name="Workload Optimization",
      behavioural_question="Is the workload using tools, models, time and resources efficiently for the task being performed?",
      metric_keys=("insights.repetition", "insights.duration_outlier", "insights.token_outlier",
                   "activity.action_rate_ratio"),
      missing_metrics=("cost_per_task", "cache_efficiency", "redundant_reads"),
      downstream_cops=("resource_management",),
      permitted_actions=("NONE", "WATCH", "INSTRUCT"),
      interpretations=("EFFICIENT", "REDUNDANT", "BOTTLENECK", "OVERPROVISIONED", "UNDERPROVISIONED")),

    S(key="audit_traceability", name="Audit & Traceability",
      behavioural_question="Can every consequential finding, decision and action be attributed and reconstructed from trustworthy evidence?",
      metric_keys=("insights.unattributed", "telemetry.turn_linkage", "telemetry.lane_linkage",
                   "telemetry.source_agreement"),
      missing_metrics=("decision_evidence_coverage", "raw_ref_resolvability"),
      context_keys=("evidence_rows",),
      downstream_cops=("governance_architecture",),
      permitted_actions=("NONE", "WATCH", "ALERT"),
      interpretations=("COMPLETE", "PARTIAL", "BROKEN_TRACE", "UNATTRIBUTABLE")),

    S(key="knowledge_policy_management", name="Knowledge & Policy Management",
      behavioural_question="Are the rules, policies and governance knowledge currently being used still current, coherent and internally consistent?",
      metric_keys=(),
      missing_metrics=("policy_store", "policy_version", "rule_conflicts", "rule_hit_rate", "override_rate"),
      downstream_cops=("governance_architecture",),
      permitted_actions=("NONE", "WATCH"),
      interpretations=("CURRENT", "STALE", "CONFLICT", "UPDATE_REQUIRED"),
      cadence="aggregate"),

    S(key="agent_lifecycle_management", name="Agent Lifecycle Management",
      behavioural_question="Has an agent's identity, version, configuration, capability or connectivity changed in a way requiring governance attention?",
      metric_keys=("access.first_seen_mcp",),
      missing_metrics=("tool_version_change", "model_change", "new_machine", "first_last_seen",
                       "hook_config_removed"),
      downstream_cops=("classification_tagging", "policy_enforcement"),
      permitted_actions=("NONE", "WATCH", "ALERT"),
      interpretations=("UNCHANGED", "NEW", "CHANGED", "DRIFTED", "INACTIVE", "RETIRED")),

    S(key="governance_architecture", name="Governance Architecture",
      behavioural_question="Does the current governance architecture adequately cover the estate and the behaviours actually occurring?",
      metric_keys=("telemetry.unknown_share",),
      missing_metrics=("observable_field_status_per_tool", "cops_coverage", "unhandled_exceptions",
                       "repeated_escalations"),
      downstream_cops=(),
      permitted_actions=("NONE", "WATCH"),
      interpretations=("ADEQUATE", "GAP", "STRUCTURAL_CHANGE_RECOMMENDED"),
      cadence="aggregate"),
]

BY_KEY = {s.key: s for s in SPECS}
ACTIVE = tuple(s.key for s in SPECS if s.active)
# Convention: interpretations[0] is the "nothing needs attention" reading,
# used when the runner skips a costly interpreter because no metric is off.
# Order the active COPS run in: evidence first, then interpretation, then response.
ORDER = ("runtime_monitoring", "anomaly_detection", "interaction_discovery",
         "policy_enforcement", "incident_response")
