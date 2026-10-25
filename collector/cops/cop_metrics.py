"""Which metrics each COP asks about, in one place.

Derived from spec.metric_keys. Kept here so the demos and the harness cannot
disagree about what a COP reads, which is the sort of drift that makes two
views of the same session contradict each other.
"""

from .specs import BY_KEY

# The COPS the demos show, in the order they show them. Their metrics come
# from the specs, so this list cannot drift from spec.metric_keys.
_SHOWN = ("runtime_monitoring", "anomaly_detection", "resource_management",
          "interaction_discovery", "policy_enforcement", "audit_traceability",
          "classification_tagging", "incident_response")

ASKS = {k: list(BY_KEY[k].metric_keys) for k in _SHOWN}

NAMES = {
    "runtime_monitoring": "Runtime Monitoring", "anomaly_detection": "Anomaly Detection",
    "resource_management": "Resource Management", "interaction_discovery": "Interaction Discovery",
    "policy_enforcement": "Policy Enforcement", "audit_traceability": "Audit & Traceability",
    "classification_tagging": "Classification", "incident_response": "Incident Response",
}

ORDER = list(ASKS)
