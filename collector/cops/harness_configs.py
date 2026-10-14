"""
Two reference COPs, configured against the one harness.

They are deliberately different. Runtime Monitoring sits closest to the running
agent and needs recent state, so its memory is short and live. Resource
Management needs a baseline to compare against, so it reaches for history
instead. Same harness, different contract, which is the whole claim.

Mapping the other sixteen means adding an entry here, never a code path.
"""

from .harness import HarnessConfig, Budgets

RUNTIME_MONITORING = HarnessConfig(
    cop="runtime_monitoring",
    skills=("read_metric", "read_evidence", "retrieve_collector_breakdown",
            "record_decision", "record_evidence_gap"),
    tools=("metric_reader", "evidence_service", "checkpoint_service",
           "persistence_store"),
    # it judges what is happening now, so it needs the recent window and
    # almost no history
    memory={"working": True, "session": True, "historical": "none",
            "retain": ["collector failures", "open alerts", "previous window status"]},
    budgets=Budgets(max_steps=8, max_tool_calls=4, max_context_requests=1,
                    max_retries=1, max_runtime_s=20),
    reverify="standard",
    cache_ttl_s=30,
    autonomous_actions=("NONE", "WATCH"),
    approval_actions=("ALERT", "REQUEST_CONTEXT"),
    prohibited_actions=("PAUSE", "BLOCK", "INSTRUCT", "ESCALATE"),
)

RESOURCE_MANAGEMENT = HarnessConfig(
    cop="resource_management",
    skills=("read_metric", "read_evidence", "compare_baseline",
            "retrieve_history", "retrieve_entitlement", "record_decision",
            "record_evidence_gap"),
    tools=("metric_reader", "evidence_service", "history_retriever",
           "entitlement_lookup", "persistence_store"),
    # consumption only means something against a baseline, so this one reaches
    # back; entitlement is listed although nothing supplies it yet, which is
    # why UNAUTHORIZED stays unreachable
    memory={"working": True, "session": True, "historical": "targeted",
            "retain": ["usage baseline per tool", "previous resource warnings"]},
    budgets=Budgets(max_steps=10, max_tool_calls=6, max_context_requests=2,
                    max_retries=1, max_runtime_s=30),
    reverify="standard",
    cache_ttl_s=300,
    autonomous_actions=("NONE", "WATCH"),
    approval_actions=("ALERT", "INSTRUCT"),
    prohibited_actions=("PAUSE", "BLOCK", "ESCALATE"),
)

# The rest map the same way. Only the differences are written out.
OTHERS = {
    "anomaly_detection":      {"memory": "targeted history for baselines",
                               "autonomous": ("NONE", "WATCH"),
                               "note": "needs history; must never claim prohibition"},
    "interaction_discovery":  {"memory": "known relationships, previous routes",
                               "autonomous": ("NONE", "WATCH"),
                               "note": "graph state is its memory"},
    "policy_enforcement":     {"memory": "policy version only",
                               "autonomous": ("NONE", "WATCH"),
                               "approval": ("ALERT", "REQUEST_APPROVAL"),
                               "note": "deterministic; strict reverification"},
    "incident_response":      {"memory": "open incidents, prior responses",
                               "autonomous": ("NONE", "WATCH"),
                               "approval": ("ALERT", "ESCALATE", "REQUEST_APPROVAL"),
                               "prohibited_until_live": ("PAUSE", "BLOCK"),
                               "note": "the only COP with intervening actions at all"},
    "audit_traceability":     {"memory": "retrieval rather than retention",
                               "autonomous": ("NONE", "WATCH"),
                               "note": "least live memory, most lookup"},
    "classification_tagging": {"memory": "taxonomy, and tags that persist on entities",
                               "autonomous": ("NONE",),
                               "note": "records only; no alerting"},
}

BY_COP = {RUNTIME_MONITORING.cop: RUNTIME_MONITORING,
          RESOURCE_MANAGEMENT.cop: RESOURCE_MANAGEMENT}


def default_for(cop):
    """A COP with no contract yet gets the most restrictive one."""
    return BY_COP.get(cop, HarnessConfig(cop=cop, skills=("read_metric", "read_evidence"),
                                         tools=("metric_reader", "evidence_service"),
                                         budgets=Budgets(max_steps=6, max_tool_calls=2)))
