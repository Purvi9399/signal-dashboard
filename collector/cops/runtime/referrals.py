"""
COP-to-COP referrals that execute.

triage.ReferralRouter opens a referral contract and stops there. This module
runs it: one COP asks another for evidence, the receiving COP is executed
through the same LangGraph COP subgraph (graph.run_cop), its answer is checked,
and the initiating COP is run again with the answer as an upstream input so
VS-01, the handling quotient and the routing are recomputed by the same code.

    investigate(rt, cop)            one COP's investigation, as a LangGraph graph:

        assess -> plan_referrals -> dispatch -> reassess -> evaluate -> finalise
                       ^                                       |
                       +------------- more needs, rounds left -+

    dispatch runs each receiving COP as its own investigate() one level deeper,
    so a receiver may refer onward within the same limits.

What a referral may ask for (permitted scope)
    Only a COP the initiator consumes (specs.CONSUMES), because only those
    findings are inputs to the initiator's rules and to its VS-01 profile.
    Milestone 1 enables RM, BA and WO as receivers (SCOPE_RECEIVERS); a need
    outside it is recorded as not referred, never executed.

When a referral is opened (needs())
    A verified upstream the VS-01 score counts is absent or unverified, or the
    score assumed the telemetry reliability because Runtime Monitoring had not
    run (support._assumptions), and either the finding is not SUPPORTED or the
    selected action does something (anything but NONE and WATCH). A SUPPORTED
    finding that only records is not re-investigated: there is nothing to gain.

Limits, each enforced here
    depth          MAX_DEPTH hops (triage.MAX_HOPS); a deeper request is refused
    cycle          a receiver already in the chain is refused
    rounds         MAX_ROUNDS referral-and-reassess rounds per investigation
    retries        MAX_RETRIES re-runs of a receiver whose run errored
    runs           MAX_RUNS COP runs in the whole investigation tree
    cost           COST_LIMIT_USD for the whole tree; each receiver runs with its
                   LLM budget capped at what is left, and the gateway refuses the
                   next call once it is spent (the check is before a call, so the
                   last call can overshoot it); a spent budget is never retried
    timeout        TIMEOUT_S per receiving investigation, measured on return
                   (the run is not pre-empted; a late answer is discarded)

What an answer may become (verify_response)
    RS-01  identity: the answer is the receiver's finding for this case and tenant
    RS-02  verified: the receiver's own independent verifiers passed
    RS-03  traceable: every evidence ref in its claims resolves in its own run
    RS-04  in scope: only allowlisted tools, dry-run, no action executed
    RS-05  within limits: time and cost
    All five pass: admissible, delivered as a verified upstream finding.
    RS-02 alone fails: delivered as context only (verified=False), which the
    rules, the tools and VS-01 already refuse to count. Any other failure: not
    delivered. Nothing here sets verified=True on anything.

Everything a referral produces is persisted under its own run id, so the first
and the revised decision of the same COP are separate rows.
"""

import copy, time
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from ..specs import BY_KEY, CONSUMES, topological_order
from .contracts import stable_id, SUBSTANTIVE
from .persistence import decision_id
from . import support as SUP
from . import triage as TRI

REFERRAL_VERSION = "referral-exec-1"
MAX_DEPTH = TRI.MAX_HOPS                # hops from the root investigation
MAX_ROUNDS = 2                          # referral-and-reassess rounds per investigation
MAX_RETRIES = 1                         # re-runs of a receiver whose run errored
MAX_RUNS = 12                           # COP runs in one investigation tree
COST_LIMIT_USD = TRI.REFERRAL_COST_USD  # whole tree
TIMEOUT_S = TRI.REFERRAL_TIMEOUT_S      # per receiving investigation
SCOPE_RECEIVERS = ("runtime_monitoring", "behavior_analytics", "workflow_orchestration")
QUIET_ACTIONS = ("NONE", "WATCH")

# response statuses
ANSWERED = "ANSWERED_VERIFIED"
UNVERIFIED = "ANSWERED_UNVERIFIED"
NO_FINDING = "ANSWERED_NO_FINDING"
REUSED = "REUSED_VERIFIED"
FAILED = "RECEIVER_FAILED"
TIMED_OUT = "TIMED_OUT"
REFUSED_CYCLE, REFUSED_DEPTH = "REFUSED_CYCLE", "REFUSED_DEPTH"
REFUSED_SCOPE, REFUSED_BUDGET, REFUSED_RUNS = "REFUSED_SCOPE", "REFUSED_BUDGET", "REFUSED_RUN_LIMIT"
LIMIT_STATUSES = (TIMED_OUT, REFUSED_CYCLE, REFUSED_DEPTH, REFUSED_BUDGET, REFUSED_RUNS)

# resolutions
CONFIRMED = "CONFIRMED"         # same reading; uncertainty reduced
REVISED = "REVISED"             # the reading or its support decision changed
UNCHANGED = "UNCHANGED"         # reassessed, nothing moved
UNRESOLVED = "UNRESOLVED"       # no admissible evidence came back
NOT_NEEDED = "NOT_NEEDED"       # nothing to refer

DECISION_RANK = {SUP.REJECTED: 0, SUP.INSUFFICIENT: 1, SUP.PROVISIONAL: 2, SUP.SUPPORTED: 3}


def _iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).isoformat(timespec="milliseconds")


# ====================================================================== contracts

@dataclass
class ReferralRequest:
    case_id: str
    tenant_id: str
    session_id: str
    initiator: str
    receiver: str
    question: str
    reason: str
    original_finding: dict          # id, label, status, VS-01 support/against/unknown, quotient, tier, action
    missing_evidence: list          # [{item, state, detail}]
    trace_ids: dict                 # initiator run, thread, decision, finding, tool calls, model calls, parent
    permitted_scope: dict           # tenant, session, receiver tools, mode, may execute, depth, cost, time
    chain: list
    depth: int
    investigation_id: str
    round: int = 1
    created_at: str = ""
    version: str = REFERRAL_VERSION

    @property
    def referral_id(self):
        return stable_id(self.investigation_id, self.initiator, self.receiver, self.round, "referral-exec")

    def to_dict(self):
        d = asdict(self)
        d["referral_id"] = self.referral_id
        return d


@dataclass
class ReferralResponse:
    referral_id: str
    receiver: str
    status: str
    admissible: bool = False
    delivered_as: str = "not_delivered"     # evidence | context_only | not_delivered
    finding: Optional[dict] = None          # the receiver's final finding, as published
    verification_support: Optional[dict] = None
    evidence: list = field(default_factory=list)   # the receiver's claims with their refs
    checks: list = field(default_factory=list)     # RS-01..RS-05
    receiver_trace: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    attempts: int = 0
    detail: str = ""
    answered_at: str = ""

    def to_dict(self):
        return asdict(self)


# ====================================================================== snapshots

def snapshot(out, run_key, rt):
    """What one COP run concluded, in the terms the referral compares."""
    f = out["finding"]
    vs = f.get("verification_support") or {}
    tri = f.get("triage") or {}
    pa = out.get("proposed_action") or (out.get("result") or {}).get("proposed_action") or {}
    hq = tri.get("perceived_handling_quotient") or {}
    res = out.get("result") or {}
    return {
        "run_key": run_key, "run_id": res.get("run_id"), "at": _iso(),
        "decision_id": decision_id(res.get("run_id"), res.get("session_id"), f.get("cop")) if res else None,
        "finding_id": f.get("finding_id"), "label": f.get("label"), "status": f.get("status"),
        "verified": bool(f.get("verified")), "provisional": bool(f.get("provisional")),
        "verification_support": {"decision": vs.get("decision"), "score": vs.get("score"),
                                 "against": vs.get("against"), "unknown": vs.get("unknown"),
                                 "telemetry_reliability": vs.get("telemetry_reliability"),
                                 "assumptions": [a["item"] for a in vs.get("assumptions") or []],
                                 "components": [{"name": c["name"], "direction": c["item"]["direction"],
                                                 "weight": c["weight"], "contribution": c.get("contribution", 0.0),
                                                 "basis": c["item"].get("basis", "")[:200]}
                                                for c in vs.get("components") or []]},
        "quotient": hq.get("value"), "quotient_components": {k: v.get("value") for k, v in
                                                             (hq.get("components") or {}).items()},
        "outcome": tri.get("outcome"),
        "tier": (tri.get("routing") or {}).get("tier"),
        "routing_reason": (tri.get("routing") or {}).get("primary_reason"),
        "selected_action": (tri.get("action_selection") or {}).get("selected"),
        "action_reason": (tri.get("action_selection") or {}).get("reason"),
        "cause": (tri.get("action_selection") or {}).get("cause"),
        "final_action": pa.get("typed_action") or pa.get("action"),
        "authorised": (pa.get("final_authority") or {}).get("authorised"),
        "execution_status": pa.get("execution_status"),
        "upstream_used": {u["cop"]: {"label": u.get("label"), "verified": u.get("verified")}
                          for u in res.get("upstream") or [] if u.get("present")},
        "llm_calls": len(res.get("llm_invocations") or []),
        "tool_call_ids": [c["tool_call_id"] for c in res.get("tool_calls") or []],
        "cost_usd": float((res.get("llm_spend") or {}).get("cost_usd") or 0.0),
    }


# ====================================================================== needs

def needs(cop, out, upstream, asked, on_behalf=False):
    """Typed needs for other COPs' findings. Returns (needs, not_referred).
    on_behalf: this COP is answering a referral, so it resolves what its own answer
    leaves unknown even when its own action would only record."""
    f = out["finding"]
    vs = f.get("verification_support") or {}
    snap_action = ((f.get("triage") or {}).get("action_selection") or {}).get("selected") or "NONE"
    acts = snap_action not in QUIET_ACTIONS
    if vs.get("decision") == SUP.SUPPORTED and not acts and not on_behalf:
        return [], [{"item": "*", "reason": "SUPPORTED and the action only records: nothing to check"}]
    want = {}
    for c in vs.get("components") or []:
        if c["kind"] == "upstream" and c["item"]["direction"] == "unknown":
            want[c["upstream"]] = {"item": c["name"], "state": "unknown", "weight": c["weight"],
                                   "detail": c["item"].get("basis", "")}
    for a in vs.get("assumptions") or []:
        want.setdefault(a["owner"], {"item": a["item"], "state": "assumed", "value": a.get("value"),
                                     "detail": a["basis"]})
    ref = (f.get("triage") or {}).get("referral") or {}
    if ref.get("kind") == "cop_support" and ref.get("receiver") in BY_KEY:
        want.setdefault(ref["receiver"], {"item": f"finding:{ref['receiver']}", "state": "requested_by_triage",
                                          "detail": ref.get("reason", "")})
    out_needs, skipped = [], []
    for r in [k for k in topological_order() if k in want]:
        n = {"receiver": r, **want[r]}
        if r in asked:
            skipped.append({**n, "reason": "already asked in this investigation"})
        elif r not in CONSUMES[cop]:
            skipped.append({**n, "reason": f"{cop} does not consume {r}: its finding is not an input"})
        elif r not in SCOPE_RECEIVERS:
            skipped.append({**n, "reason": f"{r} is outside the referral scope enabled for milestone 1"})
        else:
            out_needs.append(n)
    return out_needs, skipped


def _question(initiator, receiver, need):
    return (f"{BY_KEY[receiver].behavioural_question} Asked by {BY_KEY[initiator].name} because "
            f"{need['item']} is {need['state']}: {need['detail'][:200]}")


# ====================================================================== response checks

def _refs_resolve(result, claims, case=None):
    """Evidence refs in the receiver's claims that do not resolve in its own run.
    A ref of a kind this check cannot resolve counts as unresolved."""
    rows = {r["metric"] for r in (result.get("evidence") or {}).get("rows", [])}
    tools = {c["tool_call_id"] for c in result.get("tool_calls") or []}
    fids = {u.get("finding_id") for u in result.get("upstream") or [] if u.get("finding_id")}
    obs = {r.get("observable_id") for r in (case.rows if case else []) if r.get("session_id") == result.get("session_id")}
    arts = set((case.artifacts if case else {}) or {})
    bad = []
    for c in claims:
        for ref in c.get("evidence_refs") or []:
            kind, _, ident = ref.partition(":")
            ok = (kind == "metric" and ident in rows) or (kind in ("tool", "tool_call") and ident in tools) or \
                 (kind == "finding" and ident in fids) or (kind == "observable" and ident in obs) or \
                 (kind == "artifact" and ident in arts) or kind == "memory"
            if not ok:
                bad.append(ref)
    return bad


def verify_response(req, out, scope, elapsed, cost, case=None):
    """RS-01..RS-05 on a receiver's answer. Returns (checks, admissible, delivered_as)."""
    res = out.get("result") or {}
    f = out["finding"]
    pa = res.get("proposed_action") or {}
    chk = []

    def add(cid, ok, detail):
        chk.append({"check_id": cid, "result": "PASS" if ok else "FAIL", "detail": detail})
        return ok

    ident = add("RS-01", f.get("cop") == req.receiver and f.get("case_id") == req.case_id
                and res.get("tenant_id") == req.tenant_id and res.get("session_id") == req.session_id,
                f"{f.get('cop')} / {f.get('case_id')} / {res.get('tenant_id')}")
    verified = add("RS-02", bool(f.get("verified")) and f.get("status") == SUBSTANTIVE,
                   f"receiver finding {f.get('label')} status {f.get('status')} verified={f.get('verified')}")
    bad = _refs_resolve(res, f.get("claims") or [], case)
    trace = add("RS-03", not bad, "every evidence ref resolves in the receiver's run" if not bad
                else f"unresolved refs: {bad[:5]}")
    tools_used = {c["tool"] for c in res.get("tool_calls") or []}
    out_of_scope = sorted(tools_used - set(scope["receiver_tools"]))
    executed = pa.get("execution_status") in ("EXECUTED_MOCK", "EXECUTED")
    in_scope = add("RS-04", not out_of_scope and not executed and res.get("mode") == "dry_run",
                   f"tools {sorted(tools_used)}; mode {res.get('mode')}; execution {pa.get('execution_status')}"
                   + (f"; outside allowlist {out_of_scope}" if out_of_scope else ""))
    limits = add("RS-05", elapsed <= scope["timeout_s"] and cost <= scope["budget_usd"] + 1e-9,
                 f"{elapsed:.3f}s of {scope['timeout_s']}s; ${cost:.6f} of ${scope['budget_usd']:.6f}")
    if ident and trace and in_scope and limits:
        return chk, verified, ("evidence" if verified else "context_only")
    return chk, False, "not_delivered"


def _deliverable(out, admissible, referral_id):
    """The receiver's published finding as the initiator receives it. verified is
    never raised here: an unverified answer stays unverified (context only)."""
    pub = dict(out["published"])
    pub["verified"] = bool(pub.get("verified")) and admissible
    vs = out["finding"].get("verification_support") or {}
    pub["verification_support"] = {"decision": vs.get("decision"), "score": vs.get("score"),
                                   "against": vs.get("against"), "unknown": vs.get("unknown")}
    pub["referral"] = {"referral_id": referral_id, "admissible": admissible}
    return pub


# ====================================================================== the investigation

class Tree:
    """Shared by every investigation in one tree: limits, spend, answers, the event log."""

    def __init__(self, rt, clock=time.monotonic, cost_limit=COST_LIMIT_USD, timeout_s=TIMEOUT_S,
                 max_depth=MAX_DEPTH, max_rounds=MAX_ROUNDS, max_runs=MAX_RUNS, max_retries=MAX_RETRIES,
                 run_hook=None):
        self.rt, self.clock = rt, clock
        self.cost_limit, self.timeout_s, self.max_depth = cost_limit, timeout_s, max_depth
        self.max_rounds, self.max_runs, self.max_retries = max_rounds, max_runs, max_retries
        self.spent, self.runs, self.answers, self.events = 0.0, 0, {}, []
        self.failed = {}                    # receiver -> {status, referral_id}: timed out or failed in this tree
        self.run_hook = run_hook            # tests: fn(cop, run_key) called before a run (fault injection)
        self.root_id = None
        self.states = {}                    # run_key -> the COP run's full final state (for the action phase)

    def remaining(self):
        return round(self.cost_limit - self.spent, 6)

    def log(self, kind, cop, **detail):
        e = {"seq": len(self.events) + 1, "at": _iso(), "kind": kind, "cop": cop, **detail}
        self.events.append(e)
        return e

    def run(self, cop, upstream, run_key, budget_cap=None):
        """One COP run through the LangGraph subgraph, under its own run id."""
        from .graph import run_cop
        if self.run_hook:
            self.run_hook(cop, run_key)
        rt = copy.copy(self.rt)
        rt.run_id = f"{self.rt.run_id}:{run_key}"
        rt.mode = "dry_run" if run_key.startswith("ref") else self.rt.mode
        if budget_cap is not None:
            prof = dict(rt.profiles)
            p = prof[cop]
            prof[cop] = replace(p, budget=replace(p.budget, max_cost_usd=max(0.0, min(p.budget.max_cost_usd,
                                                                                         budget_cap))))
            rt.profiles = prof
        self.runs += 1
        out = run_cop(rt, cop, upstream=upstream, thread_id=f"{rt.case.case_id}:{cop}:{run_key}")
        cost = float((out["result"].get("llm_spend") or {}).get("cost_usd") or 0.0)
        self.spent = round(self.spent + cost, 6)
        self.states[run_key] = out
        return out


class InvState(TypedDict, total=False):
    investigation_id: str
    cop: str
    depth: int
    chain: list
    upstream: dict
    round: int
    asked: list
    runs: list                 # snapshots, first is the initial assessment
    pending: list              # requests planned this round
    responses: list            # every response, all rounds
    not_referred: list
    delivered: dict            # receiver -> what was delivered this round
    resolution: dict
    final: dict
    last_out: dict
    upstream_referral: Optional[str]


TREES = {}


def _tree(state):
    return TREES[state["investigation_id"].split("/")[0]]


def _final_out(state):
    return state["last_out"]


def n_assess(state):
    tr = _tree(state)
    cop = state["cop"]
    key = f"{'ref' if state['depth'] else 'root'}:{cop}:{state['investigation_id']}:r0"
    tr.log("assessment_started", cop, depth=state["depth"], chain=state["chain"],
           upstream={k: {"label": v.get("label"), "verified": v.get("verified")}
                     for k, v in (state.get("upstream") or {}).items()})
    cap = tr.remaining() if state["depth"] else None
    out = tr.run(cop, state.get("upstream") or {}, key, budget_cap=cap)
    snap = snapshot(out, key, tr.rt)
    tr.log("assessment_completed", cop, run_key=key, label=snap["label"], status=snap["status"],
           verified=snap["verified"], vs=snap["verification_support"]["decision"],
           score=snap["verification_support"]["score"], against=snap["verification_support"]["against"],
           unknown=snap["verification_support"]["unknown"], quotient=snap["quotient"], tier=snap["tier"],
           action=snap["selected_action"])
    return {"runs": [snap], "last_out": _slim(out), "round": 0, "asked": [], "responses": [], "not_referred": []}


def _slim(out):
    return {"finding": out["finding"], "published": out["published"], "result": out["result"],
            "proposed_action": out.get("proposed_action"), "node_timings": out.get("node_timings") or {}}


def build_request(tr, investigation_id, cop, out, snap, n, chain, depth, rnd=1, parent=None):
    """A typed referral request from the initiator's run and one need."""
    f = out["finding"]
    res = out["result"]
    return ReferralRequest(
        case_id=tr.rt.case.case_id, tenant_id=tr.rt.case.tenant_id, session_id=tr.rt.case.session_id,
        initiator=cop, receiver=n["receiver"], question=_question(cop, n["receiver"], n),
        reason=f"{n['item']} {n['state']}",
        original_finding={"finding_id": f.get("finding_id"), "label": f.get("label"), "status": f.get("status"),
                          "verified": bool(f.get("verified")),
                          "verification_support": {k: snap["verification_support"][k]
                                                   for k in ("decision", "score", "against", "unknown")},
                          "quotient": snap["quotient"], "tier": snap["tier"],
                          "selected_action": snap["selected_action"]},
        missing_evidence=[{k: n.get(k) for k in ("item", "state", "detail", "value") if n.get(k) is not None}]
                         + [{"item": m.get("metric") or m.get("source"), "state": m.get("evidence", "missing"),
                             "detail": m.get("reason", "")} for m in (f.get("missing_evidence") or [])][:8],
        trace_ids={"investigation_id": investigation_id, "initiator_run_id": res.get("run_id"),
                   "initiator_thread_id": f"{tr.rt.case.case_id}:{cop}:{snap['run_key']}",
                   "initiator_decision_id": snap["decision_id"], "finding_id": f.get("finding_id"),
                   "tool_call_ids": snap["tool_call_ids"],
                   "llm_invocation_ids": [i.get("invocation_id") for i in res.get("llm_invocations") or []],
                   "parent_referral_id": parent},
        permitted_scope={"tenant_id": tr.rt.case.tenant_id, "session_id": tr.rt.case.session_id,
                         "agent_scope": tr.rt.case.agent_scope, "receivers": list(CONSUMES[cop]),
                         "receiver_tools": tr.rt.tools.allowlist(n["receiver"]),
                         "mode": "dry_run", "may_execute_actions": False,
                         "may_refer_onward": depth + 1 < tr.max_depth,
                         "depth": depth + 1, "max_depth": tr.max_depth,
                         "budget_usd": tr.remaining(), "timeout_s": tr.timeout_s,
                         "memory": "the receiver's own read and write policy (memory.POLICIES)"},
        chain=list(chain) + [n["receiver"]], depth=depth + 1,
        investigation_id=investigation_id, round=rnd, created_at=_iso())


def refer(tree, initiator, receiver, initiator_out, chain=None, depth=0, reason="requested directly"):
    """Open and run one referral outside a full investigation (tests, operators).
    The same request, guards and response checks as the graph uses."""
    if tree.root_id is None:
        tree.root_id = stable_id(tree.rt.case.case_id, initiator, "direct")[:13]
        TREES[tree.root_id] = tree
    snap = snapshot(initiator_out, "direct", tree.rt)
    req = build_request(tree, tree.root_id, initiator, initiator_out, snap,
                        {"receiver": receiver, "item": f"finding:{receiver}", "state": "requested",
                         "detail": reason}, chain or [initiator], depth).to_dict()
    tree.log("referral_requested", initiator, referral_id=req["referral_id"], receiver=receiver,
             reason=req["reason"], round=req["round"], depth=req["depth"])
    return req, dispatch_one(tree, req)


def n_plan(state):
    tr = _tree(state)
    cop = state["cop"]
    out = state["last_out"]
    nd, skipped = needs(cop, out, state.get("upstream") or {}, set(state["asked"]), on_behalf=state["depth"] > 0)
    snap = state["runs"][-1]
    pending = []
    if state["round"] >= tr.max_rounds:
        skipped += [{**n, "reason": f"round limit {tr.max_rounds} reached"} for n in nd]
        nd = []
    for n in nd:
        req = build_request(tr, state["investigation_id"], cop, out, snap, n, state["chain"], state["depth"],
                            state["round"] + 1, state.get("upstream_referral"))
        pending.append(req.to_dict())
        tr.log("referral_requested", cop, referral_id=req.referral_id, receiver=n["receiver"],
               reason=req.reason, round=req.round, depth=req.depth)
    for s in skipped:
        tr.log("referral_not_opened", cop, receiver=s.get("receiver"), reason=s["reason"])
    return {"pending": pending, "not_referred": state["not_referred"] + skipped,
            "asked": state["asked"] + [p["receiver"] for p in pending]}


def route_after_plan(state):
    return "dispatch" if state["pending"] else "finalise"


def _refuse(tr, req, status, detail):
    tr.log("referral_refused", req["initiator"], referral_id=req["referral_id"], receiver=req["receiver"],
           status=status, detail=detail)
    return ReferralResponse(req["referral_id"], req["receiver"], status, detail=detail, answered_at=_iso()).to_dict()


def dispatch_one(tr, req):
    rcv = req["receiver"]
    if rcv in req["chain"][:-1]:
        return _refuse(tr, req, REFUSED_CYCLE, f"cycle: {' -> '.join(req['chain'])}")
    if rcv not in req["permitted_scope"]["receivers"]:
        return _refuse(tr, req, REFUSED_SCOPE, f"{req['initiator']} may not consume {rcv}")
    if rcv in tr.answers:
        prev = tr.answers[rcv]
        tr.log("referral_answer_reused", req["initiator"], referral_id=req["referral_id"], receiver=rcv,
               from_referral=prev["referral_id"])
        return {**prev, "referral_id": req["referral_id"], "status": REUSED,
                "detail": f"verified answer already returned in this investigation ({prev['referral_id']})",
                "cost_usd": 0.0, "elapsed_s": 0.0, "attempts": 0}
    if rcv in tr.failed:
        # a receiver that timed out or failed in this tree is not run again for another initiator
        prev = tr.failed[rcv]
        tr.log("referral_failure_reused", req["initiator"], referral_id=req["referral_id"], receiver=rcv,
               status=prev["status"], from_referral=prev["referral_id"])
        return ReferralResponse(req["referral_id"], rcv, prev["status"], attempts=0,
                                detail=f"not run again: {prev['status']} earlier in this investigation "
                                       f"({prev['referral_id']})", answered_at=_iso()).to_dict()
    if req["depth"] > tr.max_depth:
        # checked after reuse: handing over an answer already verified in this tree is not a hop
        return _refuse(tr, req, REFUSED_DEPTH, f"depth {req['depth']} over {tr.max_depth}")
    if tr.remaining() <= 0:
        return _refuse(tr, req, REFUSED_BUDGET, f"cost limit ${tr.cost_limit} spent")
    if tr.runs >= tr.max_runs:
        return _refuse(tr, req, REFUSED_RUNS, f"run limit {tr.max_runs} reached")
    tr.log("referral_dispatched", req["initiator"], referral_id=req["referral_id"], receiver=rcv,
           depth=req["depth"], budget_usd=req["permitted_scope"]["budget_usd"])
    up = {k: tr.answers[k]["_delivered"] for k in CONSUMES[rcv] if k in tr.answers}
    attempts, err, sub = 0, "", None
    t0, spent0 = tr.clock(), tr.spent
    while attempts <= tr.max_retries:
        attempts += 1
        try:
            sub = investigate(tr, rcv, up, chain=req["chain"], depth=req["depth"],
                              parent=req["referral_id"], attempt=attempts)
        except Exception as e:                 # a receiver that crashes is a failed answer, retried once
            err = f"{type(e).__name__}: {e}"[:300]
            tr.log("receiver_error", rcv, referral_id=req["referral_id"], attempt=attempts, error=err)
            sub = None
            continue
        f = sub["last_out"]["finding"]
        if (f.get("cop_specific") or {}).get("llm_error") or f.get("status") == "FAILED":
            err = (f.get("cop_specific") or {}).get("llm_error") or f.get("rationale", "")[:200]
            tr.log("receiver_error", rcv, referral_id=req["referral_id"], attempt=attempts, error=err)
            if attempts <= tr.max_retries and not str(err).startswith("budget_exhausted") \
                    and tr.remaining() > 0:
                continue                      # a spent budget is a limit, not a transient error
        break
    elapsed, cost = tr.clock() - t0, round(tr.spent - spent0, 6)
    scope = req["permitted_scope"]
    if sub is None:
        r = ReferralResponse(req["referral_id"], rcv, FAILED, attempts=attempts, elapsed_s=round(elapsed, 3),
                             cost_usd=cost, detail=f"receiver did not complete: {err}", answered_at=_iso())
        tr.log("referral_failed", rcv, referral_id=req["referral_id"], detail=r.detail)
        tr.failed[rcv] = {"status": FAILED, "referral_id": req["referral_id"]}
        return r.to_dict()
    out = sub["last_out"]
    checks, admissible, delivered_as = verify_response(
        ReferralRequest(**{k: req[k] for k in ReferralRequest.__dataclass_fields__}), out, scope, elapsed, cost,
        tr.rt.case)
    f = out["finding"]
    if elapsed > scope["timeout_s"]:
        status = TIMED_OUT
    elif err and (f.get("cop_specific") or {}).get("llm_error"):
        status = FAILED
    elif f.get("status") != SUBSTANTIVE:
        status = NO_FINDING
    elif admissible:
        status = ANSWERED
    else:
        status = UNVERIFIED
    if status in (TIMED_OUT, FAILED):
        admissible, delivered_as = False, "not_delivered"
        tr.failed[rcv] = {"status": status, "referral_id": req["referral_id"]}
    res = out["result"]
    r = ReferralResponse(
        req["referral_id"], rcv, status, admissible=admissible, delivered_as=delivered_as,
        finding={k: f.get(k) for k in ("finding_id", "cop", "label", "status", "verified", "provisional",
                                       "confidence", "severity", "rationale")},
        verification_support=sub["runs"][-1]["verification_support"],
        evidence=[{"statement": c.get("statement"), "kind": c.get("kind"), "refs": c.get("evidence_refs")}
                  for c in f.get("claims") or []],
        checks=checks,
        receiver_trace={"investigation_id": sub["investigation_id"], "run_id": res.get("run_id"),
                        "thread_id": f"{tr.rt.case.case_id}:{rcv}:{sub['runs'][-1]['run_key']}",
                        "decision_id": sub["runs"][-1]["decision_id"],
                        "graph_nodes": list((out.get("node_timings") or {}).keys()),
                        "tool_call_ids": sub["runs"][-1]["tool_call_ids"],
                        "llm_calls": sum(s["llm_calls"] for s in sub["runs"]),
                        "runs": len(sub["runs"]), "resolution": sub["resolution"]["status"],
                        "onward": [{"receiver": x["receiver"], "status": x["status"]} for x in sub["responses"]]},
        cost_usd=cost, elapsed_s=round(elapsed, 3), attempts=attempts,
        detail=f"{f.get('label')} ({f.get('status')}); {delivered_as}", answered_at=_iso()).to_dict()
    tr.log("referral_answered", rcv, referral_id=req["referral_id"], status=status, label=f.get("label"),
           verified=bool(f.get("verified")), admissible=admissible, delivered_as=delivered_as,
           elapsed_s=round(elapsed, 3), cost_usd=cost,
           checks={c["check_id"]: c["result"] for c in checks})
    if delivered_as != "not_delivered":
        r["_delivered"] = _deliverable(out, admissible, req["referral_id"])
        if admissible:
            tr.answers[rcv] = r
    return r


def n_dispatch(state):
    tr = _tree(state)
    responses, delivered = [], {}
    for req in state["pending"]:
        r = dispatch_one(tr, req)
        responses.append(r)
        if r.get("_delivered"):
            delivered[r["receiver"]] = r["_delivered"]
    return {"responses": state["responses"] + [{k: v for k, v in r.items() if k != "_delivered"}
                                                 for r in responses],
            "delivered": delivered, "pending": []}


def route_after_dispatch(state):
    tr = _tree(state)
    ok = any(d.get("verified") for d in state["delivered"].values())
    if not ok:
        return "finalise"                 # nothing admissible came back: nothing to reassess on
    if tr.remaining() <= 0 or tr.runs >= tr.max_runs:
        tr.log("reassessment_skipped", state["cop"], reason="cost or run limit reached")
        return "finalise"
    return "reassess"


def n_reassess(state):
    tr = _tree(state)
    cop = state["cop"]
    up = {**(state.get("upstream") or {}), **state["delivered"]}
    rnd = state["round"] + 1
    key = f"{'ref' if state['depth'] else 'root'}:{cop}:{state['investigation_id']}:r{rnd}"
    tr.log("reassessment_started", cop, round=rnd, run_key=key,
           new_inputs={k: {"label": v.get("label"), "verified": v.get("verified")}
                       for k, v in state["delivered"].items()})
    out = tr.run(cop, up, key, budget_cap=tr.remaining() if state["depth"] else None)
    snap = snapshot(out, key, tr.rt)
    first = state["runs"][0]["verification_support"]
    tr.log("reassessment_completed", cop, round=rnd, run_key=key, label=snap["label"], status=snap["status"],
           vs=snap["verification_support"]["decision"],
           support={"initial": first["score"], "revised": snap["verification_support"]["score"]},
           against={"initial": first["against"], "revised": snap["verification_support"]["against"]},
           unknown={"initial": first["unknown"], "revised": snap["verification_support"]["unknown"]},
           quotient={"initial": state["runs"][0]["quotient"], "revised": snap["quotient"]},
           tier={"initial": state["runs"][0]["tier"], "revised": snap["tier"]},
           action={"initial": state["runs"][0]["selected_action"], "revised": snap["selected_action"]})
    return {"runs": state["runs"] + [snap], "last_out": _slim(out), "upstream": up, "round": rnd,
            "delivered": {}}


def n_evaluate(state):
    return {}


def route_after_evaluate(state):
    tr = _tree(state)
    if state["round"] >= tr.max_rounds:
        return "finalise"
    nd, _ = needs(state["cop"], state["last_out"], state.get("upstream") or {}, set(state["asked"]),
                  on_behalf=state["depth"] > 0)
    return "plan_referrals" if nd else "finalise"


def resolution(runs, responses, not_referred):
    a, b = runs[0], runs[-1]
    va, vb = a["verification_support"], b["verification_support"]
    limits = [r for r in responses if r["status"] in LIMIT_STATUSES]
    admitted = [r for r in responses if r.get("admissible")]
    if not responses and len(runs) == 1:
        st, why = NOT_NEEDED, "no referral was needed" if not not_referred else \
            "needs were found but none could be referred: " + "; ".join(n["reason"] for n in not_referred)[:300]
    elif len(runs) == 1:
        st = UNRESOLVED
        why = "no admissible answer: " + "; ".join(f"{r['receiver']} {r['status']}" for r in responses)
    elif (a["label"], va["decision"]) != (b["label"], vb["decision"]):
        st = REVISED
        why = (f"{a['label']} {va['decision']} -> {b['label']} {vb['decision']}")
    elif b["outcome"] == "INCONCLUSIVE" or vb["decision"] == SUP.INSUFFICIENT:
        st = UNRESOLVED
        why = (f"answers were admitted ({', '.join(r['receiver'] for r in admitted)}) but the reading is still "
               f"{b['label']} {vb['decision']}; unknown {va['unknown']} -> {vb['unknown']}")
    elif (vb["unknown"] or 0) < (va["unknown"] or 0) - 1e-9 or len(vb["assumptions"]) < len(va["assumptions"]):
        st = CONFIRMED
        why = (f"{b['label']} {vb['decision']} stands; unknown {va['unknown']} -> {vb['unknown']}"
               + ("; telemetry reliability now observed, not assumed" if len(vb["assumptions"]) < len(va["assumptions"])
                  else ""))
    else:
        st, why = UNCHANGED, "the answers did not move support, against or unknown"
    still = b["outcome"] == "INCONCLUSIVE" or vb["decision"] in (SUP.PROVISIONAL, SUP.INSUFFICIENT)
    direction = None
    if len(runs) > 1:
        ra, rb = DECISION_RANK.get(va["decision"], 1), DECISION_RANK.get(vb["decision"], 1)
        direction = "strengthened" if (rb, vb["score"] or 0) > (ra, va["score"] or 0) else \
            ("weakened" if (rb, vb["score"] or 0) < (ra, va["score"] or 0) else "level")
    ca = {c["name"]: c for c in va["components"]}
    changes = [{"component": c["name"], "from": ca.get(c["name"], {}).get("direction"), "to": c["direction"],
                "contribution": {"from": ca.get(c["name"], {}).get("contribution"), "to": c["contribution"]},
                "basis": c["basis"]}
               for c in vb["components"] if len(runs) > 1 and (
                   ca.get(c["name"], {}).get("direction") != c["direction"]
                   or abs((ca.get(c["name"], {}).get("contribution") or 0) - (c["contribution"] or 0)) > 1e-9)]
    return {"status": st, "reason": why, "support_direction": direction, "still_uncertain": still,
            "component_changes": changes,
            "limit_hit": [f"{r['receiver']} {r['status']}: {r['detail']}" for r in limits],
            "admitted": [r["receiver"] for r in admitted]}


def n_finalise(state):
    tr = _tree(state)
    cop = state["cop"]
    res = resolution(state["runs"], state["responses"], state["not_referred"])
    last = state["runs"][-1]
    tier, why, human_task = last["tier"], last["routing_reason"], None
    if res["limit_hit"]:
        # a referral limit is a human_when condition (triage.SPECIALISTS): never retried here
        tier = TRI.HUMAN
        why = f"referral limit hit ({'; '.join(res['limit_hit'])[:200]}); " + (why or "")
        human_task = {"kind": "referral_limit", "question": f"{cop}: {res['reason']}"[:300],
                      "limits": res["limit_hit"]}
    elif state["responses"] and res["still_uncertain"]:
        # referrals were made and the reading is still provisional, insufficient or inconclusive:
        # nothing further is retried; a reviewer sees the case
        tier = TRI.HUMAN if tier == TRI.HUMAN else TRI.ASSISTED
        why = f"additional evidence did not resolve the uncertainty ({res['reason'][:160]}); " \
              f"a reviewer sees the case before {last['selected_action']} is relied on. " + (why or "")
        human_task = {"kind": "unresolved_uncertainty", "question": f"{cop}: {res['reason']}"[:300]}
    final = {"cop": cop, "label": last["label"], "status": last["status"], "verified": last["verified"],
             "verification_support": last["verification_support"], "quotient": last["quotient"],
             "tier": tier, "routing_reason": why, "selected_action": last["selected_action"],
             "final_action": last["final_action"], "execution_status": last["execution_status"],
             "human_task": human_task, "run_key": last["run_key"], "decision_id": last["decision_id"]}
    tr.log("investigation_finalised", cop, resolution=res["status"], reason=res["reason"],
           tier=tier, action=last["selected_action"], depth=state["depth"])
    return {"resolution": res, "final": final}


def build_investigation_graph():
    g = StateGraph(InvState)
    for name, fn in (("assess", n_assess), ("plan_referrals", n_plan), ("dispatch", n_dispatch),
                     ("reassess", n_reassess), ("evaluate", n_evaluate), ("finalise", n_finalise)):
        g.add_node(name, fn)
    g.add_edge(START, "assess")
    g.add_edge("assess", "plan_referrals")
    g.add_conditional_edges("plan_referrals", route_after_plan, ["dispatch", "finalise"])
    g.add_conditional_edges("dispatch", route_after_dispatch, ["reassess", "finalise"])
    g.add_edge("reassess", "evaluate")
    g.add_conditional_edges("evaluate", route_after_evaluate, ["plan_referrals", "finalise"])
    g.add_edge("finalise", END)
    return g.compile()


_GRAPH = None


def investigation_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_investigation_graph()
    return _GRAPH


def investigate(tree, cop, upstream=None, chain=None, depth=0, parent=None, attempt=1):
    """Run one COP's investigation (with its referrals) and return the final state."""
    inv = stable_id(tree.rt.case.case_id, cop, depth, parent, attempt, len(tree.events), "investigation")[:13]
    iid = f"{tree.root_id}/{inv}" if tree.root_id else inv
    if tree.root_id is None:
        tree.root_id = iid
        TREES[iid] = tree
    out = investigation_graph().invoke(
        {"investigation_id": iid, "cop": cop, "depth": depth, "chain": list(chain or [cop]),
         "upstream": dict(upstream or {}), "upstream_referral": parent},
        {"recursion_limit": 60})
    return out


def run_investigation(rt, cop, upstream=None, **limits):
    """Entry point: the root investigation of one COP, with every limit explicit.
    Returns {investigation, tree} where tree has the event log and spend."""
    tree = Tree(rt, **limits)
    try:
        out = investigate(tree, cop, upstream)
    finally:
        TREES.pop(tree.root_id, None)
    return {"investigation": _public(out), "events": tree.events, "spent_usd": tree.spent, "runs": tree.runs,
            "limits": {"max_depth": tree.max_depth, "max_rounds": tree.max_rounds, "max_runs": tree.max_runs,
                       "max_retries": tree.max_retries, "cost_limit_usd": tree.cost_limit,
                       "timeout_s": tree.timeout_s},
            "last_out": out["last_out"], "upstream": out.get("upstream") or {},
            "states": tree.states}


def _public(out):
    return {k: out.get(k) for k in ("investigation_id", "cop", "depth", "chain", "runs", "responses",
                                     "not_referred", "resolution", "final")}
