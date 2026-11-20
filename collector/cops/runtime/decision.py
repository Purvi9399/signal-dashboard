"""
The decision record every COP publishes: a common core that an independent verifier can read the
same way for all eighteen COPs, and an extension block for what only one COP produces.

build() assembles it from the run state after the memory write, from what the run already holds.
A part the run does not have is None or empty with the reason in `absent`, never filled in.
validate() returns the problems with a record, as strings.
"""

from .contracts import CONTRACT_VERSION, SUBSTANTIVE, FAIL
from ..specs import BY_KEY, CONSUMES, consumers

VERSION = "cops-decision/1"
INCONCLUSIVE_LABEL = "INCONCLUSIVE"
# uncertainty.limitations: what a decision rests on without having it. Additive to cops-decision/1; a record
# without the field still validates.
MISSING_EVIDENCE, NOT_CALCULATED = "MISSING_EVIDENCE", "NOT_CALCULATED"
LIMIT_NOT_APPROVED, INFERRED_RELATIONSHIP = "LIMIT_NOT_APPROVED", "INFERRED_RELATIONSHIP"
LIMITATION_KINDS = (MISSING_EVIDENCE, NOT_CALCULATED, LIMIT_NOT_APPROVED, INFERRED_RELATIONSHIP)
MISSING_STATES = ("MISSING_DATA", "NOT_OBSERVED", "EVIDENCE_MISSING", "INSUFFICIENT_HISTORY")
CORE = ("decision_contract", "cop", "case", "questions_assessed", "metrics", "question_findings",
        "upstream_considered", "memory_used", "conclusion", "severity", "supporting_evidence",
        "contrary_evidence", "uncertainty", "recommendation", "authority", "recipients", "provenance",
        "extensions", "absent")
# cop_specific keys that are part of the core record rather than extensions.
LIFTED = ("questions", "memory_payload", "recipients", "evidence_assessment", "contrary", "also_held", "limitations")


def _questions(f):
    """(question trace by id, package version) for a COP with a question contract, else (None, None)."""
    cs = f.get("cop_specific") or {}
    trace = cs.get("questions") or (cs.get("evidence_assessment") or {}).get("questions")
    if not trace:
        return None, None
    from . import evidence_assessment as EA
    return trace, EA.load_package(f["cop"]).get("version")


def _question_finding(qid, t):
    return {"id": qid, "question": t["question"], "answer": t["answer"], "sufficiency": t["sufficiency"],
            "explanation": t.get("explanation") or "; ".join(t.get("because") or []),
            "basis": t.get("basis"), "metrics": t.get("metrics") or t.get("metrics_consumed"),
            "evidence_refs": t.get("raw_evidence_refs", []),
            "reasons": t.get("sufficiency_reasons", []), "not_assessed": t.get("not_assessed", []),
            "drives_conclusion": t.get("drives_label", t.get("label_ceiling") not in (None, "SUFFICIENT"))}


def limitation(kind, question, subject, reason_code, detail, evidence_refs=(), source="cop"):
    out = {"kind": kind, "question": question, "subject": subject, "reason_code": reason_code, "detail": detail,
           "evidence_refs": list(evidence_refs), "source": source}
    if kind == INFERRED_RELATIONSHIP:
        out["verified"] = False
    return out


def _derived_limitations(findings, f):
    """Limitations read from what every record already holds: each question's unassessed metrics and checks,
    and the finding's missing evidence."""
    out = []
    for x in findings:
        for u in (x.get("basis") or {}).get("unassessed", []):
            subject, state = u.get("metric") or u.get("check"), u.get("state")
            if state == NOT_CALCULATED:
                out.append(limitation(NOT_CALCULATED, x["id"], subject, NOT_CALCULATED,
                                      u.get("missing_inputs") or u.get("reason", ""), source="derived"))
            elif u.get("because") == LIMIT_NOT_APPROVED:
                out.append(limitation(LIMIT_NOT_APPROVED, x["id"], subject, LIMIT_NOT_APPROVED, u.get("reason", ""),
                                      source="derived"))
            elif state in MISSING_STATES or u.get("because"):
                out.append(limitation(MISSING_EVIDENCE, x["id"], subject, u.get("because") or state,
                                      u.get("reason", ""), source="derived"))
    for m in f.get("missing_evidence") or []:
        out.append(limitation(MISSING_EVIDENCE, None, m if isinstance(m, str) else str(m), MISSING_EVIDENCE,
                              m if isinstance(m, str) else str(m), source="derived"))
    return out


def _limitations(cop_reported, findings, f):
    out, seen = [], set()
    for x in [c for c in cop_reported if isinstance(c, dict)] + _derived_limitations(findings, f):
        key = (x["kind"], x["question"], x["subject"], x["reason_code"])
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _statement(x):
    return (f"{x['question'] + ': ' if x['question'] else ''}{x['kind']} {x['subject']}: {x['detail']}"
            + (" (not verified)" if x.get("verified") is False else ""))


def build(state, rt):
    cop, f = state["cop"], state["finding"]
    spec = BY_KEY[cop]
    pa = state.get("proposed_action") or {}
    tri = state.get("triage") or {}
    routing = tri.get("routing") or {}
    mem = state.get("memory_view") or {}
    cs = f.get("cop_specific") or {}
    absent = {}

    trace, pkg_version = _questions(f)
    if trace is None:
        absent["question_findings"] = "this COP has no question contract (cops/packages/<cop>/METRICS.yaml)"
        questions = [{"id": None, "question": spec.behavioural_question, "assessed_individually": False}]
        findings = []
    else:
        questions = [{"id": q, "question": t["question"], "assessed_individually": True} for q, t in trace.items()]
        findings = [_question_finding(q, t) for q, t in trace.items()]

    used = {u["cop"]: u for u in f.get("upstream_used") or []}
    upstream = [{**v, "used": v["cop"] in used, "counted_as": (used.get(v["cop"]) or {}).get("counted_as")}
                for v in state.get("upstream_view") or []]
    precedents = [c.get("record_id") for c in f.get("claims", []) if c.get("kind") == "memory_precedent"]
    if not mem.get("available"):
        absent["memory_used"] = mem.get("denied") or "no memory gateway for this run"

    vr = state.get("verifier_results") or []
    failed = [{"check": r["check_id"], "result": r["result"], "effect": r["effect"], "detail": r.get("detail", "")}
              for r in vr if r["result"] in (FAIL, "WARN")]
    inconclusive_q = [{"id": x["id"], "reasons": x["reasons"]} for x in findings if x["answer"] == INCONCLUSIVE_LABEL]

    limits = _limitations(cs.get("limitations") or [], findings, f)
    deferred = f["status"] != SUBSTANTIVE
    llm = state.get("llm_invocations") or []
    return {
        "decision_contract": VERSION,
        "cop": {"key": cop, "name": spec.name, "classification": state["classification"],
                "interpreter": "llm" if state["classification"] == "llm" else "rules"},
        "case": {"case_id": rt.case.case_id, "session_id": rt.case.session_id, "tenant_id": rt.case.tenant_id,
                 "run_id": rt.run_id, "evaluation_cycle": rt.case.computed_at, "synthetic": rt.case.synthetic},
        "questions_assessed": questions,
        "metrics": [{k: r.get(k) for k in ("metric", "evidence", "state", "value", "threshold", "evidence_refs")}
                    for r in (state.get("picture") or {}).get("rows", [])],
        "question_findings": findings,
        "upstream_considered": upstream,
        "memory_used": {"available": bool(mem.get("available")),
                        "established": [r["record_id"] for r in mem.get("established", [])],
                        "verified_pending": [r["record_id"] for r in mem.get("verified_pending", [])],
                        "unverified": len(mem.get("unverified", [])), "cited_as_precedent": precedents,
                        "write": state.get("memory_write")},
        "conclusion": {"label": f["label"], "status": f["status"], "verified": f["verified"],
                       "confidence": f["confidence"], "rationale": f.get("rationale", ""),
                       "also_held": cs.get("also_held", [])},
        "severity": {"severity": f.get("severity", "NONE"), "priority": routing.get("tier"),
                     "triage_outcome": tri.get("outcome")},
        "supporting_evidence": [{"statement": c.get("statement"), "kind": c.get("kind"),
                                 "evidence_refs": c.get("evidence_refs", [])} for c in f.get("claims", [])],
        "contrary_evidence": {"qualifications": f.get("qualifications", []), "verifier_failures": failed,
                              "cop_reported": cs.get("contrary", []),
                              "limiting": (tri.get("evidence_support") or {}).get("limiting", []),
                              "partial_or_unanswered": [x["id"] for x in findings
                                                        if x["answer"] in (INCONCLUSIVE_LABEL,) + _partial()]},
        "uncertainty": {"confidence": f["confidence"], "confidence_basis": tri.get("confidence"),
                        "missing_evidence": f.get("missing_evidence", []),
                        "missing_dependencies": f.get("missing_dependencies", []),
                        "inconclusive_questions": inconclusive_q, "limitations": limits,
                        "cop_limitations": [_statement(x) for x in limits if x["source"] == "cop"]},
        "recommendation": {"action": pa.get("action"), "typed_action": pa.get("typed_action"),
                           "reason": pa.get("reason", ""), "execution_status": pa.get("execution_status"),
                           "deferred": deferred,
                           "deferral_reason": f.get("rationale", "") if deferred else None},
        "authority": {"requires_approval": pa.get("requires_approval"), "tier": pa.get("authority_tier"),
                      "authorised": pa.get("authorised"), "reason": pa.get("authority_reason"),
                      "approval": pa.get("approval"), "human_needed": routing.get("human_needed")},
        "recipients": {"downstream_cops": list(consumers(cop)), "notified": cs.get("recipients", []),
                       "referral": (tri.get("referral") or {}).get("receiver")},
        "provenance": {"contract_version": CONTRACT_VERSION, "package_version": pkg_version,
                       "metric_version": rt.case.cfg.get("metric_version"),
                       "rule": f"collector/cops/runtime/rules.py:{cop}",
                       "verifier_registry": next((r.get("version") for r in vr), None),
                       "verifiers_run": sorted({r["check_id"] for r in vr}),
                       "model_calls": [{k: x.get(k) for k in ("model", "prompt_version", "simulated", "tokens_measured")}
                                       for x in llm],
                       "tool_calls": [{k: c.get(k) for k in ("tool", "tool_call_id", "status")}
                                      for c in state.get("tool_calls") or []],
                       "decided_at": rt.case.computed_at},
        "extensions": {cop: {k: v for k, v in cs.items() if k not in LIFTED}},
        "absent": absent,
    }


def _partial():
    from .cop_package import ID_PARTIAL, INCOMPLETE
    return ID_PARTIAL + INCOMPLETE


def validate(d):
    probs = [f"missing {k}" for k in CORE if k not in d]
    if probs:
        return probs
    cop = d["cop"]["key"]
    spec = BY_KEY.get(cop)
    if spec is None:
        return [f"unknown COP {cop}"]
    lab = d["conclusion"]["label"]
    if lab != INCONCLUSIVE_LABEL and spec.interpretations and lab not in spec.interpretations:
        probs.append(f"label {lab} is not in {cop}'s vocabulary")
    if not isinstance(d["conclusion"]["verified"], bool):
        probs.append("conclusion.verified is not a boolean")
    if d["question_findings"]:
        from . import evidence_assessment as EA
        qs = EA.load_package(cop)["questions"]
        for x in d["question_findings"]:
            if x["id"] not in qs:
                probs.append(f"question {x['id']} is not in {cop}'s contract")
            elif x["answer"] != INCONCLUSIVE_LABEL and x["answer"] not in qs[x["id"]]["outcomes"]:
                probs.append(f"{x['id']}: answer {x['answer']} is not one of its outcomes")
            if x["answer"] == INCONCLUSIVE_LABEL and not x["reasons"]:
                probs.append(f"{x['id']}: inconclusive with no reason")
        if {x["id"] for x in d["question_findings"]} != set(qs):
            probs.append("question_findings do not cover every question in the contract")
    elif "question_findings" not in d["absent"]:
        probs.append("no question findings and no reason given")
    for x in (d["uncertainty"].get("limitations") or []):
        if x.get("kind") not in LIMITATION_KINDS:
            probs.append(f"limitation kind {x.get('kind')} is not one of {', '.join(LIMITATION_KINDS)}")
        elif not x.get("detail"):
            probs.append(f"limitation {x['kind']} {x.get('subject')} has no detail")
        elif x["kind"] == INFERRED_RELATIONSHIP and x.get("verified") is not False:
            probs.append(f"inferred relationship {x.get('subject')} is not marked unverified")
    for m in d["metrics"]:
        if not m.get("metric"):
            probs.append("a metric entry has no metric id")
    if d["recommendation"]["deferred"] and not d["recommendation"]["deferral_reason"]:
        probs.append("deferred with no reason")
    known = set(BY_KEY)
    for r in d["recipients"]["downstream_cops"] + d["recipients"]["notified"]:
        if r not in known:
            probs.append(f"recipient {r} is not a COP")
    if set(u["cop"] for u in d["upstream_considered"]) - set(CONSUMES[cop]):
        probs.append("upstream_considered names a COP this one does not consume")
    return probs
