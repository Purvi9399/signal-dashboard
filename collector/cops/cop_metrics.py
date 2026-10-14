"""Which metrics each COP asks about, in one place.

Mirrors spec.metric_keys. Kept here so the demos and the harness cannot
disagree about what a COP reads, which is the sort of drift that makes two
views of the same session contradict each other.
"""

ASKS = {
    "runtime_monitoring":     ["telemetry.collector_coverage", "telemetry.source_agreement",
                               "telemetry.turn_linkage", "telemetry.lane_linkage",
                               "telemetry.unknown_share"],
    "anomaly_detection":      ["insights.token_outlier", "insights.duration_outlier",
                               "path.transition_novelty", "activity.action_rate_ratio",
                               "access.first_seen_mcp", "insights.retry", "insights.repetition"],
    "resource_management":    ["insights.token_outlier", "insights.duration_outlier",
                               "insights.stalled", "activity.action_rate_ratio"],
    "interaction_discovery":  ["interaction.subagent_lanes", "interaction.mcp_servers",
                               "access.first_seen_mcp", "telemetry.lane_linkage",
                               "insights.outside"],
    "policy_enforcement":     ["insights.blocked", "insights.sensitive_unasked",
                               "insights.outside", "insights.secrets"],
    "audit_traceability":     ["insights.unattributed", "telemetry.turn_linkage",
                               "telemetry.lane_linkage", "telemetry.source_agreement"],
    "classification_tagging": ["insights.secrets", "insights.sensitive_unasked",
                               "insights.outside", "insights.blocked"],
    "incident_response":      ["insights.secrets", "insights.sensitive_unasked",
                               "insights.blocked"],
}

NAMES = {
    "runtime_monitoring": "Runtime Monitoring", "anomaly_detection": "Anomaly Detection",
    "resource_management": "Resource Management", "interaction_discovery": "Interaction Discovery",
    "policy_enforcement": "Policy Enforcement", "audit_traceability": "Audit & Traceability",
    "classification_tagging": "Classification", "incident_response": "Incident Response",
}

ORDER = list(ASKS)
