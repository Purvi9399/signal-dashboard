"""
The LangGraph runtime: one COP subgraph for all eighteen, and a case graph
that runs them in dependency order.

COP subgraph (every node name below is a graph node; timings are recorded):

    receive_case -> load_inputs -> validate_evidence -> retrieve_memory
      -> computational_baseline
      -> deterministic COPs:  (the baseline is the decision)
         LLM COPs:            llm_plan_investigation
                              -> validate_tool_request -> execute_authorised_tool
                              -> validate_tool_result -> llm_assess_evidence
                              -> more evidence? back to validate_tool_request
                              -> formulate_finding
      -> independent_verify -> authority_check
      -> [await_approval: interrupt, supervised mode only]
      -> execute_action -> record_decision -> validate_memory_update
      -> persist_and_publish

State is plain JSON so it checkpoints. Objects that cannot checkpoint (the
case data, gateways, stores) live in RUNTIMES under the case id and are
rebuilt by the caller after a restart; nothing in them is needed to resume
except the store, which is a file.

Modes
    dry_run      default. Actions are authorised or not, and recorded; nothing
                 is executed. execution_status says NOT_EXECUTED_DRY_RUN.
    supervised   actions in APPROVAL_ACTIONS stop at await_approval for a
                 person's decision, then run against MockEnforcementGateway.
                 There is no live enforcement gateway in this repository.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from pydantic import ValidationError

from ..specs import BY_KEY, CONSUMES, topological_order
from ..authority import check as authority_check_fn, TIER, HUMAN_APPROVED_ONLY
from ..models import INCONCLUSIVE as INCONCLUSIVE_LABEL
from .contracts import (Finding, ProposedAction, ORIGIN_LLM, ORIGIN_RULES, SUBSTANTIVE,
                        INCONCLUSIVE, MISSING_DEPENDENCY, REJECTED, FAILED, BLOCKS_FINDING,
                        BLOCKS_ACTION, FAIL, NOT_EXECUTED_DRY_RUN, NOT_EXECUTED_NO_ACTION,
                        NOT_EXECUTED_BLOCKED, AWAITING_APPROVAL, APPROVAL_REJECTED,
                        EXECUTED_MOCK, EXECUTION_FAILED, UNVALIDATED,
                        CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE, CLAIM_TOOL_OBSERVATION,
                        CLAIM_UPSTREAM, CONTRACT_VERSION, stable_id)
from .evidence import assess, usable
from .memory import MemoryAccessDenied, POLICIES, VERIFIED
from .rules import RULES, DETERMINISTIC_TOOLS, evidence_conf
from .tools import TOOLS, scan
from .verifiers import (verify_finding, verify_action, verify_memory_update, blocking,
                        REGISTRY_VERSION)
from .profiles import build_profiles
from .llm import LLMBudget

MODES = ("dry_run", "supervised")
SECURITY_CODES = ("unknown_tool", "not_in_allowlist", "out_of_scope")
QUIET_ACTIONS = ("NONE", "WATCH")


# ====================================================================== runtime objects

class MockEnforcementGateway:
    """Stands in for enforcement. Records what would have been applied.

    fail: actions whose execution raises; unconfirmed: actions whose outcome
    the gateway cannot observe afterwards. Both exist to exercise the failure
    paths; there is no real enforcement anywhere in this runtime.
    """
    simulated = True

    def __init__(self, fail=(), unconfirmed=()):
        self.fail, self.unconfirmed = set(fail), set(unconfirmed)
        self.applied = []

    def execute(self, cop, case_id, action, target):
        if action in self.fail:
            raise RuntimeError(f"mock gateway refused {action}")
        self.applied.append({"cop": cop, "case_id": case_id, "action": action, "target": target})
        ok = action not in self.unconfirmed
        return {"applied": True, "outcome_confirmed": ok, "simulated": True,
                "detail": f"mock: {action} on {target or 'session'}"
                          + ("" if ok else "; outcome could not be observed")}


@dataclass
class Runtime:
    case: Any                      # CaseData
    tools: Any                     # ToolGateway
    memory: Any                    # MemoryGateway
    llm: Any                       # LLMGateway
    store: Any                     # SQLiteStore (or anything with save_result)
    profiles: dict = field(default_factory=build_profiles)
    mode: str = "dry_run"
    enforcement: Any = field(default_factory=MockEnforcementGateway)
    live_session: bool = False     # whether intervention could still apply to the session
    run_id: str = "eval"

    def __post_init__(self):
        assert self.mode in MODES, self.mode


RUNTIMES = {}


def register(rt):
    RUNTIMES[rt.case.case_id] = rt
    return rt


def _rt(state):
    return RUNTIMES[state["case_id"]]


# ====================================================================== state

class COPState(TypedDict, total=False):
    case_id: str
    cop: str
    classification: str
    mode: str
    upstream: dict              # {cop: published finding}, supplied by the case graph
    upstream_view: list         # [{cop, present, verified, label, finding_id}]
    picture: dict
    memory_view: dict
    tool_calls: list            # every call record, with result
    llm_invocations: list
    security_events: list
    missing_evidence: list
    plan: dict
    assessments: list
    pending: list               # tool requests waiting for validation
    authorised: list            # requests that passed validation
    new_calls: list             # ids executed this round
    hypothesis_trail: list
    spent: dict
    tool_budget: dict
    llm_error: Optional[str]
    stop_reason: Optional[str]
    candidate: Optional[dict]
    baseline: Optional[dict]
    finding: dict
    verifier_results: list
    proposed_action: dict
    approval: Optional[dict]
    execution: Optional[dict]
    memory_write: Optional[dict]
    result: dict
    published: dict
    node_timings: dict


def timed(name):
    def wrap(fn):
        def node(state):
            t0 = time.perf_counter()
            out = fn(state) or {}
            ms = round((time.perf_counter() - t0) * 1000, 3)
            out["node_timings"] = {**state.get("node_timings", {}), **out.get("node_timings", {}),
                                   name: ms}
            return out
        node.__name__ = name
        return node
    return wrap


def _events(rt, cop, picture=None):
    spec = BY_KEY[cop]
    ev = rt.case.case_events(spec.metric_keys)
    return usable(ev, picture) if picture else ev


def _tool_ctx(rt, state):
    return {"case_id": rt.case.case_id, "tenant_id": rt.case.tenant_id,
            "agent_scope": rt.case.agent_scope, "upstream": state.get("upstream") or {}}


def _fallback_action(spec):
    return "WATCH" if "WATCH" in spec.permitted_actions else "NONE"


# ====================================================================== shared head

@timed("receive_case")
def receive_case(state):
    rt = _rt(state)
    cop = state["cop"]
    p = rt.profiles[cop]
    b = p.budget
    return {"classification": p.classification, "mode": rt.mode,
            "tool_calls": [], "llm_invocations": [], "security_events": [], "missing_evidence": [],
            "assessments": [], "hypothesis_trail": [], "pending": [], "authorised": [],
            "new_calls": [], "llm_error": None, "stop_reason": None, "candidate": None,
            "baseline": None, "approval": None, "execution": None, "memory_write": None,
            "spent": {"llm_calls": 0, "tokens": 0, "cost_usd": 0.0, "started": time.time()},
            "tool_budget": {"tool_calls": 0, "max_tool_calls": b.max_tool_calls}}


@timed("load_inputs")
def load_inputs(state):
    """Record which upstream findings arrived and in what standing.

    Unverified upstream findings are delivered but rules and tools consume
    only verified substantive ones (rules.verified, t_get_verified_findings).
    """
    up = state.get("upstream") or {}
    view = []
    for k in CONSUMES[state["cop"]]:
        f = up.get(k)
        view.append({"cop": k, "present": f is not None,
                     "verified": bool(f and f.get("verified")),
                     "status": f.get("status") if f else None,
                     "label": f.get("label") if f else None,
                     "finding_id": f.get("finding_id") if f else None})
    return {"upstream_view": view}


@timed("validate_evidence")
def validate_evidence(state):
    rt = _rt(state)
    spec = BY_KEY[state["cop"]]
    pic = assess(_events(rt, state["cop"]), spec.metric_keys, rt.case.session_id)
    miss = [{"metric": r["metric"], "evidence": r["evidence"], "reason": r["reason"]}
            for r in pic["rows"] if r["evidence"] in ("UNKNOWN", "MISSING", "INVALID")]
    return {"picture": pic, "missing_evidence": miss}


def _mem_view(r):
    return {"record_id": r["record_id"], "record_type": r["record_type"],
            "status": r["validation_status"], "payload": r["payload"],
            "from_case": (r["provenance"] or {}).get("case_id")}


@timed("retrieve_memory")
def retrieve_memory(state):
    rt = _rt(state)
    if rt.memory is None:
        return {"memory_view": {"available": False}}
    try:
        got = rt.memory.read(state["cop"], rt.case.case_id, rt.case.tenant_id,
                             agent_scope=rt.case.agent_scope)
    except MemoryAccessDenied as e:
        return {"memory_view": {"available": False, "denied": str(e)}}
    return {"memory_view": {"available": True,
                            "established": [_mem_view(r) for r in got["established"]],
                            "verified_pending": [_mem_view(r) for r in got["verified_pending"]],
                            "unverified": [_mem_view(r) for r in got["unverified"]],
                            "excluded": got["excluded"]}}


def _rule_ctx(rt, state, calls):
    spec = BY_KEY[state["cop"]]
    tools = {}
    for c in calls:
        tools.setdefault(c["tool"], {"tool_call_id": c["tool_call_id"], "status": c["status"],
                                     "result": c.get("result")})
    return {"spec": spec, "events": _events(rt, state["cop"], state["picture"]),
            "picture": state["picture"], "upstream": state.get("upstream") or {},
            "tools": tools, "memory": state.get("memory_view") or {}, "case": rt.case}


def _as_finding(rt, cop, f, origin, extra=None):
    """A rules or LLM output dict -> Finding dict plus proposed action."""
    spec = BY_KEY[cop]
    status = f.get("status", SUBSTANTIVE)
    if f["label"] == INCONCLUSIVE_LABEL and status == SUBSTANTIVE:
        status = INCONCLUSIVE
    fd = Finding(cop=cop, case_id=rt.case.case_id, label=f["label"], status=status,
                 confidence=float(f.get("confidence") or 0.0), origin=origin,
                 claims=list(f.get("claims", [])), rationale=f.get("rationale", ""),
                 severity=f.get("severity", "NONE"),
                 missing_evidence=list(f.get("missing_evidence", [])),
                 missing_dependencies=list(f.get("missing_dependencies", [])),
                 upstream_used=list(f.get("upstream_used", [])),
                 cop_specific=dict(f.get("cop_specific") or {}),
                 hypothesis_trail=list(f.get("hypothesis_trail", []))).to_dict()
    fd["proposed_action"] = {"action": f.get("action") or _fallback_action(spec),
                             "reason": f.get("action_reason", "")}
    fd.update(extra or {})
    return fd


@timed("computational_baseline")
def computational_baseline(state):
    """Deterministic reading. For the eight deterministic COPs it is the
    decision; for the ten LLM COPs it is recorded beside the model's finding
    as a separately labelled baseline and never substituted for it."""
    rt = _rt(state)
    cop = state["cop"]
    names = DETERMINISTIC_TOOLS.get(cop, ())
    budget = {"tool_calls": 0, "max_tool_calls": len(names)}   # separate from the LLM's budget
    calls = []
    for name in names:
        calls.append(rt.tools.execute(cop, name, {}, "rules", budget, _tool_ctx(rt, state)))
    missing = [c["result"]["missing_dependency"] for c in calls
               if c.get("result") and "missing_dependency" in c["result"]]
    try:
        out = RULES[cop](_rule_ctx(rt, state, calls))
    except Exception as e:                                     # a rule bug is a FAILED finding
        out = {"label": INCONCLUSIVE_LABEL, "confidence": 0.0, "action": "NONE",
               "rationale": f"rule error {type(e).__name__}: {e}"[:300], "claims": [],
               "status": FAILED}
    if missing and out.get("status", SUBSTANTIVE) == SUBSTANTIVE and out["label"] == INCONCLUSIVE_LABEL:
        out["status"] = MISSING_DEPENDENCY
    out.setdefault("missing_dependencies", [])
    for m in missing:
        if m not in out["missing_dependencies"]:
            out["missing_dependencies"].append(m)
    f = _as_finding(rt, cop, out, ORIGIN_RULES)
    if state["classification"] == "deterministic":
        return {"tool_calls": state["tool_calls"] + calls, "finding": f, "baseline": None}
    f["baseline_note"] = "computational baseline; not the decision and not a verifier"
    return {"tool_calls": state["tool_calls"] + calls, "baseline": f}


# ====================================================================== LLM loop

def _tools_available(rt, cop):
    out = []
    for name in rt.profiles[cop].tools:
        schema = TOOLS[name][1]
        out.append({"tool": name, "args": {k: {kk: (list(vv) if isinstance(vv, tuple) else vv)
                                                for kk, vv in v.items()} for k, v in schema.items()}})
    return out


def _result_view(c):
    if c.get("quarantined"):
        return {"quarantined": True, "flags": c["injection_flags"],
                "note": "result withheld: it contains instruction-shaped text"}
    return c.get("result")


def _payload(rt, state, purpose):
    cop = state["cop"]
    spec = BY_KEY[cop]
    p = rt.profiles[cop]
    b = p.budget
    sp = state["spent"]
    up = state.get("upstream") or {}
    mem = state.get("memory_view") or {}
    return {
        "purpose": purpose,
        "cop": cop,
        "behavioural_question": spec.behavioural_question,
        "case": {"case_id": rt.case.case_id, "session_id": rt.case.session_id,
                 "synthetic": rt.case.synthetic},
        "evidence": [{k: r.get(k) for k in ("metric", "evidence", "reason", "state", "value",
                                             "threshold")} | {"refs": (r.get("evidence_refs") or [])[:5]}
                     for r in state["picture"]["rows"]],
        "upstream": [{"cop": k, "label": f["label"], "status": f["status"], "verified": f["verified"],
                      "finding_id": f.get("finding_id"), "lineage": f.get("lineage", [])}
                     for k, f in sorted(up.items())],
        "memory": {"established": mem.get("established", []),
                   "verified_pending": mem.get("verified_pending", []),
                   "unverified_count": len(mem.get("unverified", []))},
        "tools_available": _tools_available(rt, cop),
        "tool_results": [{"tool_call_id": c["tool_call_id"], "tool": c["tool"], "args": c["args"],
                          "status": c["status"], "rejection_reason": c["rejection_reason"],
                          "result": _result_view(c)}
                         for c in state["tool_calls"] if c["requested_by"] == "llm"],
        "hypothesis_trail": state["hypothesis_trail"],
        "budget_remaining": {"llm_calls": b.max_llm_calls - sp["llm_calls"],
                             "tool_calls": b.max_tool_calls - state["tool_budget"]["tool_calls"]},
        "output_detail_schema": p.detail_schema.model_json_schema() if p.detail_schema else {},
    }


def _invoke(rt, state, purpose):
    cop = state["cop"]
    p = rt.profiles[cop]
    refs = [f"metric:{r['metric']}" for r in state["picture"]["rows"]] + \
           [f"tool:{c['tool_call_id']}" for c in state["tool_calls"]]
    spent = dict(state["spent"])
    data, recs, err = rt.llm.invoke(cop, rt.case.case_id, purpose, p.prompt_version,
                                    p.system_prompt(), _payload(rt, state, purpose), p.budget,
                                    spent, evidence_refs=refs, tools_exposed=list(p.tools))
    return data, recs, err, spent


@timed("llm_plan_investigation")
def llm_plan_investigation(state):
    rt = _rt(state)
    data, recs, err, spent = _invoke(rt, state, "plan")
    out = {"llm_invocations": state["llm_invocations"] + recs, "spent": spent}
    if err:
        return {**out, "llm_error": err, "stop_reason": err}
    trail = state["hypothesis_trail"] + [{"step": "plan", "hypothesis": data["hypothesis"],
                                          "known": data["known"], "unknown": data["unknown"],
                                          "uncertain": data["uncertain"]}]
    return {**out, "plan": data, "pending": data["tool_requests"], "hypothesis_trail": trail}


@timed("validate_tool_request")
def validate_tool_request(state):
    rt = _rt(state)
    cop = state["cop"]
    calls, sec, ok = list(state["tool_calls"]), list(state["security_events"]), []
    budget = dict(state["tool_budget"])
    shadow = dict(budget)               # counts approvals so a batch cannot exceed the budget
    for req in state.get("pending") or []:
        reason = rt.tools.precheck(cop, req["tool"], req.get("args") or {}, shadow)
        if reason:
            rec = rt.tools.reject(cop, req["tool"], req.get("args") or {}, "llm", reason)
            calls.append(rec)
            if reason[0] in SECURITY_CODES:
                sec.append({"kind": "prohibited_tool_request", "cop": cop, "tool": req["tool"],
                            "code": reason[0], "detail": reason[1],
                            "tool_call_id": rec["tool_call_id"]})
        else:
            shadow["tool_calls"] += 1
            ok.append(req)
    return {"tool_calls": calls, "security_events": sec, "authorised": ok, "pending": []}


@timed("execute_authorised_tool")
def execute_authorised_tool(state):
    rt = _rt(state)
    budget = dict(state["tool_budget"])
    calls, new = list(state["tool_calls"]), []
    for req in state.get("authorised") or []:
        c = rt.tools.execute(state["cop"], req["tool"], req.get("args") or {}, "llm", budget,
                             _tool_ctx(rt, state))
        c["purpose"] = req.get("purpose", "")
        calls.append(c)
        new.append(c["tool_call_id"])
    return {"tool_calls": calls, "tool_budget": budget, "authorised": [], "new_calls": new}


@timed("validate_tool_result")
def validate_tool_result(state):
    """Tool output is data. Instruction-shaped text is quarantined: the model
    sees that a result was withheld and why, never the text itself."""
    calls, sec, miss = [], list(state["security_events"]), list(state["missing_evidence"])
    new = set(state.get("new_calls") or [])
    for c in state["tool_calls"]:
        if c["tool_call_id"] in new:
            c = dict(c)
            if c.get("injection_flags"):
                c["quarantined"] = True
                sec.append({"kind": "prompt_injection_in_tool_result", "cop": state["cop"],
                            "tool": c["tool"], "tool_call_id": c["tool_call_id"],
                            "flags": c["injection_flags"]})
            if c["status"] == "MISSING_DEPENDENCY":
                miss.append({"source": c["result"]["missing_dependency"].get("dependency"),
                             "reason": "tool reported a missing dependency",
                             "tool_call_id": c["tool_call_id"]})
        calls.append(c)
    return {"tool_calls": calls, "security_events": sec, "missing_evidence": miss, "new_calls": []}


@timed("llm_assess_evidence")
def llm_assess_evidence(state):
    rt = _rt(state)
    data, recs, err, spent = _invoke(rt, state, "assess")
    out = {"llm_invocations": state["llm_invocations"] + recs, "spent": spent}
    if err:
        return {**out, "llm_error": err, "stop_reason": err}
    prev = state["hypothesis_trail"][-1]["hypothesis"] if state["hypothesis_trail"] else None
    trail = state["hypothesis_trail"] + [{
        "step": f"assess-{len(state['assessments']) + 1}", "hypothesis": data["hypothesis"],
        "changed": data["hypothesis_changed"] or (prev is not None and prev != data["hypothesis"]),
        "change_reason": data["change_reason"], "decision": data["decision"],
        "after_tools": [c["tool"] for c in state["tool_calls"] if c["requested_by"] == "llm"]}]
    upd = {**out, "assessments": state["assessments"] + [data], "hypothesis_trail": trail}
    if data["decision"] == "conclude":
        upd["candidate"] = data["finding"]
        if data["finding"] is None:
            upd.update(llm_error="llm_output_invalid", stop_reason="conclude_without_finding")
    elif data["decision"] == "inconclusive":
        upd["stop_reason"] = "model_inconclusive"
        upd["missing_evidence"] = state["missing_evidence"] + [
            {"source": m, "reason": "named by the investigation"} for m in data["missing_evidence"]]
    else:
        upd["pending"] = data["tool_requests"]
    return upd


def route_after_plan(state):
    if state.get("llm_error"):
        return "formulate_finding"
    return "validate_tool_request" if state.get("pending") else "llm_assess_evidence"


def route_after_assess(state):
    rt = _rt(state)
    b = rt.profiles[state["cop"]].budget
    if state.get("llm_error") or state.get("candidate") is not None or state.get("stop_reason"):
        return "formulate_finding"
    if not state.get("pending"):
        return "formulate_finding"           # asked for more but named nothing to fetch
    if state["spent"]["llm_calls"] >= b.max_llm_calls:
        return "formulate_finding"
    if state["tool_budget"]["tool_calls"] >= state["tool_budget"]["max_tool_calls"]:
        return "formulate_finding"
    return "validate_tool_request"


@timed("formulate_finding")
def formulate_finding(state):
    rt = _rt(state)
    cop = state["cop"]
    spec = BY_KEY[cop]
    p = rt.profiles[cop]
    cand = state.get("candidate")
    stop = state.get("stop_reason")
    b = p.budget
    if cand is None and not stop:
        if state["spent"]["llm_calls"] >= b.max_llm_calls:
            stop = "budget_exhausted:max_llm_calls"
        elif state["tool_budget"]["tool_calls"] >= state["tool_budget"]["max_tool_calls"]:
            stop = "budget_exhausted:max_tool_calls"
        else:
            stop = "no_further_evidence_requested"
    detail_err = None
    if cand is not None and p.detail_schema is not None:
        try:
            cand = {**cand, "detail": p.detail_schema.model_validate(cand.get("detail") or {}).model_dump()}
        except ValidationError as e:
            detail_err = str(e).splitlines()[:4]
    base = state.get("baseline") or {}
    extra = {"hypothesis_trail": state["hypothesis_trail"],
             "baseline": {"label": base.get("label"), "confidence": base.get("confidence"),
                          "status": base.get("status"), "origin": ORIGIN_RULES,
                          "note": "computational baseline, recorded separately"} if base else None}
    if cand is None or detail_err:
        why = stop or "detail_invalid"
        f = {"label": INCONCLUSIVE_LABEL, "confidence": 0.0, "status": INCONCLUSIVE,
             "action": _fallback_action(spec),
             "action_reason": "investigation did not reach a finding",
             "rationale": f"LLM investigation stopped: {why}"
                          + (f"; detail failed schema: {detail_err}" if detail_err else ""),
             "missing_evidence": state["missing_evidence"],
             "cop_specific": {"stop_reason": why, "llm_error": state.get("llm_error"),
                              "detail_errors": detail_err}}
        return {"finding": _as_finding(rt, cop, f, ORIGIN_LLM, extra), "stop_reason": why}
    claims = [{**c, "origin": ORIGIN_LLM} for c in cand["claims"]]
    f = {"label": cand["label"], "confidence": cand["confidence"], "claims": claims,
         "rationale": cand["rationale"], "action": cand["proposed_action"],
         "action_reason": cand.get("action_reason", ""),
         "missing_evidence": state["missing_evidence"] + [
             {"source": m, "reason": "named by the investigation"} for m in cand["missing_evidence"]],
         "severity": "NONE",
         "cop_specific": {"detail": cand["detail"],
                          "alternative_explanations": cand["alternative_explanations"]}}
    return {"finding": _as_finding(rt, cop, f, ORIGIN_LLM, extra), "stop_reason": "concluded"}


# ====================================================================== shared tail

def _lineage(f, upstream):
    lin = set()
    for c in f.get("claims", []):
        if c.get("metric_id"):
            lin.add(c["metric_id"])
        if c.get("kind") == CLAIM_TOOL_OBSERVATION and c.get("tool_call_id"):
            lin.add(f"tool_call:{c['tool_call_id']}")
        if c.get("kind") == CLAIM_UPSTREAM:
            u = (upstream or {}).get(c.get("upstream_cop")) or {}
            lin |= set(u.get("lineage") or [])
    return sorted(lin)


@timed("independent_verify")
def independent_verify(state):
    rt = _rt(state)
    cop = state["cop"]
    spec = BY_KEY[cop]
    f = dict(state["finding"])
    up = state.get("upstream") or {}
    calls = {c["tool_call_id"]: c for c in state["tool_calls"]}
    ceiling = evidence_conf(up)
    rerun = None
    if state["classification"] == "deterministic":
        # RP-01 only: the same rules again. Diagnostic, never counted as verification.
        try:
            rerun = RULES[cop](_rule_ctx(rt, state, [c for c in state["tool_calls"]
                                                      if c["requested_by"] == "rules"]))["label"]
        except Exception:
            rerun = None
    results = verify_finding({"finding": f, "spec": spec, "picture": state["picture"],
                              "events": _events(rt, cop, state["picture"]), "case": rt.case,
                              "tool_calls": calls, "upstream": up, "memory": rt.memory, "cop": cop,
                              "evidence_ceiling": ceiling, "baseline": state.get("baseline"),
                              "rule_rerun": rerun})
    results = [r if isinstance(r, dict) else r.to_dict() for r in results]
    blocked = blocking(results, BLOCKS_FINDING)
    f["lineage"] = _lineage(f, up)
    quals = [f"{r['check_id']}: {r['reason_code']}" for r in results if r["result"] == "WARN"]
    if blocked:
        f["rejected_candidate"] = {k: f.get(k) for k in ("label", "confidence", "claims", "rationale",
                                                          "status", "proposed_action")}
        f.update(status=REJECTED, label=INCONCLUSIVE_LABEL, confidence=0.0, verified=False,
                 rationale=f"candidate {f['label']} rejected by "
                           f"{', '.join(r['check_id'] for r in blocked)}: "
                           + "; ".join(r["detail"] for r in blocked if r["detail"])[:400])
        f["proposed_action"] = {"action": _fallback_action(spec),
                                "reason": "candidate finding failed verification; action withdrawn"}
    else:
        f["verified"] = f["status"] == SUBSTANTIVE
        if f["verified"] and f["confidence"] > ceiling:
            quals.append(f"confidence {f['confidence']} capped at evidence ceiling {ceiling}")
            f["confidence"] = round(ceiling, 3)
    f["qualifications"] = quals
    f["verifier_registry"] = REGISTRY_VERSION
    return {"finding": f, "verifier_results": results}


@timed("authority_check")
def authority_check(state):
    rt = _rt(state)
    cop = state["cop"]
    spec = BY_KEY[cop]
    p = rt.profiles[cop]
    f = state["finding"]
    requested = (f.get("proposed_action") or {}).get("action", "NONE")
    reason = (f.get("proposed_action") or {}).get("reason", "")
    allowed, sub, why = authority_check_fn(spec, requested, rt.mode, rt.live_session)
    action = requested if allowed else (sub if sub in spec.permitted_actions else _fallback_action(spec))
    pa = ProposedAction(cop=cop, action=action, reason=reason,
                        target=rt.case.session_id, authority_tier=TIER.get(action, ""),
                        authorised=allowed, authority_reason=why).to_dict()
    pa["requested_action"] = requested
    checks = [r.to_dict() if not isinstance(r, dict) else r
              for r in verify_action(spec, pa, rt.mode, rt.case, f)]
    pre = [r for r in checks if r["check_id"] in ("AU-01", "PE-01")]
    if blocking(pre, BLOCKS_ACTION):
        pa["execution_status"] = NOT_EXECUTED_BLOCKED
        pa["authority_reason"] = (why + "; " if why else "") + "; ".join(
            f"{r['check_id']} {r['reason_code']}" for r in blocking(pre, BLOCKS_ACTION))
    elif action in QUIET_ACTIONS:
        pa["execution_status"] = NOT_EXECUTED_NO_ACTION
    elif rt.mode == "dry_run":
        pa["execution_status"] = NOT_EXECUTED_DRY_RUN
    elif action in p.approval_actions:
        pa["requires_approval"] = True
        pa["execution_status"] = AWAITING_APPROVAL
    else:
        # ALERT, PASS_TO_COP, REQUEST_CONTEXT: informational, nothing to enforce
        pa["execution_status"] = NOT_EXECUTED_NO_ACTION
    return {"proposed_action": pa, "verifier_results": state["verifier_results"] + pre}


def route_after_authority(state):
    return "await_approval" if state["proposed_action"].get("requires_approval") else "execute_action"


@timed("await_approval")
def await_approval(state):
    pa = state["proposed_action"]
    f = state["finding"]
    answer = interrupt({"kind": "approval_request", "case_id": state["case_id"], "cop": state["cop"],
                        "action": pa["action"], "reason": pa["reason"], "target": pa["target"],
                        "finding": {"label": f["label"], "status": f["status"],
                                    "confidence": f["confidence"], "verified": f["verified"]},
                        "simulated_execution": True})
    # Fail closed: anything other than an explicit approval with a reviewer is a rejection.
    ok = isinstance(answer, dict) and answer.get("decision") == "approve" and answer.get("reviewer")
    approval = {"decision": "approve" if ok else "reject",
                "reviewer": (answer or {}).get("reviewer") if isinstance(answer, dict) else None,
                "note": (answer or {}).get("note", "") if isinstance(answer, dict) else
                        f"unrecognised resume value {answer!r}"[:200],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return {"approval": approval}


@timed("execute_action")
def execute_action(state):
    rt = _rt(state)
    spec = BY_KEY[state["cop"]]
    pa = dict(state["proposed_action"])
    approval, execution = state.get("approval"), None
    if pa.get("requires_approval"):
        pa["approval"] = approval
        if not approval or approval["decision"] != "approve":
            pa["execution_status"] = APPROVAL_REJECTED
        else:
            try:
                execution = rt.enforcement.execute(state["cop"], state["case_id"], pa["action"], pa["target"])
            except Exception as e:
                execution = {"applied": False, "outcome_confirmed": False, "simulated": True,
                             "detail": f"{type(e).__name__}: {e}"[:200]}
            pa["execution_outcome"] = execution
            pa["execution_status"] = EXECUTED_MOCK if execution["outcome_confirmed"] else EXECUTION_FAILED
        post = [r.to_dict() if not isinstance(r, dict) else r
                for r in verify_action(spec, pa, rt.mode, rt.case, state["finding"], approval, execution)]
        post = [r for r in post if r["check_id"] in ("AU-02", "EX-01")]
        return {"proposed_action": pa, "execution": execution,
                "verifier_results": state["verifier_results"] + post}
    return {"proposed_action": pa}


@timed("record_decision")
def record_decision(state):
    rt = _rt(state)
    cop = state["cop"]
    f = state["finding"]
    sp = state.get("spent") or {}
    r = {
        "run_id": rt.run_id, "session_id": rt.case.session_id, "cop": cop,
        "case_id": rt.case.case_id, "tenant_id": rt.case.tenant_id,
        "classification": state["classification"], "mode": rt.mode,
        "behavioural_question": BY_KEY[cop].behavioural_question,
        "finding": f, "proposed_action": state["proposed_action"],
        "evidence": state["picture"], "upstream": state["upstream_view"],
        "downstream_cops": list(BY_KEY[cop].downstream_cops),
        "tool_calls": [{k: v for k, v in c.items() if k != "result"} for c in state["tool_calls"]],
        "llm_invocations": state["llm_invocations"],
        "verifier_results": state["verifier_results"],
        "security_events": state["security_events"],
        "interpreter": "llm" if state["classification"] == "llm" else "rules",
        "evidence_confidence": evidence_conf(state.get("upstream") or {}),
        "node_timings": state.get("node_timings", {}),
        "total_ms": sum((state.get("node_timings") or {}).values()),
        "llm_spend": {k: v for k, v in sp.items() if k != "started"},
        "decided_at": rt.case.computed_at, "error": state.get("llm_error"),
    }
    return {"result": r}


@timed("validate_memory_update")
def validate_memory_update(state):
    rt = _rt(state)
    cop = state["cop"]
    spec = BY_KEY[cop]
    f = state["finding"]
    quiet = spec.interpretations[0] if spec.interpretations else None
    if rt.memory is None:
        return {"memory_write": {"written": False, "reason": "no memory gateway"}}
    if f["status"] == SUBSTANTIVE and f["label"] != quiet:
        status, label = (VERIFIED if f["verified"] else UNVALIDATED), f["label"]
    elif f["status"] == REJECTED:
        # kept as an unverified observation so a later case can see it was
        # proposed and rejected; never precedent
        status, label = UNVALIDATED, (f.get("rejected_candidate") or {}).get("label")
    else:
        return {"memory_write": {"written": False, "reason": f"nothing to remember ({f['status']}, {f['label']})"}}
    rtype = POLICIES[cop]["writes"][0]
    payload = {"label": label, "finding_status": f["status"], "confidence": f["confidence"],
               "rationale": (f.get("rationale") or "")[:400], "lineage": f.get("lineage", []),
               "session_id": rt.case.session_id,
               "claims": [{"statement": c.get("statement"), "metric_id": c.get("metric_id"),
                           "asserted_state": c.get("asserted_state")} for c in f.get("claims", [])][:10]}
    prov = {"case_id": rt.case.case_id, "finding_id": f["finding_id"], "tenant_id": rt.case.tenant_id,
            "session_id": rt.case.session_id, "verified": f["verified"],
            "verifier_registry": REGISTRY_VERSION, "contract_version": CONTRACT_VERSION,
            "synthetic": rt.case.synthetic}
    checks = [r if isinstance(r, dict) else r.to_dict()
              for r in verify_memory_update(cop, rt.case, rtype, payload, prov, status, f["verified"])]
    vr = state["verifier_results"] + checks
    if any(r["result"] == FAIL for r in checks):
        return {"memory_write": {"written": False, "reason": checks[0]["detail"], "record_type": rtype},
                "verifier_results": vr}
    try:
        rec = rt.memory.write(cop, rt.case.case_id, rt.case.tenant_id, rtype, payload, prov,
                              agent_scope=rt.case.agent_scope if POLICIES[cop]["scope"] == "agent" else "*",
                              status=status)
    except MemoryAccessDenied as e:
        return {"memory_write": {"written": False, "reason": str(e), "record_type": rtype},
                "verifier_results": vr}
    return {"memory_write": {"written": True, "record_id": rec["record_id"], "record_type": rtype,
                             "status": status}, "verifier_results": vr}


@timed("persist_and_publish")
def persist_and_publish(state):
    rt = _rt(state)
    r = {**state["result"], "verifier_results": state["verifier_results"],
         "memory_write": state.get("memory_write"),
         "node_timings": {**state.get("node_timings", {})}}
    r["finding"] = {**r["finding"], "memory_write": state.get("memory_write")}
    if rt.store is not None:
        rt.store.save_result(r)
    pub = {k: state["finding"].get(k) for k in ("finding_id", "cop", "label", "status", "confidence",
                                                 "verified", "lineage", "claims", "rationale",
                                                 "severity", "cop_specific", "upstream_used")}
    pub["proposed_action"] = state["proposed_action"]
    return {"result": r, "published": pub}


# ====================================================================== assembly

def build_cop_graph(checkpointer=None):
    g = StateGraph(COPState)
    for fn in (receive_case, load_inputs, validate_evidence, retrieve_memory, computational_baseline,
               llm_plan_investigation, validate_tool_request, execute_authorised_tool,
               validate_tool_result, llm_assess_evidence, formulate_finding, independent_verify,
               authority_check, await_approval, execute_action, record_decision,
               validate_memory_update, persist_and_publish):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "receive_case")
    g.add_edge("receive_case", "load_inputs")
    g.add_edge("load_inputs", "validate_evidence")
    g.add_edge("validate_evidence", "retrieve_memory")
    g.add_edge("retrieve_memory", "computational_baseline")
    g.add_conditional_edges("computational_baseline",
                            lambda s: "llm_plan_investigation" if s["classification"] == "llm"
                            else "independent_verify",
                            ["llm_plan_investigation", "independent_verify"])
    g.add_conditional_edges("llm_plan_investigation", route_after_plan,
                            ["validate_tool_request", "llm_assess_evidence", "formulate_finding"])
    g.add_edge("validate_tool_request", "execute_authorised_tool")
    g.add_edge("execute_authorised_tool", "validate_tool_result")
    g.add_edge("validate_tool_result", "llm_assess_evidence")
    g.add_conditional_edges("llm_assess_evidence", route_after_assess,
                            ["validate_tool_request", "formulate_finding"])
    g.add_edge("formulate_finding", "independent_verify")
    g.add_edge("independent_verify", "authority_check")
    g.add_conditional_edges("authority_check", route_after_authority,
                            ["await_approval", "execute_action"])
    g.add_edge("await_approval", "execute_action")
    g.add_edge("execute_action", "record_decision")
    g.add_edge("record_decision", "validate_memory_update")
    g.add_edge("validate_memory_update", "persist_and_publish")
    g.add_edge("persist_and_publish", END)
    return g.compile(checkpointer=checkpointer)


class CaseState(TypedDict, total=False):
    case_id: str
    order: list
    findings: dict              # {cop: published finding}
    results: dict               # {cop: full result}
    consumption: list           # [{consumer, producer, finding_id, verified, label}]


_COP_GRAPH = None


def cop_graph():
    global _COP_GRAPH
    if _COP_GRAPH is None:
        _COP_GRAPH = build_cop_graph()           # inherits the parent's checkpointer
    return _COP_GRAPH


def _cop_node(cop):
    def node(state, config):
        found = state.get("findings") or {}
        up = {k: found[k] for k in CONSUMES[cop] if k in found}
        out = cop_graph().invoke({"case_id": state["case_id"], "cop": cop, "upstream": up}, config)
        cons = [{"consumer": cop, "producer": k, "finding_id": f.get("finding_id"),
                 "label": f.get("label"), "verified": f.get("verified"),
                 "consumed_as": "evidence" if f.get("verified") else "context_only_unverified"}
                for k, f in sorted(up.items())]
        missing = [{"consumer": cop, "producer": k, "finding_id": None, "label": None,
                    "verified": False, "consumed_as": "absent"} for k in CONSUMES[cop] if k not in found]
        return {"findings": {**found, cop: out["published"]},
                "results": {**(state.get("results") or {}), cop: out["result"]},
                "consumption": (state.get("consumption") or []) + cons + missing}
    node.__name__ = f"cop_{cop}"
    return node


def build_case_graph(cops=None, checkpointer=None):
    order = topological_order(cops)
    g = StateGraph(CaseState)
    prev = START
    for cop in order:
        g.add_node(cop, _cop_node(cop))
        g.add_edge(prev, cop)
        prev = cop
    g.add_edge(prev, END)
    return g.compile(checkpointer=checkpointer), order


def run_case(rt, cops=None, checkpointer=None, thread_id=None):
    """Run a case. Returns the final state, or {"interrupted": [...], ...} when an
    approval is pending (resume with resume_case)."""
    register(rt)
    graph, order = build_case_graph(cops, checkpointer)
    cfg = {"configurable": {"thread_id": thread_id or rt.case.case_id}, "recursion_limit": 200}
    out = graph.invoke({"case_id": rt.case.case_id, "order": order, "findings": {}, "results": {},
                        "consumption": []}, cfg)
    return _shape(out)


def resume_case(rt, decision, cops=None, checkpointer=None, thread_id=None):
    register(rt)
    graph, _ = build_case_graph(cops, checkpointer)
    cfg = {"configurable": {"thread_id": thread_id or rt.case.case_id}, "recursion_limit": 200}
    return _shape(graph.invoke(Command(resume=decision), cfg))


def _shape(out):
    intr = out.get("__interrupt__") or []
    res = {k: v for k, v in out.items() if k != "__interrupt__"}
    res["interrupted"] = [i.value for i in intr]
    return res


def run_cop(rt, cop, upstream=None, checkpointer=None, thread_id=None):
    """One COP on its own (tests, and the per-COP evaluation cases)."""
    register(rt)
    g = build_cop_graph(checkpointer)
    cfg = {"configurable": {"thread_id": thread_id or f"{rt.case.case_id}:{cop}"}, "recursion_limit": 100}
    out = g.invoke({"case_id": rt.case.case_id, "cop": cop, "upstream": upstream or {}}, cfg)
    if out.get("__interrupt__"):
        return {"interrupted": [i.value for i in out["__interrupt__"]], **out}
    return out
