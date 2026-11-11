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

CONTRACT_VERSION = "cops-runtime/2"   # /2 adds the triage record (graded verification); /1 fields unchanged
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
EXECUTED_SANDBOX = "EXECUTED_SANDBOX"  # carried out by the local sandbox gateway and verified there
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
    subject: str = ""                # what was checked, when one check runs on more than one thing
    # One entry per failed claim or condition: {claim, statement, kind, field, asserted, observed,
    # code, recoverable}. Lets a reader (and the recovery path) see exactly what failed.
    failures: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @property
    def blocks(self):
        return self.result == FAIL and self.effect != DIAGNOSTIC


@dataclass
class ProposedAction:
    cop: str
    action: str                      # legacy-compatible name (authority.LEGACY_TO_TYPED); unchanged contract
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
    cost_basis: str                  # measured | configured_price_estimate | simulated_tokens_x_configured_price | unpriced
    structured_response: Optional[dict]
    validation: dict                 # {ok, errors}
    retry_count: int = 0
    error: Optional[str] = None
    timed_out: bool = False
    tokens_measured: bool = False    # True only when the provider reported the counts
    stop_reason: Optional[str] = None
    request_id: Optional[str] = None # provider request id, for tracing a live call

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- triage (contract /2)
#
# grade_and_route (graph.py) attaches one TriageRecord to every finding as
# finding["triage"] and to the decision record as record["triage"]. Its four
# decision fields are stored separately and never derived from one another:
#   outcome           PASS | FAIL | INCONCLUSIVE   (finding validity, governance reading)
#   evidence_support  1-5 with supporting and limiting evidence (EvidenceSupport)
#   confidence        0-1 with its basis (ConfidenceBasis)
#   routing           tier and what happens next (Routing)
# proposed_action gains typed_action (authority.TAXONOMY) and triage_selected;
# proposed_action.action keeps the legacy name. Built by triage.classify.
OUTCOMES = ("PASS", "FAIL", "INCONCLUSIVE")
ROUTING_TIERS = ("autonomous", "assisted", "specialist", "human")
CANDIDATE_VERIFICATION = ("VERIFIED", "CONTRADICTED", "UNSUPPORTED", "UNTRUSTED_EVIDENCE", "NO_CANDIDATE")


@dataclass
class EvidenceSupport:
    score: int                       # 1-5 (triage.SCORE_MEANING)
    meaning: str
    rule: str                        # which derivation rule produced the score
    reading: Optional[str]           # the label the score is about (the rejected candidate's, if rejected)
    candidate_verification: str      # CANDIDATE_VERIFICATION
    provisional: bool                # score <= 3 on a verified reading: limitations are explicit
    supporting: list                 # [{kind, evidence, count}]
    limiting: list                   # [{kind, item, material, detail?}]
    contradictions: dict             # {factual: [...], interpretive: [...], status}


@dataclass
class ConfidenceBasis:
    value: float                     # min(raw, cap_from_score); 0 without a definite verified reading
    raw: float
    raw_source: str
    cap_from_score: float
    capped: bool
    calibration: str                 # "uncalibrated: ..." until validated outcomes exist
    definite_reading: bool
    note: str


@dataclass
class HandlingQuotient:
    value: float                     # perceived_handling_quotient, 0-1
    version: str
    components: dict                 # {name: {value, weight, contribution, basis}}
    note: str


@dataclass
class Routing:
    tier: str                        # ROUTING_TIERS
    band_from_quotient: str
    primary_reason: str
    rules_fired: list                # precedence rules R1-R6 that applied
    threshold_version: str
    next_step: str
    human_needed: bool
    automated_action_authorised: Optional[bool]   # set by authority_check
    specialist_triggers: list
    supporting_metrics: dict


@dataclass
class TriageRecord:
    version: str
    cop: str
    outcome: str
    outcome_reason: str
    evidence_support: dict           # EvidenceSupport
    confidence: dict                 # ConfidenceBasis
    llm_assessment: Optional[dict]   # the model's own candidate: never the verified score
    perceived_handling_quotient: dict  # HandlingQuotient
    thresholds: dict                 # triage.Thresholds for this COP
    routing: dict                    # Routing
    action_selection: dict           # {original_proposal, verified_proposal, selected, legacy_alias, reason, cause}
    referral: Optional[dict] = None  # triage.ReferralContract
    human_task: Optional[dict] = None
    authority: Optional[dict] = None   # set by authority_check
    feedback: Optional[dict] = None    # set by execute_action (step 12)
