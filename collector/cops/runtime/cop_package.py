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


def _code_only():
    with open(os.path.join(EA.HERE, "metric_registry.json")) as f:
        return json.load(f).get("code_only_pairs", [])


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
    code_only = {p["metric"] for p in _code_only() if p["cop"] == cop}
    for m, entry in (pkg.get("mapping") or {}).items():
        if (entry or {}).get("code_only") and m not in code_only:
            probs.append(f"{m} is marked code_only but metric_registry.json has no such pair for {cop}")
    mapped |= {m for m, e in (pkg.get("mapping") or {}).items() if (e or {}).get("code_only")} & code_only
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


# ---------------------------------------------------------------- Interaction Discovery

ID = "interaction_discovery"
# Answers that leave part of the interaction network unknown.
ID_PARTIAL = ("ENTITIES_PARTIALLY_IDENTIFIED", "PARTIALLY_RECONSTRUCTED", "NOT_RECONSTRUCTABLE",
              "RECONSTRUCTION_IMPAIRED")
# Runtime Monitoring answers that leave actions unattached to their turns and lanes.
RM_UNLINKED = ("PARTIALLY_LINKED", "UNLINKED")


def _present(a, metric):
    x = _item(a, metric)
    return x if x["state"] == EA.PRESENT else None


def _unassessed(a, *metrics):
    return [{"metric": m, "state": _item(a, m)["state"], "reason": _item(a, m)["reason"]}
            for m in metrics if _item(a, m)["state"] != EA.PRESENT]


def _id1(a, graph=None):
    ent, lanes, mcp = (_item(a, m) for m in ("interaction_unique_entities", "interaction.subagent_lanes",
                                             "interaction.mcp_servers"))
    tools = sorted({e["to"][len("tool:"):] for e in (graph or {}).get("edges", []) if e["to"].startswith("tool:")})
    basis = {"entities": ent["value"], "agents": ent["features"].get("agents"),
             "tools": ent["features"].get("tools"), "tool_names": tools,
             "subagent_lanes": lanes["features"].get("lanes", []),
             "mcp_servers": (mcp["features"].get("servers") or {}) if mcp["state"] == EA.PRESENT else None,
             "unidentified": _unassessed(a, "interaction.mcp_servers")}
    return ("ENTITIES_PARTIALLY_IDENTIFIED" if basis["unidentified"] else "ENTITIES_IDENTIFIED"), basis


def _id2(a):
    edges = _item(a, "interaction_edge_count")
    comp, orphan, lane, turn = (_present(a, m) for m in ("interaction_graph_completeness",
                                                         "interaction_orphaned_events", "telemetry.lane_linkage",
                                                         "telemetry.turn_linkage"))
    gaps = []
    if comp and comp["value"] < 1:
        gaps.append(f"{comp['features'].get('unresolved')} of {comp['features'].get('rows')} subagent rows have "
                    "no resolvable parent")
    if orphan and orphan["value"] > 0:
        gaps.append(f"{orphan['value']} subagent events belong to no known parent")
    if lane and lane["value"] < 1:
        gaps.append(f"subagent lanes {', '.join(lane['features'].get('unlinked', []))} are not linked to a handoff")
    if turn and turn["value"] < 1:
        gaps.append(f"{turn['features'].get('without_turn')} of {turn['features'].get('steps')} steps carry no turn")
    basis = {"edges": edges["value"], "by_kind": edges["features"].get("by_kind", {}),
             "edge_list": edges["features"].get("edges", []),
             "graph_completeness": comp["value"] if comp else None,
             "orphaned_events": orphan["value"] if orphan else None,
             "lane_linkage": lane["value"] if lane else None, "turn_linkage": turn["value"] if turn else None,
             "gaps": gaps, "orphan_refs": orphan["evidence_refs"] if orphan else []}
    if comp and comp["value"] == 0:
        return "NOT_RECONSTRUCTABLE", basis
    return ("PARTIALLY_RECONSTRUCTED" if gaps else "FULLY_RECONSTRUCTED"), basis


def _route_memory(memory):
    """Routes a validated route_record establishes, and those only verified (not precedent)."""
    def routes(bucket):
        return {r: rec["record_id"] for rec in (memory or {}).get(bucket, [])
                if rec.get("record_type", "route_record") == "route_record"
                for r in (rec.get("payload") or {}).get("routes", [])}
    return routes("established"), routes("verified_pending")


def _no_route_to_judge(a):
    """ID-3 is answered without history when the session has no interaction edge: no route exists to be new.
    Only the history-dependent requirement may be what is missing."""
    edges = _item(a, "interaction_edge_count")
    missing = a["sufficiency"]["missing_requirements"]
    return edges["state"] == EA.PRESENT and edges["value"] == 0 and missing == ["interaction_route_novelty"] \
        and not [r for r in a["sufficiency"]["reasons"] if r.get("check")]


def _id3(a, memory=None, known=None):
    nov, fsm, tn = (_item(a, m) for m in ("interaction_route_novelty", "access.first_seen_mcp",
                                          "path.transition_novelty"))
    established, pending = _route_memory(memory)
    listed = list(nov["features"].get("new", [])) if nov["state"] == EA.PRESENT else []
    count = round(nov["value"] * nov["features"]["edges"]) if nov["state"] == EA.PRESENT else 0
    servers = list(fsm["features"].get("new_servers", [])) if fsm["state"] == EA.PRESENT else []
    candidates = listed + [f"mcp:{s}" for s in servers]
    from_memory = sorted(r for r in candidates if r in established)
    new = [r for r in listed if r not in established]
    new_servers = [s for s in servers if f"mcp:{s}" not in established]
    basis = {"new_routes": new, "new_route_count": count - (len(listed) - len(new)),
             "new_mcp_servers": new_servers, "routes_in_session": nov["features"].get("edges"),
             "earlier_sessions": nov["features"].get("earlier_sessions"),
             "known_from_memory": from_memory, "memory_records": sorted({established[r] for r in from_memory}),
             "pending_in_memory": sorted(r for r in candidates if r in pending and r not in established),
             "transition_novelty": tn["value"] if tn["state"] == EA.PRESENT else None,
             "transition_state": tn["metric_state"],
             "new_tools": [r for r in (known or {}).get("new_routes", []) if r.startswith("tool:")],
             "unassessed": _unassessed(a, "access.first_seen_mcp", "path.transition_novelty")}
    if nov["state"] != EA.PRESENT:
        basis["note"] = f"no interaction edge in the session ({nov['state']}: {nov['reason']})"
    return ("NEW_ROUTES_APPEARED" if basis["new_route_count"] > 0 or new_servers else "NO_NEW_ROUTES"), basis


def _id4(a, states):
    depth = _item(a, "interaction_propagation_depth")
    breadth = _present(a, "interaction_propagation_breadth")
    judged = [_item(a, m) for m in ("interaction.subagent_lanes", "interaction.mcp_servers", "insights.outside")]
    beyond = [x for x in judged if x["state"] == EA.PRESENT and x["metric_state"] in states]
    basis = {"depth": depth["value"], "structural_depth": depth["features"].get("structural"),
             "reported_depth": depth["features"].get("reported"),
             "depth_conflict": depth["features"].get("conflict"),
             "breadth": breadth["value"] if breadth else None,
             "children": breadth["features"].get("children") if breadth else None,
             "beyond": [{"metric": x["requirement"], "value": x["value"], "state": x["metric_state"]} for x in beyond],
             "within": [{"metric": x["requirement"], "value": x["value"], "state": x["metric_state"]}
                        for x in judged if x["state"] == EA.PRESENT and x not in beyond],
             "not_judged": ["interaction_propagation_depth: no approved threshold",
                            "interaction_propagation_breadth: no approved threshold"],
             "unassessed": _unassessed(a, "interaction.mcp_servers", "insights.outside")}
    return ("BEYOND_APPROVED_BOUNDS" if beyond else "WITHIN_APPROVED_BOUNDS"), basis


def _rm_answers(rm):
    qs = (((rm or {}).get("cop_specific") or {}).get("evidence_assessment") or {}).get("questions") or {}
    return {q: t.get("answer") for q, t in qs.items()}   # outcome strings


def _delegation_without_identity(rows, lanes):
    """Rows that show delegation happened while no subagent lane carries an agent identity."""
    has, num = EA.signal_metrics.has, EA.signal_metrics.num
    hits = [r for r in rows if "subagent" in (r.get("observable_type") or "") or r.get("is_multi_agent") is True
            or (has(r.get("delegation_depth")) and num(r.get("delegation_depth")) > 0)]
    return [] if lanes else [f"observable:{r['observable_id']}" for r in hits]


def _id5(a, earlier, rm=None, delegation=()):
    cov, unk = _item(a, "telemetry.collector_coverage"), _item(a, "telemetry.unknown_share")
    agr, orphan = _present(a, "telemetry.source_agreement"), _present(a, "interaction_orphaned_events")
    causes = []
    if cov["features"].get("missing"):
        causes.append(f"expected collectors missing: {', '.join(cov['features']['missing'])}")
    if unk["metric_state"] != "NORMAL":
        causes.append(f"telemetry.unknown_share {unk['value']} is {unk['metric_state']}")
    if agr and agr["metric_state"] != "NORMAL":
        causes.append(f"telemetry.source_agreement {agr['value']} is {agr['metric_state']}")
    if orphan and orphan["value"] > 0:
        causes.append(f"{orphan['value']} subagent events have no known parent")
    ids = a["presence"]["fields"].get("agent_id", {})
    if delegation:
        causes.append(f"{len(delegation)} row(s) show delegation but no subagent lane carries an agent_id")
    for qid, ans in earlier.items():
        if ans["outcome"] == INCONCLUSIVE:
            causes.append(f"{qid} could not be answered ({ans['sufficiency']})")
        elif ans["outcome"] in ID_PARTIAL:
            causes.append(f"{qid}: {ans['outcome']}")
        elif ans["basis"].get("depth_conflict"):
            causes.append(f"{qid}: {ans['basis']['depth_conflict']} (reported {ans['basis']['reported_depth']}, "
                          f"linked structure {ans['basis']['structural_depth']})")
    rm_ans = _rm_answers(rm)
    upstream = {"runtime_monitoring": None}
    if rm:
        upstream["runtime_monitoring"] = {"label": rm.get("label"), "verified": rm.get("verified"),
                                          "finding_id": rm.get("finding_id"),
                                          "RM-3": rm_ans.get("RM-3")}
        if rm.get("verified") and rm.get("label") != "SUFFICIENT":
            causes.append(f"Runtime Monitoring reads the telemetry {rm['label']}")
        if rm_ans.get("RM-3") in RM_UNLINKED:
            causes.append(f"Runtime Monitoring RM-3: {rm_ans['RM-3']}")
    basis = {"causes": causes, "collector_coverage": cov["value"], "missing_collectors": cov["features"].get("missing"),
             "unknown_share": unk["value"], "unknown_share_state": unk["metric_state"],
             "source_agreement": agr["value"] if agr else None, "agent_id_presence": ids.get("status"),
             "delegation_without_identity": list(delegation)[:20],
             "upstream": upstream, "unassessed": _unassessed(a, "interaction_unattributed_edges")}
    if not rm:
        basis["limitation"] = "no Runtime Monitoring finding was available to this run"
    return ("RECONSTRUCTION_IMPAIRED" if causes else "RECONSTRUCTION_RELIABLE"), basis


def observe_interaction_discovery(assessments, graph=None, memory=None, known=None, upstream=None, delegation=()):
    """{question_id: assessment} -> {question_id: answer}. memory: the route_record view (established,
    verified_pending); upstream: {cop: published finding}; delegation: refs of rows that show delegation
    with no subagent identity (_delegation_without_identity)."""
    pkg = EA.load_package(ID)
    states = tuple(pkg["propagation_states"])
    rm = (upstream or {}).get("runtime_monitoring")
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf != "SUFFICIENT" and not (qid == "ID-3" and _no_route_to_judge(a)):
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf, "basis": {},
                        "because": a["sufficiency"]["reasons"]}
            continue
        outcome, basis = {"ID-1": lambda: _id1(a, graph), "ID-2": lambda: _id2(a),
                          "ID-3": lambda: _id3(a, memory, known), "ID-4": lambda: _id4(a, states),
                          "ID-5": lambda: _id5(a, {k: out[k] for k in q.get("depends_on", [])}, rm,
                                               delegation)}[qid]()
        if outcome not in q["outcomes"]:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
    return out


def assess_interaction_discovery(rows, session_id, cfg=None, computed_at=None, graph=None, memory=None,
                                 known=None, upstream=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    assessments = {qid: EA.assess(ID, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[ID]}
    lanes = (events.get("interaction.subagent_lanes") or {}).get("value")
    answers = observe_interaction_discovery(assessments, graph, memory, known, upstream,
                                            _delegation_without_identity(srows, lanes))
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain(qid, ans):
    b = ans["basis"]
    if ans["outcome"] == INCONCLUSIVE:
        return "not answered: " + "; ".join(f"{r.get('requirement') or r.get('check')} {r['code']}"
                                            f"{': ' + r['detail'] if r.get('detail') else ''}" for r in ans["because"])
    if qid == "ID-1":
        s = (f"{b['agents']} agent lane(s), {len(b['subagent_lanes'])} of them subagents, {b['tools']} tool(s)"
             + (f", {len(b['mcp_servers'])} MCP server(s)" if b["mcp_servers"] is not None else ""))
        return s + ("; not identified: " + ", ".join(f"{u['metric']} ({u['reason'] or u['state']})"
                                                     for u in b["unidentified"]) if b["unidentified"] else "")
    if qid == "ID-2":
        return f"{b['edges']} delegation/MCP edge(s)" + ("; " + "; ".join(b["gaps"]) if b["gaps"] else
                                                          "; every subagent lane and step is attached")
    if qid == "ID-3":
        if ans["outcome"] == "NEW_ROUTES_APPEARED":
            return ("new for this operator: " + ", ".join(b["new_routes"] + [f"mcp:{s}" for s in b["new_mcp_servers"]
                                                                            if f"mcp:{s}" not in b["new_routes"]])
                    + f" (against {b['earlier_sessions']} earlier sessions)")
        return (b.get("note") or f"every route was seen in {b['earlier_sessions']} earlier sessions") + \
            (f"; known from validated memory: {', '.join(b['known_from_memory'])}" if b["known_from_memory"] else "")
    if qid == "ID-4":
        s = f"depth {b['depth']}, breadth {b['breadth']}"
        if b["beyond"]:
            return s + "; beyond approved bounds: " + ", ".join(f"{x['metric']}={x['value']} ({x['state']})"
                                                               for x in b["beyond"])
        return s + "; every judged measure is within its approved threshold"
    return "; ".join(b["causes"]) if b["causes"] else "no missing identity, link or record affects the network"


# What each label proposes, carried over from cops/interpret.py:_interaction_discovery: base confidence
# before the evidence scale, action, why, and who is told.
ID_LABELS = {
    "NEW_ROUTE": (0.8, "PASS_TO_COP", "a route that did not exist before now does; whether it is permitted is "
                  "Policy Enforcement's question", ("policy_enforcement", "audit_traceability")),
    "PROPAGATING": (0.7, "PASS_TO_COP", "activity reached beyond where it started",
                    ("policy_enforcement", "risk_mitigation")),
    "PATH_PARTIAL": (0.6, "WATCH", "the interaction network cannot be fully reconstructed", ("audit_traceability",)),
    "CONTAINED": (0.8, "NONE", "", ()),
    INCONCLUSIVE: (0.0, "WATCH", "whether a route is new cannot be judged, so containment is not concluded", ()),
}
# Questions whose gaps mean the network itself is incomplete. ID-3 is not one: missing history leaves
# route novelty undecided, not the graph partial.
ID_GRAPH = ("ID-1", "ID-2", "ID-4", "ID-5")


def decide_interaction_discovery(questions, evidence_scale=1.0):
    """The COP decision from the five answers (INTERPRETATION.md).

    A new route or propagation beyond approved bounds is positive evidence and stands on a partial
    graph; an incomplete graph only stops the COP from concluding CONTAINED."""
    from ..interpret import SEV, RANK
    ans = {q: v["answer"] for q, v in questions.items()}
    out = lambda q: ans[q]["outcome"]
    new, spread = out("ID-3") == "NEW_ROUTES_APPEARED", out("ID-4") == "BEYOND_APPROVED_BOUNDS"
    partial = [q for q in ID_GRAPH if out(q) == INCONCLUSIVE or out(q) in ID_PARTIAL]
    undecided = [q for q in ("ID-3",) if out(q) == INCONCLUSIVE]
    held = [l for l, on in (("NEW_ROUTE", new), ("PROPAGATING", spread), ("PATH_PARTIAL", bool(partial)),
                            (INCONCLUSIVE, bool(undecided))) if on]
    label = held[0] if held else "CONTAINED"
    drivers = {"NEW_ROUTE": ["ID-3"], "PROPAGATING": ["ID-4"], "PATH_PARTIAL": partial, INCONCLUSIVE: undecided,
               "CONTAINED": list(ans)}[label]
    base, action, reason, recipients = ID_LABELS[label]
    if label == "PATH_PARTIAL" and out("ID-1") == INCONCLUSIVE and out("ID-2") == INCONCLUSIVE:
        base = 0.2
    states = []
    if label == "NEW_ROUTE":
        fsm = _item(questions["ID-3"]["assessment"], "access.first_seen_mcp")
        states = [fsm["metric_state"]] if ans["ID-3"]["basis"]["new_mcp_servers"] else []
        sev = max([SEV.get(s, "NONE") for s in states] + ["LOW"], key=RANK.get)
    elif label == "PROPAGATING":
        sev = max((SEV[x["state"]] for x in ans["ID-4"]["basis"]["beyond"]), key=RANK.get)
    elif label == "PATH_PARTIAL":
        hot = [x["metric_state"] for q in partial for x in questions[q]["assessment"]["presence"]["items"]
               if x["state"] == EA.PRESENT and x["metric_state"] in SEV]
        sev = max([SEV[s] for s in hot] + ["NONE"], key=RANK.get)
    else:
        sev = "NONE"
    trace = {}
    for qid, q in questions.items():
        a, an = q["assessment"], q["answer"]
        trace[qid] = {
            "question": a["question"], "answer": an["outcome"], "sufficiency": a["sufficiency"]["status"],
            "explanation": _explain(qid, an), "basis": an["basis"],
            "metrics": [{"metric": x["requirement"], "kind": x["kind"], "state": x["state"], "value": x["value"],
                         "metric_state": x["metric_state"]} for x in a["presence"]["items"]],
            "checks_consumed": a["reliability"]["verified_checks"] + a["reliability"]["blocking_checks"],
            "raw_evidence_refs": _refs(a)[:20],
            "sufficiency_reasons": [{k: r.get(k) for k in ("code", "requirement", "check", "detail")}
                                    for r in a["sufficiency"]["reasons"]],
            "not_assessed": an.get("not_assessed", []), "drives_label": qid in drivers}
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items() for x in v["answer"].get("findings", [])]
    if ans["ID-4"].get("basis", {}).get("depth_conflict"):
        contrary.append(f"ID-4: {ans['ID-4']['basis']['depth_conflict']}")
    contrary += [f"{l} also holds" for l in held[1:]]
    return {"label": label, "confidence": round(base * min(1.0, evidence_scale), 3), "severity": sev,
            "action": action, "action_reason": reason, "recipients": list(recipients),
            "drivers": drivers, "also_held": held[1:], "contrary": contrary, "questions": trace}
