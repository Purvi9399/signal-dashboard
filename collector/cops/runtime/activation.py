"""
COPS activation contract: which COP investigates what, under which approved mapping.

The many-to-many mapping between metrics and COPs, and the conditions under which a
COP becomes eligible to investigate a case, as versioned, validated and approved
data. The router (router.py) evaluates it; this module only defines and governs it.

Reused, not replaced
    metric definitions    signal_metrics.METRICS + metrics_config.json thresholds
    behavioural question  specs.SPECS[cop].behavioural_question (spec version)
    interpretation        docs/cops/metric_cop_mappings.json (rule interpreter roles), or the
                          deterministic rule in runtime/rules.RULES when it reads the metric
    evidence requirements the mapping doc's evidence_requirements + the VS-01 profile
    dependencies          specs.CONSUMES (the executed case graph)
    consultation          referrals.py executes it; triage.SPECIALISTS names support COPs
    ids                   contracts.stable_id / digest

Contracts
    MetricDefinition      one metric with quality and provenance metadata
    CopMetricEdge         versioned metric -> COP edge: question, interpretation ref, evidence req.
    ActivationPolicy      owned by one COP: a typed rule reference over metric states, the
                          context it needs, and what to do when that context is missing
    ConsultationContract  typed COP -> COP consultation, executed through referrals.py
    ActivationRecord      one immutable transition of one COP in one case (router.py writes them)

Governance
    MappingBundle holds metric definitions, edges, policies and consultation contracts, and is
    identified by a content hash. MappingRegistry keeps bundles PROPOSED until a named person
    approves them; only an APPROVED bundle can be activated or used by the router. Feedback
    (or any non-human source) may propose a bundle; it can never approve or activate one.

Hard rule: a policy may only name a metric through an edge whose interpretation is DEFINED.
An UNDEFINED interpretation can never become an executable trigger (validate_bundle).
"""

import inspect
import json
import os
import sys
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime, timezone
from typing import Optional

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_metrics                                      # noqa: E402

from ..specs import BY_KEY, CONSUMES                        # noqa: E402
from .contracts import stable_id, digest                    # noqa: E402
from . import referrals as RF                               # noqa: E402
from . import rules as RU                                   # noqa: E402
from . import triage as TRI                                 # noqa: E402

CONTRACT_VERSION = "cops-activation/1"
REPO = os.path.dirname(HERE)
MAPPING_DOC = os.path.join(REPO, "docs", "cops", "metric_cop_mappings.json")

# metric states, ordered for "at least" comparisons; UNKNOWN / NOT_APPLICABLE are not on the ladder
LADDER = ("NORMAL", "WARNING", "EXCEPTION", "CRITICAL")
NO_JUDGEMENT = ("UNKNOWN", "NOT_APPLICABLE", "MISSING")

ELIGIBLE, NOT_ELIGIBLE, UNKNOWN = "ELIGIBLE", "NOT_ELIGIBLE", "UNKNOWN"
# what a policy does when the context it needs cannot be judged
REPORT_UNKNOWN = "REPORT_UNKNOWN"          # verdict UNKNOWN, not activated
ACTIVATE_TO_ASSESS = "ACTIVATE_TO_ASSESS"  # activated so the COP itself reports what is missing
MISSING_BEHAVIOURS = (REPORT_UNKNOWN, ACTIVATE_TO_ASSESS)

# ActivationRecord transitions
CANDIDATE, ACTIVATED, WAITING, COMPLETED, SUPPRESSED, TIMED_OUT = (
    "CANDIDATE", "ACTIVATED", "WAITING", "COMPLETED", "SUPPRESSED", "TIMED_OUT")
TRANSITIONS = (CANDIDATE, ELIGIBLE, NOT_ELIGIBLE, UNKNOWN, WAITING, ACTIVATED, COMPLETED, SUPPRESSED, TIMED_OUT)

PROPOSED, APPROVED, REJECTED, SUPERSEDED = "PROPOSED", "APPROVED", "REJECTED", "SUPERSEDED"
HUMAN = "human"


# ====================================================================== contracts

@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    basis: str                      # the observable field family the metric needs
    required_fields: tuple          # observables columns (collector/observables_schema.sql)
    applies_when: str
    thresholds: dict                # metrics_config.json
    quality: dict                   # what makes the value judgeable
    provenance: dict                # computed_by, code_ref, config digest


@dataclass(frozen=True)
class CopMetricEdge:
    edge_id: str
    cop: str
    metric_id: str
    behavioural_question: str
    spec_version: str
    interpretation_status: str      # DEFINED | UNDEFINED
    interpretation_ref: Optional[str]
    interpretation_rule: Optional[str]
    role: str
    evidence_requirements: dict
    source: str


@dataclass(frozen=True)
class Condition:
    metric_id: str
    at_least: str                   # a LADDER state


@dataclass(frozen=True)
class ActivationPolicy:
    cop: str
    owner: str                      # the COP that owns the rule
    rule_ref: str                   # a key of RULE_KINDS
    conditions: tuple               # Condition, for metric rules
    required_context: tuple         # metric ids the verdict depends on
    missing_evidence: str           # MISSING_BEHAVIOURS
    depends_on: tuple               # COPs whose findings it consumes (specs.CONSUMES)
    rationale: str
    version: str = "1"

    @property
    def policy_id(self):
        return f"{self.cop}@{self.version}#{digest(asdict(self))[:10]}"


@dataclass(frozen=True)
class ConsultationContract:
    initiator: str
    receiver: str
    kind: str                       # upstream_evidence | specialist_support
    question: str                   # the receiver's behavioural question
    when: str                       # the referral rule (referrals.needs) that opens it
    max_depth: int
    timeout_s: float
    source: str

    @property
    def contract_id(self):
        return f"{self.initiator}->{self.receiver}#{digest(asdict(self))[:10]}"


@dataclass(frozen=True)
class ActivationRecord:
    record_id: str
    seq: int
    case_id: str
    investigation_id: str
    cop: str
    transition: str
    reasons: tuple
    mapping_version: str
    policy_id: Optional[str]
    evidence: tuple                 # ({metric, state, value, refs}, ...)
    prev_record_id: Optional[str]
    decision_id: Optional[str] = None
    detail: tuple = ()              # extra (key, value) pairs: outcome, consultations, waiting_on
    at: str = ""                    # wall time; not part of the record id

    def canonical(self):
        """Everything but wall time: what a replay must reproduce exactly."""
        d = asdict(self)
        d.pop("at")
        return d


# ====================================================================== typed rules

def _state_ok(state, at_least):
    return state in LADDER and LADDER.index(state) >= LADDER.index(at_least)


def rule_any_condition(policy, evidence):
    """ELIGIBLE when any condition's metric is at or above its state. Missing context ->
    UNKNOWN only when no other condition already makes the COP eligible."""
    hits, unknown, quiet = [], [], []
    for c in policy.conditions:
        e = evidence.get(c.metric_id)
        st = e["state"] if e else "MISSING"
        if st in NO_JUDGEMENT:
            unknown.append(f"{c.metric_id} is {st}: cannot be judged")
        elif _state_ok(st, c.at_least):
            hits.append(f"{c.metric_id}={e['value']} is {st} (needs >= {c.at_least})")
        else:
            quiet.append(f"{c.metric_id}={e['value']} is {st} (needs >= {c.at_least})")
    if hits:
        return ELIGIBLE, tuple(hits + [f"also: {u}" for u in unknown])
    if unknown:
        return UNKNOWN, tuple(unknown + quiet)
    return NOT_ELIGIBLE, tuple(quiet)


def rule_case_has_telemetry(policy, evidence):
    """Telemetry reliability is assessed for every case that has rows: the reliability of
    every other COP's evidence depends on it."""
    if not evidence.get("__rows__", {}).get("value"):
        return NOT_ELIGIBLE, ("no observables in the case",)
    judged = [m for m in policy.required_context if (evidence.get(m) or {}).get("state") in LADDER]
    unk = [m for m in policy.required_context if m not in judged]
    return ELIGIBLE, (f"{evidence['__rows__']['value']} observables in the case",
                      *(f"{m} is {(evidence.get(m) or {}).get('state', 'MISSING')}" for m in unk))


RULE_KINDS = {
    "rule:any_condition@1": {"fn": rule_any_condition, "needs_conditions": True,
                             "doc": "eligible if any listed metric reaches its state"},
    "rule:case_has_telemetry@1": {"fn": rule_case_has_telemetry, "needs_conditions": False,
                                  "doc": "eligible for every case with observables"},
}


# ====================================================================== derived definitions

def _config():
    return signal_metrics.load_config()


def metric_definitions():
    cfg = _config()
    out = {}
    for mid, name, fn, basis, fields, applies in signal_metrics.METRICS:
        out[mid] = MetricDefinition(
            mid, name, basis, tuple(fields), applies, dict(cfg["thresholds"].get(mid) or {}),
            quality={"judgeable_when": f"basis '{basis}' present in the session rows; otherwise state UNKNOWN",
                     "applies_when": applies,
                     "min_sessions_for_baseline": cfg.get("min_sessions_for_baseline")},
            provenance={"computed_by": fn.__name__,
                        "code_ref": f"collector/signal_metrics.py:{inspect.getsourcelines(fn)[1]}",
                        "config_digest": digest(cfg["thresholds"].get(mid) or {})})
    # derived after the others in signal_metrics.compute: the share of judged metrics that are UNKNOWN
    u = "telemetry.unknown_share"
    out[u] = MetricDefinition(
        u, "Share of metrics that could not be judged", "derived", (), "always", dict(cfg["thresholds"][u]),
        quality={"judgeable_when": "always: derived from the other metrics' states", "applies_when": "always"},
        provenance={"computed_by": "signal_metrics.compute (derived)",
                    "code_ref": "collector/signal_metrics.py:" + str(next(
                        i for i, l in enumerate(open(signal_metrics.__file__), 1) if '"telemetry.unknown_share",' in l)),
                    "config_digest": digest(cfg["thresholds"][u])})
    return out


def _rule_reads(cop, metric_id):
    fn = RU.RULES.get(cop)
    if fn is None:
        return None
    src = inspect.getsource(fn)
    return f"collector/cops/runtime/rules.py:{fn.__name__}" if f'"{metric_id}"' in src else None


def cop_metric_edges(cops=None):
    doc = json.load(open(MAPPING_DOC))
    pairs = {(p["cop"], p["metric_id"]): p for p in doc["pairs"]}
    out = {}
    for cop, spec in BY_KEY.items():
        if cops and cop not in cops:
            continue
        for mid in spec.metric_keys:
            p = pairs.get((cop, mid)) or {}
            role = p.get("role") or "UNDEFINED"
            ref, rule, status = None, None, "UNDEFINED"
            if role != "UNDEFINED":
                ref, rule, status = p.get("rule_source"), p.get("interpretation_rule"), "DEFINED"
            elif _rule_reads(cop, mid):
                ref, status, role = _rule_reads(cop, mid), "DEFINED", "rule_input"
                rule = "read by the deterministic rule; see the function for the exact condition"
            req = dict(p.get("evidence_requirements") or {})
            e = CopMetricEdge(
                edge_id=f"{cop}|{mid}@{spec.version}", cop=cop, metric_id=mid,
                behavioural_question=spec.behavioural_question, spec_version=str(spec.version),
                interpretation_status=status, interpretation_ref=ref, interpretation_rule=rule, role=role,
                evidence_requirements=req,
                source="docs/cops/metric_cop_mappings.json + collector/cops/specs.py"
                       + (" + collector/cops/runtime/rules.py" if ref and "rules.py" in ref else ""))
            out[e.edge_id] = e
    return out


def consultation_contracts():
    """Typed consultations for the first slice. Each is executable only through referrals.py,
    and only where referrals already allows a referral: the receiver is consumed by the
    initiator and inside SCOPE_RECEIVERS."""
    def c(i, r, kind, src):
        return ConsultationContract(i, r, kind, BY_KEY[r].behavioural_question,
                                    "referrals.needs: the VS-01 upstream component is unknown or assumed, "
                                    "and the finding is not SUPPORTED or its action is not quiet",
                                    RF.MAX_DEPTH, RF.TIMEOUT_S, src)
    return (
        c("recovery_resilience", "behavior_analytics", "upstream_evidence", "collector/cops/specs.py CONSUMES"),
        c("anomaly_detection", "behavior_analytics", "specialist_support",
          "collector/cops/runtime/triage.py SPECIALISTS[anomaly_detection].support_cops"),
        c("anomaly_detection", "runtime_monitoring", "upstream_evidence", "collector/cops/specs.py CONSUMES"),
        c("behavior_analytics", "runtime_monitoring", "upstream_evidence", "collector/cops/specs.py CONSUMES"),
    )


def P(cop, rule, conds, context, missing, rationale):
    return ActivationPolicy(cop, cop, rule, tuple(Condition(m, s) for m, s in conds), tuple(context), missing,
                            tuple(CONSUMES[cop]), rationale)


SLICE = ("runtime_monitoring", "behavior_analytics", "anomaly_detection", "recovery_resilience")


def policies_v1():
    """The first slice's policies. Thresholds are the metrics_config states; the choice of
    state per COP is the COP owner's and is what the approval covers."""
    return (
        P("runtime_monitoring", "rule:case_has_telemetry@1", (),
          ("telemetry.collector_coverage", "telemetry.unknown_share", "telemetry.turn_linkage"),
          ACTIVATE_TO_ASSESS,
          "telemetry reliability bounds every other COP's evidence; Runtime Monitoring reports what is missing"),
        P("behavior_analytics", "rule:any_condition@1",
          (("insights.retry", "EXCEPTION"), ("insights.failed", "EXCEPTION"), ("insights.repetition", "EXCEPTION")),
          ("insights.retry", "insights.failed", "insights.repetition"), REPORT_UNKNOWN,
          "one failed test run followed by a rerun is ordinary debugging (retry=1 is WARNING); "
          "behaviour is investigated from EXCEPTION up"),
        P("anomaly_detection", "rule:any_condition@1",
          (("insights.token_outlier", "EXCEPTION"), ("insights.duration_outlier", "EXCEPTION"),
           ("activity.action_rate_ratio", "EXCEPTION"), ("access.first_seen_mcp", "EXCEPTION"),
           ("path.transition_novelty", "EXCEPTION")),
          ("insights.token_outlier", "insights.duration_outlier", "activity.action_rate_ratio",
           "access.first_seen_mcp", "path.transition_novelty"), REPORT_UNKNOWN,
          "only the mapping's driver metrics; insights.retry and insights.repetition are supporting-only "
          "for this COP and never trigger it"),
        P("recovery_resilience", "rule:any_condition@1",
          (("insights.reverted", "WARNING"), ("insights.retry", "EXCEPTION"), ("insights.failed", "EXCEPTION")),
          ("insights.reverted", "insights.retry", "insights.failed"), REPORT_UNKNOWN,
          "work put back, or repeated failure, raises whether a viable state was restored"),
    )


# ====================================================================== bundle and validation

@dataclass(frozen=True)
class MappingBundle:
    label: str
    metrics: tuple                  # MetricDefinition
    edges: tuple                    # CopMetricEdge (only the slice's COPs)
    policies: tuple                 # ActivationPolicy
    consultations: tuple            # ConsultationContract
    parent: Optional[str] = None    # version this bundle was derived from

    @property
    def version(self):
        body = {"metrics": [asdict(m) for m in self.metrics], "edges": [asdict(e) for e in self.edges],
                "policies": [asdict(p) for p in self.policies],
                "consultations": [asdict(c) for c in self.consultations]}
        return f"map-{digest(body)}"

    def policy(self, cop):
        return next((p for p in self.policies if p.cop == cop), None)

    def edge(self, cop, metric_id):
        return next((e for e in self.edges if e.cop == cop and e.metric_id == metric_id), None)

    def consultation_map(self):
        out = {}
        for c in self.consultations:
            out.setdefault(c.initiator, set()).add(c.receiver)
        return out

    def to_dict(self):
        return {"contract": CONTRACT_VERSION, "version": self.version, "label": self.label, "parent": self.parent,
                "metrics": [asdict(m) for m in self.metrics], "edges": [asdict(e) for e in self.edges],
                "policies": [{**asdict(p), "policy_id": p.policy_id} for p in self.policies],
                "consultations": [{**asdict(c), "contract_id": c.contract_id} for c in self.consultations]}


def default_bundle(label="first slice: RM, BA, AD, R&R"):
    edges = cop_metric_edges(SLICE)
    used = {m for e in edges.values() for m in [e.metric_id]}
    mdefs = metric_definitions()
    return MappingBundle(label, tuple(mdefs[m] for m in sorted(used) if m in mdefs),
                         tuple(edges[k] for k in sorted(edges)), policies_v1(), consultation_contracts())


class InvalidMapping(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _cycle(deps):
    seen, stack = set(), set()

    def visit(n, path):
        if n in stack:
            return path[path.index(n):] + [n]
        if n in seen:
            return None
        seen.add(n)
        stack.add(n)
        for d in deps.get(n, ()):
            c = visit(d, path + [n])
            if c:
                return c
        stack.discard(n)
        return None
    for n in sorted(deps):
        c = visit(n, [])
        if c:
            return c
    return None


def validate_bundle(b):
    errs = []
    mids = {m.metric_id for m in b.metrics}
    cops = {p.cop for p in b.policies}
    if len(cops) != len(b.policies):
        errs.append("more than one policy for a COP")
    for p in b.policies:
        if p.cop not in BY_KEY:
            errs.append(f"{p.cop}: not a COP in specs")
            continue
        if p.owner != p.cop:
            errs.append(f"{p.cop}: policy owned by {p.owner}; a COP owns its own activation rule")
        kind = RULE_KINDS.get(p.rule_ref)
        if kind is None:
            errs.append(f"{p.cop}: rule_ref {p.rule_ref} is not a registered typed rule")
        elif kind["needs_conditions"] and not p.conditions:
            errs.append(f"{p.cop}: {p.rule_ref} needs conditions")
        if p.missing_evidence not in MISSING_BEHAVIOURS:
            errs.append(f"{p.cop}: missing_evidence {p.missing_evidence} not in {MISSING_BEHAVIOURS}")
        for c in p.conditions:
            if c.at_least not in LADDER:
                errs.append(f"{p.cop}: {c.metric_id} state {c.at_least} not in {LADDER}")
            e = b.edge(p.cop, c.metric_id)
            if e is None:
                errs.append(f"{p.cop}: {c.metric_id} has no edge to this COP")
            elif e.interpretation_status != "DEFINED":
                errs.append(f"{p.cop}: {c.metric_id} interpretation is UNDEFINED; it cannot be a trigger")
            elif e.role.startswith("supporting"):
                errs.append(f"{p.cop}: {c.metric_id} is supporting-only for this COP; it cannot be a trigger")
        for m in p.required_context:
            if m not in mids:
                errs.append(f"{p.cop}: required context {m} has no metric definition")
        bad_dep = [d for d in p.depends_on if d not in CONSUMES[p.cop]]
        if bad_dep:
            errs.append(f"{p.cop}: depends_on {bad_dep} not consumed by the case graph (specs.CONSUMES)")
    cyc = _cycle({p.cop: [d for d in p.depends_on if d in cops] for p in b.policies})
    if cyc:
        errs.append("dependency cycle: " + " -> ".join(cyc))
    for c in b.consultations:
        if c.receiver not in CONSUMES.get(c.initiator, ()):
            errs.append(f"consultation {c.initiator}->{c.receiver}: receiver is not consumed by the initiator")
        if c.receiver not in RF.SCOPE_RECEIVERS:
            errs.append(f"consultation {c.initiator}->{c.receiver}: receiver outside referrals.SCOPE_RECEIVERS")
        if c.max_depth > RF.MAX_DEPTH or c.timeout_s > RF.TIMEOUT_S:
            errs.append(f"consultation {c.initiator}->{c.receiver}: exceeds the referral depth or time limit")
        if c.kind == "specialist_support" and c.receiver not in TRI.SPECIALISTS.get(c.initiator, {}).get(
                "support_cops", ()):
            errs.append(f"consultation {c.initiator}->{c.receiver}: not a support COP in triage.SPECIALISTS")
    return errs


# ====================================================================== governed registry

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class NotApproved(PermissionError):
    pass


@dataclass
class _Entry:
    bundle: MappingBundle
    status: str
    proposed_by: str
    proposer_kind: str
    proposed_at: str
    reason: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    history: list = field(default_factory=list)


class MappingRegistry:
    """Proposed -> approved by a named person -> active. Nothing else changes the active bundle."""

    def __init__(self, clock=_now):
        self.clock = clock
        self.entries = {}
        self.active_version = None

    def propose(self, bundle, by, kind, reason):
        errs = validate_bundle(bundle)
        v = bundle.version
        if v in self.entries:
            return self.entries[v]
        e = _Entry(bundle, REJECTED if errs else PROPOSED, by, kind, self.clock(), reason)
        e.history.append({"at": e.proposed_at, "event": "proposed", "by": by, "kind": kind,
                          "errors": errs})
        self.entries[v] = e
        return e

    def approve(self, version, by, kind):
        e = self.entries[version]
        if kind != HUMAN:
            raise NotApproved(f"{kind} {by!r} cannot approve a mapping; only a named person can")
        if e.status != PROPOSED:
            raise NotApproved(f"{version} is {e.status}; only a valid PROPOSED bundle can be approved")
        if by == e.proposed_by:
            raise NotApproved("the proposer cannot approve their own mapping change")
        e.status, e.approved_by, e.approved_at = APPROVED, by, self.clock()
        e.history.append({"at": e.approved_at, "event": "approved", "by": by})
        return e

    def activate(self, version):
        e = self.entries.get(version)
        if e is None or e.status != APPROVED:
            raise NotApproved(f"{version} is {'unknown' if e is None else e.status}; only APPROVED can be active")
        if self.active_version and self.active_version != version:
            self.entries[self.active_version].history.append({"at": self.clock(), "event": "superseded",
                                                              "by_version": version})
        self.active_version = version
        e.history.append({"at": self.clock(), "event": "activated"})
        return e

    def active(self):
        if not self.active_version:
            raise NotApproved("no approved mapping is active")
        return self.entries[self.active_version].bundle

    def pinned(self, version):
        """A bundle for replay: any approved version, active or not."""
        e = self.entries.get(version)
        if e is None or e.status != APPROVED:
            raise NotApproved(f"{version} cannot be used: {'unknown' if e is None else e.status}")
        return e.bundle

    def propose_from_feedback(self, base_version, changes, by, reason):
        """Feedback may only propose. changes: {cop: {metric_id: new_state}}."""
        base = self.entries[base_version].bundle
        pols = []
        for p in base.policies:
            ch = changes.get(p.cop) or {}
            conds = tuple(Condition(c.metric_id, ch.get(c.metric_id, c.at_least)) for c in p.conditions)
            extra = tuple(Condition(m, s) for m, s in ch.items() if m not in {c.metric_id for c in p.conditions})
            pols.append(replace(p, conditions=conds + extra, version=str(int(p.version) + 1) if ch else p.version))
        b = replace(base, policies=tuple(pols), parent=base_version, label=f"feedback proposal on {base_version}")
        return self.propose(b, by, "feedback", reason)

    def to_dict(self):
        return {"active_version": self.active_version,
                "versions": [{"version": v, "label": e.bundle.label, "status": e.status, "parent": e.bundle.parent,
                              "proposed_by": e.proposed_by, "proposer_kind": e.proposer_kind,
                              "approved_by": e.approved_by, "reason": e.reason, "history": e.history}
                             for v, e in self.entries.items()]}
