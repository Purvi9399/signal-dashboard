"""
The three shapes every COP shares.

MetricEvent    what a COP receives: one row of metric_events, never raw telemetry
COPReadSpec    what a COP is: a question, the metrics it reads, what it may do
COPDecision    what a COP produces, in one format, so it can be passed on and audited
"""

import hashlib, json, uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

NS = uuid.UUID("5e1a7c0e-0000-4000-8000-000000000002")

STATES = ("NORMAL", "WARNING", "EXCEPTION", "CRITICAL", "UNKNOWN", "NOT_APPLICABLE")
ATTENTION = ("WARNING", "EXCEPTION", "CRITICAL")

ACTIONS = ("NONE", "WATCH", "ALERT", "INSTRUCT", "REQUEST_CONTEXT",
           "REQUEST_APPROVAL", "PASS_TO_COP", "ESCALATE", "PAUSE", "BLOCK")
# Actions that change what an agent may do. Refused outside the live window,
# since a finished session cannot be paused, and refused in dry-run.
INTERVENING = ("PAUSE", "BLOCK")


@dataclass(frozen=True)
class MetricEvent:
    metric_event_id: str
    metric_id: str
    metric_name: str
    session_id: str
    value: Optional[float]
    state: str
    threshold: Optional[float] = None
    direction: Optional[str] = None
    trace_id: Optional[str] = None
    tool: Optional[str] = None
    features: dict = field(default_factory=dict)
    evidence_refs: tuple = ()
    source: str = ""
    metric_version: str = ""
    computed_at: str = ""
    basis_present: bool = True

    @classmethod
    def from_row(cls, r):
        keep = {k: r.get(k) for k in cls.__dataclass_fields__}
        keep["evidence_refs"] = tuple(r.get("evidence_refs") or ())
        keep["features"] = r.get("features") or {}
        keep["basis_present"] = r.get("basis_present", True) is not False
        return cls(**keep)

    def brief(self):
        """What an interpreter sees: the metric, not the rows behind it."""
        return {"metric": self.metric_id, "name": self.metric_name, "value": self.value,
                "threshold": self.threshold, "direction": self.direction, "state": self.state,
                "features": self.features, "trace_id": self.trace_id,
                "evidence_refs": list(self.evidence_refs[:5]),
                "evidence_count": len(self.evidence_refs)}


@dataclass(frozen=True)
class COPReadSpec:
    key: str                      # stable id, e.g. "anomaly_detection"
    name: str                     # "Anomaly Detection"
    behavioural_question: str
    metric_keys: tuple            # metric_ids that exist in signal_metrics today
    missing_metrics: tuple = ()   # what the question needs and nothing computes yet
    context_keys: tuple = ()      # narrow queries it may make, see reader.CONTEXT
    downstream_cops: tuple = ()
    permitted_actions: tuple = ("NONE", "WATCH")
    interpretations: tuple = ()   # the labels it may conclude with
    consumes_decisions_from: tuple = ()   # upstream COPS whose decisions it reads
    cadence: str = "session"      # "session" or "aggregate" (estate-level, slower)
    active: bool = False
    version: str = "1"


@dataclass
class COPDecision:
    cop: str
    cop_version: str
    session_id: str
    run_id: str
    behavioural_question: str
    metric_id: Optional[str] = None          # the metric that most drove the decision
    trace_id: Optional[str] = None
    evidence_refs: list = field(default_factory=list)
    metrics_read: list = field(default_factory=list)       # [{metric, value, state}]
    context_used: list = field(default_factory=list)       # [{key, rows, ms}]
    upstream_decisions: list = field(default_factory=list) # decision_ids it consumed
    interpretation: str = ""
    rationale: str = ""
    confidence: float = 0.0
    evidence_confidence: Optional[float] = None
    severity: str = "NONE"
    action: str = "NONE"
    action_reason: str = ""
    downstream_cops: list = field(default_factory=list)
    interpreter: str = ""                    # "rules" or "llm:<model>"
    skipped_interpreter: bool = False        # nothing needed attention, no model call made
    mode: str = "dry_run"
    evaluation_ms: int = 0
    failed: bool = False
    error: Optional[str] = None
    decided_at: str = ""

    @property
    def decision_id(self):
        return str(uuid.uuid5(NS, f"{self.run_id}|{self.session_id}|{self.cop}"))

    def inputs_hash(self):
        """Same inputs, same hash: makes two runs over one session comparable."""
        blob = json.dumps({"m": self.metrics_read, "u": self.upstream_decisions,
                           "c": [c["key"] for c in self.context_used]}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    def row(self):
        d = asdict(self)
        d["decision_id"] = self.decision_id
        d["inputs_hash"] = self.inputs_hash()
        return d
