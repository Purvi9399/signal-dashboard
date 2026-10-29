"""
Typed contracts for the LangGraph COP runtime.

These sit beside, not instead of, cops/models.py. MetricEvent, COPReadSpec
and COPDecision keep their shapes; the runtime adds what they never carried:
evidence semantics, typed findings with claims, verifier results, proposed
actions with authority outcomes, memory records and LLM invocation records.

Every object here is plain data that serialises to JSON, because it travels
through LangGraph state and is checkpointed.

Compatibility note: malhar-sovereign/project-signal's
collector_framework/cops/contracts.py could not be read from this session.
Names here follow this repository's models.py. Mapping them onto that file is
an open item (see docs/cops/RUNTIME.md, "Needs agreement").
"""

from __future__ import annotations

import hashlib, json, uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

CONTRACT_VERSION = "cops-runtime/1"
NS = uuid.UUID("5e1a7c0e-0000-4000-8000-000000000003")

# ---------------------------------------------------------------- evidence semantics
#
# Five ways a metric can stand, and they must never be merged:
#   OBSERVED        a value was computed and judged against its threshold
#   UNKNOWN         the condition may have arisen and could not be observed
#   NOT_APPLICABLE  the condition the metric asks about never arose
#   INVALID         a row arrived but is malformed or self-contradictory
#   MISSING         no row at all: the metric was never computed for this case
OBSERVED, UNKNOWN, NOT_APPLICABLE, INVALID, MISSING = (
    "OBSERVED", "UNKNOWN", "NOT_APPLICABLE", "INVALID", "MISSING")
EVIDENCE_STATES = (OBSERVED, UNKNOWN, NOT_APPLICABLE, INVALID, MISSING)
JUDGED_STATES = ("NORMAL", "WARNING", "EXCEPTION", "CRITICAL")
ATTENTION_STATES = ("WARNING", "EXCEPTION", "CRITICAL")

# ---------------------------------------------------------------- finding status
SUBSTANTIVE = "SUBSTANTIVE"            # a label the evidence supports
INCONCLUSIVE = "INCONCLUSIVE"          # looked, could not decide; reasons given
MISSING_DEPENDENCY = "MISSING_DEPENDENCY"   # a required source does not exist
REJECTED = "REJECTED_BY_VERIFIER"      # a candidate failed a blocking check
FAILED = "FAILED"                      # the COP itself errored
FINDING_STATUSES = (SUBSTANTIVE, INCONCLUSIVE, MISSING_DEPENDENCY, REJECTED, FAILED)

# ---------------------------------------------------------------- verifier effects
BLOCKS_FINDING = "BLOCKS_FINDING"      # failure: the finding may not be substantive
BLOCKS_ACTION = "BLOCKS_ACTION"        # failure: the proposed action may not proceed
BLOCKS_MEMORY = "BLOCKS_MEMORY"        # failure: the memory write is refused
DIAGNOSTIC = "DIAGNOSTIC"              # recorded, changes nothing
PASS, FAIL, WARN, NOT_RUN = "PASS", "FAIL", "WARN", "NOT_RUN"

# ---------------------------------------------------------------- execution status
NOT_EXECUTED_DRY_RUN = "NOT_EXECUTED_DRY_RUN"
NOT_EXECUTED_NO_ACTION = "NOT_EXECUTED_NO_ACTION"
NOT_EXECUTED_BLOCKED = "NOT_EXECUTED_BLOCKED"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVAL_REJECTED = "APPROVAL_REJECTED"
EXECUTED_MOCK = "EXECUTED_MOCK"        # ran against the mock enforcement gateway only
EXECUTION_FAILED = "EXECUTION_FAILED"  # attempted and the outcome check failed

# ---------------------------------------------------------------- claim kinds
CLAIM_METRIC_STATE = "metric_state"        # metric_id is in asserted_state
CLAIM_METRIC_VALUE = "metric_value"        # metric_id has asserted_value
CLAIM_TOOL_OBSERVATION = "tool_observation"  # tool call returned result_field == asserted_value
CLAIM_MEMORY_PRECEDENT = "memory_precedent"  # a validated record supports this
CLAIM_UPSTREAM = "upstream_finding"        # an upstream COP concluded label
CLAIM_ABSENCE = "absence"                  # something expected was not present
CLAIM_KINDS = (CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE, CLAIM_TOOL_OBSERVATION,
               CLAIM_MEMORY_PRECEDENT, CLAIM_UPSTREAM, CLAIM_ABSENCE)

ORIGIN_RULES, ORIGIN_LLM = "computational", "llm_hypothesis"


def stable_id(*parts):
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def digest(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Claim:
    """One checkable statement inside a finding."""
    statement: str
    kind: str
    metric_id: Optional[str] = None
    asserted_state: Optional[str] = None
    asserted_value: Optional[float] = None
    tool_call_id: Optional[str] = None
    result_field: Optional[str] = None
    record_id: Optional[str] = None
    upstream_cop: Optional[str] = None
    upstream_label: Optional[str] = None
    evidence_refs: list = field(default_factory=list)
    origin: str = ORIGIN_RULES

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        keep = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        keep.setdefault("evidence_refs", [])
        keep["evidence_refs"] = list(keep.get("evidence_refs") or [])
        return cls(**keep)


@dataclass
class Finding:
    """What a COP concludes, before and after verification."""
    cop: str
    case_id: str
    label: str
    status: str
    confidence: float
    origin: str                      # computational | llm_hypothesis
    claims: list = field(default_factory=list)          # [Claim dict]
    rationale: str = ""
    severity: str = "NONE"
    missing_evidence: list = field(default_factory=list)   # [{metric|source, reason}]
    missing_dependencies: list = field(default_factory=list)  # [{dependency, required_for, how_to_supply}]
    lineage: list = field(default_factory=list)          # root metric ids / sources it rests on
    upstream_used: list = field(default_factory=list)    # [{cop, label, finding_id, counted_as}]
    cop_specific: dict = field(default_factory=dict)
    verified: bool = False
    qualifications: list = field(default_factory=list)   # verifier notes that lowered standing
    hypothesis_trail: list = field(default_factory=list)  # LLM COPs: how the hypothesis moved

    @property
    def finding_id(self):
        return stable_id(self.case_id, self.cop, "finding")

    def to_dict(self):
        d = asdict(self)
        d["finding_id"] = self.finding_id
        return d

    @classmethod
    def from_dict(cls, d):
        keep = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**keep)


@dataclass
class VerifierResult:
    check_id: str
    version: str
    category: str
    result: str                      # PASS | FAIL | WARN | NOT_RUN
    effect: str                      # BLOCKS_FINDING | BLOCKS_ACTION | BLOCKS_MEMORY | DIAGNOSTIC
    reason_code: str
    detail: str = ""
    evidence_refs: list = field(default_factory=list)
    independent: bool = True         # False for diagnostics that re-run the same logic

    def to_dict(self):
        return asdict(self)

    @property
    def blocks(self):
        return self.result == FAIL and self.effect != DIAGNOSTIC


@dataclass
class ProposedAction:
    cop: str
    action: str
    reason: str
    target: Optional[str] = None     # resource or agent the action would apply to
    requires_approval: bool = False
    authority_tier: str = ""
    authorised: Optional[bool] = None
    authority_reason: str = ""
    approval: Optional[dict] = None  # {decision, reviewer, at, note}
    execution_status: str = NOT_EXECUTED_NO_ACTION
    execution_outcome: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- memory
UNVALIDATED, VALIDATED, REJECTED_RECORD, EXPIRED = "UNVALIDATED", "VALIDATED", "REJECTED", "EXPIRED"


@dataclass
class MemoryRecord:
    tenant_id: str
    agent_scope: str                 # agent / operator the record is about, or "*"
    owner_cop: str
    record_type: str
    payload: dict
    provenance: dict                 # {case_id, finding_id, decision_id, evidence_refs, verifier_ids}
    validation_status: str = UNVALIDATED
    created_at: str = ""
    expires_at: Optional[str] = None
    record_id: str = ""

    def __post_init__(self):
        if not self.record_id:
            # same finding written twice upserts; different agents or findings never collide
            self.record_id = stable_id(self.tenant_id, self.agent_scope, self.owner_cop, self.record_type,
                                       digest(self.payload), self.provenance.get("case_id"),
                                       self.provenance.get("finding_id"))

    def to_dict(self):
        return asdict(self)


@dataclass
class LLMInvocation:
    """One model call, recorded whether it succeeded or not."""
    invocation_id: str
    cop: str
    case_id: str
    purpose: str                     # plan | assess
    provider: str
    model: str
    model_version: str
    prompt_version: str
    simulated: bool
    evidence_refs: list
    tools_exposed: list
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: float
    cost_usd: Optional[float]
    cost_basis: str                  # measured | list_price_estimate | unpriced
    structured_response: Optional[dict]
    validation: dict                 # {ok, errors}
    retry_count: int = 0
    error: Optional[str] = None
    timed_out: bool = False

    def to_dict(self):
        return asdict(self)
