"""
What should exist when something happens.

Without this table the system cannot tell "nothing happened" from "something
happened and the collector failed to describe it". Every other part of the
evidence layer reads it.

Two tables, deliberately separate:

EVENT_DATA_MAP   for each kind of event, what information should exist and
                 which collectors can supply it, in precedence order.
METRIC_NEEDS     for each metric, which observables it needs and whether a
                 gap blocks the metric or merely weakens it.

Materiality is declared here, not inferred at runtime. Whether a missing field
blocks a decision is a judgement about the question being asked, so it is
written down where it can be reviewed.
"""

# ---------------------------------------------------------------- events

EVENT_DATA_MAP = {
    "file_access": {
        "required": ["file_path", "actor_id", "timestamp"],
        "optional": ["content_hash", "chars_added", "sensitivity_tier"],
        "collectors": ["fs_watcher", "local_hooks", "otel"],
        "occurrence_basis": "trace_steps where step in (LOOK, CHANGE, CREATE, DELETE)",
    },
    "tool_call": {
        "required": ["tool_name", "actor_id", "timestamp"],
        "optional": ["tool_arguments", "tool_result", "tool_duration_ms"],
        "collectors": ["local_hooks", "otel", "cli_wrapper"],
        "occurrence_basis": "trace_steps with tool_name present",
    },
    "mcp_call": {
        "required": ["mcp_server", "actor_id", "timestamp"],
        "optional": ["mcp_tool", "tool_arguments"],
        "collectors": ["mcp_proxy", "local_hooks", "otel"],
        "occurrence_basis": "trace_steps with mcp_server present",
    },
    "delegation": {
        "required": ["lane_id", "parent_agent_id", "timestamp"],
        "optional": ["agent_type", "delegation_depth"],
        "collectors": ["local_hooks", "otel", "fs_watcher"],
        "occurrence_basis": "observables with observable_type like subagent%",
    },
    "approval": {
        "required": ["permission_decision", "timestamp"],
        "optional": ["permission_scope", "permission_options", "approval_wait_ms"],
        "collectors": ["local_hooks"],
        "occurrence_basis": "observables with observable_type = permission_request",
    },
    "command": {
        "required": ["command", "actor_id", "timestamp"],
        "optional": ["exit_code", "stdout", "stderr"],
        "collectors": ["local_hooks", "cli_wrapper", "otel"],
        "occurrence_basis": "trace_steps with command present",
    },
    "model_request": {
        "required": ["model", "total_tokens", "timestamp"],
        "optional": ["cost_usd", "cache_read_tokens", "request_latency_ms"],
        "collectors": ["otel", "fs_watcher", "local_hooks"],
        "occurrence_basis": "observables with model present",
    },
}

# ---------------------------------------------------------------- metrics

BLOCKING, DEGRADING, IMMATERIAL = "BLOCKING", "DEGRADING", "IMMATERIAL"

METRIC_NEEDS = {
    "insights.sensitive_unasked": {
        "events": ["file_access", "approval"],
        "materiality": {"file_path": BLOCKING, "sensitivity_tier": BLOCKING,
                        "permission_decision": DEGRADING},
        "why": "cannot determine resource sensitivity without knowing the resource",
    },
    "insights.outside": {
        "events": ["file_access"],
        "materiality": {"file_path": BLOCKING, "workspace": BLOCKING},
        "why": "cannot determine whether a boundary was crossed without both sides of it",
    },
    "insights.secrets": {
        "events": ["file_access"],
        "materiality": {"file_path": DEGRADING, "content_hash": DEGRADING},
        "why": "detection runs on content; the path sharpens it but is not required",
    },
    "insights.token_outlier": {
        "events": ["model_request"],
        "materiality": {"total_tokens": BLOCKING, "model": DEGRADING,
                        "lane_id": DEGRADING},
        "why": "without a token count there is nothing to compare; lanes only apportion it",
    },
    "insights.duration_outlier": {
        "events": ["tool_call"],
        "materiality": {"tool_duration_ms": BLOCKING, "tool_name": DEGRADING},
        "why": "duration is the measure; the tool name groups the baseline",
    },
    "activity.action_rate_ratio": {
        "events": ["tool_call"],
        "materiality": {"timestamp": BLOCKING, "tool_name": IMMATERIAL},
        "why": "rate needs times; which tool it was does not change the rate",
    },
    "interaction.subagent_lanes": {
        "events": ["delegation"],
        "materiality": {"lane_id": DEGRADING, "parent_agent_id": DEGRADING},
        "why": "lanes can be counted from subagent_start without lane identity; "
               "the edges between them cannot be reconstructed",
    },
    "access.first_seen_mcp": {
        "events": ["mcp_call"],
        "materiality": {"mcp_server": BLOCKING},
        "why": "novelty is a property of the server's identity",
    },
    "insights.stalled": {
        "events": ["command"],
        "materiality": {"command": DEGRADING, "timestamp": BLOCKING},
        "why": "a stall is a gap in time; the command only describes it",
    },
    "telemetry.collector_coverage": {
        "events": ["tool_call"],
        "materiality": {"tool_name": IMMATERIAL, "timestamp": IMMATERIAL},
        "why": "this metric reports on collection itself; a gap is its subject, not its obstacle",
    },
    "telemetry.lane_linkage": {
        "events": ["delegation"],
        "materiality": {"lane_id": IMMATERIAL, "parent_agent_id": IMMATERIAL},
        "why": "a missing lane id is the finding here, not a blocker",
    },
    "telemetry.turn_linkage": {
        "events": ["tool_call"],
        "materiality": {"turn_id": IMMATERIAL},
        "why": "a missing turn id is the finding here, not a blocker",
    },

    "insights.repetition": {
        "events": ["tool_call"],
        "materiality": {"turn_id": BLOCKING, "tool_name": BLOCKING,
                        "tool_arguments": DEGRADING},
        "why": "repetition is defined within a turn; without turns there is no window",
    },
}


# Materiality belongs to the question, not only to the metric. The same
# missing field stops one COP and merely weakens another: without file_path,
# Policy Enforcement cannot say whether a boundary was crossed, while
# Interaction Discovery still knows the route, because the server is named.
# Overrides are declared here so the difference is reviewable rather than
# emergent.
COP_MATERIALITY = {
    "interaction_discovery": {"file_path": DEGRADING, "sensitivity_tier": IMMATERIAL},
    "audit_traceability":    {"file_path": DEGRADING, "lane_id": DEGRADING},
    "classification_tagging": {"file_path": DEGRADING, "sensitivity_tier": DEGRADING},
    "incident_response":     {"file_path": DEGRADING, "sensitivity_tier": DEGRADING},
    # Runtime Monitoring reports on collection itself: a gap is its subject.
    "runtime_monitoring":    {"*": IMMATERIAL},
    "resource_management":   {"file_path": IMMATERIAL, "sensitivity_tier": IMMATERIAL,
                              "lane_id": DEGRADING},
    "anomaly_detection":     {"file_path": IMMATERIAL, "sensitivity_tier": IMMATERIAL},
}


def events_for(metric_id):
    return METRIC_NEEDS.get(metric_id, {}).get("events", [])


def materiality(metric_id, field, cop=None):
    """How much this gap matters, to this COP, for this metric."""
    if cop and cop in COP_MATERIALITY:
        over = COP_MATERIALITY[cop]
        if "*" in over:
            return over["*"]
        if field in over:
            return over[field]
    return METRIC_NEEDS.get(metric_id, {}).get("materiality", {}).get(field, DEGRADING)


def collectors_for(field):
    """Precedence order for a field, from whichever events require it."""
    for ev in EVENT_DATA_MAP.values():
        if field in ev["required"] or field in ev.get("optional", []):
            return list(ev["collectors"])
    return []
