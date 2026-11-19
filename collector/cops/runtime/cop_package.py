"""
Individual COP packages (cops/packages/<cop>/): validation of the machine-readable
contract, and the per-question observation that turns an evidence assessment
into an answer.

An answer is given only when the question's evidence is SUFFICIENT; otherwise
the outcome is INCONCLUSIVE with the assessment's reasons. Observation uses
metric values, the states the approved thresholds in metrics_config.json give
them, and the reliability checks. aggregate_runtime_monitoring turns the five
answers into each question's contribution to the COP label (INTERPRETATION.md).
"""

import json, os

from . import evidence_assessment as EA
from ..specs import BY_KEY

INCONCLUSIVE = "INCONCLUSIVE"
KINDS = (EA.REQUIRED, EA.WHEN_APPLICABLE, EA.OPTIONAL)
BY = ("deterministic", "llm")


def _registry():
    with open(os.path.join(EA.HERE, "metric_registry.json")) as f:
        return {m["id"]: m for m in json.load(f)["metrics"]}


def validate(cop):
    """Problems with a COP's METRICS.yaml, as a list of strings. Empty when valid."""
    sm = EA.signal_metrics
    pkg = EA.load_package(cop)
    reg = _registry()
    built = set(sm.METRIC_IDS) | set(sm.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    spec = BY_KEY[cop]
    probs = []
    if pkg.get("cop") != cop:
        probs.append(f"cop is {pkg.get('cop')!r}, expected {cop!r}")
    mapped = {m for m, r in reg.items() if cop in r.get("consumers", [])}
    listed = set(pkg.get("mapping") or {})
    for m in sorted(mapped - listed):
        probs.append(f"mapping document assigns {m} but METRICS.yaml does not list it")
    for m in sorted(listed - mapped):
        probs.append(f"METRICS.yaml lists {m} but the mapping document does not assign it")
    for m in spec.metric_keys:
        if m not in listed:
            probs.append(f"specs.py metric_keys has {m}, missing from METRICS.yaml")
    used = {}
    for qid, q in (pkg.get("questions") or {}).items():
        for k in ("question", "fields", "requires", "verified", "blocking", "outcomes"):
            if k not in q:
                probs.append(f"{qid}: no {k}")
        for r in q.get("requires", []):
            if r.get("kind") not in KINDS:
                probs.append(f"{qid}: {r.get('metric')} has kind {r.get('kind')!r}")
            if r.get("metric") not in built and r.get("kind") != EA.OPTIONAL:
                probs.append(f"{qid}: {r.get('metric')} is not built and is not OPTIONAL")
            if r.get("metric") not in listed:
                probs.append(f"{qid}: {r.get('metric')} is not in the mapping list")
            used.setdefault(r.get("metric"), set()).add(qid)
        for c in q.get("verified", []) + q.get("blocking", []):
            if c not in EA.CHECKS:
                probs.append(f"{qid}: unknown check {c}")
        if set(q.get("verified", [])) & set(q.get("blocking", [])):
            probs.append(f"{qid}: a check is both verified and blocking")
        if INCONCLUSIVE in q.get("outcomes", []):
            probs.append(f"{qid}: INCONCLUSIVE is implied, not listed as an outcome")
        for d in q.get("depends_on", []):
            if d not in pkg["questions"] or d == qid:
                probs.append(f"{qid}: depends_on {d} is not another question")
    for m, entry in (pkg.get("mapping") or {}).items():
        declared = set((entry or {}).get("used_by") or [])
        actual = used.get(m, set())
        if declared != actual:
            probs.append(f"{m}: used_by says {sorted(declared)}, questions use {sorted(actual)}")
        if not declared and not (entry or {}).get("note"):
            probs.append(f"{m}: unused without a note saying why")
    for key, lim in (pkg.get("limits") or {}).items():
        if lim.get("check") not in EA.CHECKS:
            probs.append(f"limit {key}: unknown check {lim.get('check')}")
    for name, op in (pkg.get("operations") or {}).items():
        if op.get("by") not in BY:
            probs.append(f"operation {name}: by is {op.get('by')!r}")
    return probs


# ---------------------------------------------------------------- Runtime Monitoring

def _item(a, metric):
    return next(x for x in a["presence"]["items"] if x["requirement"] == metric)


def _check(a, name):
    return next(c for c in a["reliability"]["checks"] if c["check"] == name)


def _seen_by(r):
    return {c for c in [r.get("collector")] + list(r.get("contributing") or []) if c}


def collector_operation(rows, expected, capability=None):
    """When each expected collector was observed in the session, and which stopped part-way.

    A collector stopped when, after its last observation, the session holds an event of a type it
    reported earlier that only other collectors saw; or when collector_capability (if supplied)
    says it was last seen before the session ended, or is silent.
    """
    ts = EA.signal_traces.ts
    dated = sorted((r for r in rows if ts(r.get("occurred_at"))), key=lambda r: ts(r["occurred_at"]))
    end = ts(dated[-1]["occurred_at"]) if dated else None
    cap = {c.get("collector"): c for c in (capability or [])}
    out, stopped = {}, {}
    for c in expected:
        mine = [r for r in dated if c in _seen_by(r)]
        if not mine:
            out[c] = {"observed": False}
            continue
        last = ts(mine[-1]["occurred_at"])
        types = {r.get("observable_type") for r in mine}
        after = [r for r in dated if ts(r["occurred_at"]) > last and r.get("observable_type") in types]
        out[c] = {"observed": True, "first_seen": mine[0]["occurred_at"], "last_seen": mine[-1]["occurred_at"],
                  "rows": len(mine), "later_events_without_it": [f"observable:{r['observable_id']}" for r in after]}
        why = []
        if after:
            why.append(f"{len(after)} later event(s) of types it reported were seen only by other collectors")
        row = cap.get(c)
        if row is not None:
            out[c]["capability"] = {k: row.get(k) for k in ("configured", "observed_state", "last_seen")}
            seen = ts(row.get("last_seen"))
            if row.get("observed_state") == "silent":
                why.append("collector_capability reports it silent")
            elif seen and end and (seen.tzinfo is None) == (end.tzinfo is None) and seen < end:
                why.append(f"collector_capability last saw it at {row['last_seen']}, before the session ended")
        if why:
            stopped[c] = why
    return {"collectors": out, "stopped": sorted(stopped), "stopped_because": stopped,
            "capability_supplied": capability is not None}


def _rm1(a, operation=None):
    cov = _item(a, "telemetry.collector_coverage")
    f = cov["features"]
    basis = {"value": cov["value"], "state": cov["metric_state"], "expected": f.get("expected"),
             "present": f.get("present"), "missing": f.get("missing")}
    if operation is not None:
        basis.update(stopped=operation["stopped"], stopped_because=operation["stopped_because"],
                     operation=operation["collectors"], capability_supplied=operation["capability_supplied"])
    return ("EXPECTED_COLLECTORS_MISSING" if f.get("missing") else "ALL_EXPECTED_COLLECTORS_CONTRIBUTED"), basis


def _rm2(a):
    c = _check(a, EA.CONFLICTING_SOURCES)
    return ("SOURCES_DISAGREE" if c["outcome"] == EA.VERIFIED_FAILED else "SOURCES_AGREE"), \
        {**c["evidence"], "agreement": c["value"], "disagreeing": c["evidence_refs"]}


def _rm3(a):
    turn, lane = _item(a, "telemetry.turn_linkage"), _item(a, "telemetry.lane_linkage")
    basis = {"turn_linkage": turn["value"], "turn_state": turn["metric_state"],
             "steps_without_turn": turn["features"].get("without_turn"),
             "lane_linkage": lane["value"], "lane_state": lane["state"]}
    if turn["value"] == 0:
        return "UNLINKED", basis
    if turn["value"] == 1 and (lane["state"] != EA.PRESENT or lane["value"] == 1):
        return "ALL_ACTIONS_LINKED", basis
    return "PARTIALLY_LINKED", basis


def _rm4(a):
    checks = {k: _check(a, k) for k in (EA.INGEST_LATENCY, EA.STALE, EA.DISCONTINUITY)}
    failed = sorted(k for k, c in checks.items() if c["outcome"] == EA.VERIFIED_FAILED)
    basis = {k: {"value": c.get("value"), "limit": c.get("limit"), "outcome": c["outcome"]} for k, c in checks.items()}
    return ("NOT_CURRENT_OR_CONTINUOUS" if failed else "CURRENT_AND_CONTINUOUS"), {**basis, "failed": failed}


def _rm5(a, earlier):
    unk = _item(a, "telemetry.unknown_share")
    causes = []
    if unk["metric_state"] != "NORMAL":
        causes.append(f"telemetry.unknown_share {unk['value']} is {unk['metric_state']} against its approved "
                      "threshold")
    for qid, ans in earlier.items():
        if ans["outcome"] == INCONCLUSIVE:
            causes.append(f"{qid} could not be answered ({ans['sufficiency']})")
        elif ans["outcome"] in INCOMPLETE:
            causes.append(f"{qid}: {ans['outcome']}")
        if ans["basis"].get("stopped"):
            causes.append(f"{qid}: {', '.join(ans['basis']['stopped'])} stopped reporting during the session")
    basis = {"unknown_share": unk["value"], "unknown_share_state": unk["metric_state"],
             "unknown": unk["features"].get("unknown"), "causes": causes}
    return ("MUST_REMAIN_UNCERTAIN" if causes else "COMPLETE_AND_TRUSTWORTHY"), basis


# Answers to RM-1..RM-4 that mean part of the evidence is missing or unusable.
INCOMPLETE = ("EXPECTED_COLLECTORS_MISSING", "PARTIALLY_LINKED", "UNLINKED", "NOT_CURRENT_OR_CONTINUOUS")

OBSERVE = {"RM-1": _rm1, "RM-2": _rm2, "RM-3": _rm3, "RM-4": _rm4}


def observe_runtime_monitoring(assessments, operation=None):
    """{question_id: assessment} -> {question_id: answer}."""
    pkg = EA.load_package("runtime_monitoring")["questions"]
    out = {}
    for qid in pkg:
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf != "SUFFICIENT":
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf, "basis": {},
                        "because": a["sufficiency"]["reasons"]}
            continue
        if qid == "RM-5":
            outcome, basis = _rm5(a, {k: out[k] for k in pkg[qid].get("depends_on", [])})
        elif qid == "RM-1":
            outcome, basis = _rm1(a, operation)
        else:
            outcome, basis = OBSERVE[qid](a)
        if outcome not in pkg[qid]["outcomes"]:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
    return out


def assess_runtime_monitoring(rows, session_id, cfg=None, computed_at=None, raw_events=None, capability=None,
                              native=None):
    """The five questions for one session: each assessment with its answer.

    raw_events: stored source events the rows link to. capability: collector_capability rows for the
    session's tool. native: an index of the native transcripts raw_ref names. Any may be None, and the
    checks that need it say so."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    assessments = {qid: EA.assess("runtime_monitoring", qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS["runtime_monitoring"]}
    tool = next((r.get("tool") for r in srows if r.get("tool")), None)
    expected = cfg["expected_collectors"].get(tool or "", [])
    cap = None if capability is None else [c for c in capability if c.get("tool") in (tool, None)]
    answers = observe_runtime_monitoring(assessments, collector_operation(srows, expected, cap))
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


# ---------------------------------------------------------------- aggregation

RELIABLE_SUFFICIENT = "RELIABLE_SUFFICIENT"
DEGRADED_TELEMETRY = "DEGRADED_TELEMETRY"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
CONTRADICTORY_OR_UNVERIFIABLE = "CONTRADICTORY_OR_UNVERIFIABLE"
ASSESSMENT_INCONCLUSIVE = "ASSESSMENT_INCONCLUSIVE"
CLASSES = (RELIABLE_SUFFICIENT, ASSESSMENT_INCONCLUSIVE, DEGRADED_TELEMETRY, EVIDENCE_MISSING,
           CONTRADICTORY_OR_UNVERIFIABLE)
# What each class allows the label to be, at best. ASSESSMENT_INCONCLUSIVE leaves the label to the
# approved thresholds and is reported as a qualification.
CEILING = {RELIABLE_SUFFICIENT: "SUFFICIENT", ASSESSMENT_INCONCLUSIVE: "SUFFICIENT",
           DEGRADED_TELEMETRY: "DEGRADED", EVIDENCE_MISSING: "DEGRADED", CONTRADICTORY_OR_UNVERIFIABLE: "DEGRADED"}
LABEL_ORDER = ("SUFFICIENT", "DEGRADED", "INSUFFICIENT")

# Reason codes that mean a check or metric could not run, not that evidence is missing or bad.
NOT_RUN = (EA.NOT_EXECUTED, EA.LIMIT_NOT_APPROVED, EA.NO_EVALUATION_TIME, EA.RAW_STORE_NOT_SUPPLIED,
           EA.NOT_CALCULATED, EA.INSUFFICIENT_HISTORY, EA.COMPARISON_NOT_POSSIBLE)
DEGRADING = {"RM-1": ("EXPECTED_COLLECTORS_MISSING",), "RM-3": ("PARTIALLY_LINKED", "UNLINKED"),
             "RM-4": ("NOT_CURRENT_OR_CONTINUOUS",)}


def _unrun(r):
    return f"{', '.join(r['inputs_not_supplied'])} not supplied" if r.get("inputs_not_supplied") else r["code"]


def _contribution(qid, q):
    """(class, why) for one question's answer."""
    ans = q["answer"]
    if ans["outcome"] == INCONCLUSIVE:
        because = ans["because"]
        failed = [r for r in because if r["code"] in EA.CHECKS or r.get("contradicts")]
        if failed:
            return CONTRADICTORY_OR_UNVERIFIABLE, [r["detail"] for r in failed]
        missing = [r for r in because if r["code"] not in NOT_RUN and not r.get("inputs_not_supplied")]
        if missing:
            return EVIDENCE_MISSING, [f"{r.get('requirement') or r.get('check')}: {r['detail']}" for r in missing]
        return ASSESSMENT_INCONCLUSIVE, [f"{r.get('requirement') or r.get('check')}: {_unrun(r)}" for r in because]
    if qid == "RM-2" and ans["outcome"] == "SOURCES_DISAGREE":
        return CONTRADICTORY_OR_UNVERIFIABLE, [f"sources disagree on {', '.join(ans['basis']['disagreeing'])}"]
    why = []
    if ans["outcome"] in DEGRADING.get(qid, ()):
        why.append(ans["outcome"])
    if ans["basis"].get("stopped"):
        why.append(f"{', '.join(ans['basis']['stopped'])} stopped reporting during the session")
    if qid == "RM-5" and ans["basis"]["unknown_share_state"] != "NORMAL":
        why.append(f"telemetry.unknown_share is {ans['basis']['unknown_share_state']}")
    if why:
        return DEGRADED_TELEMETRY, why
    if ans.get("not_assessed"):
        return RELIABLE_SUFFICIENT, [f"not assessed: {x.get('requirement') or x.get('check')}"
                                     for x in ans["not_assessed"]]
    return RELIABLE_SUFFICIENT, []


def _refs(a):
    refs = {r for x in a["presence"]["items"] for r in x["evidence_refs"]}
    refs |= {r for c in a["reliability"]["checks"] for r in c["evidence_refs"]
             if c["check"] in a["reliability"]["verified_checks"] + a["reliability"]["blocking_checks"]}
    return sorted(refs)


def aggregate_runtime_monitoring(questions, threshold_label):
    """The COP label from the approved-threshold label and the five answers, with the trace.

    The label is the worse of threshold_label (rules.py, approved thresholds) and the ceiling of
    the worst question class. A question never raises the label to INSUFFICIENT and never lowers it.
    """
    deps = {k: v.get("depends_on", []) for k, v in EA.load_package("runtime_monitoring")["questions"].items()}
    trace, classes = {}, {}
    for qid, q in questions.items():
        cls, why = _contribution(qid, q)
        inherited = [classes[d] for d in deps.get(qid, []) if d in classes]
        if cls == RELIABLE_SUFFICIENT and q["answer"]["outcome"] == "MUST_REMAIN_UNCERTAIN" and inherited:
            cls = max(inherited, key=CLASSES.index)
            why = [f"inherited: {'; '.join(q['answer']['basis']['causes'])}"]
        classes[qid] = cls
        a, ans = q["assessment"], q["answer"]
        trace[qid] = {
            "question": a["question"],
            "metrics_consumed": [x["requirement"] for x in a["presence"]["items"]],
            "checks_consumed": a["reliability"]["verified_checks"] + a["reliability"]["blocking_checks"],
            "raw_evidence_refs": _refs(a)[:20],
            "presence": a["presence"]["status"], "reliability": a["reliability"]["status"],
            "sufficiency": a["sufficiency"]["status"],
            "sufficiency_reasons": [{k: r.get(k) for k in ("code", "requirement", "check")}
                                    for r in a["sufficiency"]["reasons"]],
            "answer": ans["outcome"], "contribution": cls, "label_ceiling": CEILING[cls], "because": why,
        }
    worst = max(classes.values(), key=CLASSES.index) if classes else ASSESSMENT_INCONCLUSIVE
    from_questions = max((CEILING[c] for c in classes.values()), key=LABEL_ORDER.index, default="SUFFICIENT")
    label = max(threshold_label, from_questions, key=LABEL_ORDER.index)
    qualifications = [f"{qid}: {'; '.join(t['because'])}" for qid, t in trace.items()
                      if t["contribution"] == ASSESSMENT_INCONCLUSIVE]
    return {"label": label, "threshold_label": threshold_label, "question_label": from_questions,
            "assessment_class": worst, "classes": classes,
            "by_class": {c: sorted(q for q, k in classes.items() if k == c) for c in CLASSES
                         if c in classes.values()},
            "qualifications": qualifications, "questions": trace}
