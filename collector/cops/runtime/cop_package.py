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


def _missing_inputs(pkg, basis):
    """Each NOT_CALCULATED entry carries its mapping note: what the metric lacks."""
    for u in basis.get("unassessed", []):
        if u.get("state") == EA.NOT_CALCULATED:
            u["missing_inputs"] = " ".join(((pkg["mapping"].get(u["metric"]) or {}).get("note") or "").split())


def _provenance_view(a):
    return [{"outcome": c["outcome"], "detail": c["detail"], "because": c.get("not_executed_because")}
            for c in a["reliability"]["checks"] if c["check"] == EA.UNVERIFIABLE_PROVENANCE]


ID_TEMPORAL = (EA.INCOMPARABLE_TIMESTAMPS, EA.TIMESTAMP_ORDER)
# Metric states in which an approved limit was not evaluated (no basis, or nothing to judge).
NOT_EVALUATED_STATES = ("UNKNOWN", "NOT_APPLICABLE")


def _id1(a, graph=None, links=None):
    ent, lanes, mcp = (_item(a, m) for m in ("interaction_unique_entities", "interaction.subagent_lanes",
                                             "interaction.mcp_servers"))
    tools = sorted({e["to"][len("tool:"):] for e in (graph or {}).get("edges", []) if e["to"].startswith("tool:")})
    basis = {"entities": ent["value"], "agents": ent["features"].get("agents"),
             "tools": ent["features"].get("tools"), "tool_names": tools,
             "subagent_lanes": lanes["features"].get("lanes", []),
             "mcp_servers": (mcp["features"].get("servers") or {}) if mcp["state"] == EA.PRESENT else None,
             "tools_by_lane": (links or {}).get("tools_by_lane", {}),
             "identity": "subagent lanes are named by agent_id; the main agent carries none",
             "provenance": _provenance_view(a),
             "unidentified": _unassessed(a, "interaction.mcp_servers")}
    return ("ENTITIES_PARTIALLY_IDENTIFIED" if basis["unidentified"] else "ENTITIES_IDENTIFIED"), basis


def _id2(a, links=None):
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
    ls = (links or {}).get("links", [])
    timed = [x for x in ls if x["basis"] == "INFERRED"]
    if timed:
        gaps.append(f"{len(timed)} delegation link(s) rest on timing alone, with no recorded parent_agent_id: "
                    + ", ".join(x["link"] for x in timed))
    late = [x for x in ls if x["basis"] == "INFERRED_AGAINST_TIME_ORDER"]
    if late:
        gaps.append(f"{len(late)} delegation link(s) inferred from a handoff recorded after the lane began: "
                    + ", ".join(x["link"] for x in late))
    temporal = {c: _check(a, c) for c in ID_TEMPORAL}
    gaps += [x["detail"] for x in temporal.values() if x["outcome"] == EA.VERIFIED_FAILED]
    basis = {"edges": edges["value"], "by_kind": edges["features"].get("by_kind", {}),
             "edge_list": edges["features"].get("edges", []),
             "graph_completeness": comp["value"] if comp else None,
             "orphaned_events": orphan["value"] if orphan else None,
             "lane_linkage": lane["value"] if lane else None, "turn_linkage": turn["value"] if turn else None,
             "gaps": gaps, "orphan_refs": orphan["evidence_refs"] if orphan else [],
             "links": ls, "recorded_links": [x["link"] for x in ls if x["basis"] == "RECORDED"],
             "inferred_links": [x["link"] for x in ls if x["basis"].startswith("INFERRED")],
             "temporal_checks": {c: {"outcome": x["outcome"], "detail": x["detail"],
                                     "because": x.get("not_executed_because")} for c, x in temporal.items()},
             "provenance": _provenance_view(a)}
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


def _id3(a, memory=None, known=None, links=None):
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
             "unassessed": _unassessed(a, "access.first_seen_mcp", "path.transition_novelty"),
             "continuity": {**(links or {}).get("continuity", {}),
                            "earlier_sessions": nov["features"].get("earlier_sessions")}}
    if nov["state"] != EA.PRESENT:
        basis["note"] = f"no interaction edge in the session ({nov['state']}: {nov['reason']})"
    return ("NEW_ROUTES_APPEARED" if basis["new_route_count"] > 0 or new_servers else "NO_NEW_ROUTES"), basis


def _id4(a, states, measures, observations):
    depth = _item(a, "interaction_propagation_depth")
    breadth = _present(a, "interaction_propagation_breadth")
    judged = [_item(a, m) for m in measures]
    evaluated = [x for x in judged if x["state"] == EA.PRESENT and x["metric_state"] not in NOT_EVALUATED_STATES]
    beyond = [x for x in evaluated if x["metric_state"] in states]
    unevaluated = [x for x in judged if x not in evaluated]
    basis = {"depth": depth["value"], "structural_depth": depth["features"].get("structural"),
             "reported_depth": depth["features"].get("reported"),
             "depth_conflict": depth["features"].get("conflict"),
             "breadth": breadth["value"] if breadth else None,
             "children": breadth["features"].get("children") if breadth else None,
             "beyond": [{"metric": x["requirement"], "value": x["value"], "state": x["metric_state"]} for x in beyond],
             "within": [{"metric": x["requirement"], "value": x["value"], "state": x["metric_state"]}
                        for x in evaluated if x not in beyond],
             "evaluated_limits": [x["requirement"] for x in evaluated],
             "unevaluated_limits": [{"metric": x["requirement"], "state": x["state"], "reason": x["reason"]}
                                    for x in unevaluated],
             "not_evaluated": [{"metric": m, "value": v, "status": "NOT_EVALUATED", "reason": "no approved threshold"}
                               for m, v in (("interaction_propagation_depth", depth["value"]),
                                            ("interaction_propagation_breadth", breadth["value"] if breadth else None))
                               if v is not None],
             "not_judged": ["interaction_propagation_depth: no approved threshold",
                            "interaction_propagation_breadth: no approved threshold"],
             "boundary_observations": [
                 {"metric": m, "value": x["value"], "state": x["metric_state"], "owner": o["owner"], "why": o["why"],
                  "evidence_refs": x.get("evidence_refs", []), "judged_here": False}
                 for m, o in observations.items() for x in [_item(a, m)] if x["state"] == EA.PRESENT],
             "unassessed": _unassessed(a, "interaction.mcp_servers", *observations)}
    if beyond:
        return "BEYOND_APPROVED_BOUNDS", basis
    if unevaluated:
        basis["interpretation_reasons"] = [
            {"code": "NOT_EVALUATED", "requirement": x["requirement"], "check": None,
             "detail": f"approved limit for {x['requirement']} could not be evaluated: {x['reason'] or x['state']}"}
            for x in unevaluated]
        return INCONCLUSIVE, basis
    return "WITHIN_APPROVED_BOUNDS", basis


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


def interaction_links(session):
    """How each interaction link is evidenced. A delegation link is RECORDED when the lane's rows carry
    parent_agent_id; INFERRED when only signal_traces.link_lanes joined the lane to a handoff by time (the
    handoff is an observed event; the parent-child relationship is not); and an
    inferred link whose handoff was recorded after the lane's first step is INFERRED_AGAINST_TIME_ORDER
    (link_lanes falls back to the first handoff). An MCP link is RECORDED from mcp_server on the row."""
    sm, ts = EA.signal_metrics, EA.signal_traces.ts
    rows = sm.unique_rows(session)
    steps = {x["step_id"]: x for x in session.steps}
    out = []
    for lane, lane_steps in sm.sub_lanes(session).items():
        mine = [r for r in rows if sm.lane_of(r) == lane]
        parents = sorted({r["parent_agent_id"] for r in mine if sm.has(r.get("parent_agent_id"))})
        first = lane_steps[0] if lane_steps else {}
        if parents:
            out.append({"link": f"{parents[0]}>{lane}", "kind": "delegation", "basis": "RECORDED",
                        "reason_code": "PARENT_AGENT_ID_RECORDED",
                        "refs": [f"observable:{r['observable_id']}" for r in mine
                                 if sm.has(r.get("parent_agent_id"))][:5]})
        elif sm.linked(lane_steps):
            h = steps.get(first["parent_step_id"]) or {}
            a, b = ts(h.get("started_at")), ts(first.get("started_at"))
            late = bool(a and b and (a.tzinfo is None) == (b.tzinfo is None) and a > b)
            out.append({"link": f"main>{lane}", "kind": "delegation",
                        "basis": "INFERRED_AGAINST_TIME_ORDER" if late else "INFERRED",
                        "relationship": "INFERRED",
                        "reason_code": "FIRST_HANDOFF_FALLBACK" if late else "LATEST_PRIOR_HANDOFF",
                        "handoff": h.get("step_id"), "handoff_at": h.get("started_at"),
                        "handoff_names_agent": h.get("agent_id"),
                        "lane_started_at": first.get("started_at"),
                        "refs": [f"step:{h.get('step_id')}", f"step:{first.get('step_id')}"]})
        else:
            out.append({"link": f"?>{lane}", "kind": "delegation", "basis": "UNLINKED", "reason_code": "NO_HANDOFF",
                        "refs": [f"observable:{r['observable_id']}" for r in mine][:5]})
    mcp = {}
    for r in rows:
        if sm.has(r.get("mcp_server")):
            mcp.setdefault(f"{sm.lane_of(r)}>mcp:{r['mcp_server']}", []).append(f"observable:{r['observable_id']}")
    out += [{"link": k, "kind": "mcp", "basis": "RECORDED", "reason_code": "MCP_SERVER_RECORDED", "refs": v[:5]}
            for k, v in sorted(mcp.items())]
    tools = {}
    for r in session.actions:
        if r.get("tool_name"):
            tools.setdefault(sm.lane_of(r), set()).add(r["tool_name"])
    return {"links": out, "tools_by_lane": {k: sorted(v) for k, v in sorted(tools.items())},
            "continuity": {"compared_with": "earlier sessions of the same operator and tool",
                           "operator_recorded": session.operator is not None, "tool": session.tool}}


def observe_interaction_discovery(assessments, graph=None, memory=None, known=None, upstream=None, delegation=(),
                                  links=None):
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
        outcome, basis = {"ID-1": lambda: _id1(a, graph, links), "ID-2": lambda: _id2(a, links),
                          "ID-3": lambda: _id3(a, memory, known, links),
                          "ID-4": lambda: _id4(a, states, pkg["propagation_measures"],
                                               pkg.get("boundary_observations") or {}),
                          "ID-5": lambda: _id5(a, {k: out[k] for k in q.get("depends_on", [])}, rm,
                                               delegation)}[qid]()
        if outcome not in q["outcomes"] and not (outcome == INCONCLUSIVE and basis.get("interpretation_reasons")):
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        _missing_inputs(pkg, basis)
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE:
            out[qid]["because"] = basis["interpretation_reasons"]
    return out


def assess_interaction_discovery(rows, session_id, cfg=None, computed_at=None, graph=None, memory=None,
                                 known=None, upstream=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    assessments = {qid: EA.assess(ID, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[ID]}
    lanes = (events.get("interaction.subagent_lanes") or {}).get("value")
    session = next(x for x in EA._computed(rows, cfg, computed_at)[1] if x.id == session_id)
    answers = observe_interaction_discovery(assessments, graph, memory, known, upstream,
                                            _delegation_without_identity(srows, lanes), interaction_links(session))
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
        s = f"{b['edges']} delegation/MCP edge(s)" + ("; " + "; ".join(b["gaps"]) if b["gaps"] else
                                                       "; every subagent lane and step is attached")
        inferred = b.get("inferred_links") or []
        return s + (f"; {len(inferred)} delegation link(s) inferred from timing, not recorded: {', '.join(inferred)}"
                    if inferred else "")
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
        if b.get("interpretation_reasons"):
            return s + "; " + "; ".join(x["detail"] for x in b["interpretation_reasons"])
        return (s + "; within the approved limits evaluated (" + ", ".join(b["evaluated_limits"])
                + "); depth and breadth measured, NOT_EVALUATED: no approved threshold")
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


def _question_trace(questions, explain, drivers):
    """Each question's answer with its explanation, metrics, evidence and reasons, as the finding carries it."""
    trace = {}
    for qid, q in questions.items():
        a, an = q["assessment"], q["answer"]
        trace[qid] = {
            "question": a["question"], "answer": an["outcome"], "sufficiency": a["sufficiency"]["status"],
            "explanation": explain(qid, an), "basis": an["basis"],
            "metrics": [{"metric": x["requirement"], "kind": x["kind"], "state": x["state"], "value": x["value"],
                         "metric_state": x["metric_state"]} for x in a["presence"]["items"]],
            "checks_consumed": a["reliability"]["verified_checks"] + a["reliability"]["blocking_checks"],
            "raw_evidence_refs": _refs(a)[:20],
            "sufficiency_reasons": [{k: r.get(k) for k in ("code", "requirement", "check", "detail")}
                                    for r in a["sufficiency"]["reasons"] + (an.get("basis") or {}).get(
                                        "interpretation_reasons", [])],
            "not_assessed": an.get("not_assessed", []), "drives_label": qid in drivers}
    return trace


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
    trace = _question_trace(questions, _explain, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items() for x in v["answer"].get("findings", [])]
    if ans["ID-4"].get("basis", {}).get("depth_conflict"):
        contrary.append(f"ID-4: {ans['ID-4']['basis']['depth_conflict']}")
    contrary += [f"{l} also holds" for l in held[1:]]
    return {"label": label, "confidence": round(base * min(1.0, evidence_scale), 3), "severity": sev,
            "action": action, "action_reason": reason, "recipients": list(recipients),
            "drivers": drivers, "also_held": held[1:], "contrary": contrary, "questions": trace,
            "limitations": _id_limitations(ans)}


def _id_limitations(ans):
    """What the answers rest on without having it (decision.LIMITATION_KINDS)."""
    from . import decision as D
    id2, id4 = (ans[q].get("basis") or {} for q in ("ID-2", "ID-4"))
    out = [D.limitation(D.INFERRED_RELATIONSHIP, "ID-2", x["link"], x["reason_code"],
                        f"parent-child link inferred from the timing of handoff {x.get('handoff')} "
                        f"({x.get('handoff_at')}) and the lane's first step ({x.get('lane_started_at')}); "
                        "no row records parent_agent_id", x["refs"])
           for x in id2.get("links", []) if x.get("relationship") == "INFERRED"]
    done = (EA.VERIFIED_OK, EA.VERIFIED_FAILED)
    out += [D.limitation(D.MISSING_EVIDENCE, q, EA.UNVERIFIABLE_PROVENANCE, p.get("because") or p["outcome"],
                         p["detail"])
            for q in ("ID-1", "ID-2") for p in (ans[q].get("basis") or {}).get("provenance", [])
            if p["outcome"] not in done]
    out += [D.limitation(D.MISSING_EVIDENCE, "ID-2", c, x.get("because") or x["outcome"], x["detail"])
            for c, x in id2.get("temporal_checks", {}).items() if x["outcome"] not in done]
    for m, key in (("interaction_propagation_depth", "depth"), ("interaction_propagation_breadth", "breadth")):
        if id4.get(key) is not None:
            out.append(D.limitation(D.LIMIT_NOT_APPROVED, "ID-4", m, "NOT_EVALUATED",
                                    f"measured {id4[key]}; no threshold is approved, so it is reported, not judged, "
                                    "and cannot make the label PROPAGATING"))
    return out


# ---------------------------------------------------------------- Audit & Traceability

AT = "audit_traceability"
AT_CLEAN = ("ATTRIBUTED", "FULLY_RECONSTRUCTED", "RELIABLE", "TRACEABLE", "DEFENSIBLE")
# Answers that mean an essential causal or provenance link is broken (BROKEN_TRACE).
AT_BROKEN = ("CAUSE_NOT_ATTRIBUTED", "SEQUENCE_BROKEN", "UNRELIABLE", "NOT_TRACEABLE", "NOT_DEFENSIBLE")
# Answers that leave audit information incomplete while the chain still reconstructs (PARTIAL).
AT_GAP = ("RECONSTRUCTED_WITH_GAPS", "DEGRADED", "PARTIALLY_TRACEABLE", "IMPAIRED")
# Metrics whose states give the severity, as the former rules._audit_meaning did: the specs.py metric_keys.
AT_SEVERITY = ("insights.unattributed", "telemetry.turn_linkage", "telemetry.lane_linkage",
               "telemetry.source_agreement")
AT_NOT_BUILT = ("audit_decision_evidence_linkage", "audit_intervention_traceability",
                "audit_policy_version_traceability", "audit_verifier_coverage", "audit_evidence_sufficiency")


def _answers(f):
    return {q: t.get("answer") for q, t in (((f or {}).get("cop_specific") or {}).get("questions") or {}).items()}


def _upstream_view(f, *qids):
    if not f:
        return None
    ans = _answers(f) if f.get("cop") != "runtime_monitoring" else _rm_answers(f)
    return {"label": f.get("label"), "verified": f.get("verified"), "finding_id": f.get("finding_id"),
            **{q: ans.get(q) for q in qids}}


def _actor_without_effects(a, trace):
    """AT-1 is answered when only disk-change attribution is unassessed and actions are on record with no
    operator: the actor is not established whatever the disk changes show."""
    missing = a["sufficiency"]["missing_requirements"]
    return missing == ["insights.unattributed"] and (trace.get("actions_without_actor") or 0) > 0 \
        and not [r for r in a["sufficiency"]["reasons"] if r.get("check")]


def _at1(a, trace):
    un, rate, act, mcp = (_item(a, m) for m in ("insights.unattributed", "audit_actor_attribution_rate",
                                                "audit_action_attribution_rate", "interaction.mcp_servers"))
    no_actor = list(trace.get("effects_without_actor") or [])
    actions = trace.get("actions_without_actor") or 0
    unaccounted = un["value"] if un["state"] == EA.PRESENT else None
    basis = {"actor": trace.get("actor"), "effects_without_actor": no_actor, "actions_without_actor": actions,
             "effects_without_step": unaccounted, "effect_refs": list(trace.get("unattributed_effects") or []),
             "disk_changes": trace.get("disk_changes"), "disk_changes_attributed": trace.get("disk_changes_attributed"),
             "rows_naming_operator": rate["value"], "rows": rate["features"].get("rows"),
             "action_attribution_rate": act["value"] if act["state"] == EA.PRESENT else None,
             "mcp_servers": (mcp["features"].get("servers") or {}) if mcp["state"] == EA.PRESENT else None,
             "not_judged": ["audit_actor_attribution_rate: a row without an operator belongs to its session's "
                            "operator (tools._actor_known); reported, not judged"],
             "unassessed": _unassessed(a, "audit_action_attribution_rate", "interaction.mcp_servers")}
    if un["state"] != EA.PRESENT:
        basis["note"] = f"disk-change attribution not assessed ({un['state']}: {un['reason']})"
    if no_actor or actions:
        return "ACTOR_NOT_ESTABLISHED", basis
    return ("CAUSE_NOT_ATTRIBUTED" if unaccounted else "ATTRIBUTED"), basis


def _at2(a, trace, rm=None, idf=None):
    comp, recon, lanes = (_item(a, m) for m in ("audit_trace_completeness", "audit_execution_reconstruction_rate",
                                                "interaction.subagent_lanes"))
    turn, lane, tn = (_present(a, m) for m in ("telemetry.turn_linkage", "telemetry.lane_linkage",
                                               "path.transition_novelty"))
    broken, gaps, contrary = [], [], []
    orphans = list(trace.get("lanes_without_parent") or [])
    if orphans:
        broken.append(f"{len(orphans)} subagent lane(s) have no parent step: {', '.join(orphans)}")
    steps = comp["features"].get("steps") or 0
    uncited = steps - round(comp["value"] * steps) if steps else 0
    if uncited:
        gaps.append(f"{uncited} of {steps} steps cite no observable")
    placed = recon["features"].get("reconstructable")
    if placed is not None and placed < (recon["features"].get("steps") or 0):
        gaps.append(f"{recon['features']['steps'] - placed} of {recon['features']['steps']} steps cannot be placed "
                    "in a turn and a linked lane")
    if trace.get("steps_without_turn"):
        gaps.append(f"{trace['steps_without_turn']} steps have no turn id; their order still reconstructs the chain")
    unlinked = [x for x in (lane["features"].get("unlinked", []) if lane else []) if x not in orphans]
    if unlinked:
        gaps.append(f"subagent lanes {', '.join(unlinked)} are not linked to a handoff")
    rm3 = _rm_answers(rm).get("RM-3") if rm else None
    if rm3 in RM_UNLINKED:
        gaps.append(f"Runtime Monitoring RM-3: {rm3}")
    id2 = _answers(idf).get("ID-2") if idf else None
    if id2 == "NOT_RECONSTRUCTABLE":
        broken.append("Interaction Discovery ID-2: NOT_RECONSTRUCTABLE")
    elif id2 == "PARTIALLY_RECONSTRUCTED":
        gaps.append("Interaction Discovery ID-2: PARTIALLY_RECONSTRUCTED")
    if broken and id2 == "FULLY_RECONSTRUCTED":
        contrary.append("Interaction Discovery ID-2 answered FULLY_RECONSTRUCTED")
    if broken and rm3 == "ALL_ACTIONS_LINKED":
        contrary.append("Runtime Monitoring RM-3 answered ALL_ACTIONS_LINKED")
    basis = {"steps": trace.get("steps"), "lanes": trace.get("lanes"), "lanes_without_parent": orphans,
             "steps_without_turn": trace.get("steps_without_turn"), "trace_completeness": comp["value"],
             "execution_reconstruction_rate": recon["value"], "subagent_lanes": lanes["features"].get("lanes", []),
             "turn_linkage": turn["value"] if turn else None, "lane_linkage": lane["value"] if lane else None,
             "transition_novelty": tn["value"] if tn else None, "broken": broken, "gaps": gaps,
             "contrary": contrary, "upstream": {"runtime_monitoring": _upstream_view(rm, "RM-3"),
                                                "interaction_discovery": _upstream_view(idf, "ID-2")},
             "unassessed": _unassessed(a, "path.transition_novelty", "audit_reconstruction_confidence")}
    return ("SEQUENCE_BROKEN" if broken else "RECONSTRUCTED_WITH_GAPS" if gaps else "FULLY_RECONSTRUCTED"), basis


AT3_CHECKS = (EA.CONFLICTING_SOURCES, EA.INCOMPARABLE_TIMESTAMPS, EA.TIMESTAMP_ORDER, EA.STALE, EA.INGEST_LATENCY)
# Checks whose failure degrades AT-3; source disagreement is judged by its metric's approved state instead.
AT3_DEGRADING = (EA.INCOMPARABLE_TIMESTAMPS, EA.TIMESTAMP_ORDER, EA.STALE, EA.INGEST_LATENCY)


def _at3(a, states, rm=None):
    from .contracts import ATTENTION_STATES
    cov, unk = _item(a, "telemetry.collector_coverage"), _item(a, "telemetry.unknown_share")
    agr, turn, lane, order = (_present(a, m) for m in ("telemetry.source_agreement", "telemetry.turn_linkage",
                                                       "telemetry.lane_linkage", "audit_event_ordering_consistency"))
    unreliable, degraded = [], []
    if agr and agr["metric_state"] in states:
        unreliable.append(f"sources disagree about what happened: telemetry.source_agreement {agr['value']} is "
                          f"{agr['metric_state']}")
    elif agr and agr["metric_state"] != "NORMAL":
        degraded.append(f"telemetry.source_agreement {agr['value']} is {agr['metric_state']}")
    if cov["features"].get("missing"):
        degraded.append(f"expected collectors missing: {', '.join(cov['features']['missing'])}")
    if unk["metric_state"] != "NORMAL":
        degraded.append(f"telemetry.unknown_share {unk['value']} is {unk['metric_state']}")
    for x in (turn, lane):
        if x and x["metric_state"] in ATTENTION_STATES:
            degraded.append(f"{x['requirement']} {x['value']} is {x['metric_state']}")
    checks = {c: _check(a, c) for c in AT3_CHECKS}
    for c in AT3_DEGRADING:
        if checks[c]["outcome"] == EA.VERIFIED_FAILED:
            degraded.append(checks[c]["detail"])
    if order and order["features"].get("out_of_order"):
        degraded.append(f"{order['features']['out_of_order']} of {order['features']['pairs']} rows are out of "
                        "sequence order in time")
    if rm and rm.get("verified") and rm.get("label") != "SUFFICIENT":
        degraded.append(f"Runtime Monitoring reads the telemetry {rm['label']}")
    basis = {"collector_coverage": cov["value"], "missing_collectors": cov["features"].get("missing"),
             "unknown_share": unk["value"], "unknown_share_state": unk["metric_state"],
             "source_agreement": agr["value"] if agr else None, "turn_linkage": turn["value"] if turn else None,
             "lane_linkage": lane["value"] if lane else None, "ordering_consistency": order["value"] if order else None,
             "checks": {c: {"outcome": x["outcome"], "detail": x["detail"]} for c, x in checks.items()},
             "unreliable": unreliable, "degraded": degraded,
             "upstream": {"runtime_monitoring": _upstream_view(rm, "RM-3")},
             "unassessed": _unassessed(a, "audit_timestamp_integrity", "audit_event_ordering_consistency")
             + [{"check": c, "state": x["outcome"], "reason": x["detail"], "because": x.get("not_executed_because")}
                for c, x in checks.items() if x["outcome"] not in (EA.VERIFIED_OK, EA.VERIFIED_FAILED)]}
    return ("UNRELIABLE" if unreliable else "DEGRADED" if degraded else "RELIABLE"), basis


def _at4(a, linkage):
    mel = _present(a, "audit_metric_evidence_linkage")
    decisions = list((linkage or {}).get("decisions") or [])
    unresolvable = [d for d in decisions if d["refs"] and d["resolvable"] < d["refs"]]
    gaps = []
    if mel and mel["value"] < 1:
        bad = mel["features"].get("unresolved") or []
        gaps.append(f"{len(bad)} of {mel['features'].get('refs')} metric evidence refs do not resolve to a row in "
                    "the record")
    basis = {"decisions": decisions,
             "unresolvable": [{k: d[k] for k in ("cop", "refs", "resolvable", "unresolvable")} for d in unresolvable],
             "metric_evidence_linkage": mel["value"] if mel else None,
             "unresolved_metric_refs": (mel["features"].get("unresolved") or []) if mel else [], "gaps": gaps,
             "not_traced": [f"{m}: not built" for m in AT_NOT_BUILT],
             "unassessed": _unassessed(a, "audit_metric_evidence_linkage", *AT_NOT_BUILT)}
    if "missing_dependency" in (linkage or {}) or linkage is None:
        basis["limitation"] = "the decision linkage could not be checked"
    elif not decisions:
        basis["limitation"] = "no upstream decision was available to trace"
    return ("NOT_TRACEABLE" if unresolvable else "PARTIALLY_TRACEABLE" if gaps else "TRACEABLE"), basis


def at_gap_codes(answers):
    """Stable codes for the gaps a session's answers show: what a trace_gap record remembers."""
    return sorted(f"{q}:{v['outcome']}" for q, v in answers.items() if v["outcome"] not in AT_CLEAN)


def _trace_memory(memory):
    def gaps(bucket):
        return {g: rec["record_id"] for rec in (memory or {}).get(bucket, [])
                for g in (rec.get("payload") or {}).get("gaps", [])}
    return gaps("established"), gaps("verified_pending")


def _at5(a, earlier, upstream=None, memory=None):
    from .rules import SUBSTANTIVE
    upstream = upstream or {}
    own_broken, own_impaired, from_questions = [], [], []
    for qid, ans in earlier.items():
        if ans["outcome"] == INCONCLUSIVE:
            from_questions.append(f"{qid} could not be answered ({ans['sufficiency']})")
        elif ans["outcome"] not in AT_CLEAN:
            from_questions.append(f"{qid}: {ans['outcome']}")
    blocked = sorted(k for k, f in upstream.items() if f.get("verified") and f.get("status") == SUBSTANTIVE
                     and f.get("decision_status") == "BLOCKED_BY_EVIDENCE")
    if blocked:
        own_broken.append(f"{', '.join(blocked)} could not judge an action that is in the record")
    act = _check(a, EA.CONFLICTING_ACTION_SOURCES)
    if act["outcome"] == EA.VERIFIED_FAILED:
        own_impaired.append(f"conflicting action records: {act['detail']}")
    me, ur, pv, cr = (_present(a, m) for m in ("audit_missing_event_rate", "audit_unverifiable_record_rate",
                                               "audit_source_provenance_completeness", "audit_conflicting_record_rate"))
    if me and me["value"]:
        own_impaired.append(f"{me['features']['without_result']} of {me['features']['requests']} calls have no result")
    if ur and ur["value"]:
        own_impaired.append(f"{ur['value']:.1%} of rows are inferred and name no source rows")
    if pv and pv["features"]["with_collector"] < pv["features"]["rows"]:
        own_impaired.append(f"{pv['features']['rows'] - pv['features']['with_collector']} of {pv['features']['rows']} "
                            "rows name no collector")
    idf = upstream.get("interaction_discovery")
    id5 = _answers(idf).get("ID-5") if idf and idf.get("verified") else None
    if id5 == "RECONSTRUCTION_IMPAIRED":
        own_impaired.append("Interaction Discovery ID-5: RECONSTRUCTION_IMPAIRED")
    prov = [c for c in a["reliability"]["checks"] if c["check"] == EA.UNVERIFIABLE_PROVENANCE]
    established, pending = _trace_memory(memory)
    codes = at_gap_codes(earlier)
    recurring = sorted(g for g in codes if g in established)
    basis = {"own_broken": own_broken, "own_impaired": own_impaired, "from_questions": from_questions,
             "blocked_upstream": blocked, "conflicting_records": cr["value"] if cr else None,
             "missing_event_rate": me["value"] if me else None, "unverifiable_record_rate": ur["value"] if ur else None,
             "provenance": [{"outcome": c["outcome"], "detail": c["detail"]} for c in prov],
             "gap_codes": codes, "recurring": recurring, "memory_records": sorted({established[g] for g in recurring}),
             "pending_in_memory": sorted(g for g in codes if g in pending and g not in established),
             "memory_supplied": memory is not None,
             "unassessed": _unassessed(a, "audit_missing_event_rate", "audit_conflicting_record_rate",
                                       "audit_unverifiable_record_rate", "audit_source_provenance_completeness",
                                       "audit_raw_reference_resolution_rate", "audit_evidence_integrity",
                                       "audit_evidence_retention_coverage")}
    broken_q = [x for x in from_questions if x.split(": ")[-1] in AT_BROKEN + ("ACTOR_NOT_ESTABLISHED",)]
    if own_broken or broken_q:
        return "NOT_DEFENSIBLE", basis
    return ("IMPAIRED" if own_impaired or from_questions else "DEFENSIBLE"), basis


def observe_audit_traceability(assessments, trace=None, linkage=None, memory=None, upstream=None):
    """{question_id: assessment} -> {question_id: answer}. trace: tools.t_reconstruct_trace; linkage:
    tools.t_check_decision_linkage; memory: the trace_gap view (established, verified_pending); upstream:
    {cop: published finding}."""
    from .rules import verified
    pkg = EA.load_package(AT)
    states = tuple(pkg["unreliable_states"])
    trace = trace or {}
    rm, idf = verified(upstream, "runtime_monitoring"), verified(upstream, "interaction_discovery")
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf != "SUFFICIENT" and not (qid == "AT-1" and _actor_without_effects(a, trace)):
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf, "basis": {},
                        "because": a["sufficiency"]["reasons"]}
            continue
        outcome, basis = {"AT-1": lambda: _at1(a, trace), "AT-2": lambda: _at2(a, trace, rm, idf),
                          "AT-3": lambda: _at3(a, states, rm), "AT-4": lambda: _at4(a, linkage),
                          "AT-5": lambda: _at5(a, {k: out[k] for k in q.get("depends_on", [])}, upstream,
                                               memory)}[qid]()
        if outcome not in q["outcomes"]:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        _missing_inputs(pkg, basis)
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
    return out


def assess_audit_traceability(rows, session_id, cfg=None, computed_at=None, trace=None, linkage=None,
                              memory=None, upstream=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    assessments = {qid: EA.assess(AT, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[AT]}
    answers = observe_audit_traceability(assessments, trace, linkage, memory, upstream)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_at(qid, ans):
    b = ans["basis"]
    if ans["outcome"] == INCONCLUSIVE:
        return _explain(qid, ans)
    if qid == "AT-1":
        actor = b["actor"] or {}
        if ans["outcome"] == "ACTOR_NOT_ESTABLISHED":
            n = len(b["effects_without_actor"]) + b["actions_without_actor"]
            return f"{n} actions or effects carry no session or operator, so the responsible actor cannot be " \
                "established"
        s = f"actor {actor.get('operator')} (session {actor.get('session_id')})"
        if ans["outcome"] == "CAUSE_NOT_ATTRIBUTED":
            return (f"{b['effects_without_step']} of {b['disk_changes']} disk changes are attributed to no step; "
                    f"the {s} is known, the causal link is not")
        return s + " is recorded and every disk change is attributed to a step" + \
            (f"; {b['note']}" if b.get("note") else "")
    if qid == "AT-2":
        if b["broken"] or b["gaps"]:
            return "; ".join(b["broken"] + b["gaps"])
        return f"{b['steps']} steps, each in a turn and a linked lane"
    if qid == "AT-3":
        found = b["unreliable"] + b["degraded"]
        return "; ".join(found) if found else "sources agree, every expected collector reported, links hold"
    if qid == "AT-4":
        if b["unresolvable"]:
            return "; ".join(f"{d['cop']} cites {d['refs'] - d['resolvable']} of {d['refs']} evidence refs that do "
                             "not resolve to a row in this case" for d in b["unresolvable"])
        if b["gaps"]:
            return "; ".join(b["gaps"])
        return b.get("limitation") or f"every evidence ref of {len(b['decisions'])} upstream decision(s) resolves"
    causes = b["own_broken"] + b["own_impaired"] + b["from_questions"]
    s = "; ".join(causes) if causes else "no missing, conflicting or unverifiable record stands in the way"
    return s + (f"; recurring, per validated memory: {', '.join(b['recurring'])}" if b["recurring"] else "")


# What each label proposes, carried over from cops/interpret.py:_audit_traceability and the former rules._audit_meaning:
# base confidence before the evidence scale, action, why, and who is told.
AT_LABELS = {
    "UNATTRIBUTABLE": (0.85, "ALERT", "the responsible actor cannot be established", ("governance_architecture",)),
    "BROKEN_TRACE": (0.8, "ALERT", "an essential causal or provenance link is broken", ("governance_architecture",)),
    "PARTIAL": (0.75, "WATCH", "incomplete audit information; the essential chain is still reconstructable", ()),
    "COMPLETE": (0.85, "NONE", "", ()),
    INCONCLUSIVE: (0.0, "WATCH", "who acted or what happened could not be judged, so completeness is not concluded",
                   ()),
}
# Questions without which who acted and what happened are unknown.
AT_CORE = ("AT-1", "AT-2")


def decide_audit_traceability(questions, evidence_scale=1.0):
    """The COP decision from the five answers (INTERPRETATION.md). Labels keep their 2026-11-04 meanings:
    UNATTRIBUTABLE when the actor cannot be established, BROKEN_TRACE when an essential link is broken,
    PARTIAL when information is incomplete but the chain reconstructs. Severity never chooses the label."""
    from ..interpret import SEV, RANK
    ans = {q: v["answer"] for q, v in questions.items()}
    out = lambda q: ans[q]["outcome"]                                       # noqa: E731
    five = ans["AT-5"]["basis"] if out("AT-5") != INCONCLUSIVE else {}
    unattr = out("AT-1") == "ACTOR_NOT_ESTABLISHED"
    broken = [q for q in ("AT-1", "AT-2", "AT-3", "AT-4") if out(q) in AT_BROKEN] + \
        (["AT-5"] if five.get("own_broken") else [])
    undecided = [q for q in AT_CORE if out(q) == INCONCLUSIVE]
    partial = [q for q in ("AT-2", "AT-3", "AT-4") if out(q) in AT_GAP or out(q) == INCONCLUSIVE and q not in AT_CORE] \
        + (["AT-5"] if five.get("own_impaired") or out("AT-5") == INCONCLUSIVE else [])
    held = [lab for lab, on in (("UNATTRIBUTABLE", unattr), ("BROKEN_TRACE", bool(broken)),
                                (INCONCLUSIVE, bool(undecided)), ("PARTIAL", bool(partial))) if on]
    label = held[0] if held else "COMPLETE"
    drivers = {"UNATTRIBUTABLE": ["AT-1"], "BROKEN_TRACE": broken, INCONCLUSIVE: undecided, "PARTIAL": partial,
               "COMPLETE": list(ans)}[label]
    base, action, reason, recipients = AT_LABELS[label]
    if label == "BROKEN_TRACE":
        reason += ": " + "; ".join(_explain_at(q, ans[q]) for q in broken)
    if label in ("UNATTRIBUTABLE", "BROKEN_TRACE", "PARTIAL"):
        states = [x["metric_state"] for q in questions.values() for x in q["assessment"]["presence"]["items"]
                  if x["requirement"] in AT_SEVERITY and x["state"] == EA.PRESENT]
        floor = ["MEDIUM"] if label == "BROKEN_TRACE" and "AT-4" in broken else []
        sev = max([SEV.get(s, "NONE") for s in states] + floor + ["NONE"], key=RANK.get)
    else:
        sev = "NONE"
    trace = _question_trace(questions, _explain_at, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    contrary += [f"AT-2: {c}" for c in ans["AT-2"]["basis"].get("contrary", [])] if out("AT-2") != INCONCLUSIVE else []
    contrary += [f"{lab} also holds" for lab in held[1:]]
    return {"label": label, "confidence": round(base * min(1.0, evidence_scale), 3), "severity": sev,
            "action": action, "action_reason": reason, "recipients": list(recipients),
            "drivers": drivers, "also_held": held[1:], "contrary": contrary, "questions": trace,
            "gap_codes": at_gap_codes(ans)}


# ---------------------------------------------------------------- Policy Enforcement

PE = "policy_enforcement"
PE_RESOLUTION = "deny beats require_approval beats allow; no rule is not permission (tools.evaluate_operation)"
PE_PARTIAL = ("POLICY_PARTIALLY_APPLIES", "BOUNDARY_NOT_DEFINED", "NEW_ROUTES_UNDECIDED")
PE_DENY = ("OUTSIDE_AUTHORIZED_BOUNDARIES", "NEW_ROUTES_NOT_PERMITTED")
NO_POLICY = {"code": EA.MISSING_REFERENCE, "requirement": "policy_store", "check": None,
             "detail": "no approved policy version is in force, so no rule was evaluated"}
NO_ENTITLEMENTS = {"code": EA.MISSING_REFERENCE, "requirement": "entitlements", "check": None,
                   "detail": "an MCP server was used and no entitlement record was supplied"}


def _pe_store(policy):
    """The policy versions supplied and which one is in force (tools.latest_policy)."""
    store, v = policy.get("store") or {}, policy.get("in_force")
    versions = [{"version": x.get("version"), "effective": x.get("effective"), "approved": bool(x.get("approved_by"))}
                for x in store.get("versions", [])]
    return {"supplied": bool(store), "synthetic": bool(store.get("synthetic")),
            "in_force": None if v is None else {"version": v.get("version"), "effective": v.get("effective"),
                                                "approved_by": v.get("approved_by")},
            "not_in_force": [dict(x, why="superseded by a later approved version" if x["approved"] else "not approved")
                             for x in versions if not v or x["version"] != v.get("version")]}


def _pe_ops(policy):
    """Observed and proposed operations as evaluated against the version in force, each with its rules' effects."""
    ev, v = policy.get("evaluation") or {}, policy.get("in_force") or {}
    if "missing_dependency" in ev or not v:
        return None
    effect = {r["id"]: r.get("effect") for r in v.get("rules", [])}
    ops = [dict(o, stage="executed" if not o["blocked_by_hook"] else "stopped_by_hook") for o in ev["observed"]]
    ops += [dict(o, stage="proposed") for o in ev["proposed"]]
    for o in ops:
        o["effects"] = {r: effect.get(r) for r in o["rules"]}
    return ops


def _pe_view(o):
    return {k: o.get(k) for k in ("ref", "action", "resource", "decision", "rules", "stage")}


def _pe_entitlements(policy):
    ent = policy.get("entitlements") or {}
    used = sorted({o["resource"] for o in (policy.get("evaluation") or {}).get("observed", []) if o["action"] == "mcp"})
    supplied = "missing_dependency" not in ent and bool(ent)
    return {"applies": bool(used), "supplied": supplied, "mcp_servers_used": used,
            "entitled": ent.get("entitled_mcp_servers") if supplied else None,
            "used_not_entitled": ent.get("used_not_entitled", []) if supplied else None}


def _pe_observed(a, states):
    return [{"metric": x["requirement"], "value": x["value"], "state": x["metric_state"],
             "policy_relevant": x["metric_state"] in states, "evidence_refs": x["evidence_refs"][:10]}
            for x in a["presence"]["items"] if x["state"] == EA.PRESENT]


def _pe1(a, policy):
    ops, ent = _pe_ops(policy), _pe_entitlements(policy)
    basis = {"policy": _pe_store(policy), "entitlements": ent, "resolution": PE_RESOLUTION,
             "unassessed": _unassessed(a, "policy_applicability_coverage", "entitlement_mismatch_rate",
                                       "policy_exception_rate")}
    if ops is None:
        basis["interpretation_reasons"] = [NO_POLICY]
        return INCONCLUSIVE, basis
    counts = {}
    for o in ops:
        counts[o["decision"]] = counts.get(o["decision"], 0) + 1
    basis.update(operations=len(ops), by_decision=counts,
                 no_rule=[_pe_view(o) for o in ops if o["decision"] == "NO_RULE"][:20],
                 conflicts=[{**_pe_view(o), "effects": o["effects"], "resolved_as": o["decision"]}
                            for o in ops if len(set(o["effects"].values())) > 1][:20])
    if ent["applies"] and not ent["supplied"]:
        basis["interpretation_reasons"] = [NO_ENTITLEMENTS]
        return INCONCLUSIVE, basis
    return ("POLICY_PARTIALLY_APPLIES" if basis["no_rule"] else "POLICY_APPLIES"), basis


def _pe2(a, states, policy):
    ops, ent = _pe_ops(policy), _pe_entitlements(policy)
    observed = _pe_observed(a, states)
    basis = {"observed": observed, "policy_relevant": [x["metric"] for x in observed if x["policy_relevant"]],
             "violations": [], "attempts_stopped": [], "permitted_relevant": []}
    if ops is None:
        basis["note"] = ("observed conditions are policy-relevant at most; no rule was evaluated, so no violation "
                         "is claimed")
        basis["interpretation_reasons"] = [NO_POLICY]
        return INCONCLUSIVE, basis
    by_ref = {o["ref"]: o for o in ops if o.get("ref")}
    basis["violations"] = [_pe_view(o) for o in ops if o["decision"] == "DENY" and o["stage"] != "stopped_by_hook"]
    basis["violations"] += [{"action": "mcp", "resource": s, "decision": "NOT_ENTITLED", "rules": [],
                             "stage": "executed", "ref": None} for s in ent["used_not_entitled"] or []]
    basis["attempts_stopped"] = [_pe_view(o) for o in ops if o["stage"] == "stopped_by_hook"]
    basis["permitted_relevant"] = [{"metric": x["metric"], "ref": r, "decision": by_ref[r]["decision"],
                                    "rules": by_ref[r]["rules"]}
                                   for x in observed if x["policy_relevant"] for r in x["evidence_refs"]
                                   if r in by_ref and by_ref[r]["decision"] in ("ALLOW", "REQUIRE_APPROVAL")]
    basis["no_rule"] = [_pe_view(o) for o in ops if o["decision"] == "NO_RULE" and o["stage"] != "stopped_by_hook"][:20]
    if basis["violations"]:
        return "OUTSIDE_AUTHORIZED_BOUNDARIES", basis
    if ent["applies"] and not ent["supplied"]:
        basis["interpretation_reasons"] = [NO_ENTITLEMENTS]
        return INCONCLUSIVE, basis
    return ("BOUNDARY_NOT_DEFINED" if basis["no_rule"] else "WITHIN_AUTHORIZED_BOUNDARIES"), basis


def _pe3(a, states, policy):
    ops, ev = _pe_ops(policy), policy.get("evaluation") or {}
    observed = _pe_observed(a, states)
    link = _item(a, "approval_action_linkage")
    basis = {"observed": observed, "policy_relevant": [x["metric"] for x in observed if x["policy_relevant"]],
             "approval_action_linkage": link["value"], "unassessed": _unassessed(a, "approval_requirement_compliance")}
    if ops is None:
        basis["note"] = "whether approval was required is the policy's to say; no rule was evaluated"
        basis["interpretation_reasons"] = [NO_POLICY]
        return INCONCLUSIVE, basis
    need = [_pe_view(o) for o in ops if o["decision"] == "REQUIRE_APPROVAL"]
    basis.update(required=need, approval_recorded=ev.get("approval_recorded"),
                 approval_scope="session: a permission_decision allow or approved anywhere in the session "
                                "(tools.t_evaluate_session_operations)")
    if not need:
        return "NO_APPROVAL_REQUIRED", basis
    return ("APPROVAL_OBTAINED" if ev.get("approval_recorded") else "APPROVAL_MISSING"), basis


def _pe_routes(a, upstream):
    """New routes to evaluate: Interaction Discovery's verified ID-3 routes, or access.first_seen_mcp."""
    from .rules import verified
    idf = verified(upstream, "interaction_discovery")
    if idf:
        q3 = ((idf.get("cop_specific") or {}).get("questions") or {}).get("ID-3") or {}
        b = q3.get("basis") or {}
        named = list(b.get("new_routes") or [])
        routes = named + [f"main>mcp:{s}" for s in b.get("new_mcp_servers") or []
                          if not any(r.endswith(f">mcp:{s}") for r in named)]
        if not routes and idf["label"] == "NEW_ROUTE":
            routes = [f"main>{r}" for r in (idf.get("cop_specific") or {}).get("new_routes") or []
                      if r.startswith("mcp:")]
        return {"source": "interaction_discovery", "finding_id": idf.get("finding_id"), "label": idf["label"],
                "id3": q3.get("answer")}, routes
    fsm = _present(a, "access.first_seen_mcp")
    if fsm:
        return {"source": "access.first_seen_mcp", "value": fsm["value"]}, \
            [f"main>mcp:{s}" for s in fsm["features"].get("new_servers", [])]
    return {"source": None}, []


def _pe4(a, policy, upstream, memory):
    from .tools import evaluate_operation
    src, routes = _pe_routes(a, upstream)
    ent, v = _pe_entitlements(policy), policy.get("in_force")
    blind = [x["requirement"] for x in a["presence"]["items"]
             if x["requirement"] in ("access.first_seen_mcp", "interaction.mcp_servers") and x["state"] != EA.PRESENT]
    known = {r: x["record_id"] for x in (memory or {}).get("established", []) for r in x["payload"].get("routes", [])}
    basis = {"routes_from": src, "routes": [], "mcp_unobservable": blind,
             "unassessed": _unassessed(a, "unapproved_tool_usage", "unapproved_mcp_access",
                                       "unauthorized_interaction_route_count")}
    if not routes and src.get("label") == "NEW_ROUTE":
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "interaction_discovery",
                                            "check": None, "detail": "Interaction Discovery reports NEW_ROUTE but "
                                                                     "names no route to evaluate"}]
        return INCONCLUSIVE, basis
    if not routes:
        if blind:
            basis["interpretation_reasons"] = [{"code": EA.NOT_OBSERVED, "requirement": m, "check": None,
                                                "detail": f"{m} has no basis, so a new MCP route cannot be ruled out"}
                                               for m in blind]
            return INCONCLUSIVE, basis
        return "NO_NEW_ROUTES", basis
    if v is None:
        basis["routes"] = [{"route": r} for r in routes]
        basis["interpretation_reasons"] = [NO_POLICY]
        return INCONCLUSIVE, basis
    for r in routes:
        target = r.partition(">")[2]
        entry = {"route": r, "established_in_memory": known.get(r) or known.get(target)}
        if target.startswith("mcp:"):
            s = target[4:]
            entry.update(evaluate_operation(v, {"action": "mcp", "resource": s}, policy.get("agent", "*")))
            entry["entitled"] = None if not ent["supplied"] else s in (ent["entitled"] or [])
        else:
            entry.update(decision="NOT_EVALUATED", rules=[], entitled=None,
                         reason="the policy model has no operation for a delegation route")
        basis["routes"].append(entry)
    rs = basis["routes"]
    if any(x["decision"] == "DENY" or x["entitled"] is False for x in rs):
        return "NEW_ROUTES_NOT_PERMITTED", basis
    if any(x["decision"] in ("NO_RULE", "NOT_EVALUATED") for x in rs):
        return "NEW_ROUTES_UNDECIDED", basis
    if any(x["entitled"] is None and x["route"].partition(">")[2].startswith("mcp:") for x in rs):
        basis["interpretation_reasons"] = [NO_ENTITLEMENTS]
        return INCONCLUSIVE, basis
    return "NEW_ROUTES_PERMITTED", basis


PE_HELD = (("VIOLATION", "DENY"), ("APPROVAL_REQUIRED", "REQUIRE_APPROVAL"), ("UNCERTAIN", "UNDECIDED"),
           (INCONCLUSIVE, INCONCLUSIVE))


def _pe_held(ans, rm=None):
    """Every decision the answers support, in the label order, with the questions behind each."""
    o = lambda q: (ans.get(q) or {}).get("outcome")                                     # noqa: E731
    on = {"DENY": [q for q in ("PE-2", "PE-4") if o(q) in PE_DENY],
          "REQUIRE_APPROVAL": [q for q in ("PE-3",) if o(q) == "APPROVAL_MISSING"],
          "UNDECIDED": [q for q in ("PE-1", "PE-2", "PE-4") if o(q) in PE_PARTIAL],
          INCONCLUSIVE: [q for q in ("PE-1", "PE-2", "PE-3", "PE-4", "PE-5") if q in ans and o(q) == INCONCLUSIVE]}
    if rm and rm.get("verified") and rm.get("label") == "INSUFFICIENT":
        on[INCONCLUSIVE].append("runtime_monitoring")
    return [(d, on[d]) for _, d in PE_HELD if on[d]]


def _pe5(a, deps, upstream, memory, policy):
    from .rules import verified
    rm = verified(upstream, "runtime_monitoring")
    held = _pe_held(deps, rm)
    v = policy.get("in_force") or {}
    ops = _pe_ops(policy) or []
    mine = {(o["action"], o["resource"]) for o in ops}
    precedents = []
    for x in (memory or {}).get("established", []):
        p = x["payload"]
        same = [d for d in p.get("decisions", []) if (d.get("action"), d.get("resource")) in mine]
        if same:
            precedents.append({"record_id": x["record_id"], "label": p.get("label"),
                               "policy_version": p.get("policy_version"), "operations": same[:5],
                               "status": "SAME_POLICY" if p.get("policy_version") == v.get("version")
                               else "SUPERSEDED"})
    basis = {"held": [{"decision": d, "questions": qs} for d, qs in held], "resolution": PE_RESOLUTION,
             "upstream": {"runtime_monitoring": None if not rm else {"label": rm["label"],
                                                                     "finding_id": rm.get("finding_id")}},
             "precedents": precedents, "observed": _pe_observed(a, ()),
             "unassessed": _unassessed(a, "insights.unattributed", "policy_evidence_sufficiency")}
    if not rm:
        basis["limitation"] = "no Runtime Monitoring finding was available to this run"
    if not held:
        return "ALLOW", basis
    if held[0][0] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [
            {"code": "DEPENDS_ON_INCONCLUSIVE", "requirement": q, "check": None,
             "detail": "Runtime Monitoring reads the telemetry INSUFFICIENT" if q == "runtime_monitoring"
             else f"{q} could not be answered"} for q in held[0][1]]
        return INCONCLUSIVE, basis
    return held[0][0], basis


def observe_policy_enforcement(assessments, policy, upstream=None, memory=None):
    """{question_id: assessment} -> {question_id: answer}. policy: {store, in_force, evaluation, entitlements,
    agent} as tools.t_assess_policy_questions supplies them; memory: {record_type: (established,
    verified_pending)} for enforcement_record and route_record."""
    pkg = EA.load_package(PE)
    states = tuple(pkg["relevance_states"])
    memory = memory or {}
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf != "SUFFICIENT":
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf, "basis": {},
                        "because": a["sufficiency"]["reasons"]}
            continue
        outcome, basis = {"PE-1": lambda: _pe1(a, policy), "PE-2": lambda: _pe2(a, states, policy),
                          "PE-3": lambda: _pe3(a, states, policy),
                          "PE-4": lambda: _pe4(a, policy, upstream, memory.get("route_record")),
                          "PE-5": lambda: _pe5(a, {k: out[k] for k in q.get("depends_on", [])}, upstream,
                                               memory.get("enforcement_record"), policy)}[qid]()
        if outcome not in q["outcomes"] and not (outcome == INCONCLUSIVE and basis.get("interpretation_reasons")):
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        _missing_inputs(pkg, basis)
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE:
            out[qid]["because"] = basis["interpretation_reasons"]
    return out


def assess_policy_enforcement(rows, session_id, cfg=None, computed_at=None, policy=None, upstream=None,
                              memory=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    assessments = {qid: EA.assess(PE, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[PE]}
    answers = observe_policy_enforcement(assessments, policy or {}, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _ops_text(xs):
    return ", ".join(f"{x['action']} {x['resource'] or '(no resource)'} ({x['decision']}"
                     + (f" by {', '.join(x['rules'])}" if x.get("rules") else "") + ")" for x in xs[:5])


def _explain_pe(qid, ans):
    b = ans["basis"]
    if ans["outcome"] == INCONCLUSIVE:
        s = _explain(qid, ans)
        rel = b.get("policy_relevant") if b else None
        return s + (f"; observed and policy-relevant, not judged: {', '.join(rel)}" if rel else "")
    if qid == "PE-1":
        s = f"{b['operations']} operations evaluated against {b['policy']['in_force']['version']}: " + \
            ", ".join(f"{n} {d}" for d, n in sorted(b["by_decision"].items()))
        if b["no_rule"]:
            s += "; no rule for " + _ops_text(b["no_rule"])
        if b["conflicts"]:
            s += f"; {len(b['conflicts'])} operations matched rules with different effects, resolved as {PE_RESOLUTION}"
        if b["entitlements"]["applies"]:
            s += f"; entitlements apply to {', '.join(b['entitlements']['mcp_servers_used'])}"
        return s
    if qid == "PE-2":
        if b["violations"]:
            return "outside its authorized boundaries: " + _ops_text(b["violations"])
        s = ("no rule defines the boundary for " + _ops_text(b["no_rule"])) if b["no_rule"] else \
            "every executed and proposed operation is within the policy in force"
        if b["permitted_relevant"]:
            s += "; observed policy-relevant and permitted: " + ", ".join(
                f"{x['metric']} {x['ref']} ({x['decision']} by {', '.join(x['rules'])})"
                for x in b["permitted_relevant"][:5])
        if b["attempts_stopped"]:
            s += f"; {len(b['attempts_stopped'])} denied attempt(s) stopped by a hook and not executed: " + \
                _ops_text(b["attempts_stopped"])
        return s
    if qid == "PE-3":
        if ans["outcome"] == "NO_APPROVAL_REQUIRED":
            return "no operation requires approval under the policy in force"
        return f"approval required for {_ops_text(b['required'])}; " + \
            ("approval recorded in the session" if b["approval_recorded"] else "no approval recorded")
    if qid == "PE-4":
        if not b["routes"]:
            return "no new tool, MCP server or interaction route"
        return f"new routes from {b['routes_from']['source']}: " + ", ".join(
            f"{x['route']} {x['decision']}" + ("" if x["entitled"] is None else f", entitled {x['entitled']}")
            for x in b["routes"])
    s = "; ".join(f"{h['decision']} from {', '.join(h['questions'])}" for h in b["held"]) or \
        "every question found the operations permitted"
    return s + "".join(f"; precedent {p['record_id']} {p['label']} under {p['policy_version']} ({p['status']})"
                       for p in b["precedents"])


# What each label proposes, carried over from rules.policy_enforcement's evaluated-policy path:
# confidence, action, severity, why.
PE_LABELS = {
    "VIOLATION": (0.85, "ALERT", "HIGH", "an executed operation matched a deny rule in the policy in force"),
    "APPROVAL_REQUIRED": (0.85, "REQUEST_APPROVAL", "MEDIUM",
                          "the policy requires approval for an operation and none is recorded"),
    "UNCERTAIN": (0.4, "WATCH", "LOW", "operations with no matching rule: not shown to be permitted"),
    "ALLOWED": (0.85, "NONE", "NONE", ""),
    INCONCLUSIVE: (0.0, "WATCH", "NONE", "the policy evidence needed to decide is missing, so permission is not "
                                         "concluded"),
}


def decide_policy_enforcement(questions):
    """The COP decision from the five answers (INTERPRETATION.md): PE-5's answer when it was given, otherwise
    the same order over PE-1 to PE-4. A verified deny stands with other questions undecided."""
    pkg = EA.load_package(PE)
    ans = {q: v["answer"] for q, v in questions.items()}
    held = _pe_held(ans)
    if ans["PE-5"]["outcome"] != INCONCLUSIVE:
        decision = ans["PE-5"]["outcome"]
    else:
        decision = held[0][0] if held and held[0][0] != INCONCLUSIVE else INCONCLUSIVE
    label = pkg["labels"][decision]
    labels = [pkg["labels"][d] for d, _ in held]
    also = [x for x in labels if x != label]
    drivers = next((qs for d, qs in held if d == decision), list(ans)) if decision != "ALLOW" else list(ans)
    base, action, sev, reason = PE_LABELS[label]
    two = ans["PE-2"].get("basis") or {}
    if label == "VIOLATION":
        executed = [x for x in two.get("violations", []) if x["stage"] == "executed"]
        routes = [x for x in (ans["PE-4"].get("basis") or {}).get("routes", [])
                  if x["decision"] == "DENY" or x.get("entitled") is False]
        if not executed and not routes:
            action, reason = "BLOCK", "a proposed operation is denied by the policy in force; recommend blocking it"
    elif label == "UNCERTAIN" and drivers == ["PE-4"] and all(
            x["decision"] == "NOT_EVALUATED" for x in ans["PE-4"]["basis"]["routes"] if x["decision"] != "ALLOW"):
        reason = "a new route the policy model cannot evaluate is not a permitted one"
    recipients = {"VIOLATION": ["incident_response", "audit_traceability"],
                  "APPROVAL_REQUIRED": ["incident_response", "audit_traceability"],
                  "UNCERTAIN": ["audit_traceability"]}.get(label, [])
    if label == "VIOLATION" and "insights.secrets" in two.get("policy_relevant", []):
        recipients.append("risk_mitigation")
    one = ans["PE-1"].get("basis") or {}
    if one.get("conflicts") or one.get("no_rule"):
        recipients.append("knowledge_policy_management")
    trace = _question_trace(questions, _explain_pe, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    contrary += [f"PE-1: {c['action']} {c['resource']} matched "
                 f"{', '.join(f'{r} {e}' for r, e in c['effects'].items())}; resolved {c['resolved_as']}"
                 for c in one.get("conflicts", [])]
    five = ans["PE-5"].get("basis") or {}
    contrary += [f"PE-5: precedent {p['record_id']} was {p['label']} under {p['policy_version']}, superseded"
                 for p in five.get("precedents", []) if p["status"] == "SUPERSEDED"]
    contrary += [f"{x} also holds" for x in also]
    return {"label": label, "confidence": base, "severity": sev, "action": action, "action_reason": reason,
            "recipients": recipients, "drivers": drivers, "also_held": also, "contrary": contrary,
            "questions": trace, "limitations": _pe_limitations(ans), "decision": decision}


def _pe_limitations(ans):
    from . import decision as D
    out = []
    for q, x in ans.items():
        for r in (x.get("basis") or {}).get("interpretation_reasons", []):
            if r["code"] == EA.MISSING_REFERENCE:
                out.append(D.limitation(D.MISSING_EVIDENCE, q, r["requirement"], r["code"], r["detail"]))
    three = ans["PE-3"].get("basis") or {}
    if ans["PE-3"]["outcome"] == "APPROVAL_OBTAINED":
        out.append(D.limitation(D.MISSING_EVIDENCE, "PE-3", "approval_operation_link", "SESSION_LEVEL_APPROVAL",
                                f"approval is read at session level ({three['approval_scope']}); "
                                f"approval_action_linkage is {three['approval_action_linkage']}"))
    for x in (ans["PE-4"].get("basis") or {}).get("routes", []):
        if x.get("decision") == "NOT_EVALUATED":
            out.append(D.limitation(D.MISSING_EVIDENCE, "PE-4", x["route"], "NO_POLICY_OPERATION", x["reason"]))
    return out


# ---------------------------------------------------------------- Anomaly Detection

AD = "anomaly_detection"
AD_DEVIATES = {"AD-1": "UNUSUALLY_FREQUENT", "AD-2": "SIGNIFICANTLY_DIFFERENT", "AD-3": "RATE_UNUSUAL",
               "AD-4": "UNFAMILIAR"}
AD_QUIET = {"AD-1": "NOT_UNUSUAL", "AD-2": "COMPARABLE", "AD-3": "RATE_WITHIN_BASELINE", "AD-4": "FAMILIAR"}
AD_SHORT = "BASELINE_INSUFFICIENT"
AD_OBSERVED = ("AD-1", "AD-2", "AD-3", "AD-4")
# Each question's measures, and the linkage that must hold before a quiet answer is given.
AD_MEASURES = {"AD-1": ("insights.repetition", "insights.retry", "insights.failed"),
               "AD-2": ("insights.token_outlier", "insights.duration_outlier"),
               "AD-3": ("activity.action_rate_ratio",),
               "AD-4": ("path.transition_novelty", "access.first_seen_mcp")}
AD_LINKAGE = {"AD-1": "telemetry.turn_linkage", "AD-4": "telemetry.lane_linkage"}
AD_REPORTED = {"AD-2": ("token_expansion_per_turn",),
               "AD-4": ("interaction.mcp_servers", "interaction.subagent_lanes", "resource_access_novelty_files")}
AD_NOT_BUILT = {"AD-4": ("tool_use_deviation_per_agent",), "AD-5": ("task_adjusted_anomaly_score",)}
# The directions metrics_config.json approves no limit for: measured, never judged.
AD_ONE_SIDED = {"AD-2": ("insights.token_outlier", "lower token use than the estate"),
                "AD-3": ("activity.action_rate_ratio", "a lower action rate than the tool median")}
DEVIATES, WITHIN, NO_BASELINE, UNOBSERVED, NOT_JUDGED, DOES_NOT_APPLY = (
    "DEVIATES", "WITHIN", "NO_BASELINE", "UNOBSERVED", "NOT_JUDGED", "DOES_NOT_APPLY")
INTERPRETATION_REQUIRED = "INTERPRETATION_REQUIRED"
DECIDED_WITHOUT_MODEL = "decided_by_package_without_model"
LEGITIMACY_NOT_ESTABLISHED = "LEGITIMACY_NOT_ESTABLISHED"
# The measures a task class can explain: the quantities it compares like for like. The others (repetition,
# retry, failure, duration, novelty, first-seen MCP) have no task-class basis.
AD_CLASS_MEASURES = ("insights.token_outlier", "activity.action_rate_ratio")
NO_WORKLOAD_RECORD = ("no approved record of an authorised workload change or of a task's expected volume "
                      "exists; neither is accepted as context")
NO_WINDOW = ("no approved baseline window: every session of the fetched estate weighs the same, however old "
             "(metrics_config.json has no window)")


# The measures whose comparison group contains the judged session. Anomaly Detection reads them leave-one-out:
# the same signal_metrics function against signal_metrics.estate of the other sessions (purvi, 2026-11-24, U-AD7).
AD_SELF_INCLUDING = ("insights.token_outlier", "insights.duration_outlier", "activity.action_rate_ratio")
AD_BASELINE_METHOD = "leave_one_out@1"
# The COPs that read the self-including measures leave-one-out: Anomaly Detection (U-AD7) and Resource
# Management (purvi, 2026-11-26, U-RS1). Their pictures, rules and metric tools read ad_case_events.
LEAVE_ONE_OUT_COPS = ("anomaly_detection", "resource_management")


def ad_leave_one_out(sessions, s, cfg, events):
    """The session's metric_event dicts, with each self-including measure re-read by signal_metrics.metric_event
    against the estate without the session. The self-including reading stays in features.self_inclusive."""
    sm = EA.signal_metrics
    est = sm.estate([x for x in sessions if x.id != s.id])
    entries = {m[0]: m for m in sm.METRICS}
    out = []
    for e in events:
        if e["metric_id"] in AD_SELF_INCLUDING:
            x = sm.metric_event(s, entries[e["metric_id"]], est, cfg, e["computed_at"])
            x["features"] = {**x["features"], "baseline_method": AD_BASELINE_METHOD,
                             "self_inclusive": {"value": e["value"], "state": e["state"]}}
            x["source"] = f"{x['source']}/{AD_BASELINE_METHOD}"
            e = x
        out.append(e)
    return out


def ad_case_events(case, metric_ids=None):
    """CaseData.case_events as Anomaly Detection reads them: the package, its picture (VS-01, DS-03, the rule
    baseline) and its metric tools all see the same leave-one-out rows. Other COPs keep case.case_events."""
    from ..models import MetricEvent
    s = case.session
    if s is None:
        return case.case_events(metric_ids)
    mine = [e for e in case.events if e["session_id"] == case.session_id
            and (metric_ids is None or e["metric_id"] in metric_ids)]
    return [MetricEvent.from_row(e) for e in ad_leave_one_out(case.sessions, s, case.cfg, mine)]


def _version(cfg, ids):
    import hashlib
    return f"{cfg.get('metric_version')}#" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:10]


def _ad_baselines(sessions, s, cfg):
    """Which comparison each baseline-dependent measure uses, how large it is, and whether it meets the
    minimum, read from signal_metrics.estate as the metrics computed it. Nothing is recomputed here. The
    self-including measures are compared with the other sessions only (AD_BASELINE_METHOD); the self-including
    comparison is recorded beside them."""
    sm = EA.signal_metrics
    others = [x for x in sessions if x.id != s.id]
    e, e_all = sm.estate(others), sm.estate(sessions)
    need, need_t = cfg["min_sessions_for_baseline"], cfg["min_transitions_for_novelty"]
    with_tokens = [x.id for x in others if x.tokens > 0]
    with_steps = [x.id for x in others if any(sm.num(st.get("duration_ms")) > 0 for st in x.steps)]
    same_tool = [x.id for x in sessions if x.tool == s.tool]
    other_tool = [x for x in same_tool if x != s.id]
    earlier = [x.id for x in sessions if x.operator == s.operator and (x.start or "") < (s.start or "")]
    servers = sorted({r["mcp_server"] for r in s.rows if sm.has(r.get("mcp_server"))})
    rate = e["median_rate_by_tool"].get(s.tool)
    trans = len(s.transitions())

    def loo(ids, **incl):
        return {"method": AD_BASELINE_METHOD, "version": f"{_version(cfg, ids)}/{AD_BASELINE_METHOD}",
                "self_inclusive": incl}
    return {
        "insights.token_outlier": {
            "comparison": "every other session of the fetched estate with token counts, this one left out",
            "sessions": len(with_tokens), "needed": need, "median": e["median_tokens"], "this_session": s.tokens,
            **loo(with_tokens, sessions=e_all["sessions_with_tokens"], median=e_all["median_tokens"]),
            "sufficient": bool(e["median_tokens"]) and e["sessions_with_tokens"] >= need,
            "gap": None if e["median_tokens"] and e["sessions_with_tokens"] >= need else
            f"{len(with_tokens)} other sessions with token counts; {need} needed (min_sessions_for_baseline)"},
        "insights.duration_outlier": {
            "comparison": "every timed step of the other sessions of the fetched estate, this session's left out",
            "sessions": len(with_steps), "needed": None, "median_step_ms": e["median_duration_ms"],
            "limit_ms": (e["median_duration_ms"] or 0) * cfg["duration_outlier_multiple"],
            **loo(with_steps, median_step_ms=e_all["median_duration_ms"]),
            "sufficient": bool(e["median_duration_ms"]),
            "gap": None if e["median_duration_ms"] else "no step of another session has a recorded duration"},
        "activity.action_rate_ratio": {
            "comparison": f"the other sessions of {s.tool}, this one left out", "sessions": len(other_tool),
            "needed": need, "median_actions_per_min": rate,
            **loo(other_tool, sessions=len(same_tool), median_actions_per_min=e_all["median_rate_by_tool"].get(s.tool)),
            "sufficient": bool(rate) and len(other_tool) >= need,
            "gap": None if rate and len(other_tool) >= need else
            f"{len(other_tool)} other sessions of {s.tool}; {need} needed (min_sessions_for_baseline)"},
        "path.transition_novelty": {
            "comparison": f"transitions of the other sessions of {s.tool}", "sessions": len(same_tool) - 1,
            "needed": need, "session_transitions": trans, "transitions_needed": need_t,
            "version": _version(cfg, [x for x in same_tool if x != s.id]),
            "sufficient": len(same_tool) - 1 >= need and trans >= need_t,
            "gap": None if len(same_tool) - 1 >= need and trans >= need_t else "; ".join(
                x for x in (f"{len(same_tool) - 1} other sessions of {s.tool}; {need} needed"
                            if len(same_tool) - 1 < need else "",
                            f"this session has {trans} transitions; {need_t} needed (min_transitions_for_novelty)"
                            if trans < need_t else "") if x)},
        "access.first_seen_mcp": {
            "comparison": f"earlier sessions of operator {s.operator}", "sessions": len(earlier), "needed": 1,
            "servers_used": servers, "version": _version(cfg, earlier),
            "sufficient": bool(earlier) or not servers,
            "gap": None if earlier or not servers else
            f"{s.operator} has no earlier session, so every server used is new to them by definition"},
    }


def _ad_measure(a, metric, base, states):
    x = _item(a, metric)
    b = base.get(metric)
    out = {"metric": metric, "value": x["value"], "state": x["metric_state"], "presence": x["state"],
           "evidence_refs": x["evidence_refs"][:10], **({"baseline": b} if b else {})}
    if x["state"] == EA.NOT_APPLICABLE:
        return dict(out, verdict=DOES_NOT_APPLY, reason=x["reason"])
    if b and not b["sufficient"]:
        return dict(out, verdict=NO_BASELINE, reason=b["gap"])
    if x["state"] != EA.PRESENT:
        return dict(out, verdict=UNOBSERVED, reason=x["reason"] or x["state"])
    if x["metric_state"] == "NO_THRESHOLD":
        return dict(out, verdict=NOT_JUDGED, reason="no approved threshold")
    if x["metric_state"] in states:
        return dict(out, verdict=DEVIATES)
    return dict(out, verdict=WITHIN, **({"warning": True} if x["metric_state"] == "WARNING" else {}))


def _ad_linkage(a, metric, states):
    x = _item(a, metric)
    if x["state"] == EA.NOT_APPLICABLE:
        return None
    if x["state"] != EA.PRESENT:
        return {"metric": metric, "value": None, "state": x["metric_state"],
                "detail": f"{metric} could not be measured ({x['reason'] or x['state']})"}
    if x["metric_state"] in states:
        return {"metric": metric, "value": x["value"], "state": x["metric_state"],
                "detail": f"{metric} is {x['value']} ({x['metric_state']}): actions not linked are not compared"}
    return None


def _ad_observe(qid, a, base, states, upstream):
    ms = [_ad_measure(a, m, base, states) for m in AD_MEASURES[qid]]
    link = _ad_linkage(a, AD_LINKAGE[qid], states) if qid in AD_LINKAGE else None
    basis = {"measures": ms, "deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "warnings": [m["metric"] for m in ms if m.get("warning")],
             "reported_not_judged": [{"metric": m, "value": _item(a, m)["value"], "state": _item(a, m)["metric_state"],
                                      "reason": _item(a, m)["reason"]} for m in AD_REPORTED.get(qid, ())],
             "unassessed": _unassessed(a, *AD_NOT_BUILT.get(qid, ())), "linkage": link}
    if qid == "AD-1":
        basis["comparison"] = "approved absolute thresholds per session (metrics_config.json); no history is compared"
        basis["upstream"] = _ad_behaviour(upstream)
    if qid in AD_ONE_SIDED:
        m, what = AD_ONE_SIDED[qid]
        basis["not_evaluated"] = [{"metric": m, "direction": "below", "value": _item(a, m)["value"],
                                   "detail": f"{what} is not judged: metrics_config.json approves an upper "
                                             f"limit only"}]
    dev = basis["deviating"]
    if dev:
        return AD_DEVIATES[qid], basis
    open_ = [m for m in ms if m["verdict"] not in (WITHIN, DOES_NOT_APPLY)]
    if not open_ and not link:
        return AD_QUIET[qid], basis
    if qid != "AD-1" and open_ and not link and all(m["verdict"] == NO_BASELINE for m in open_):
        return AD_SHORT, basis
    basis["interpretation_reasons"] = [
        {"code": EA.INSUFFICIENT_HISTORY if m["verdict"] == NO_BASELINE else EA.MISSING_DATA,
         "requirement": m["metric"], "check": None, "detail": m["reason"]} for m in open_] + \
        ([{"code": "LINKAGE_DEGRADED", "requirement": link["metric"], "check": None, "detail": link["detail"]}]
         if link else [])
    return INCONCLUSIVE, basis


def _ad_behaviour(upstream):
    """Behaviour Analytics, when verified LOOPING or POOR_RECOVERY: recorded beside AD-1, counted only when its
    lineage is independent of the metrics this COP reads (rules.independent_upstream)."""
    from .rules import verified, independent_upstream
    ba = verified(upstream, "behavior_analytics", "LOOPING", "POOR_RECOVERY")
    if not ba:
        return None
    ind = independent_upstream(tuple(BY_KEY[AD].metric_keys) + AD_MEASURES["AD-1"], {"behavior_analytics": ba})
    return {"cop": "behavior_analytics", "label": ba["label"], "finding_id": ba.get("finding_id"),
            "counted_as": "independent" if ind["independent"] else
            "correlated, not counted: " + ind["correlated"][0]["why"]}


def _ad_precedents(memory, deviating):
    out = []
    for x in (memory or {}).get("established", []):
        p = x["payload"]
        same = sorted(set(p.get("deviating_metrics") or []) & set(deviating))
        out.append({"record_id": x["record_id"], "label": p.get("label"), "same_metrics": same,
                    "status": x.get("status"), "use": "comparison only, never proof"})
    return out[:10]


def _ad_context(deviations, peers, memory):
    """For each deviating measure, the checkable context that could explain it as legitimate variation
    (GROUND_TRUTH.yaml, revision 2). Task-class peers are compared with signal_metrics' own metric function and
    approved thresholds on signal_metrics.estate of the peers (the session left out); a precedent is a
    VALIDATED NORMAL observation that names the measure."""
    sm = EA.signal_metrics
    pkg = EA.load_package(AD)
    states = tuple(pkg["deviation_states"])
    fns = {"insights.token_outlier": sm.m_token_outlier, "activity.action_rate_ratio": sm.m_action_rate}
    out = []
    for d in deviations:
        m, srcs = d["metric"], []
        if m in AD_CLASS_MEASURES:
            src = {"source": "task_classes"}
            if not peers or peers.get("classes") is None:
                src.update(explains=False, gap="no task-class record supplied")
            elif peers.get("class") is None:
                src.update(explains=False, gap="the session has no task class in the record")
            else:
                cfg, ps, need = peers["cfg"], peers["sessions"], peers["cfg"]["min_sessions_for_baseline"]
                src.update({"task_class": peers["class"], "peers": len(ps), "needed": need,
                            "peer_sessions": sorted(x.id for x in ps)})
                if len(ps) < need:
                    src.update(explains=False, gap=f"{len(ps)} other sessions of class {peers['class']}; {need} "
                                                   f"needed (min_sessions_for_baseline)")
                else:
                    v = fns[m](peers["session"], sm.estate(ps), cfg)[0]
                    st = sm.judge(v, cfg["thresholds"].get(m, {}))
                    src.update(peer_value=v, peer_state=st, explains=v is not None and st not in states)
                    if v is None:
                        src["gap"] = "no peer baseline for this measure"
            srcs.append(src)
        for r in (memory or {}).get("established", []):
            pl = r.get("payload") or {}
            if pl.get("label") == "NORMAL" and m in (pl.get("deviating_metrics") or []):
                srcs.append({"source": "validated_precedent", "record_id": r["record_id"],
                             "status": r.get("status"), "explains": True})
        by = [x["source"] for x in srcs if x.get("explains")]
        out.append({"question": d["question"], "metric": m, "state": d["state"], "explained": bool(by),
                    "by": by, "sources": srcs,
                    **({} if by else {"gap": "; ".join(x.get("gap", "") for x in srcs if x.get("gap"))
                                      or "no checkable context names this measure"})})
    return out


def _ad5(a, deps, upstream, memory, peers=None):
    from .rules import verified
    rm = verified(upstream, "runtime_monitoring")
    devs = {q: deps[q]["basis"]["deviating"] for q in AD_OBSERVED if deps[q]["outcome"] == AD_DEVIATES[q]}
    open_ = {q: deps[q]["outcome"] for q in AD_OBSERVED if deps[q]["outcome"] not in (AD_DEVIATES[q], AD_QUIET[q])}
    measures = {m["metric"]: m for q in AD_OBSERVED for m in (deps[q]["basis"] or {}).get("measures", [])}
    flat = [m for ms in devs.values() for m in ms]
    basis = {"deviations": [{"question": q, "metric": m, "value": measures[m]["value"], "state": measures[m]["state"]}
                            for q, ms in devs.items() for m in ms],
             "not_answered": [{"question": q, "answer": o} for q, o in open_.items()],
             "upstream": {"runtime_monitoring": None if not rm else {"label": rm["label"],
                                                                     "finding_id": rm.get("finding_id")}},
             "precedents": _ad_precedents(memory, flat),
             "unassessed": _unassessed(a, "task_adjusted_anomaly_score")}
    if not rm:
        basis["limitation"] = "no Runtime Monitoring finding was available to this run"
    if devs:
        basis["context"] = _ad_context(basis["deviations"], peers, memory)
        basis["context_not_accepted"] = NO_WORKLOAD_RECORD
        basis["interpretation_reasons"] = [{
            "code": INTERPRETATION_REQUIRED, "requirement": "interpretation", "check": None,
            "detail": "deviations observed (" + "; ".join(
                f"{x['question']} {x['metric']} {x['value']} {x['state']}" for x in basis["deviations"])
                      + "): whether they are a meaningful anomaly or legitimate task variation is the model's "
                        "interpretation"}]
        return INCONCLUSIVE, basis
    if rm and rm["label"] == "INSUFFICIENT":
        basis["because"] = ("Runtime Monitoring reads the telemetry INSUFFICIENT: quiet measures on missing "
                            "telemetry are not evidence of normal behaviour")
        return "UNDETERMINED", basis
    if open_:
        basis["because"] = "not every question could be answered: " + ", ".join(f"{q} {o}" for q, o in open_.items())
        return "UNDETERMINED", basis
    return "NO_DEVIATION", basis


def observe_anomaly_detection(assessments, baselines, upstream=None, memory=None, peers=None):
    """{question_id: assessment} -> {question_id: answer}. baselines: _ad_baselines for the session; memory:
    {established, verified_pending} anomaly_observation records."""
    pkg = EA.load_package(AD)
    states = tuple(pkg["deviation_states"])
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf == "INCONCLUSIVE":
            # a blocking reliability check failed or could not run: nothing observed here is judged
            ms = [_ad_measure(a, m, baselines, states) for m in AD_MEASURES.get(qid, ())]
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf,
                        "basis": {"observed_not_judged": ms, "deviating": []},
                        "because": a["sufficiency"]["reasons"]}
            continue
        if qid == "AD-5":
            outcome, basis = _ad5(a, {k: out[k] for k in q["depends_on"]}, upstream, memory, peers)
        else:
            outcome, basis = _ad_observe(qid, a, baselines, states, upstream)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        _missing_inputs(pkg, basis)
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE:
            out[qid]["because"] = basis["interpretation_reasons"]
    return out


def assess_anomaly_detection(rows, session_id, cfg=None, computed_at=None, upstream=None, memory=None,
                             raw_events=None, native=None, task_classes=None):
    """The five questions for one session: each assessment with its answer, and the baselines used."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    events = {e["metric_id"]: e for e in ad_leave_one_out(sessions, s, cfg, list(events.values()))}
    base = _ad_baselines(sessions, s, cfg)
    assessments = {qid: EA.assess(AD, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[AD]}
    cls = (task_classes or {}).get(session_id)
    peers = {"classes": task_classes, "class": cls, "cfg": cfg, "session": s,
             "sessions": [x for x in sessions if x.id != session_id and cls is not None
                          and (task_classes or {}).get(x.id) == cls]}
    answers = observe_anomaly_detection(assessments, base, upstream, memory, peers)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _measures_text(ms):
    return ", ".join(f"{m['metric']} {m['value']} ({m['state']})" for m in ms)


def _explain_ad(qid, ans):
    b = ans["basis"]
    if ans["outcome"] == INCONCLUSIVE:
        s = _explain(qid, ans)
        seen = [m for m in b.get("observed_not_judged", []) if m["verdict"] == DEVIATES]
        return s + (f"; observed, not judged: {_measures_text(seen)}" if seen else "")
    if qid == "AD-5":
        if ans["outcome"] == "NO_DEVIATION":
            return "no question found a deviation and every question was answered"
        if ans["outcome"] == "UNDETERMINED":
            return b["because"]
        return b.get("interpretation_text") or ans["outcome"]
    ms = b["measures"]
    if ans["outcome"] == AD_DEVIATES[qid]:
        s = "deviation: " + _measures_text([m for m in ms if m["verdict"] == DEVIATES])
    elif ans["outcome"] == AD_SHORT:
        s = "baseline insufficient: " + "; ".join(f"{m['metric']}: {m['reason']}" for m in ms
                                                  if m["verdict"] == NO_BASELINE)
    else:
        s = "within the approved limits: " + _measures_text([m for m in ms if m["verdict"] == WITHIN]) + \
            (f"; WARNING recorded, not a deviation: {', '.join(b['warnings'])}" if b["warnings"] else "")
    for x in b.get("not_evaluated", []):
        s += f"; not evaluated: {x['detail']}"
    up = b.get("upstream")
    return s + (f"; {up['cop']} {up['label']} ({up['counted_as']})" if up else "")


# What each label proposes when the package decides it; the confidences are cops/interpret.py's.
AD_LABELS = {"NORMAL": (0.8, "NONE", "NONE", ""),
             "INDETERMINATE": (0.2, "WATCH", "NONE", "whether the behaviour is unusual cannot be judged on this "
                                                    "evidence"),
             INCONCLUSIVE: (0.0, "WATCH", "NONE", "deviations were observed and need interpretation")}
AD_RECIPIENTS = {"ANOMALOUS": ["incident_response", "risk_mitigation"]}


def _ad_limitations(ans):
    from . import decision as D
    out = []
    for q, (m, what) in AD_ONE_SIDED.items():
        if ans[q]["outcome"] in (AD_QUIET[q], AD_DEVIATES[q]):
            out.append(D.limitation(D.LIMIT_NOT_APPROVED, q, f"{m} (below)", D.LIMIT_NOT_APPROVED,
                                    f"{what} is measured and not judged: no lower limit is approved"))
    for q in ("AD-2", "AD-3", "AD-4"):
        if ans[q]["outcome"] in (AD_QUIET[q], AD_DEVIATES[q]):
            out.append(D.limitation(D.LIMIT_NOT_APPROVED, q, "baseline_window", D.LIMIT_NOT_APPROVED, NO_WINDOW))
    return out


def _ad_payload(ans):
    return {"deviating_metrics": sorted({m for q in AD_OBSERVED for m in (ans[q]["basis"] or {}).get("deviating", [])}),
            "answers": {q: x["outcome"] for q, x in ans.items()},
            "baselines": sorted({f"{m['metric']}@{m['baseline']['version']}" for q in AD_OBSERVED
                                 for m in (ans[q]["basis"] or {}).get("measures", []) if m.get("baseline")})}


def decide_anomaly_detection(questions, evidence_scale=1.0):
    """The package's decision from the five answers (INTERPRETATION.md). AD-5 decides NORMAL when no question
    deviates and every question was answered, INDETERMINATE when evidence or a baseline is short; with a
    deviation, AD-5 waits for the model's interpretation and `decided` is False."""
    pkg = EA.load_package(AD)
    ans = {q: v["answer"] for q, v in questions.items()}
    five = ans["AD-5"]["outcome"]
    label = pkg["labels"][five]
    base, action, sev, reason = AD_LABELS[label]
    devs = [q for q in AD_OBSERVED if ans[q]["outcome"] == AD_DEVIATES[q]]
    opens = [q for q in AD_OBSERVED if ans[q]["outcome"] not in (AD_DEVIATES[q], AD_QUIET[q])]
    drivers = (devs + ["AD-5"]) if five == INCONCLUSIVE else (opens or ["runtime_monitoring"]) + ["AD-5"] \
        if five == "UNDETERMINED" else list(ans)
    drivers = [d for d in drivers if d in ans]
    trace = _question_trace(questions, _explain_ad, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    contrary += [f"{q}: WARNING on {', '.join(ans[q]['basis']['warnings'])}, recorded and not counted"
                 for q in AD_OBSERVED if (ans[q]["basis"] or {}).get("warnings")]
    return {"label": label, "confidence": round(base * evidence_scale, 3), "severity": sev, "action": action,
            "action_reason": reason, "drivers": drivers, "contrary": contrary, "questions": trace,
            "limitations": _ad_limitations(ans), "recipients": [], "decided": five != INCONCLUSIVE,
            "memory_payload": _ad_payload(ans), "decision": five}


def interpreted_anomaly_detection(f, baseline):
    """AD-5 after verification, for a finding the model concluded: its verified label read back as AD-5's answer
    (METRICS.yaml interpretations), or INCONCLUSIVE with why. AD-1 to AD-4 are the package's, unchanged. A
    finding the package decided itself is returned as it is."""
    import copy
    from .contracts import ORIGIN_LLM
    cs = (baseline or {}).get("cop_specific") or {}
    if f.get("origin") != ORIGIN_LLM or not cs.get("questions"):
        return f
    pkg = EA.load_package(AD)
    q = copy.deepcopy(cs["questions"])
    five = q["AD-5"]
    reasons = [r for r in five["sufficiency_reasons"] if r.get("code") != INTERPRETATION_REQUIRED]
    detail = (f.get("cop_specific") or {}).get("detail") or {}
    interp = {"by": "llm", "verified": bool(f.get("verified")), "status": f["status"],
              "deviating_quantities": detail.get("deviating_quantities", []),
              "alternative_explanations": (f.get("cop_specific") or {}).get("alternative_explanations", []),
              "alternatives_considered": detail.get("alternative_explanations_considered", []),
              "baseline_version": detail.get("baseline_version"), "telemetry_quality": detail.get("telemetry_quality")}
    if f["status"] == "SUBSTANTIVE" and f["label"] in pkg["interpretations"]:
        answer = pkg["interpretations"][f["label"]]
        text = f"model interpretation {f['label']} ({'verified' if f.get('verified') else 'not verified'})"
        if interp["alternatives_considered"] or interp["alternative_explanations"]:
            text += "; alternatives considered: " + "; ".join(interp["alternatives_considered"]
                                                              or interp["alternative_explanations"])
    else:
        answer = INCONCLUSIVE
        cand = (f.get("rejected_candidate") or {}).get("label")
        if cand:
            r = {"code": "INTERPRETATION_REJECTED", "requirement": "interpretation", "check": None,
                 "detail": f"model interpretation {cand} rejected: {f.get('rationale', '')[:240]}"}
            ctxs = (five.get("basis") or {}).get("context") or []
            if cand == "NORMAL" and not (ctxs and all(x["explained"] for x in ctxs)):
                reasons.append({"code": LEGITIMACY_NOT_ESTABLISHED, "requirement": "interpretation", "check": None,
                                "detail": "legitimate variation needs checkable context for every deviation; "
                                          "not explained: " + "; ".join(f"{x['metric']} ({x.get('gap')})"
                                                                        for x in ctxs if not x["explained"])})
        else:
            r = {"code": "NO_INTERPRETATION", "requirement": "interpretation", "check": None,
                 "detail": "no verified model interpretation: "
                           + str((f.get("cop_specific") or {}).get("stop_reason") or f.get("rationale", ""))[:200]}
        reasons.append(r)
        interp["candidate"] = cand
        text = "not answered: " + r["detail"]
    five.update(answer=answer, explanation=text, sufficiency_reasons=reasons, drives_label=True)
    five["basis"] = {**(five["basis"] or {}), "interpretation": interp, "interpretation_text": text}
    devs = [k for k in AD_OBSERVED if q[k]["answer"] == AD_DEVIATES[k]]
    for k in AD_OBSERVED:
        q[k]["drives_label"] = k in devs
    out = dict(f)
    out["cop_specific"] = {**(f.get("cop_specific") or {}), "questions": q, "drivers": devs + ["AD-5"],
                           "limitations": cs.get("limitations", []), "contrary": cs.get("contrary", []),
                           "recipients": AD_RECIPIENTS.get(f["label"], []) if answer != INCONCLUSIVE else [],
                           "memory_payload": {**(cs.get("memory_payload") or {}),
                                              "answers": {k: v["answer"] for k, v in q.items()}},
                           "decision": answer,
                           # a verified legitimate variation explains a deviation: remembered, so that a reviewer
                           # can validate it as a precedent (MEMORY.md)
                           **({"remember_quiet": True} if answer == "LEGITIMATE_VARIATION" else {})}
    return out


def ad_legitimacy(case, memory_view, claims, tool_calls):
    """The verifiers' re-check of a legitimate-variation reading: the package's deviations and their context
    recomputed from the case rows, the case's task-class record and validated memory (never from the model's
    text), and whether the finding cites that context (a tool_observation on assess_anomaly_questions under
    questions.AD-5.basis.context)."""
    if case is None or not case.session:
        return None
    mem = {"established": (memory_view or {}).get("established", [])}
    q = assess_anomaly_detection(case.rows, case.session_id, case.cfg, case.computed_at, None, mem,
                                 task_classes=case.artifact("task_classes"))
    five = q["AD-5"]["answer"]["basis"] or {}
    context = five.get("context") or []
    tools = {c.get("tool_call_id"): c.get("tool") for c in (tool_calls.values() if isinstance(tool_calls, dict)
                                                             else tool_calls or [])}
    cited = any(c.get("kind") == "tool_observation" and tools.get(c.get("tool_call_id")) == "assess_anomaly_questions"
                and "AD-5" in str(c.get("result_field") or "") and "basis.context" in str(c.get("result_field") or "") for c in claims or [])
    return {"deviating": [x["metric"] for x in context], "explained": [x["metric"] for x in context if x["explained"]],
            "unexplained": [x["metric"] for x in context if not x["explained"]],
            "all_explained": bool(context) and all(x["explained"] for x in context), "cited": cited,
            "context": context}


LEGITIMACY = {AD: ad_legitimacy}


# ---------------------------------------------------------------- Resource Management

RS = "resource_management"
RS_OBSERVED = ("RS-1", "RS-2", "RS-3", "RS-4")
# The measures each question judges: the legacy rule's BURN and WASTE (cops/interpret.py), nothing else.
RS_MEASURES = {"RS-1": ("insights.token_outlier",),
               "RS-2": ("insights.duration_outlier", "activity.action_rate_ratio"),
               "RS-3": ("insights.stalled",)}
# Assigned to RS-3 and observed; the legacy rule does not count them as waste (DECISIONS.md, U-RS2).
RS_NOT_COUNTED = {"RS-3": ("insights.repetition", "insights.retry", "insights.failed")}
RS_REPORTED = {"RS-1": ("token_consumption_per_session", "token_consumption_per_turn", "cost_per_session",
                        "resource_consumption_rate"),
               "RS-2": ("execution_duration_per_session", "execution_duration_threshold_breach", "tool_timeout_rate"),
               "RS-3": ("resource_stall_rate", "resource_redundant_execution_cost", "resource_concurrency_level"),
               "RS-4": ("interaction.subagent_lanes", "interaction.mcp_servers", "subagent_resource_consumption"),
               "RS-5": ("telemetry.collector_coverage", "telemetry.unknown_share")}
RS_NOT_BUILT = {"RS-1": ("token_budget_utilization", "token_quota_exceeded", "cost_budget_utilization"),
                "RS-3": ("resource_capacity_utilization",),
                "RS-4": ("model_selection_compliance", "resource_quota_compliance"),
                "RS-5": ("resource_limit_proximity", "resource_threshold_breach_count")}
# The directions metrics_config.json approves no limit for: measured, never judged.
RS_ONE_SIDED = {"RS-1": ("insights.token_outlier", "lower token use than the estate"),
                "RS-2": ("activity.action_rate_ratio", "a lower action rate than the tool median")}
RS_ATTENTION = {"RS-1": ("ABOVE_EXPECTED", "ELEVATED"), "RS-2": ("OUTSIDE_THRESHOLDS", "ELEVATED"),
                "RS-3": ("WASTE_OBSERVED", "ELEVATED")}
RS_OPEN = (INCONCLUSIVE, "BASELINE_INSUFFICIENT")
# Resource Management reads the self-including measures leave-one-out, with Anomaly Detection's shared method
# (ad_leave_one_out, _ad_baselines; purvi approved U-RS1 on 2026-11-26). The self-inclusive reading it used
# before is kept in the comparison record beside each reading and decides nothing.
RS_BASELINE_METHOD = AD_BASELINE_METHOD
RS_PREVIOUS_METHOD = "self_inclusive"
NO_QUOTA_PERIOD = ("the quota record carries no period: the session's tokens are compared with token_budget, as "
                   "rules.py did before this package")
NOT_UNAUTHORIZED = ("above the estate's own normal, not above an allowance: only the quota decides whether "
                    "consumption is permitted")


def _rs_self_inclusive(sessions, s, cfg):
    """The comparison each baseline-dependent measure used before U-RS1, as signal_metrics computes it: the
    estate with the session included. Read from signal_metrics.estate; recorded for comparison only."""
    sm = EA.signal_metrics
    e = sm.estate(sessions)
    need = cfg["min_sessions_for_baseline"]
    with_tokens = [x.id for x in sessions if x.tokens > 0]
    with_steps = [x.id for x in sessions if any(sm.num(st.get("duration_ms")) > 0 for st in x.steps)]
    same_tool = [x.id for x in sessions if x.tool == s.tool]
    n_tool = e["sessions_by_tool"].get(s.tool, 0)
    rate = e["median_rate_by_tool"].get(s.tool)

    def ver(ids):
        return {"method": RS_PREVIOUS_METHOD, "version": f"{_version(cfg, ids)}/{RS_PREVIOUS_METHOD}"}
    return {
        "insights.token_outlier": {
            "comparison": "every session of the fetched estate with token counts, this one included",
            "sessions": e["sessions_with_tokens"], "needed": need, "median": e["median_tokens"],
            "this_session": s.tokens, **ver(with_tokens),
            "sufficient": bool(e["median_tokens"]) and e["sessions_with_tokens"] >= need,
            "gap": None if e["median_tokens"] and e["sessions_with_tokens"] >= need else
            f"{e['sessions_with_tokens']} sessions with token counts, this one included; {need} needed "
            f"(min_sessions_for_baseline)"},
        "insights.duration_outlier": {
            "comparison": "every timed step of the fetched estate, this session's included",
            "sessions": len(with_steps), "needed": None, "median_step_ms": e["median_duration_ms"],
            "limit_ms": (e["median_duration_ms"] or 0) * cfg["duration_outlier_multiple"], **ver(with_steps),
            "sufficient": bool(e["median_duration_ms"]),
            "gap": None if e["median_duration_ms"] else "no step of the estate has a recorded duration"},
        "activity.action_rate_ratio": {
            "comparison": f"the sessions of {s.tool}, this one included", "sessions": n_tool, "needed": need,
            "median_actions_per_min": rate, **ver(same_tool),
            "sufficient": bool(rate) and n_tool >= need,
            "gap": None if rate and n_tool >= need else
            f"{n_tool} sessions of {s.tool}, this one included; {need} needed (min_sessions_for_baseline)"},
    }


def rs_baseline_comparison(sessions, s, cfg, events, loo=None):
    """For each self-including measure, the reading Resource Management uses (leave-one-out, Anomaly Detection's
    shared ad_leave_one_out and _ad_baselines) and the self-inclusive reading it used before U-RS1, each with its
    baseline method, version and sufficiency. events are the self-inclusive metric_event dicts; loo the same
    rows re-read leave-one-out (computed here when not given). The self-inclusive reading decides nothing."""
    if loo is None:
        loo = {e["metric_id"]: e for e in ad_leave_one_out(sessions, s, cfg,
                                                           [events[m] for m in AD_SELF_INCLUDING if m in events])}
    lb, sb = _ad_baselines(sessions, s, cfg), _rs_self_inclusive(sessions, s, cfg)
    out = {}
    for m in AD_SELF_INCLUDING:
        si, lo = events.get(m), loo.get(m)
        if si is None or lo is None:
            continue
        out[m] = {"used": RS_BASELINE_METHOD,
                  "leave_one_out": {"value": lo["value"], "state": lo["state"], "method": lb[m]["method"],
                                    "version": lb[m]["version"], "sessions": lb[m]["sessions"],
                                    "baseline_sufficient": lb[m]["sufficient"], "gap": lb[m]["gap"]},
                  "self_inclusive": {"value": si["value"], "state": si["state"], "method": sb[m]["method"],
                                     "version": sb[m]["version"], "sessions": sb[m]["sessions"],
                                     "baseline_sufficient": sb[m]["sufficient"], "gap": sb[m]["gap"]},
                  "differs": si["state"] != lo["state"] or lb[m]["sufficient"] != sb[m]["sufficient"]}
    return out


def _rs_reported(a, qid):
    return [{"metric": m, "value": _item(a, m)["value"], "state": _item(a, m)["metric_state"],
             "presence": _item(a, m)["state"], "reason": _item(a, m)["reason"]} for m in RS_REPORTED.get(qid, ())]


def _rs_permitted(a, quota):
    """Whether the session's tokens are within the quota record's token_budget (tools.get_quota). The quota is
    the only entitlement source; with no quota, permission is NOT_EVALUATED."""
    tok = _present(a, "token_consumption_per_session")
    tokens = tok["value"] if tok else None
    base = {"source": "quotas (tools.get_quota)", "tokens": tokens}
    if not quota or "missing_dependency" in quota:
        why = ((quota or {}).get("missing_dependency") or {}).get("how_to_supply") or "no quota configured"
        return dict(base, status="NOT_EVALUATED", requirement="budget_or_quota", reason=f"no quota record: {why}")
    if quota.get("token_budget") is None:
        return dict(base, status="NOT_EVALUATED", requirement="budget_or_quota",
                    reason="the quota record has no token_budget")
    base.update(token_budget=quota["token_budget"], period=quota.get("period") or NO_QUOTA_PERIOD)
    if tokens is None:
        return dict(base, status="NOT_EVALUATED", requirement="token_consumption_per_session",
                    reason=f"tokens not measured ({_item(a, 'token_consumption_per_session')['reason']})")
    return dict(base, status="ABOVE_QUOTA" if tokens > quota["token_budget"] else "WITHIN_QUOTA")


def _rs_measured(qid, a, base, states):
    ms = [_ad_measure(a, m, base, states) for m in RS_MEASURES.get(qid, ())]
    basis = {"measures": ms, "deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "warnings": [m["metric"] for m in ms if m.get("warning")],
             "reported_not_judged": _rs_reported(a, qid), "unassessed": _unassessed(a, *RS_NOT_BUILT.get(qid, ()))}
    if qid in RS_ONE_SIDED:
        m, what = RS_ONE_SIDED[qid]
        basis["not_evaluated"] = [{"metric": m, "direction": "below", "value": _item(a, m)["value"],
                                   "detail": f"{what} is not judged: metrics_config.json approves an upper "
                                             f"limit only"}]
    return ms, basis


def _rs_open(ms, basis):
    open_ = [m for m in ms if m["verdict"] not in (WITHIN, DOES_NOT_APPLY)]
    if open_ and all(m["verdict"] == NO_BASELINE for m in open_):
        return "BASELINE_INSUFFICIENT"
    basis["interpretation_reasons"] = [
        {"code": EA.INSUFFICIENT_HISTORY if m["verdict"] == NO_BASELINE else EA.MISSING_DATA,
         "requirement": m["metric"], "check": None, "detail": m["reason"]} for m in open_]
    return INCONCLUSIVE


def _rs1(a, base, states, quota, cmp):
    ms, basis = _rs_measured("RS-1", a, base, states)
    basis["permitted"] = p = _rs_permitted(a, quota)
    basis["leave_one_out"] = cmp.get("insights.token_outlier")
    m = ms[0]
    if p["status"] == "ABOVE_QUOTA":
        return "ABOVE_PERMITTED", basis
    if m["verdict"] == DEVIATES:
        basis["not_unauthorized"] = NOT_UNAUTHORIZED if p["status"] == "WITHIN_QUOTA" else \
            NOT_UNAUTHORIZED + "; no quota is configured, so permission is not evaluated"
        return "ABOVE_EXPECTED", basis
    if m["verdict"] == WITHIN:
        if m.get("warning"):
            return "ELEVATED", basis
        return ("WITHIN_LIMITS" if p["status"] == "WITHIN_QUOTA" else "WITHIN_EXPECTED"), basis
    return _rs_open(ms, basis), basis


def _rs2(a, base, states, cmp):
    ms, basis = _rs_measured("RS-2", a, base, states)
    basis["leave_one_out"] = {m: cmp[m] for m in RS_MEASURES["RS-2"] if m in cmp}
    brk = _item(a, "execution_duration_threshold_breach")
    if brk["state"] != EA.PRESENT:
        basis.setdefault("not_evaluated", []).append(
            {"metric": "execution_duration_threshold_breach", "detail": f"session duration is reported, not judged: "
                                                                         f"{brk['reason'] or brk['state']}"})
    if basis["deviating"]:
        return "OUTSIDE_THRESHOLDS", basis
    if basis["warnings"]:
        return "ELEVATED", basis
    if all(m["verdict"] in (WITHIN, DOES_NOT_APPLY) for m in ms):
        return "WITHIN_THRESHOLDS", basis
    return _rs_open(ms, basis), basis


def _rs3(a, base, states):
    ms, basis = _rs_measured("RS-3", a, base, states)
    soft = tuple(EA.load_package(RS)["elevated_states"])
    basis["not_counted"] = [
        {"metric": m, "value": _item(a, m)["value"], "state": _item(a, m)["metric_state"],
         "presence": _item(a, m)["state"], "beyond_threshold": _item(a, m)["metric_state"] in states + soft,
         "why": "observed and reported; the legacy rule counts stalls and step durations as waste, not repeated "
                "or failed operations (U-RS2)"} for m in RS_NOT_COUNTED["RS-3"]]
    basis["repeated_operations_observed"] = [x["metric"] for x in basis["not_counted"] if x["beyond_threshold"]]
    basis["capacity"] = {"status": "NOT_EVALUATED", "requirement": "resource_capacity_utilization",
                         "detail": "no collector captures system capacity, concurrency limits or queue lengths, so "
                                   "whether waste creates a capacity problem is not evaluated"}
    if basis["deviating"]:
        return "WASTE_OBSERVED", basis
    if basis["warnings"]:
        return "ELEVATED", basis
    if all(m["verdict"] in (WITHIN, DOES_NOT_APPLY) for m in ms):
        return "NO_WASTE_COUNTED", basis
    return _rs_open(ms, basis), basis


def _rs_spans(s):
    spans = {}
    for lane, steps in s.trace["lanes"].items():
        t = [x for st in steps for x in (st.get("started_at"), st.get("ended_at")) if x]
        if t:
            spans[lane] = (min(t), max(t))
    return spans


def _rs4(a, s, states, upstream):
    """Whether the session's consumption can be attributed to its lanes and MCP servers. Consumption metrics
    are session totals; they are attributed only through a verified Interaction Discovery finding whose ID-2
    answer is FULLY_RECONSTRUCTED and whose every delegation link is RECORDED (parent_agent_id on the lane's
    rows). A link inferred from timing, or no link, never attributes. A verified finding without an ID-2 answer
    verifies no relationship, so it never attributes either (purvi, 2026-11-26)."""
    from .rules import verified
    sm = EA.signal_metrics
    lanes = sorted(l for l in s.trace["lanes"] if l != "main")
    servers = sorted({r["mcp_server"] for r in s.rows if sm.has(r.get("mcp_server"))})
    models = sorted({str(r.get("model") or r.get("model_id")) for r in s.rows if sm.has(r.get("model"))
                     or sm.has(r.get("model_id"))})
    sub = _item(a, "subagent_resource_consumption")
    basis = {"subagent_lanes": lanes, "mcp_servers": servers, "reported_not_judged": _rs_reported(a, "RS-4"),
             "breadth_beyond_limits": [{"metric": m, "value": _item(a, m)["value"], "state": _item(a, m)["metric_state"],
                                        "counted": False, "why": "breadth of the interaction, not a consumption "
                                                                 "measure; Interaction Discovery judges it"}
                                       for m in ("interaction.subagent_lanes", "interaction.mcp_servers")
                                       if _item(a, m)["metric_state"] in states],
             "subagent_tokens": {"value": sub["value"], "presence": sub["state"], "reason": sub["reason"]},
             "models_observed": models,
             "model_selection": {"status": "NOT_EVALUATED", "requirement": "model_selection_compliance",
                                 "detail": "no approved model registry (allowlists kind model)"},
             "unassessed": _unassessed(a, *RS_NOT_BUILT["RS-4"])}
    if not lanes and not servers:
        basis["attribution"] = {"status": "NOT_NEEDED", "reason": "one lane and no MCP server"}
        return "SINGLE_AGENT", basis
    idf = verified(upstream, "interaction_discovery")
    id2q = (((idf or {}).get("cop_specific") or {}).get("questions") or {}).get("ID-2") or {}
    id2 = id2q.get("answer")
    links = [x for x in ((id2q.get("basis") or {}).get("links") or []) if x.get("kind") == "delegation"]
    basis["links"] = {"recorded": [x["link"] for x in links if x["basis"] == "RECORDED"],
                      "inferred": [x["link"] for x in links if x["basis"].startswith("INFERRED")],
                      "unlinked": [x["link"] for x in links if x["basis"] == "UNLINKED"],
                      "from": None if idf is None else idf.get("finding_id")}
    weak = basis["links"]["inferred"] + basis["links"]["unlinked"]
    recorded = {x["link"].split(">", 1)[-1] for x in links if x["basis"] == "RECORDED"}
    no_record = [l for l in lanes if l not in recorded]
    if idf is None or idf["label"] == "PATH_PARTIAL" or id2 != "FULLY_RECONSTRUCTED" or weak or no_record:
        why = ("no verified Interaction Discovery finding" if idf is None else
               "Interaction Discovery's finding carries no ID-2 answer, so no relationship is verified"
               if id2 is None else
               f"Interaction Discovery reports the interaction path as partial (ID-2 {id2})"
               if id2 != "FULLY_RECONSTRUCTED" else
               "Interaction Discovery reports the interaction path as partial" if idf["label"] == "PATH_PARTIAL" else
               "delegation link(s) not recorded: " + ", ".join(weak) if weak else
               "no recorded delegation link for lane(s): " + ", ".join(no_record))
        basis["attribution"] = {"status": "UNATTRIBUTED", "reason": why, "subagent_lanes": len(lanes),
                                "mcp_servers": len(servers)}
        return "UNATTRIBUTED", basis
    edges = (idf.get("cop_specific") or {}).get("edges") or []
    agents = sorted(e["to"][len("agent:"):] for e in edges if e["to"].startswith("agent:"))
    work = [e for e in edges if not e["to"].startswith("agent:")]
    by_actor = {}
    for e in work:
        by_actor[e["from"]] = by_actor.get(e["from"], 0) + e["count"]
    mcp = {e["to"][len("mcp:"):]: e["count"] for e in work if e["to"].startswith("mcp:")}
    total = sum(e["count"] for e in work)
    spans = _rs_spans(s)
    overlapping = sorted(x for x in agents if x in spans and any(
        b not in (x, "main") and spans[b][0] <= spans[x][1] and spans[x][0] <= spans[b][1] for b in spans))
    basis["attribution"] = {
        "status": "ATTRIBUTED", "source_finding": idf.get("finding_id"), "interaction": idf["label"],
        "subagent_lanes": agents, "parallel_lanes": overlapping, "calls_by_actor": by_actor,
        "mcp_calls": mcp, "mcp_call_share": round(sum(mcp.values()) / total, 3) if total else None,
        "subagent_call_share": round(sum(n for x, n in by_actor.items() if x != "main") / total, 3)
        if total else None}
    return "ATTRIBUTED", basis


def _rs_precedents(memory):
    return [{"record_id": x["record_id"], "label": (x.get("payload") or {}).get("label"), "status": x.get("status"),
             "use": "comparison only, never proof"} for x in (memory or {}).get("established", [])][:10]


def _rs_counted(deps):
    """The judged measures in a deviation state (hot) and at WARNING (soft), from the questions that were
    answered: the legacy rule's hot and soft lists."""
    hot, soft = [], []
    for q in ("RS-1", "RS-2", "RS-3"):
        b = deps[q]["basis"] or {}
        for m in b.get("measures", []):
            x = {"question": q, "metric": m["metric"], "value": m["value"], "state": m["state"]}
            if m["verdict"] == DEVIATES:
                hot.append(x)
            elif m.get("warning"):
                soft.append(x)
    return hot, soft


def _rs5(a, deps, upstream, memory):
    from .rules import verified
    pkg = EA.load_package(RS)
    rm = verified(upstream, "runtime_monitoring")
    hot, soft = _rs_counted(deps)
    attention = {q: deps[q]["outcome"] for q in RS_ATTENTION if deps[q]["outcome"] in RS_ATTENTION[q]}
    open_ = {q: deps[q]["outcome"] for q in RS_OBSERVED if deps[q]["outcome"] in RS_OPEN}
    basis = {"hot": hot, "elevated": soft, "burn": [x["metric"] for x in hot if x["metric"] in pkg["burn"]],
             "waste": [x["metric"] for x in hot if x["metric"] in pkg["waste"]],
             "attention": attention, "not_answered": [{"question": q, "answer": o} for q, o in open_.items()],
             "permitted": (deps["RS-1"]["basis"] or {}).get("permitted"),
             "attribution": ((deps["RS-4"]["basis"] or {}).get("attribution") or {}).get("status"),
             "upstream": {"runtime_monitoring": None if not rm else {"label": rm["label"],
                                                                     "finding_id": rm.get("finding_id")}},
             "approaching": {"status": "NOT_EVALUATED", "requirement": "resource_limit_proximity",
                             "detail": "no approved limit-approach threshold and no budgets_and_quotas reference; "
                                       "only exceeding the quota's token_budget is evaluated"},
             "reported_not_judged": _rs_reported(a, "RS-5"),
             "precedents": _rs_precedents(memory), "unassessed": _unassessed(a, *RS_NOT_BUILT["RS-5"])}
    if not rm:
        basis["limitation"] = "no Runtime Monitoring finding was available to this run"
    if deps["RS-1"]["outcome"] == "ABOVE_PERMITTED":
        basis["because"] = "the session's tokens exceed the quota's token_budget"
        return "INTERVENTION_REQUIRED", basis
    if attention:
        basis["because"] = "consumption above the approved bounds: " + ", ".join(
            f"{x['metric']} {x['value']} ({x['state']})" for x in hot + soft)
        return "ATTENTION", basis
    if rm and rm["label"] == "INSUFFICIENT":
        basis["because"] = ("Runtime Monitoring reads the telemetry INSUFFICIENT: consumption measures quiet on "
                            "missing telemetry are not evidence of consumption within bounds")
        return "UNDETERMINED", basis
    if open_:
        basis["because"] = "not every question could be answered: " + ", ".join(f"{q} {o}" for q, o in open_.items())
        return "UNDETERMINED", basis
    basis["because"] = "no judged consumption measure is above its approved bounds and no quota is exceeded"
    return "NO_INTERVENTION", basis


def observe_resource_management(assessments, baselines, session, quota=None, upstream=None, memory=None,
                                comparison=None):
    """{question_id: assessment} -> {question_id: answer}. baselines: _ad_baselines (leave-one-out); quota: tools.get_quota's
    result; memory: {established, verified_pending} usage_baseline records; comparison: rs_baseline_comparison."""
    pkg = EA.load_package(RS)
    states = tuple(pkg["deviation_states"])
    cmp = comparison or {}
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf == "INCONCLUSIVE":
            # a blocking reliability check failed or could not run: nothing observed here is judged
            ms = [_ad_measure(a, m, baselines, states) for m in RS_MEASURES.get(qid, ())]
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf,
                        "basis": {"observed_not_judged": ms, "deviating": []},
                        "because": a["sufficiency"]["reasons"]}
            continue
        if qid == "RS-1":
            outcome, basis = _rs1(a, baselines, states, quota, cmp)
        elif qid == "RS-2":
            outcome, basis = _rs2(a, baselines, states, cmp)
        elif qid == "RS-3":
            outcome, basis = _rs3(a, baselines, states)
        elif qid == "RS-4":
            outcome, basis = _rs4(a, session, states, upstream)
        else:
            outcome, basis = _rs5(a, {k: out[k] for k in q["depends_on"]}, upstream, memory)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        _missing_inputs(pkg, basis)
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE:
            out[qid]["because"] = basis["interpretation_reasons"]
    return out


def assess_resource_management(rows, session_id, cfg=None, computed_at=None, quota=None, upstream=None,
                               memory=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    loo = {e["metric_id"]: e for e in ad_leave_one_out(sessions, s, cfg, list(events.values()))}
    cmp = rs_baseline_comparison(sessions, s, cfg, events, loo)
    events = loo
    base = _ad_baselines(sessions, s, cfg)
    assessments = {qid: EA.assess(RS, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[RS]}
    answers = observe_resource_management(assessments, base, s, quota, upstream, memory, cmp)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_rs(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if o == INCONCLUSIVE:
        s = _explain(qid, ans)
        seen = [m for m in b.get("observed_not_judged", []) if m["verdict"] == DEVIATES]
        return s + (f"; observed, not judged: {_measures_text(seen)}" if seen else "")
    if qid == "RS-5":
        return b["because"]
    if qid == "RS-4":
        at = b["attribution"]
        if o == "SINGLE_AGENT":
            s = "one lane and no MCP server: nothing to attribute"
        elif o == "ATTRIBUTED":
            s = (f"attributed through Interaction Discovery ({at['interaction']}): {len(at['subagent_lanes'])} "
                 f"subagent lane(s) on recorded links, {sum(at['mcp_calls'].values())} MCP call(s)")
        else:
            s = f"{at['subagent_lanes']} subagent lane(s) and {at['mcp_servers']} MCP server(s) not attributed: " \
                f"{at['reason']}"
        return s + "; model selection NOT_EVALUATED (no approved model registry)"
    ms = b["measures"]
    if o == "BASELINE_INSUFFICIENT":
        s = "baseline insufficient: " + "; ".join(f"{m['metric']}: {m['reason']}" for m in ms
                                                  if m["verdict"] == NO_BASELINE)
    elif b["deviating"]:
        s = "above the approved thresholds: " + _measures_text([m for m in ms if m["verdict"] == DEVIATES])
    elif b["warnings"]:
        s = "elevated (WARNING): " + _measures_text([m for m in ms if m.get("warning")])
    elif ms and all(m["verdict"] in (WITHIN, DOES_NOT_APPLY) for m in ms):
        s = "within the approved thresholds: " + _measures_text([m for m in ms if m["verdict"] == WITHIN])
    else:
        s = o
    if qid == "RS-1":
        p = b["permitted"]
        s += (f"; quota: {p['tokens']} tokens against token_budget {p['token_budget']} ({p['status']})"
              if p["status"] != "NOT_EVALUATED" else f"; permission NOT_EVALUATED: {p['reason']}")
        if b.get("not_unauthorized"):
            s += f"; {b['not_unauthorized']}"
    if qid == "RS-3":
        if b["repeated_operations_observed"]:
            s += "; observed, not counted as waste (U-RS2): " + ", ".join(
                f"{x['metric']} {x['value']} ({x['state']})" for x in b["not_counted"] if x["beyond_threshold"])
        s += "; capacity effect NOT_EVALUATED (no capacity data)"
    for x in b.get("not_evaluated", []):
        s += f"; not evaluated: {x['detail']}"
    return s


# What each label proposes; the confidences, severities and actions are cops/interpret.py's resource_management.
RS_REASON = {"ATTENTION": "cost and waste are governance concerns in themselves; whether the work was permitted is "
                          "Policy Enforcement's question",
             "UNAUTHORIZED": "consumption above the configured quota",
             "INDETERMINATE": "whether consumption is within bounds cannot be judged on this evidence; a missing "
                              "meter is not evidence of thrift",
             INCONCLUSIVE: "the evidence needed to decide is missing"}


def _rs_limitations(ans):
    from . import decision as D
    out = []
    one = ans["RS-1"].get("basis") or {}
    p = one.get("permitted") or {}
    if ans["RS-1"]["outcome"] != INCONCLUSIVE and p.get("status") == "NOT_EVALUATED":
        out.append(D.limitation(D.MISSING_EVIDENCE, "RS-1", p["requirement"],
                                EA.MISSING_REFERENCE if p["requirement"] == "budget_or_quota" else EA.MISSING_DATA,
                                f"whether consumption is permitted is not evaluated: {p['reason']}"))
    if p.get("status") in ("WITHIN_QUOTA", "ABOVE_QUOTA") and p.get("period") == NO_QUOTA_PERIOD:
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RS-1", "quota_period", D.LIMIT_NOT_APPROVED, NO_QUOTA_PERIOD))
    for q, (m, what) in RS_ONE_SIDED.items():
        if ans[q]["outcome"] not in RS_OPEN:
            out.append(D.limitation(D.LIMIT_NOT_APPROVED, q, f"{m} (below)", D.LIMIT_NOT_APPROVED,
                                    f"{what} is measured and not judged: no lower limit is approved"))
    for q in ("RS-1", "RS-2"):
        if ans[q]["outcome"] not in RS_OPEN:
            out.append(D.limitation(D.LIMIT_NOT_APPROVED, q, "baseline_window", D.LIMIT_NOT_APPROVED, NO_WINDOW))
    if ans["RS-2"]["outcome"] != INCONCLUSIVE:
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RS-2", "execution_duration_threshold_breach",
                                D.LIMIT_NOT_APPROVED, "no approved session duration threshold: the session's "
                                                      "duration is reported, not judged"))
    if ans["RS-3"]["outcome"] != INCONCLUSIVE:
        out.append(D.limitation(D.MISSING_EVIDENCE, "RS-3", "resource_capacity_utilization", D.NOT_CALCULATED,
                                ans["RS-3"]["basis"]["capacity"]["detail"]))
    four = ans["RS-4"].get("basis") or {}
    if ans["RS-4"]["outcome"] != INCONCLUSIVE:
        out.append(D.limitation(D.MISSING_EVIDENCE, "RS-4", "model_selection_compliance", EA.MISSING_REFERENCE,
                                four["model_selection"]["detail"]))
    for link in (four.get("links") or {}).get("inferred", []):
        out.append(D.limitation(D.INFERRED_RELATIONSHIP, "RS-4", link, "LINK_INFERRED_FROM_TIMING",
                                "the parent-child relationship is inferred from handoff timing, not recorded; no "
                                "consumption is attributed through it"))
    five = ans["RS-5"].get("basis") or {}
    if ans["RS-5"]["outcome"] != INCONCLUSIVE:
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RS-5", "resource_limit_proximity", D.LIMIT_NOT_APPROVED,
                                five["approaching"]["detail"]))
    if ans["RS-5"]["outcome"] == "ATTENTION" and five.get("burn"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RS-5", "workload_record", EA.MISSING_REFERENCE,
                                NO_WORKLOAD_RECORD + ": a legitimate larger workload is not distinguished from "
                                                     "excessive consumption"))
    return out


def _rs_payload(ans):
    five = ans["RS-5"].get("basis") or {}
    return {"answers": {q: x["outcome"] for q, x in ans.items()},
            "counted": [f"{x['metric']}@{x['state']}" for x in five.get("hot", []) + five.get("elevated", [])],
            "baselines": sorted({f"{m['metric']}@{m['baseline']['version']}" for q in ("RS-1", "RS-2", "RS-3")
                                 for m in (ans[q]["basis"] or {}).get("measures", []) if m.get("baseline")}),
            "permitted": (five.get("permitted") or {}).get("status"),
            "attribution": five.get("attribution"),
            "precedents_read": [p["record_id"] for p in five.get("precedents", [])]}


def decide_resource_management(questions, evidence_scale=1.0):
    """The COP decision from the five answers (INTERPRETATION.md). RS-5's answer gives the label; ATTENTION is
    EXCESSIVE when a burn measure is hot and INEFFICIENT otherwise. Confidence, severity, action and recipients
    are the legacy rule's formulas over the counted measures."""
    from ..interpret import SEV, RANK
    pkg = EA.load_package(RS)
    ans = {q: v["answer"] for q, v in questions.items()}
    five = ans["RS-5"]["outcome"]
    b5 = ans["RS-5"].get("basis") or {}
    hot, soft = b5.get("hot", []), b5.get("elevated", [])
    burn = [x for x in hot if x["metric"] in pkg["burn"]]
    waste = [x for x in hot if x["metric"] in pkg["waste"]]
    n = len(hot + soft)
    reading = min(0.9, 0.6 + 0.1 * n) if n else 0.8
    if five == "ATTENTION":
        label = "EXCESSIVE" if burn else "INEFFICIENT"
        loud = len(hot) >= 2 or any(x["state"] == "CRITICAL" for x in hot)
        conf, action = reading, "ALERT" if loud else "WATCH"
        sev = max((SEV[x["state"]] for x in hot or soft), key=RANK.get)
        reason = RS_REASON["ATTENTION"]
        drivers = list(b5["attention"]) + ["RS-5"]
    else:
        label = pkg["labels"][five]
        conf, action, sev, reason = {
            "UNAUTHORIZED": (reading, "ALERT", "HIGH", RS_REASON["UNAUTHORIZED"]),
            "NORMAL": (0.8, "NONE", "NONE", ""),
            "INDETERMINATE": (0.2, "WATCH", "NONE", RS_REASON["INDETERMINATE"]),
            INCONCLUSIVE: (0.0, "WATCH", "NONE", RS_REASON[INCONCLUSIVE])}[label]
        drivers = ["RS-1", "RS-5"] if label == "UNAUTHORIZED" else \
            ([x["question"] for x in b5.get("not_answered", [])] or ["runtime_monitoring"]) + ["RS-5"] \
            if label == "INDETERMINATE" else list(ans)
    drivers = [d for d in drivers if d in ans]
    recipients = (["risk_mitigation"] if burn else []) + (["workload_optimization"] if waste else [])
    trace = _question_trace(questions, _explain_rs, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    three = ans["RS-3"].get("basis") or {}
    contrary += [f"RS-3: {x['metric']} {x['value']} ({x['state']}), recorded and not counted as waste (U-RS2)"
                 for x in three.get("not_counted", []) if x["beyond_threshold"]]
    contrary += [f"RS-4: {x['metric']} {x['value']} ({x['state']}), {x['why']}"
                 for x in (ans["RS-4"].get("basis") or {}).get("breadth_beyond_limits", [])]
    rm = (b5.get("upstream") or {}).get("runtime_monitoring")
    if rm and rm["label"] == "INSUFFICIENT" and five in ("ATTENTION", "INTERVENTION_REQUIRED"):
        contrary.append("RS-5: Runtime Monitoring reads the telemetry INSUFFICIENT; the reading stands on the "
                        "measures observed and its confidence is scaled by the evidence level")
    for q in ("RS-1", "RS-2"):
        lo = (ans[q].get("basis") or {}).get("leave_one_out") or {}
        for m, c in ({"insights.token_outlier": lo} if q == "RS-1" and lo else lo if q == "RS-2" else {}).items():
            if c and c.get("differs"):
                contrary.append(f"{q}: {m} reads {c['leave_one_out']['state']} leave-one-out (used) and "
                                f"{c['self_inclusive']['state']} self-inclusive (the reading before U-RS1)"
                                + ("" if c["leave_one_out"]["baseline_sufficient"] ==
                                   c["self_inclusive"]["baseline_sufficient"] else
                                   f"; the baseline is {'sufficient' if c['self_inclusive']['baseline_sufficient'] else 'insufficient'} "
                                   f"only with this session counted in it"))
    return {"label": label, "confidence": round(conf * evidence_scale, 3), "severity": sev, "action": action,
            "action_reason": reason, "recipients": recipients, "drivers": drivers, "also_held": [],
            "contrary": contrary, "questions": trace, "limitations": _rs_limitations(ans),
            "memory_payload": _rs_payload(ans), "decision": five}


# ---------------------------------------------------------------- Classification & Tagging

CT = "classification_tagging"
CT_FAMILY_QUESTIONS = ("CT-1", "CT-2", "CT-3")
CT_OBSERVED = {"CT-1": ("access.first_seen_mcp",),
               "CT-2": ("insights.no_oversight", "oversight.fast_approvals", "oversight.declined_inferred")}
CT_CONCERNS = ("insights.failed", "insights.repetition", "insights.retry", "insights.stalled", "insights.reverted",
               "insights.lockfile", "insights.token_outlier", "insights.duration_outlier", "path.transition_novelty",
               "activity.action_rate_ratio", "interaction.subagent_lanes", "interaction.mcp_servers")
CT_TELEMETRY = ("insights.unattributed", "telemetry.collector_coverage", "telemetry.source_agreement",
                "telemetry.turn_linkage", "telemetry.lane_linkage", "telemetry.unknown_share")
# The canonical upstream (specs.SPEC_CONSUMES) and the three purvi named on 2026-11-26 (TOPOLOGY_CONSUMES).
CT_UPSTREAM = ("anomaly_detection", "agent_lifecycle_management", "policy_enforcement", "interaction_discovery",
               "resource_management")
CT_UNMAPPED = ("no approved rule maps a behavioural, execution or resource concern to LOOPS, OUTCOME or COST "
               "(cops/specs.py interpretations); the concern is recorded and does not classify the event")
CT_ENTITY_FIELDS = ("observable_type", "collector", "session_id", "turn_id", "agent_id", "parent_agent_id",
                    "operator_id", "tool_use_id", "tool_name", "file_path", "command", "mcp_server", "mcp_tool",
                    "raw_ref")
CT_REASON = {INCONCLUSIVE: "the event could not be classified: see the question answers"}
OUTSIDE_CANDIDATES = "INTERPRETATION_OUTSIDE_CANDIDATES"


def _ct_family_of(pkg):
    return {fam: d["question"] for fam, d in pkg["families"].items()}


def _ct_measures(a, metrics, states):
    return [_ad_measure(a, m, {}, states) for m in metrics]


def _ct_disputed(session):
    """The rows the collectors disagree on (collectors_agree false), by evidence ref."""
    return {f"observable:{r.get('observable_id')}" for r in (session.rows if session else [])
            if r.get("collectors_agree") is False}


def _ct_family(qid, a, pkg, states, session=None):
    """CT-1 to CT-3: whether the question's family is indicated by its counted metrics (pkg families). A counted
    metric in a deviation state indicates the family even when another is unobserved (positive evidence stands);
    nothing indicated with a counted metric unobserved leaves the question unanswered (workbook row 11).
    purvi, 2026-11-27 (U-CT3, D-CT18): when the collectors disagree on every row behind the indicating metrics,
    the family is directly disputed and has no independent support: the question is INCONCLUSIVE. One
    undisputed indicating row supports it; the disputed rows are contrary evidence."""
    fam = next(f for f, d in pkg["families"].items() if d["question"] == qid)
    counted = _ct_measures(a, pkg["families"][fam]["metrics"], states)
    observed = _ct_measures(a, CT_OBSERVED.get(qid, ()), states)
    basis = {"family": fam, "measures": counted, "indicating": [m["metric"] for m in counted if m["verdict"] == DEVIATES],
             "observed_not_counted": [dict(m, counted=False, why="assigned to this question and observed; only "
                                                                 "cops/specs.py metric_keys indicate a family")
                                      for m in observed],
             "warnings": [m["metric"] for m in counted if m.get("warning")]}
    if basis["indicating"]:
        refs = sorted({r for m in counted if m["verdict"] == DEVIATES for r in m.get("evidence_refs") or []})
        bad = _ct_disputed(session)
        basis["indicating_refs"] = refs
        basis["disputed_refs"] = [r for r in refs if r in bad]
        if refs and len(basis["disputed_refs"]) == len(refs):
            basis["disputed"] = True
            basis["interpretation_reasons"] = [
                {"code": EA.CONFLICTING_SOURCES, "requirement": m, "check": "collectors_agree",
                 "detail": f"{m} indicates {fam} only on rows the collectors disagree on "
                           f"({', '.join(basis['disputed_refs'])}): directly disputed evidence cannot establish "
                           f"{fam} without independent support"} for m in basis["indicating"]]
            return INCONCLUSIVE, basis
        return "FAMILY_INDICATED", basis
    open_ = [m for m in counted if m["verdict"] not in (WITHIN, DOES_NOT_APPLY)]
    if not open_:
        return "NOT_INDICATED", basis
    basis["interpretation_reasons"] = [
        {"code": EA.MISSING_DATA, "requirement": m["metric"], "check": None,
         "detail": f"{m['metric']} {m['reason']}: whether the event is {fam} is not evaluated (missing metadata degrades "
                   f"classification, workbook row 11)"} for m in open_]
    return INCONCLUSIVE, basis


def _ct_upstream(upstream):
    """Each consumed COP's finding as context: present or absent, verified or not, and whether its lineage is
    independent of this COP's counted metrics (rules.independent_upstream). Upstream findings never indicate a
    family and are never counted."""
    from .rules import independent_upstream
    from ..specs import BY_KEY as _BY
    up = upstream or {}
    own = set(_BY[CT].metric_keys)
    split = independent_upstream(own, {k: up[k] for k in CT_UPSTREAM if k in up})
    corr = {x["cop"]: x["why"] for x in split["correlated"]}
    # a producer whose own counted metrics are this COP's reads the same evidence, whatever its lineage records
    # (Policy Enforcement's lineage holds tool calls; its metric_keys hold the four counted metrics)
    for k in CT_UPSTREAM:
        same = sorted(set(_BY[k].metric_keys) & own)
        if k in up and same and k not in corr:
            corr[k] = f"its metric_keys share {same}"
    out = {}
    for k in CT_UPSTREAM:
        f = up.get(k)
        if f is None:
            out[k] = {"present": False, "used": False, "why": "no finding in this case"}
            continue
        ok = bool(f.get("verified")) and f.get("status") == "SUBSTANTIVE"
        out[k] = {"present": True, "label": f.get("label"), "finding_id": f.get("finding_id"),
                  "verified": bool(f.get("verified")), "status": f.get("status"),
                  "lineage": sorted(f.get("lineage") or []), "used": ok,
                  "counted_as": ("not used: not a verified substantive finding" if not ok else
                                 f"context, not counted: correlated ({corr[k]})" if k in corr else
                                 "context, not counted: independent lineage, but upstream findings never "
                                 "indicate a family")}
    return out


def _ct4(a, states, upstream_view):
    ms = _ct_measures(a, CT_CONCERNS, states)
    concerns = [m for m in ms if m["verdict"] == DEVIATES]
    up = [{"cop": k, "label": v["label"], "finding_id": v["finding_id"], "counted_as": v["counted_as"]}
          for k, v in upstream_view.items() if k in ("anomaly_detection", "resource_management") and v["used"]
          and v["label"] not in ("NORMAL", "INCONCLUSIVE")]
    basis = {"measures": ms, "concerns": [m["metric"] for m in concerns],
             "warnings": [m["metric"] for m in ms if m.get("warning")],
             "unobserved": [m["metric"] for m in ms if m["verdict"] in (UNOBSERVED, NO_BASELINE)],
             "upstream_concerns": up, "counted": False,
             "baselines": "insights.token_outlier, insights.duration_outlier and activity.action_rate_ratio are read "
                          "as signal_metrics computes them (self-inclusive); they are observed, never counted",
             "unmapped": CT_UNMAPPED if concerns or up else None}
    return ("CONCERNS_OBSERVED" if concerns or up else "NONE_OBSERVED"), basis


def _ct_lane_links(upstream):
    """(recorded delegation lanes, why not) from a verified Interaction Discovery finding: only a RECORDED link
    on an answered, FULLY_RECONSTRUCTED ID-2 associates a subagent lane with its parent (as RS-4)."""
    from .rules import verified
    idf = verified(upstream, "interaction_discovery")
    if idf is None:
        return set(), "no verified Interaction Discovery finding"
    id2q = (((idf.get("cop_specific") or {}).get("questions") or {}).get("ID-2")) or {}
    if id2q.get("answer") is None:
        return set(), "Interaction Discovery's finding carries no ID-2 answer, so no relationship is verified"
    links = [x for x in ((id2q.get("basis") or {}).get("links") or []) if x.get("kind") == "delegation"]
    rec = {x["link"].split(">", 1)[-1]: x["link"] for x in links if x["basis"] == "RECORDED"}
    if id2q["answer"] != "FULLY_RECONSTRUCTED" or idf["label"] == "PATH_PARTIAL":
        return set(), f"Interaction Discovery reports the interaction path as partial (ID-2 {id2q['answer']})"
    return rec, None


def _ct_entities(s, refs, upstream):
    """The actors, resources and evidence the event is associated with: the rows the indicating metrics cite,
    their recorded fields, and whether a subagent lane is linked to its parent through a recorded link."""
    sm = EA.signal_metrics
    want = set(refs)
    rows = [r for r in s.rows if f"observable:{r.get('observable_id')}" in want]
    rec, why = _ct_lane_links(upstream)
    ents = []
    for r in rows:
        lane = r.get("agent_id") or "main"
        e = {"ref": f"observable:{r['observable_id']}", **{k: r.get(k) for k in CT_ENTITY_FIELDS if sm.has(r.get(k))},
             "lane": lane, "missing_fields": [k for k in ("agent_id", "tool_name", "raw_ref") if not sm.has(r.get(k))]}
        if lane == "main":
            e["linkage"] = {"status": "MAIN_LANE"}
        elif lane in rec:
            e["linkage"] = {"status": "RECORDED", "link": rec[lane], "through": "interaction_discovery"}
        else:
            e["linkage"] = {"status": "NOT_LINKED", "reason": why or f"no recorded delegation link for lane {lane}"}
        ents.append(e)
    st = "NO_ENTITIES" if not ents else \
        ("LINKED" if all(e["linkage"]["status"] in ("MAIN_LANE", "RECORDED") for e in ents) else "PARTIAL")
    return ents, st


def _ct5(a, s, states, deps, pkg, taxonomy, upstream_view, upstream, memory):
    fam_q = _ct_family_of(pkg)
    tel = _ct_measures(a, CT_TELEMETRY, states)
    tax_ok = bool(taxonomy) and "missing_dependency" not in taxonomy
    defined = {x["id"] for x in (taxonomy or {}).get("families", [])} if tax_ok else set()
    indicated = [f for f in pkg["precedence"] if deps[fam_q[f]]["outcome"] == "FAMILY_INDICATED"]
    undefined = [f for f in indicated if f not in defined]
    cands = [f for f in indicated if f in defined]
    unanswered = [q for q in CT_FAMILY_QUESTIONS if deps[q]["outcome"] == INCONCLUSIVE]
    disputed_q = [q for q in unanswered if (deps[q]["basis"] or {}).get("disputed")]
    judged = [m for q in CT_FAMILY_QUESTIONS for m in (deps[q]["basis"] or {}).get("measures", [])
              if m["verdict"] in (WITHIN, DEVIATES)]
    refs = sorted({r for f in cands for m in deps[fam_q[f]]["basis"]["measures"] if m["verdict"] == DEVIATES
                   for r in m["evidence_refs"]})
    ents, link = _ct_entities(s, refs, upstream)
    basis = {"taxonomy": {"version": (taxonomy or {}).get("version") if tax_ok else None,
                          "status": "APPROVED" if tax_ok else "NOT_SUPPLIED", "families": sorted(defined)},
             "indicated": indicated, "candidates": cands, "undefined": undefined,
             "families_not_evaluated": [next(f for f, q in fam_q.items() if q == x) for x in unanswered
                                        if x not in disputed_q],
             "disputed": [next(f for f, q in fam_q.items() if q == x) for x in disputed_q],
             "precedence_reading": cands[0] if cands else None,
             "entities": ents, "linkage": link, "telemetry": tel,
             "telemetry_deviating": [m["metric"] for m in tel if m["verdict"] == DEVIATES],
             "upstream": upstream_view, "precedents": _rs_precedents(memory),
             "not_calculated": [{"metric": k, "state": EA.NOT_CALCULATED, "missing_inputs": " ".join(v.split())}
                                for k, v in pkg["not_calculated"].items()]}
    # collector disagreement, split by whether it touches a row behind an indicating metric (U-CT3)
    bad = _ct_disputed(s)
    ind_refs = {r for q in CT_FAMILY_QUESTIONS for r in (deps[q]["basis"] or {}).get("indicating_refs", [])}
    basis["disagreement"] = {"rows": sorted(bad), "on_indicating_rows": sorted(bad & ind_refs),
                             "unrelated": sorted(bad - ind_refs)}
    basis["evidence_status"] = {
        "observed": [f"{m['metric']}@{m['state']}" for q in CT_FAMILY_QUESTIONS
                     for m in (deps[q]["basis"] or {}).get("measures", []) if m["verdict"] == DEVIATES],
        "disputed": sorted(bad & ind_refs),
        "inferred": None, "insufficient": []}
    if not tax_ok:
        basis["evidence_status"]["insufficient"].append("no approved taxonomy")
        basis["interpretation_reasons"] = [{"code": EA.MISSING_REFERENCE, "requirement": "taxonomy", "check": None,
                                            "detail": "no approved, versioned taxonomy: no family can be named"}]
        return "UNDETERMINED", basis
    if len(cands) == 1:
        basis["evidence_status"]["inferred"] = {"family": cands[0], "by": "package rule (one candidate family)"}
        return "CLASSIFIED", basis
    if len(cands) > 1:
        basis["evidence_status"]["inferred"] = {"family": None, "by": "model interpretation required",
                                                "candidates": cands}
        basis["interpretation_reasons"] = [{"code": INTERPRETATION_REQUIRED, "requirement": "interpretation",
                                            "check": None, "detail": f"{len(cands)} candidate families "
                                            f"({', '.join(cands)}): which describes the event is read against the "
                                            f"taxonomy's definitions"}]
        return "AMBIGUOUS", basis
    why = ([("FAMILY_NOT_IN_TAXONOMY", f, f"{f} is indicated but not defined in taxonomy "
                                         f"{basis['taxonomy']['version']}") for f in undefined]
           + [(r.get("code") or EA.MISSING_DATA, r["requirement"], f"{q} not answered: {r['detail']}")
              for q in unanswered for r in deps[q].get("because") or []]
           + ([] if judged else [(EA.MISSING_DATA, "classification", "no counted metric could be judged")]))
    if why:
        basis["evidence_status"]["insufficient"] = [w[2] for w in why]
        basis["interpretation_reasons"] = [{"code": c, "requirement": req, "check": None, "detail": d}
                                           for c, req, d in why]
        return "UNDETERMINED", basis
    return "UNCLASSIFIED", basis


def observe_classification_tagging(assessments, session, taxonomy=None, upstream=None, memory=None):
    """{question_id: assessment} -> {question_id: answer}. taxonomy: tools.query_taxonomy's result; memory:
    {established, verified_pending} classification_example records."""
    pkg = EA.load_package(CT)
    states = tuple(pkg["deviation_states"])
    view = _ct_upstream(upstream)
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if qid in CT_FAMILY_QUESTIONS:
            outcome, basis = _ct_family(qid, a, pkg, states, session)
        elif qid == "CT-4":
            outcome, basis = _ct4(a, states, view)
        else:
            outcome, basis = _ct5(a, session, states, {k: out[k] for k in q["depends_on"]}, pkg, taxonomy, view,
                                  upstream, memory)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis, "findings": a["findings"],
                    "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE or basis.get("interpretation_reasons"):
            out[qid]["because"] = basis.get("interpretation_reasons", [])
    return out


def assess_classification_tagging(rows, session_id, cfg=None, computed_at=None, taxonomy=None, upstream=None,
                                  memory=None, raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    assessments = {qid: EA.assess(CT, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[CT]}
    answers = observe_classification_tagging(assessments, s, taxonomy, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_ct(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if qid in CT_FAMILY_QUESTIONS:
        if o == INCONCLUSIVE:
            return _explain(qid, ans)
        if o == "FAMILY_INDICATED":
            s = f"{b['family']} indicated by " + _measures_text([m for m in b["measures"] if m["verdict"] == DEVIATES])
            if b.get("disputed_refs"):
                s += (f"; collectors disagree on {', '.join(b['disputed_refs'])}, supported by "
                      f"{', '.join(r for r in b['indicating_refs'] if r not in b['disputed_refs'])}")
        else:
            s = f"{b['family']} not indicated: " + _measures_text(b["measures"])
        seen = [m for m in b["observed_not_counted"] if m["verdict"] == DEVIATES]
        return s + (f"; observed, not counted: {_measures_text(seen)}" if seen else "")
    if qid == "CT-4":
        if o == "NONE_OBSERVED":
            s = "no behavioural, execution or resource measure in a deviation state"
        else:
            s = "concerns observed, not counted: " + ", ".join(
                b["concerns"] + [f"{u['cop']} {u['label']}" for u in b["upstream_concerns"]])
        return s + (f"; unobserved: {', '.join(b['unobserved'])}" if b["unobserved"] else "")
    s = {"CLASSIFIED": lambda: f"one candidate family: {b['candidates'][0]}",
         "AMBIGUOUS": lambda: f"candidate families {', '.join(b['candidates'])}: interpretation against taxonomy "
                              f"{b['taxonomy']['version']} required (precedence reading {b['precedence_reading']})",
         "UNCLASSIFIED": lambda: f"no family indicated; every counted metric judged against taxonomy "
                                 f"{b['taxonomy']['version']}",
         "UNDETERMINED": lambda: "not classified: " + "; ".join(b["evidence_status"]["insufficient"]),
         INCONCLUSIVE: lambda: b.get("interpretation_text") or "not answered"}[o]()
    if b.get("entities"):
        s += f"; {len(b['entities'])} associated event(s), linkage {b['linkage']}"
    return s


def _ct_limitations(ans):
    from . import decision as D
    out = []
    five = ans["CT-5"].get("basis") or {}
    for f in five.get("undefined", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "CT-5", f, "FAMILY_NOT_IN_TAXONOMY",
                                f"{f} is indicated but taxonomy {five['taxonomy']['version']} does not define it: it "
                                f"is not named"))
    for f in five.get("families_not_evaluated", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, _ct_family_q(f), f, EA.MISSING_DATA,
                                f"whether the event is also {f} is not evaluated: its metadata is missing"))
    for f in five.get("disputed", []):
        q = _ct_family_q(f)
        refs = (ans[q].get("basis") or {}).get("disputed_refs", [])
        out.append(D.limitation(D.MISSING_EVIDENCE, q, f, EA.CONFLICTING_SOURCES,
                                f"{f} is indicated only on rows the collectors disagree on: directly disputed "
                                f"evidence does not establish it without independent support", refs))
    four = ans["CT-4"].get("basis") or {}
    if four.get("unmapped"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "CT-4", "LOOPS/OUTCOME/COST", D.LIMIT_NOT_APPROVED,
                                four["unmapped"]))
    for e in five.get("entities", []):
        if e["linkage"]["status"] == "NOT_LINKED":
            out.append(D.limitation(D.INFERRED_RELATIONSHIP, "CT-5", f"lane {e['lane']}", "LANE_NOT_LINKED",
                                    f"{e['ref']} was made in lane {e['lane']}; its parent is not associated: "
                                    f"{e['linkage']['reason']}", [e["ref"]]))
    dis = five.get("disagreement") or {}
    for m in five.get("telemetry_deviating", []):
        st = next(x['state'] for x in five['telemetry'] if x['metric'] == m)
        if m == "telemetry.source_agreement" and dis.get("rows"):
            touch = dis["on_indicating_rows"]
            text = (f"{m} reads {st}: the collectors disagree on " + ", ".join(dis["rows"]) + "; "
                    + (f"{', '.join(touch)} indicate(s) a family and is treated as disputed" if touch else
                       "none of these rows indicates a family, so the disagreement is unrelated and the "
                       "classification stands on the indicators observed"))
            out.append(D.limitation(D.MISSING_EVIDENCE, "CT-5", m, "TELEMETRY_QUALITY", text, dis["rows"]))
        else:
            out.append(D.limitation(D.MISSING_EVIDENCE, "CT-5", m, "TELEMETRY_QUALITY",
                                    f"{m} reads {st}: the classification stands on the indicators observed"))
    if ans["CT-5"]["outcome"] not in (INCONCLUSIVE,):
        for x in five.get("not_calculated", []):
            out.append(D.limitation(D.NOT_CALCULATED, "CT-5", x["metric"], D.NOT_CALCULATED, x["missing_inputs"]))
    return out


def _ct_family_q(family):
    return EA.load_package(CT)["families"][family]["question"]


def _ct_payload(ans, decided_by):
    five = ans["CT-5"].get("basis") or {}
    return {"answers": {q: x["outcome"] for q, x in ans.items()}, "candidates": five.get("candidates", []),
            "indicated": five.get("indicated", []), "taxonomy_version": (five.get("taxonomy") or {}).get("version"),
            "decided_by": decided_by, "entities": [e["ref"] for e in five.get("entities", [])],
            "precedents_read": [p["record_id"] for p in five.get("precedents", [])]}


def decide_classification_tagging(questions, evidence_scale=1.0):
    """The COP decision from the five answers (INTERPRETATION.md). CT-5 gives the label: CLASSIFIED is the one
    candidate family; UNCLASSIFIED the quiet reading; UNDETERMINED is INCONCLUSIVE. AMBIGUOUS is not decided here:
    the model interprets it and interpreted_classification_tagging reads its verified label back. Confidence and
    severity are the legacy rule's formulas over the classified family's indicating measures; the action is NONE."""
    from ..interpret import SEV, RANK
    pkg = EA.load_package(CT)
    ans = {q: v["answer"] for q, v in questions.items()}
    five = ans["CT-5"]["outcome"]
    b5 = ans["CT-5"].get("basis") or {}
    fam_q = _ct_family_of(pkg)
    decided = five in ("CLASSIFIED", "UNCLASSIFIED", "UNDETERMINED")
    label, conf, sev, reason, drivers = INCONCLUSIVE, 0.0, "NONE", CT_REASON[INCONCLUSIVE], list(ans)
    if five == "CLASSIFIED":
        label = b5["candidates"][0]
        hot = [m for m in ans[fam_q[label]]["basis"]["measures"] if m["verdict"] == DEVIATES]
        conf = min(0.9, 0.65 + 0.1 * len(hot))
        sev = max((SEV[m["state"]] for m in hot), key=RANK.get)
        reason, drivers = "", [fam_q[label], "CT-5"]
    elif five == "UNCLASSIFIED":
        label, conf, reason, drivers = "UNCLASSIFIED", 0.7, "", list(CT_FAMILY_QUESTIONS) + ["CT-5"]
    elif five == "AMBIGUOUS":
        drivers = [fam_q[f] for f in b5["candidates"]] + ["CT-5"]
    trace = _question_trace(questions, _explain_ct, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    if five == "CLASSIFIED" and len(b5["indicated"]) > 1:
        contrary += [f"CT-5: {f} is also indicated and not named: not in taxonomy {b5['taxonomy']['version']}"
                     for f in b5["undefined"]]
    four = ans["CT-4"].get("basis") or {}
    contrary += [f"CT-4: {m} in a deviation state, recorded and not counted: {CT_UNMAPPED}" for m in four.get("concerns", [])]
    for q in CT_FAMILY_QUESTIONS:
        for m in (ans[q].get("basis") or {}).get("observed_not_counted", []):
            if m["verdict"] == DEVIATES:
                contrary.append(f"{q}: {m['metric']} {m['value']} ({m['state']}), observed and not counted")
    contrary += [f"CT-5: {m} in a deviation state; the classification stands on the indicators observed"
                 for m in b5.get("telemetry_deviating", [])]
    for q in CT_FAMILY_QUESTIONS:
        bq = ans[q].get("basis") or {}
        if bq.get("disputed_refs"):
            contrary.append(f"{q}: the collectors disagree on {', '.join(bq['disputed_refs'])}, which indicate(s) "
                            f"{bq['family']}" + (": the family is disputed and not established" if bq.get("disputed")
                                                 else "; other indicating rows are undisputed"))
    for k, v in (b5.get("upstream") or {}).items():
        if v["used"] and k == "policy_enforcement" and label in pkg["families"]:
            contrary.append(f"CT-5: Policy Enforcement reads {v['label']}: whether the event is permitted is its "
                            f"question; the kind is {label} either way ({v['counted_as']})")
    return {"label": label, "confidence": round(conf * evidence_scale, 3), "severity": sev, "action": "NONE",
            "action_reason": reason, "recipients": ["governance_intelligence"] if label in pkg["families"] else [],
            "drivers": drivers, "also_held": [], "contrary": contrary, "questions": trace,
            "limitations": _ct_limitations(ans), "decided": decided,
            "memory_payload": _ct_payload(ans, "package" if decided else "model"), "decision": five}


def interpreted_classification_tagging(f, baseline):
    """CT-5 after verification, for a finding the model concluded on an ambiguous event: its verified label read
    back as CT-5's answer when it is one of the candidate families, otherwise INCONCLUSIVE with why. A model label
    outside the candidates is not a classification: the finding becomes INCONCLUSIVE and the label is kept as the
    rejected candidate. CT-1 to CT-4 are the package's, unchanged. A finding the package decided is returned as
    it is."""
    import copy
    from .contracts import ORIGIN_LLM
    cs = (baseline or {}).get("cop_specific") or {}
    if f.get("origin") != ORIGIN_LLM or not cs.get("questions"):
        return f
    q = copy.deepcopy(cs["questions"])
    five = q["CT-5"]
    cands = (five.get("basis") or {}).get("candidates") or []
    reasons = [r for r in five.get("sufficiency_reasons", []) if r.get("code") != INTERPRETATION_REQUIRED]
    detail = (f.get("cop_specific") or {}).get("detail") or {}
    interp = {"by": "llm", "verified": bool(f.get("verified")), "status": f["status"], "label": f["label"],
              "candidates": cands, "model_candidate_families": detail.get("candidate_families", []),
              "rejected_families": detail.get("rejected_families", {}),
              "taxonomy_version": detail.get("taxonomy_version"), "entities": detail.get("entities", [])}
    out = dict(f)
    if f["status"] == "SUBSTANTIVE" and f["label"] in cands:
        answer = "CLASSIFIED"
        text = (f"model interpretation {f['label']} ({'verified' if f.get('verified') else 'not verified'}), one of "
                f"{', '.join(cands)}" + (f"; rejected: " + "; ".join(f"{k} ({v})" for k, v in
                                                                   interp["rejected_families"].items())
                                          if interp["rejected_families"] else ""))
    else:
        answer = INCONCLUSIVE
        if f["status"] == "SUBSTANTIVE":
            r = {"code": OUTSIDE_CANDIDATES, "requirement": "interpretation", "check": None,
                 "detail": f"model interpretation {f['label']} is not one of the candidate families "
                           f"({', '.join(cands)}) the indicators support"}
            out.update(label=INCONCLUSIVE, status="INCONCLUSIVE", confidence=0.0,
                       rejected_candidate={"label": f["label"], "confidence": f.get("confidence"),
                                           "reason": r["detail"]},
                       rationale=f"INCONCLUSIVE: {r['detail']}; model rationale: {f.get('rationale', '')}"[:600])
        else:
            cand = (f.get("rejected_candidate") or {}).get("label")
            r = ({"code": "INTERPRETATION_REJECTED", "requirement": "interpretation", "check": None,
                  "detail": f"model interpretation {cand} rejected: {f.get('rationale', '')[:240]}"} if cand else
                 {"code": "NO_INTERPRETATION", "requirement": "interpretation", "check": None,
                  "detail": "no verified model interpretation: "
                            + str((f.get("cop_specific") or {}).get("stop_reason") or f.get("rationale", ""))[:200]})
        reasons.append(r)
        text = "not answered: " + r["detail"]
    five.update(answer=answer, explanation=text, sufficiency_reasons=reasons, drives_label=True)
    five["basis"] = {**(five["basis"] or {}), "interpretation": interp, "interpretation_text": text,
                     "evidence_status": {**((five["basis"] or {}).get("evidence_status") or {}),
                                         "inferred": {"family": f["label"] if answer == "CLASSIFIED" else None,
                                                      "by": "model interpretation", "candidates": cands}}}
    pay = {**(cs.get("memory_payload") or {}), "answers": {k: v["answer"] for k, v in q.items()}, "decided_by": "model"}
    out["cop_specific"] = {**(f.get("cop_specific") or {}), "questions": q, "drivers": cs.get("drivers", []),
                           "limitations": cs.get("limitations", []), "contrary": cs.get("contrary", []),
                           "recipients": ["governance_intelligence"] if answer == "CLASSIFIED" else [],
                           "memory_payload": pay, "decision": answer}
    return out


# ---------------------------------------------------------------- Risk Mitigation

RK = "risk_mitigation"
RK_QUESTIONS = ("RK-1", "RK-2", "RK-3", "RK-4")
RK_REASON = {INCONCLUSIVE: "the risk could not be assessed: see the question answers"}
RK_AUTHORITY = ("Risk Mitigation recommends controls and escalates; it never applies a control, never blocks or "
                "pauses an agent, and never accepts residual risk on the owner's behalf (triage handoff)")
RK_NO_INVENTORY = ("no enterprise asset inventory (asset, owner, criticality, workflow dependencies): exposure is "
                   "named by the events and findings involved, not by asset criticality or ownership")
RK_NO_RESIDUAL = ("residual risk after controls is not established: no approved mitigation effectiveness and no "
                  "record of an applied mitigation (risk_residual_score, risk_mitigation_effectiveness "
                  "NOT_CALCULATED)")


def _rk_pkg():
    return EA.load_package(RK)


def _rk_built(pkg, qid):
    """The built metrics a question requires (the unbuilt ones carry a NOT_CALCULATED note in the mapping)."""
    return [r["metric"] for r in pkg["questions"][qid]["requires"] if not (pkg["mapping"][r["metric"]] or {}).get("note")]


def _rk_not_built(pkg, qid):
    return [{"metric": r["metric"], "state": EA.NOT_CALCULATED,
             "missing_inputs": " ".join(pkg["mapping"][r["metric"]]["note"].split())}
            for r in pkg["questions"][qid]["requires"] if (pkg["mapping"][r["metric"]] or {}).get("note")]


def _rk_irreversible(session):
    """Irreversible operations (tools.IRREVERSIBLE on the command field), split by whether the collectors dispute
    the row (collectors_agree false)."""
    from .tools import IRREVERSIBLE
    rows = [r for r in (session.rows if session else []) if IRREVERSIBLE.search(str(r.get("command") or ""))]
    ref = lambda r: f"observable:{r.get('observable_id')}"
    return ([ref(r) for r in rows if r.get("collectors_agree") is not False],
            [ref(r) for r in rows if r.get("collectors_agree") is False])


RK_DECISION_ITEMS = ("DENY", "REQUIRE_APPROVAL")
# P-RK2 (purvi, 2026-11-27): conditions, recorded with their reasons and never counted as a signal or correlated
RK_CONDITIONS = {
    ("policy_enforcement", "APPROVAL_REQUIRED"): (
        "PENDING_AUTHORIZATION", "a pending authorization condition, not a verified violation: it does not increase "
                                 "the score on its own; the approval workflow stays with Policy Enforcement"),
    ("incident_response", "WATCH"): (
        "ADVISORY", "an advisory condition, not an incident: the independently verified evidence under it counts "
                    "through its own producer, never WATCH itself"),
}


def rk_counted_metrics():
    from ..specs import BY_KEY as _BY
    return tuple(_BY[RK].metric_keys)


def _rk_walk(x, out, depth=0):
    """Source items in a driver question's basis: decision items (DENY, REQUIRE_APPROVAL) by their row, or by the
    proposed operation when they have no row; measures in a deviation state by metric and row; a quota exceeded."""
    if depth > 6:
        return
    if isinstance(x, dict):
        if x.get("metric") and (x.get("verdict") == DEVIATES or x.get("state") in ("EXCEPTION", "CRITICAL")):
            if not str(x["metric"]).startswith("telemetry."):
                out.add(f"metric:{x['metric']}")
            out.update(r for r in x.get("evidence_refs") or [] if str(r).startswith("observable:"))
        if "ref" in x and x.get("decision") in RK_DECISION_ITEMS:
            out.add(x["ref"] or f"proposed:{x.get('action')}:{x.get('resource')}")
        if x.get("status") == "ABOVE_QUOTA":
            out.add(f"quota:{x.get('source')}")
        for v in x.values():
            _rk_walk(v, out, depth + 1)
    elif isinstance(x, list):
        for v in x:
            _rk_walk(v, out, depth + 1)


def rk_source_items(f, upstream=None, depth=0):
    """The source evidence a finding rests on (D-RK16, U-RK3): observable rows, metrics, proposed operations and
    quota records, from its lineage, its claims and its driver questions. Tool-call ids are not source items, and
    the telemetry measures (evidence quality, read by every COP) are not either. An upstream finding a claim cites
    contributes its own items."""
    out = set()
    for x in f.get("lineage") or []:
        x = str(x)
        if x.startswith("tool_call:"):
            continue
        if x.startswith("observable:"):
            out.add(x)
        elif not x.startswith("telemetry."):
            out.add(f"metric:{x}")
    byid = {u.get("finding_id"): u for u in (upstream or {}).values() if u}
    for c in f.get("claims") or []:
        av = c.get("asserted_value")
        for r in list(c.get("evidence_refs") or []) + (av if isinstance(av, list) else []):
            r = str(r)
            if r.startswith("observable:"):
                out.add(r)
            elif r.startswith("metric:") and not r.startswith("metric:telemetry."):
                out.add(r)
            elif r.startswith("finding:") and depth < 2 and byid.get(r[8:]) is not None:
                out |= rk_source_items(byid[r[8:]], upstream, depth + 1)
    cs = f.get("cop_specific") or {}
    for q in cs.get("drivers") or []:
        _rk_walk(((cs.get("questions") or {}).get(q) or {}).get("basis"), out)
    return out


def rk_signal_split(upstream, own_refs=()):
    """The consumed findings that can be risk signals: verified substantive with a risk-bearing label, split into
    independent and correlated. Used by tools.calculate_risk_components, so the legacy rule, VS-01's recomputation
    and this package read the same signals.

    A finding is correlated when its lineage overlaps this COP's counted metrics or a counted signal's lineage
    (rules.independent_upstream, D-RK5), or (purvi, 2026-11-27, U-RK3; D-RK16) when every source item it rests on
    is already carried by this COP's own observed evidence (own_refs: the rows behind its counted metrics in a
    deviation state and its undisputed irreversible operations) or by a signal already counted. Different tool-call
    ids over the same row are one signal. Producers are taken first (specs.topological_order), so a finding derived
    from another is not counted ahead of its source. Quiet and undetermined readings are named, never counted;
    a context-only producer (Classification & Tagging, U-RK1) is listed as context."""
    from .rules import independent_upstream
    from ..specs import CONSUMES, topological_order
    pkg = _rk_pkg()
    risky, quiet, und, context, conds = {}, [], [], [], {}
    for k in CONSUMES[RK]:
        f = (upstream or {}).get(k)
        if not (f and f.get("verified") and f.get("status") == "SUBSTANTIVE"):
            continue
        spec = pkg["upstream"][k]
        if spec.get("context_only"):
            context.append(k)
        elif f["label"] in spec.get("condition", []):
            conds[k] = f
        elif f["label"] in spec["risk"]:
            risky[k] = f
        elif f["label"] in spec["quiet"]:
            quiet.append(k)
        else:
            und.append(k)
    own = set(rk_counted_metrics())
    seen_lin = set(own)
    seen_src = {f"metric:{m}" for m in own} | set(own_refs or ())
    indep, corr = [], []
    for k in [x for x in topological_order(list(risky)) if x in risky]:
        f = risky[k]
        lin = set(f.get("lineage") or [])
        src = rk_source_items(f, upstream)
        row = {"cop": k, "label": f["label"], "sources": sorted(src)}
        if not lin:
            corr.append(dict(row, why="no lineage recorded; cannot show independence"))
        elif lin & seen_lin:
            corr.append(dict(row, why=f"shares {sorted(lin & seen_lin)}"))
        elif not src:
            corr.append(dict(row, why="no source evidence beyond tool-call ids and telemetry; independence is not "
                                      "shown"))
        elif not src - seen_src:
            corr.append(dict(row, why=f"rests only on {sorted(src)}, already carried by this COP's own observed "
                                      f"evidence or a signal already counted"))
        else:
            indep.append(dict(row, lineage=sorted(lin), distinct_sources=sorted(src - seen_src)))
            seen_lin |= lin
            seen_src |= src
    counted = {x["cop"] for x in indep}
    conditions = []
    for k in [x for x in topological_order(list(conds)) if x in conds]:
        f = conds[k]
        kind, reason = RK_CONDITIONS[(k, f["label"])]
        src = rk_source_items(f, upstream)
        byid = {u.get("finding_id"): (c, u) for c, u in (upstream or {}).items() if u}
        under = [{"cop": byid[r[8:]][0], "label": byid[r[8:]][1].get("label"),
                  "verified": bool(byid[r[8:]][1].get("verified")), "counted": byid[r[8:]][0] in counted}
                 for c in f.get("claims") or [] for r in c.get("evidence_refs") or []
                 if str(r).startswith("finding:") and r[8:] in byid]
        conditions.append({"cop": k, "label": f["label"], "kind": kind, "reason": reason, "counted": False,
                           "sources": sorted(src), "shares_counted_or_own": sorted(src & seen_src),
                           "underlying_findings": under, "finding_id": f.get("finding_id")})
    # the D-RK5 reading (lineage only, conditions still counted then), recorded for comparison
    legacy = [x["cop"] for x in independent_upstream(own, {**risky, **conds})["independent"]]
    return {"independent": indep, "correlated": corr, "quiet": quiet, "undetermined": und, "context": context,
            "conditions": conditions, "lineage_only_independent": legacy}


def rk_supporting_sensitivity(rows, observed_refs, irreversible_refs, independent):
    """P-RK1 (purvi, 2026-11-27): the sensitivity of the evidence that supports the counted risk. Supporting rows are
    this COP's observed rows (counted metrics in a deviation state), its undisputed irreversible rows and the rows
    the counted signals cite. A counted item (each signal, the irreversible operation) is established when one of its
    rows carries a recorded sensitivity_tier; a proposed operation carries none."""
    num = EA.signal_metrics.num
    by = {f"observable:{r.get('observable_id')}": r for r in rows or []}
    tier_of = lambda refs: [num(by[x].get("sensitivity_tier")) for x in refs
                            if x in by and by[x].get("sensitivity_tier") is not None]
    items = {x["cop"]: sorted(r for r in x.get("sources", []) if str(r).startswith("observable:"))
             for x in independent}
    if irreversible_refs:
        items["irreversible_operation"] = sorted(irreversible_refs)
    refs = set(observed_refs or ()) | {r for v in items.values() for r in v}
    known = tier_of(refs)
    return {"supporting_refs": sorted(refs), "supporting_tier": max(known) if known else None,
            "items": {k: {"refs": v, "tier": max(tier_of(v)) if tier_of(v) else None} for k, v in items.items()},
            "unestablished": sorted(k for k, v in items.items() if not tier_of(v))}


def rk_impact_bounds(register, supporting):
    """The impact range from the supporting sensitivity (P-RK1). Lower end: the highest register impact at the
    highest recorded supporting tier (tier 0 risks when none is recorded). Upper end: the same when every counted item
    is established or the recorded tier already reaches the register's highest min_sensitivity_tier; otherwise the
    register's highest impact. No safe value is assumed for an unrecorded tier."""
    risks = register.get("risks", [])
    tier = supporting.get("supporting_tier")
    lo, applies = _rk_impact(register, tier if tier is not None else 0)
    top = max([r.get("min_sensitivity_tier", 0) for r in risks] or [0])
    decided = not supporting.get("unestablished") or (tier is not None and tier >= top)
    hi = lo if decided else max([r.get("impact", 0) for r in risks] or [0])
    return {"low": lo, "high": hi, "applicable_risks": applies, "decided": decided}


def rk_band_range(register, supporting, n):
    """(band, band not excluded or None, decided) for n counted items: the same band at both ends is decided; a LOW
    lower end with a higher upper end is undetermined; an EMERGING or MATERIAL lower end stands on its positive
    evidence and the higher band is named as not excluded."""
    th = register["thresholds"]
    b = rk_impact_bounds(register, supporting)
    lo, hi = _rk_band(b["low"] * n, th), _rk_band(b["high"] * n, th)
    if lo == hi:
        return lo, None, True, b
    if lo == "LOW":
        return None, hi, False, b
    return lo, hi, True, b


def _rk_own_refs(a1, s, states):
    """The source items this COP observes itself: the rows behind its counted metrics in a deviation state and its
    undisputed irreversible operations (the same set tools.calculate_risk_components passes)."""
    ms = _ct_measures(a1, rk_counted_metrics(), states)
    refs = {r for m in ms if m["verdict"] == DEVIATES for r in m.get("evidence_refs") or []}
    return refs | set(_rk_irreversible(s)[0])


def _rk_previous_reading(upstream, session, register, tier):
    """The reading before D-RK3/D-RK6: every verified substantive consumed finding counted when independent, quiet
    readings included, and every irreversible operation, disputed ones included."""
    from .rules import independent_upstream
    from ..specs import BY_KEY as _BY, SPEC_CONSUMES
    if _rk_register_problem(register):
        return None
    up = {k: v for k, v in (upstream or {}).items() if k in SPEC_CONSUMES[RK]}
    sig = [x["cop"] for x in independent_upstream(set(_BY[RK].metric_keys), up)["independent"]]
    a, b = _rk_irreversible(session)
    impact, _ = _rk_impact(register, tier)
    score = impact * (len(sig) + (1 if a or b else 0))
    return {"signals": sig, "irreversible": a + b, "score": score, "band": _rk_band(score, register["thresholds"]),
            "note": "the rule before D-RK3 and D-RK6 counted every verified substantive upstream finding, quiet "
                    "readings included, and every irreversible operation, disputed ones included"}


def _rk_upstream(upstream, pkg, own_refs=()):
    """Each consumed COP's finding (specs.CONSUMES): present, verified, its label's class (RISK, QUIET or
    UNDETERMINED, METRICS.yaml upstream), and, for risk-bearing findings, whether its lineage is independent of
    this COP's counted metrics and of signals already counted (rules.independent_upstream). Only an independent
    risk-bearing finding is counted; quiet and undetermined readings never are."""
    from ..specs import CONSUMES
    up = upstream or {}
    out = {}
    for k in CONSUMES[RK]:
        f = up.get(k)
        if f is None:
            out[k] = {"present": False, "class": "ABSENT", "counted": False, "why": "no finding in this case"}
            continue
        ok = bool(f.get("verified")) and f.get("status") == "SUBSTANTIVE"
        cls = ("CONTEXT" if pkg["upstream"][k].get("context_only") else
               "CONDITION" if f["label"] in pkg["upstream"][k].get("condition", []) else
               "RISK" if f["label"] in pkg["upstream"][k]["risk"] else
               "QUIET" if f["label"] in pkg["upstream"][k]["quiet"] else "UNDETERMINED") if ok else "NOT_VERIFIED"
        out[k] = {"present": True, "label": f.get("label"), "finding_id": f.get("finding_id"),
                  "verified": bool(f.get("verified")), "status": f.get("status"), "class": cls,
                  "lineage": sorted(f.get("lineage") or []), "counted": False}
    split = rk_signal_split(up, own_refs)
    for x in split["independent"]:
        out[x["cop"]].update(counted=True, why="independent verified risk signal", sources=x["sources"],
                             distinct_sources=x["distinct_sources"])
    for x in split["correlated"]:
        out[x["cop"]].update(why=f"correlated, not counted: {x['why']}", sources=x["sources"])
    for x in split["conditions"]:
        out[x["cop"]].update(why=f"{x['kind']}: {x['reason']}", sources=x["sources"], kind=x["kind"],
                             shares_counted_or_own=x["shares_counted_or_own"],
                             underlying_findings=x["underlying_findings"])
    for k, v in out.items():
        if v["class"] in ("RISK", "CONDITION"):
            v["lineage_only_counted"] = k in split["lineage_only_independent"]
        v.setdefault("why", {"QUIET": f"{v.get('label')} is a quiet reading, not a risk signal",
                             "CONTEXT": f"{v.get('label')}: context only (U-RK1); a family is a kind, not a risk, "
                                        f"and is never counted",
                             "UNDETERMINED": f"{v.get('label')}: the producer could not decide; not a signal",
                             "NOT_VERIFIED": "not a verified substantive finding; supports nothing"}.get(v["class"], ""))
    return out


def _rk1(a, s, states, pkg, view):
    from ..specs import BY_KEY as _BY
    counted = _ct_measures(a, _BY[RK].metric_keys, states)
    tel = _ct_measures(a, [m for m in _rk_built(pkg, "RK-1") if m not in _BY[RK].metric_keys], states)
    irrev, disputed = _rk_irreversible(s)
    observed = [m for m in counted if m["verdict"] == DEVIATES]
    inferred = [{"cop": k, "label": v["label"], "finding_id": v["finding_id"], "counted": v["counted"], "why": v["why"],
                 "sources": v.get("sources", []), "lineage_only_counted": v.get("lineage_only_counted"),
                 **({"kind": v["kind"]} if "kind" in v else {})}
                for k, v in view.items() if v["class"] == "RISK"]
    verified_up = [k for k, v in view.items() if v["class"] in ("RISK", "CONDITION", "QUIET", "UNDETERMINED")]
    basis = {"measures": counted, "observed": [f"{m['metric']}@{m['state']}" for m in observed],
             "observed_refs": sorted({r for m in observed for r in m.get("evidence_refs") or []}),
             "irreversible": irrev, "irreversible_disputed": disputed, "inferred": inferred,
             "quiet_not_counted": [{"cop": k, "label": v["label"]} for k, v in view.items() if v["class"] == "QUIET"],
             "undetermined": [{"cop": k, "label": v["label"]} for k, v in view.items() if v["class"] == "UNDETERMINED"],
             "not_verified": [{"cop": k, "label": v["label"]} for k, v in view.items() if v["class"] == "NOT_VERIFIED"],
             "context": [{"cop": k, "label": v["label"], "finding_id": v["finding_id"], "counted": False,
                          "why": v["why"]} for k, v in view.items() if v["class"] == "CONTEXT"],
             "conditions": [{"cop": k, "label": v["label"], "finding_id": v["finding_id"], "kind": v["kind"],
                             "reason": v["why"], "counted": False, "sources": v.get("sources", []),
                             "shares_counted_or_own": v.get("shares_counted_or_own", []),
                             "underlying_findings": v.get("underlying_findings", []),
                             "lineage_only_counted": v.get("lineage_only_counted")}
                            for k, v in view.items() if v["class"] == "CONDITION"],
             "absent": [k for k, v in view.items() if v["class"] == "ABSENT"],
             "verified_upstream": verified_up, "telemetry": tel,
             "telemetry_deviating": [m["metric"] for m in tel if m["verdict"] == DEVIATES],
             "warnings": [m["metric"] for m in counted if m.get("warning")],
             "not_calculated": _rk_not_built(pkg, "RK-1"), "upstream": view}
    if observed or irrev or inferred:
        return "RISK_INDICATED", basis
    open_ = [m for m in counted if m["verdict"] not in (WITHIN, DOES_NOT_APPLY)]
    why = [{"code": EA.MISSING_DATA, "requirement": m["metric"], "check": None,
            "detail": f"{m['metric']} {m['reason']}: whether it indicates a risk is not evaluated"} for m in open_]
    if not verified_up:
        why.append({"code": EA.MISSING_DATA, "requirement": "upstream_findings", "check": None,
                    "detail": "no consumed COP (" + ", ".join(view) + ") has a verified finding: the combined "
                              "findings are not available, so the absence of risk is not shown"})
    if why:
        basis["interpretation_reasons"] = why
        return INCONCLUSIVE, basis
    return "NO_RISK_INDICATED", basis


def _rk_register_problem(register):
    why = (register or {}).get("why")
    if register and "missing_dependency" in register and why and why != "no risk register":
        # purvi, 2026-11-27 (U-RK8): an unapproved or unversioned register establishes no band and no control
        req = "risk_register.approval" if "approved" in why else "risk_register.version"
        return {"code": EA.MISSING_REFERENCE, "requirement": req, "check": None,
                "detail": f"{why}: it cannot establish an EMERGING or MATERIAL band or a control (U-RK8)"}
    if not register or "missing_dependency" in register:
        return {"code": EA.MISSING_REFERENCE, "requirement": "risk_register", "check": None,
                "detail": "no risk register: impact, thresholds and controls are undefined"}
    th = register.get("thresholds") or {}
    if "EMERGING" not in th or "MATERIAL" not in th:
        return {"code": EA.MISSING_REFERENCE, "requirement": "risk_register.thresholds", "check": None,
                "detail": f"risk register {register.get('version')} defines no EMERGING/MATERIAL thresholds"}
    return None


def _rk_impact(register, tier):
    applies = [r for r in register.get("risks", []) if r.get("min_sensitivity_tier", 0) <= tier]
    return max([r.get("impact", 0) for r in applies] or [0]), [r["id"] for r in applies]


def _rk_band(score, th):
    return "MATERIAL" if score >= th["MATERIAL"] else "EMERGING" if score >= th["EMERGING"] else "LOW"


def _rk2(a, s, states, pkg, deps, register, components, upstream):
    one = deps["RK-1"]["basis"]
    ctx = _ct_measures(a, _rk_built(pkg, "RK-2"), states)
    comp = components or {}
    basis = {"likelihood_context": ctx, "likelihood_context_deviating": [m["metric"] for m in ctx
                                                                         if m["verdict"] == DEVIATES],
             "counted": False if not ctx else "observed, never counted: the register formula counts independent "
                                              "verified signals and irreversible operations only",
             "not_calculated": _rk_not_built(pkg, "RK-2"),
             "max_sensitivity_tier": comp.get("max_sensitivity_tier", 0)}
    prob = _rk_register_problem(register)
    if prob:
        basis["interpretation_reasons"] = [prob]
        basis["previous_reading"] = None
        return INCONCLUSIVE, basis
    th = register["thresholds"]
    signals = [x["cop"] for x in one["inferred"] if x["counted"]]
    irrev = 1 if one["irreversible"] else 0
    n = len(signals) + irrev
    # P-RK1 (purvi, 2026-11-27): impact from the evidence supporting the counted risk, not the session's highest tier
    sup = rk_supporting_sensitivity(s.rows if s else [], one["observed_refs"], one["irreversible"],
                                    [x for x in one["inferred"] if x["counted"]])
    band, not_excluded, decided, bounds = rk_band_range(register, sup, n)
    impact, applies = bounds["low"], bounds["applicable_risks"]
    score = impact * n
    s_impact, _ = _rk_impact(register, basis["max_sensitivity_tier"])
    s_band = _rk_band(s_impact * n, th)
    basis.update(register_version=register.get("version"), thresholds=th, impact=impact,
                 impact_range=[bounds["low"], bounds["high"]], applicable_risks=applies,
                 supporting_sensitivity=sup,
                 signals_counted=signals + (["irreversible_operation"] if irrev else []),
                 correlated_not_counted=[x["cop"] for x in one["inferred"] if not x["counted"]],
                 conditions_not_counted=[x["cop"] for x in one["conditions"]],
                 quiet_not_counted=[x["cop"] for x in one["quiet_not_counted"]],
                 irreversible_disputed_not_counted=one["irreversible_disputed"],
                 score=score, score_range=[bounds["low"] * n, bounds["high"] * n],
                 formula=(f"impact {impact}" + (f" to {bounds['high']}" if bounds["high"] != impact else "")
                          + f" x (independent signals {len(signals)} + irreversible {irrev})"),
                 band=band, band_not_excluded=not_excluded,
                 components={k: comp.get(k) for k in ("independent_verified_signals", "irreversible_operations",
                                                      "supporting_sensitivity_tier",
                                                      "supporting_sensitivity_unestablished", "max_sensitivity_tier")},
                 previous_reading=_rk_previous_reading(upstream, s, register, basis["max_sensitivity_tier"]),
                 session_tier_reading={"impact": s_impact, "score": s_impact * n, "band": s_band,
                                       "differs": s_band != band,
                                       "note": "the formula before P-RK1: impact at the session's highest "
                                               "sensitivity tier"},
                 lineage_only_reading=_rk_lineage_only(one, impact, irrev, th))
    if not decided:
        basis["interpretation_reasons"] = [{
            "code": EA.MISSING_DATA, "requirement": "supporting_sensitivity", "check": None,
            "detail": (f"no sensitivity tier is recorded for the evidence behind {', '.join(sup['unestablished'])}: "
                       f"impact is {bounds['low']} to {bounds['high']}, score {bounds['low'] * n} (LOW) to "
                       f"{bounds['high'] * n} ({not_excluded}); no safe value is assumed (P-RK1)")}]
        return INCONCLUSIVE, basis
    return {"MATERIAL": "MATERIAL_LEVEL", "EMERGING": "EMERGING_LEVEL", "LOW": "BELOW_THRESHOLDS"}[band], basis


def _rk_lineage_only(one, impact, irrev, th):
    """The reading under D-RK5 alone (lineage overlap), before U-RK3 added source-evidence overlap."""
    sig = [x["cop"] for x in one["inferred"] + one["conditions"] if x.get("lineage_only_counted")]
    score = impact * (len(sig) + irrev)
    return {"signals": sig, "score": score, "band": _rk_band(score, th),
            "note": "D-RK5 counted a finding whose lineage (tool-call ids included) did not overlap, conditions "
                    "included; U-RK3 also requires a source item not already carried by this COP's own evidence or "
                    "a counted signal, and P-RK2 never counts a condition"}


def _rk3(a, s, states, pkg, deps):
    sm = EA.signal_metrics
    one = deps["RK-1"]
    b1 = one["basis"]
    ms = _ct_measures(a, _rk_built(pkg, "RK-3"), states)
    refs = set(b1["observed_refs"]) | set(b1["irreversible"]) | set(b1["irreversible_disputed"])
    rows = [r for r in (s.rows if s else []) if f"observable:{r.get('observable_id')}" in refs]
    ents = [{"ref": f"observable:{r['observable_id']}", "kind": "event",
             **{k: r.get(k) for k in CT_ENTITY_FIELDS if sm.has(r.get(k))},
             "lane": r.get("agent_id") or "main",
             "disputed": f"observable:{r['observable_id']}" in b1["irreversible_disputed"]} for r in rows]
    ents += [{"ref": x["finding_id"], "kind": "finding", "cop": x["cop"], "label": x["label"], "counted": x["counted"]}
             for x in b1["inferred"]]
    basis = {"entities": ents, "measures": ms, "deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "agents": sorted({e["lane"] for e in ents if e["kind"] == "event"}),
             "resources": sorted({e.get("file_path") or e.get("mcp_server") or e.get("command")
                                  for e in ents if e["kind"] == "event"} - {None}),
             "asset_inventory": "NOT_SUPPLIED", "asset_note": RK_NO_INVENTORY,
             "not_calculated": _rk_not_built(pkg, "RK-3")}
    if ents:
        return "EXPOSURE_IDENTIFIED", basis
    if one["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "RK-1", "check": None,
                                            "detail": "RK-1 is not answered, so no exposure can be ruled out"}]
        return INCONCLUSIVE, basis
    return "NO_EXPOSURE_IDENTIFIED", basis


def _rk4(a, states, pkg, deps, register, controls, memory):
    two = deps["RK-2"]
    ms = _ct_measures(a, _rk_built(pkg, "RK-4"), states)
    basis = {"oversight": ms, "oversight_deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "precedents": _rs_precedents(memory), "not_calculated": _rk_not_built(pkg, "RK-4"),
             "authority": RK_AUTHORITY, "executed": [], "residual_risk": "NOT_ESTABLISHED"}
    if two["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "RK-2", "check": None,
                                            "detail": "RK-2 is not answered, so whether mitigation is needed is not "
                                                      "known"}]
        return INCONCLUSIVE, basis
    if two["outcome"] == "BELOW_THRESHOLDS":
        basis["recommended"] = []
        return "NOT_REQUIRED", basis
    applies = set(two["basis"]["applicable_risks"])
    cs = [c for c in ((controls or {}).get("controls") or []) if c["risk"] in applies]
    basis["controls"] = cs
    basis["recommended"] = [dict(c, use="recommended to the risk owner; not applied by this COP") for c in cs
                            if c["prerequisites_met"]]
    basis["unavailable"] = [dict(c, why="its prerequisites are not met in this environment") for c in cs
                            if not c["prerequisites_met"]]
    basis["no_control"] = sorted(r for r in applies if not any(c["risk"] == r for c in cs))
    return ("CONTROL_AVAILABLE" if basis["recommended"] else "NO_CONTROL_AVAILABLE"), basis


def observe_risk_mitigation(assessments, session, register=None, components=None, controls=None, upstream=None,
                            memory=None):
    """{question_id: assessment} -> {question_id: answer}. register: tools.read_risk_register's result;
    components: tools.calculate_risk_components's (the legacy inputs); controls: tools.evaluate_control_prerequisites';
    memory: {established, verified_pending} mitigation_outcome records."""
    pkg = _rk_pkg()
    states = tuple(pkg["deviation_states"])
    view = _rk_upstream(upstream, pkg, _rk_own_refs(assessments["RK-1"], session, states))
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        if qid == "RK-1":
            outcome, basis = _rk1(a, session, states, pkg, view)
        elif qid == "RK-2":
            outcome, basis = _rk2(a, session, states, pkg, out, register, components, upstream)
        elif qid == "RK-3":
            outcome, basis = _rk3(a, session, states, pkg, out)
        else:
            outcome, basis = _rk4(a, states, pkg, out, register, controls, memory)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": a["sufficiency"]["status"], "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE or basis.get("interpretation_reasons"):
            out[qid]["because"] = basis.get("interpretation_reasons", [])
    return out


def assess_risk_mitigation(rows, session_id, cfg=None, computed_at=None, register=None, components=None,
                           controls=None, upstream=None, memory=None, raw_events=None, native=None):
    """The four questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    assessments = {qid: EA.assess(RK, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[RK]}
    answers = observe_risk_mitigation(assessments, s, register, components, controls, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_rk(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if o == INCONCLUSIVE:
        return _explain(qid, ans)
    if qid == "RK-1":
        if o == "NO_RISK_INDICATED":
            s = ("no counted metric in a deviation state, no irreversible operation, no verified risk-bearing "
                 "finding; quiet readings: " + (", ".join(f"{x['cop']} {x['label']}" for x in b["quiet_not_counted"])
                                                or "none"))
        else:
            parts = ([f"observed {', '.join(b['observed'])}"] if b["observed"] else []) + \
                    ([f"irreversible {', '.join(b['irreversible'])}"] if b["irreversible"] else []) + \
                    ([f"inferred {', '.join(x['cop'] + ' ' + x['label'] + (' (counted)' if x['counted'] else ' (correlated)') for x in b['inferred'])}"]
                     if b["inferred"] else [])
            s = "; ".join(parts)
        if b["irreversible_disputed"]:
            s += f"; disputed irreversible, not counted: {', '.join(b['irreversible_disputed'])}"
        return s
    if qid == "RK-2":
        return (f"{b['formula']} = {b['score']}"
                + (f" to {b['score_range'][1]}" if b["score_range"][1] != b["score"] else "")
                + f" against register {b['register_version']} thresholds "
                f"{b['thresholds']}: {b['band']}" + (f"; counted {', '.join(b['signals_counted'])}" if
                                                       b["signals_counted"] else "")
                + (f"; correlated, not counted: {', '.join(b['correlated_not_counted'])}" if
                   b["correlated_not_counted"] else "")
                + (f"; conditions, not counted: {', '.join(b['conditions_not_counted'])}"
                   if b.get("conditions_not_counted") else "")
                + (f"; {b['band_not_excluded']} not excluded" if b.get("band_not_excluded") else ""))
    if qid == "RK-3":
        if o == "NO_EXPOSURE_IDENTIFIED":
            return "no event or finding carries a risk; " + RK_NO_INVENTORY
        return (f"{len(b['entities'])} exposed event(s) or finding(s); agents {', '.join(b['agents']) or 'none'}; "
                f"resources {', '.join(b['resources']) or 'none'}; asset inventory not supplied")
    if o == "NOT_REQUIRED":
        return "below the register's thresholds: no mitigation is required"
    if o == "CONTROL_AVAILABLE":
        return ("recommended, not applied: " + ", ".join(f"{c['control']} for {c['risk']} (reversible {c['reversible']})"
                                                         for c in b["recommended"]) + "; residual risk not established")
    return ("no control available: " + "; ".join([f"{c['control']} for {c['risk']}: {c['why']}" for c in b["unavailable"]]
                                                  + [f"{r} lists no control" for r in b["no_control"]])
            + "; the decision is left to a person")


def _rk_limitations(ans):
    from . import decision as D
    out = []
    b1 = ans["RK-1"].get("basis") or {}
    b2 = ans["RK-2"].get("basis") or {}
    b4 = ans["RK-4"].get("basis") or {}
    pkg = _rk_pkg()
    for k in b1.get("absent", []):
        ctx_only = pkg["upstream"][k].get("context_only")
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-1", k, "UPSTREAM_ABSENT",
                                f"no {k} finding in this case: " + ("its context is not listed (context only, "
                                                                     "never counted)" if ctx_only else
                                                                     "its risk is not combined")))
    for x in b1.get("not_verified", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-1", x["cop"], "UPSTREAM_NOT_VERIFIED",
                                f"{x['cop']} reads {x['label']}, not verified: it supports nothing"))
    for x in b1.get("undetermined", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-1", x["cop"], "UPSTREAM_UNDETERMINED",
                                f"{x['cop']} reads {x['label']}: it could not decide, so its risk is unknown"))
    if b1.get("irreversible_disputed"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-1", "irreversible_operation", EA.CONFLICTING_SOURCES,
                                "the collectors disagree on the row(s) carrying the irreversible operation: directly "
                                "disputed evidence is not counted", b1["irreversible_disputed"]))
    for m in b1.get("telemetry_deviating", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-1", m, "TELEMETRY_QUALITY",
                                f"{m} is in a deviation state: the combined findings rest on degraded telemetry"))
    if ans["RK-3"]["outcome"] != INCONCLUSIVE:
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-3", "asset_inventory", EA.MISSING_REFERENCE, RK_NO_INVENTORY))
    for c in b4.get("unavailable", []) or []:
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-4", c["control"], "CONTROL_PREREQUISITE_UNMET",
                                f"{c['control']} for {c['risk']}: {c['why']}"))
    if ans["RK-4"]["outcome"] in ("CONTROL_AVAILABLE", "NO_CONTROL_AVAILABLE"):
        out.append(D.limitation(D.NOT_CALCULATED, "RK-4", "residual_risk", D.NOT_CALCULATED, RK_NO_RESIDUAL))
    if ans["RK-2"]["outcome"] != INCONCLUSIVE:
        pkg = _rk_pkg()
        for qid in RK_QUESTIONS:
            for x in (ans[qid].get("basis") or {}).get("not_calculated", []):
                out.append(D.limitation(D.NOT_CALCULATED, qid, x["metric"], D.NOT_CALCULATED, x["missing_inputs"]))
        for k, v in pkg["not_calculated"].items():
            out.append(D.limitation(D.NOT_CALCULATED, "RK-2", k, D.NOT_CALCULATED, " ".join(v.split())))
    for x in b1.get("conditions", []):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RK-1", x["cop"], "CONDITION_NOT_COUNTED",
                                f"{x['cop']} reads {x['label']}: {x['reason']} (P-RK2)"))
    lo = b2.get("lineage_only_reading")
    if lo and lo["band"] != b2.get("band"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RK-2", "lineage_only_reading", "LINEAGE_ONLY_READING_DIFFERS",
                                f"by lineage alone (D-RK5) the score would be {lo['score']} ({lo['band']}, signals "
                                f"{lo['signals']}); findings resting on the same source evidence are one signal "
                                f"(U-RK3)"))
    sup = b2.get("supporting_sensitivity") or {}
    if sup.get("unestablished") and b2.get("impact_range") and b2["impact_range"][0] != b2["impact_range"][1]:
        out.append(D.limitation(D.MISSING_EVIDENCE, "RK-2", "supporting_sensitivity", "SUPPORTING_SENSITIVITY_UNKNOWN",
                                f"no sensitivity tier recorded for the evidence behind {', '.join(sup['unestablished'])}: "
                                f"impact {b2['impact_range'][0]} to {b2['impact_range'][1]}, score "
                                f"{b2['score_range'][0]} to {b2['score_range'][1]}"
                                + (f"; {b2['band_not_excluded']} is not excluded" if b2.get("band_not_excluded")
                                   and b2.get("band") else "")))
    st = b2.get("session_tier_reading")
    if st and st["differs"]:
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RK-2", "session_tier_reading", "SESSION_TIER_READING_DIFFERS",
                                f"at the session's highest sensitivity tier {b2['max_sensitivity_tier']} the formula "
                                f"would read {st['band']} (impact {st['impact']}, score {st['score']}); impact now "
                                f"follows the supporting evidence (P-RK1)"))
    if b2.get("band"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RK-2", "confidence", "UNCALIBRATED_CONFIDENCE",
                                "confidence 0.75 is the legacy rule's fixed value, not calibrated against outcomes "
                                "(U-RK10)"))
    prev = b2.get("previous_reading")
    if prev and prev["band"] != b2.get("band"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RK-2", "previous_reading", "PREVIOUS_READING_DIFFERS",
                                f"before D-RK3/D-RK6 the rule read {prev['band']} (score {prev['score']}, signals "
                                f"{prev['signals']}, irreversible {prev['irreversible']}); risk-bearing, undisputed "
                                f"signals only read {b2['band']}"))
    return out


def _rk_status(ans, label):
    """Observed, inferred and verified risk, and the intervention it justifies, kept apart."""
    b1 = ans["RK-1"].get("basis") or {}
    b2 = ans["RK-2"].get("basis") or {}
    b4 = ans["RK-4"].get("basis") or {}
    return {"observed": {"metrics": b1.get("observed", []), "irreversible": b1.get("irreversible", []),
                         "disputed": b1.get("irreversible_disputed", [])},
            "inferred": [{k: x[k] for k in ("cop", "label", "finding_id", "counted")} for x in b1.get("inferred", [])],
            "context": [{k: x[k] for k in ("cop", "label", "finding_id")} for x in b1.get("context", [])],
            "conditions": [{k: x[k] for k in ("cop", "label", "finding_id", "kind", "reason")}
                           for x in b1.get("conditions", [])],
            "verified": {"band": label if label in ("EMERGING", "MATERIAL") else None,
                         "score": b2.get("score"), "signals_counted": b2.get("signals_counted", []),
                         "register_version": b2.get("register_version")},
            "intervention": {"answer": ans["RK-4"]["outcome"],
                             "recommended": [c["control"] for c in b4.get("recommended", []) or []],
                             "executed": [], "escalated_to_person": label == "MATERIAL",
                             "justified": ("escalation to a person" if label == "MATERIAL" else
                                           "watch; controls recommended for the owner's review" if label == "EMERGING"
                                           else "none"),
                             "authority": RK_AUTHORITY}}


def decide_risk_mitigation(questions, evidence_scale=1.0):
    """The COP decision from the four answers (INTERPRETATION.md). RK-2's register band gives the label; a LOW band
    with RK-1 unanswered is INCONCLUSIVE; RK-2 unanswered is INCONCLUSIVE. Action, severity and confidence are the
    legacy rule's (METRICS.yaml decision). Every reading is decided by the package; no model is needed."""
    pkg = _rk_pkg()
    ans = {q: v["answer"] for q, v in questions.items()}
    one, two = ans["RK-1"]["outcome"], ans["RK-2"]["outcome"]
    b1, b2 = ans["RK-1"].get("basis") or {}, ans["RK-2"].get("basis") or {}
    label = pkg["labels"][two] if two != INCONCLUSIVE else INCONCLUSIVE
    if label == "LOW" and one == INCONCLUSIVE:
        label = INCONCLUSIVE
    d = pkg["decision"].get(label, {"action": "NONE", "severity": "NONE", "confidence": 0.0})
    reason = "" if label != INCONCLUSIVE else RK_REASON[INCONCLUSIVE]
    if label == "MATERIAL":
        reason = (f"risk score {b2['score']} reaches the register's MATERIAL threshold {b2['thresholds']['MATERIAL']}: "
                  f"a person decides on mitigation; {RK_AUTHORITY}")
    elif label == "EMERGING":
        reason = (f"risk score {b2['score']} reaches the register's EMERGING threshold {b2['thresholds']['EMERGING']}: "
                  f"watched; controls recommended, not applied")
    drivers = ["RK-1", "RK-2"] if label != INCONCLUSIVE or two != INCONCLUSIVE else ["RK-2"]
    trace = _question_trace(questions, _explain_rk, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items()
                for x in v["answer"].get("findings", [])]
    contrary += [f"RK-1: {x['cop']} reads {x['label']}, {x['why']}" for x in b1.get("inferred", []) if not x["counted"]]
    contrary += [f"RK-1: {x['cop']} reads {x['label']}: a quiet reading, not counted (the legacy rule counted it)"
                 for x in b1.get("quiet_not_counted", [])]
    contrary += [f"RK-1: {r} carries an irreversible operation the collectors disagree on: disputed, not counted"
                 for r in b1.get("irreversible_disputed", [])]
    contrary += [f"RK-2: {m} in a deviation state, observed as likelihood context and not counted"
                 for m in b2.get("likelihood_context_deviating", [])]
    contrary += [f"RK-1: {x['cop']} reads {x['label']}: {x['reason']}; not counted" for x in b1.get("conditions", [])]
    if b2.get("band_not_excluded") and b2.get("band"):
        contrary.append(f"RK-2: the supporting evidence's sensitivity is not recorded: {b2['band_not_excluded']} is "
                        f"not excluded (score up to {b2['score_range'][1]})")
    if label in ("LOW",) and b1.get("observed"):
        contrary.append(f"RK-1: observed {', '.join(b1['observed'])} without an independent verified signal: observed "
                        f"risk, not verified risk")
    status = _rk_status(ans, label)
    pay = {"answers": {q: x["outcome"] for q, x in ans.items()}, "band": b2.get("band"), "score": b2.get("score"),
           "signals_counted": b2.get("signals_counted", []), "register_version": b2.get("register_version"),
           "recommended_controls": status["intervention"]["recommended"], "decided_by": "package",
           "precedents_read": [p["record_id"] for p in (ans["RK-4"].get("basis") or {}).get("precedents", [])]}
    return {"label": label, "confidence": round(d["confidence"], 3), "severity": d["severity"], "action": d["action"],
            "action_reason": reason, "recipients": ["incident_response"] if label in ("EMERGING", "MATERIAL") else [],
            "drivers": drivers, "also_held": [], "contrary": contrary, "questions": trace,
            "limitations": _rk_limitations(ans), "decided": True, "memory_payload": pay, "risk_status": status,
            "register_problem": next((x["requirement"] for x in b2.get("interpretation_reasons") or []
                                      if str(x.get("requirement", "")).startswith("risk_register")), None)
            if two == INCONCLUSIVE else None,
            "previous_reading": b2.get("previous_reading"), "decision": label}


# ---------------------------------------------------------------- Incident Response

IR = "incident_response"
IR_QUESTIONS = ("IR-1", "IR-2", "IR-3", "IR-4", "IR-5")
IR_REASON = {INCONCLUSIVE: "whether an incident exists could not be established: see the question answers"}
IR_AUTHORITY = ("Incident Response watches, alerts, requests approval and escalates; it never pauses, blocks or "
                "instructs an agent itself: a block is requested from a person and executes only after approval "
                "(agents-harness row 10: direct intervention only with authority)")
IR_NO_INVENTORY = ("no enterprise asset inventory: the potential impact is not scored (incident_impact_score "
                   "NOT_CALCULATED); the recorded sensitivity tiers of the rows involved are listed instead")
IR_NO_RESOLUTION = ("no resolution or containment records exist (incident_resolution_status, "
                    "incident_containment_status NOT_CALCULATED): RESOLVED is never answered")
# ground kinds -> what they are (DECISIONS.md D-IR2, D-IR3)
IR_INCIDENT_KINDS = ("exposure", "violation", "failed_recovery")
IR_FINDING_KINDS = ("anomaly", "deny_rule_fired", "pending_authorization", "partial_recovery")
IR_EXPOSURE_METRICS = ("insights.secrets", "insights.sensitive_unasked")
IR_QUIET_CLASSES = ("QUIET", "EVIDENCE_QUALITY")
IR_CATEGORY = {"OBSERVED_ABNORMAL": "observed abnormal behaviour: unusual is not wrong",
               "VERIFIED_FINDING": "a verified governance finding that is not an incident",
               "INCIDENT_ATTENTION": "an incident requiring attention",
               "INCIDENT_INTERVENTION": "an incident requiring intervention, which a person authorises",
               "INCIDENT_UNCONFIRMED": "an intervention incident the model did not confirm: a person decides",
               "INSUFFICIENT_EVIDENCE": "insufficient evidence to establish or rule out an incident",
               "NONE": "no incident and no finding"}


def _ir_pkg():
    return EA.load_package(IR)


def _ir_kind(cop, label, pkg):
    """(class, ground kind or None) of a consumed COP's label (METRICS.yaml upstream)."""
    spec = pkg["upstream"][cop]
    if spec.get("evidence_quality"):
        return "EVIDENCE_QUALITY", None
    if spec.get("context_only"):
        return "CONTEXT", None
    if label in spec.get("incident", []):
        return "INCIDENT", "violation" if cop == "policy_enforcement" else "failed_recovery"
    if label in spec.get("finding", []):
        return "FINDING", "pending_authorization" if cop == "policy_enforcement" else "partial_recovery"
    if label in spec.get("abnormal", []):
        return "ABNORMAL", "anomaly"
    if label in spec.get("context", []):
        return "CONTEXT", None
    if label in spec.get("quiet", []):
        return "QUIET", None
    return "UNDETERMINED", None


def _ir_upstream(upstream, pkg):
    """Each consumed COP's finding (specs.CONSUMES): present, verified, class (EVIDENCE_QUALITY, INCIDENT, FINDING,
    ABNORMAL, CONTEXT, QUIET, UNDETERMINED, NOT_VERIFIED, ABSENT), its proposed action and its source items. Only a
    verified substantive INCIDENT, FINDING or ABNORMAL reading can be a ground."""
    from ..specs import CONSUMES
    out = {}
    for k in CONSUMES[IR]:
        f = (upstream or {}).get(k)
        if f is None:
            out[k] = {"present": False, "class": "ABSENT", "why": "no finding in this case"}
            continue
        ok = bool(f.get("verified")) and f.get("status") == "SUBSTANTIVE"
        cls, kind = _ir_kind(k, f.get("label"), pkg)
        row = {"present": True, "label": f.get("label"), "finding_id": f.get("finding_id"),
               "verified": bool(f.get("verified")), "status": f.get("status"),
               "confidence": f.get("confidence"),
               # the producer's own proposal (published proposed_action.requested_action) and the action its
               # authority check let through (proposed_action.action: in dry-run a BLOCK is recorded as ALERT)
               "action": (f.get("proposed_action") or {}).get("requested_action")
               or (f.get("proposed_action") or {}).get("action"),
               "final_action": (f.get("proposed_action") or {}).get("action"),
               "lineage": sorted(f.get("lineage") or []),
               "sources": sorted(rk_source_items(f, upstream)) if ok and kind else []}
        if not ok:
            row.update({"class": "NOT_VERIFIED", "grade": cls if kind else None,
                        "why": "not a verified substantive finding; it grounds nothing"})
        else:
            row.update({"class": cls, "kind": kind, "why": {
                "EVIDENCE_QUALITY": "evidence quality, never a ground",
                "CONTEXT": "context: listed with its entities, never a ground (D-IR2, D-IR16)",
                "QUIET": f"{f.get('label')} is a quiet reading",
                "UNDETERMINED": f"{f.get('label')}: the producer could not decide; a limitation"}.get(cls, "")})
        out[k] = row
    return out


def _ir_grounds(view, exposure, blocked, disputed):
    """The grounds, in order: this COP's exposure, the deny rule, then producers in topological order. Each carries
    its source items; a ground whose items are all carried by grounds taken earlier is correlated (D-IR6)."""
    from ..specs import BY_KEY as _BY, topological_order
    seen = {f"metric:{m}" for m in _BY[IR].metric_keys}
    grounds = []
    exp_refs = sorted({r for m in exposure if m["verdict"] == DEVIATES for r in m.get("evidence_refs") or []})
    undisputed = [r for r in exp_refs if r not in disputed]
    if undisputed:
        grounds.append({"kind": "exposure", "source": "own", "metrics": [f"{m['metric']}@{m['state']}" for m in exposure
                                                                        if m["verdict"] == DEVIATES],
                        "sources": undisputed, "independent": True, "verified": True,
                        "why": "secret-shaped content or a sensitive path without approval was handled"})
        seen |= set(undisputed)
    if blocked and blocked["verdict"] == DEVIATES:
        refs = sorted(blocked.get("evidence_refs") or [])
        grounds.append({"kind": "deny_rule_fired", "source": "own", "metrics": [f"insights.blocked@{blocked['state']}"],
                        "sources": refs, "independent": bool(set(refs) - seen), "verified": True,
                        "why": "a deny rule stopped an operation: the control worked"})
        seen |= set(refs)
    for k in [x for x in topological_order() if x in view and view[x].get("kind")]:
        v = view[k]
        src = set(v["sources"])
        ind = bool(src - seen)
        grounds.append({"kind": v["kind"], "source": k, "label": v["label"], "finding_id": v["finding_id"],
                        "action": v["action"], "sources": sorted(src), "independent": ind, "verified": True,
                        "why": v["why"] or f"verified {k} {v['label']}",
                        **({} if ind else {"correlated": "rests only on source evidence already carried by an "
                                                         "earlier ground: the same evidence, one ground"})})
        seen |= src
    return grounds, exp_refs, undisputed


def _ir_confidence(upstream):
    """The legacy rule's confidence: the lowest confidence of the verified Runtime Monitoring, Anomaly Detection,
    Policy Enforcement and Interaction Discovery findings used, else 0.8 x the evidence level."""
    from ..interpret import EVIDENCE_LEVEL
    up = {k: f for k, f in (upstream or {}).items() if f and f.get("verified") and f.get("status") == "SUBSTANTIVE"}
    rm = up.get("runtime_monitoring")
    evid = EVIDENCE_LEVEL.get(rm["label"], 0.9) if rm else 0.9
    used = [up[k]["confidence"] for k in ("runtime_monitoring", "anomaly_detection", "policy_enforcement",
                                          "interaction_discovery") if k in up and up[k].get("confidence")]
    return round(min(used or [0.8 * min(1.0, evid / 0.9)]), 3)


def _ir1(a, s, states, pkg, view, exposure):
    from ..specs import BY_KEY as _BY
    req = _ct_measures(a, _BY[IR].metric_keys, states)
    by = {m["metric"]: m for m in req}
    exp = [by[m] for m in IR_EXPOSURE_METRICS]
    other = _ct_measures(a, [m for m in _rk_built(pkg, "IR-1") if m not in _BY[IR].metric_keys], states)
    disputed = _ct_disputed(s)
    grounds, exp_refs, undisputed = _ir_grounds(view, exp, by["insights.blocked"], disputed)
    cls = lambda c: [{"cop": k, "label": v["label"], "finding_id": v.get("finding_id"), "why": v["why"]}
                     for k, v in view.items() if v["class"] == c]
    basis = {"measures": req + other, "observed": [f"{m['metric']}@{m['state']}" for m in req if m["verdict"] == DEVIATES],
             "exposure_refs": exp_refs, "exposure_disputed": sorted(set(exp_refs) - set(undisputed)),
             "grounds": grounds, "incident_grounds": [g["kind"] for g in grounds if g["kind"] in IR_INCIDENT_KINDS],
             "finding_grounds": [g["kind"] for g in grounds if g["kind"] in IR_FINDING_KINDS],
             "correlated": [g for g in grounds if not g["independent"]],
             "context": cls("CONTEXT"), "quiet": cls("QUIET") + cls("EVIDENCE_QUALITY"),
             "undetermined": cls("UNDETERMINED"),
             "not_verified": [{"cop": k, "label": v["label"], "grade": v.get("grade")} for k, v in view.items()
                              if v["class"] == "NOT_VERIFIED"],
             "absent": [k for k, v in view.items() if v["class"] == "ABSENT"],
             "evidence_quality": (view.get("runtime_monitoring") or {}).get("label")
             if (view.get("runtime_monitoring") or {}).get("class") == "EVIDENCE_QUALITY" else None,
             "outbound_after_secret": list((exposure or {}).get("outbound_after_secret") or []),
             "not_calculated": _rk_not_built(pkg, "IR-1"), "upstream": view, "incidents": 1 if grounds else 0}
    if basis["incident_grounds"]:
        return "INCIDENT_INDICATED", basis
    if basis["finding_grounds"]:
        return "FINDING_WITHOUT_INCIDENT", basis
    why = [{"code": EA.MISSING_DATA, "requirement": m["metric"], "check": None,
            "detail": f"{m['metric']} {m.get('reason') or m['verdict']}: whether it grounds an incident is not "
                      f"evaluated"} for m in req if m["verdict"] not in (WITHIN, DOES_NOT_APPLY)
           and not (m["verdict"] == DEVIATES)]
    if basis["exposure_disputed"]:
        why.append({"code": EA.CONFLICTING_SOURCES, "requirement": "exposure", "check": None,
                    "detail": "the exposure rests only on rows the collectors disagree on ("
                              + ", ".join(basis["exposure_disputed"]) + "): directly disputed evidence does not "
                              "establish an incident on its own"})
    if not [k for k, v in view.items() if v["class"] not in ("ABSENT", "NOT_VERIFIED")]:
        why.append({"code": EA.MISSING_DATA, "requirement": "upstream_findings", "check": None,
                    "detail": "no consumed COP (" + ", ".join(view) + ") has a verified finding: the absence of an "
                              "incident is not shown"})
    if basis["evidence_quality"] == "INSUFFICIENT":
        why.append({"code": EA.MISSING_DATA, "requirement": "runtime_monitoring", "check": None,
                    "detail": "Runtime Monitoring verified the telemetry INSUFFICIENT: the absence of findings is not "
                              "the absence of an incident"})
    for x in basis["not_verified"]:
        if x["grade"]:
            why.append({"code": EA.MISSING_DATA, "requirement": x["cop"], "check": None,
                        "detail": f"{x['cop']} reads {x['label']}, not verified: an incident-relevant reading that "
                                  f"can neither ground nor be ruled out"})
    if why:
        basis["interpretation_reasons"] = why
        return INCONCLUSIVE, basis
    return "NO_INCIDENT_INDICATED", basis


def _ir_lane(r):
    return r.get("agent_id") or "main"


def _ir_ground(one, kind):
    return [g for g in one["basis"]["grounds"] if g["kind"] == kind]


def _ir_anomaly_independent(one):
    """A verified anomaly independent of the exposure: some of its source items are not the exposure's (D-IR6)."""
    exp = set(one["basis"]["exposure_refs"]) | {f"metric:{m}" for m in IR_EXPOSURE_METRICS}
    return [g for g in _ir_ground(one, "anomaly") if set(g["sources"]) - exp]


def _ir2(a, s, states, pkg, deps):
    one = deps["IR-1"]
    b1 = one["basis"]
    ms = _ct_measures(a, _rk_built(pkg, "IR-2"), states)
    refs = {r for g in b1["grounds"] for r in g["sources"] if str(r).startswith("observable:")}
    rows = [r for r in (s.rows if s else []) if f"observable:{r.get('observable_id')}" in refs]
    ents = [{"ref": f"observable:{r['observable_id']}", "kind": "event", "lane": _ir_lane(r),
             **{k: r.get(k) for k in CT_ENTITY_FIELDS if EA.signal_metrics.has(r.get(k))}} for r in rows]
    ents += [{"ref": g["finding_id"], "kind": "finding", "cop": g["source"], "label": g["label"],
              "independent": g["independent"]} for g in b1["grounds"] if g["source"] != "own"]
    proposed = sorted({x for g in b1["grounds"] for x in g["sources"] if str(x).startswith("proposed:")})
    tiers = sorted({r.get("sensitivity_tier") for r in rows if r.get("sensitivity_tier") is not None})
    basis = {"measures": ms, "deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES], "entities": ents,
             "agents": sorted({e["lane"] for e in ents if e["kind"] == "event"}),
             "resources": sorted({e.get("file_path") or e.get("mcp_server") or e.get("command")
                                  for e in ents if e["kind"] == "event"} - {None}) + proposed,
             "context_findings": b1["context"], "sensitivity_tiers": tiers, "impact": "NOT_CALCULATED",
             "impact_note": IR_NO_INVENTORY, "incidents": b1["incidents"],
             "not_calculated": _rk_not_built(pkg, "IR-2")}
    if one["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "IR-1", "check": None,
                                            "detail": "IR-1 is not answered, so there is nothing established to "
                                                      "rate"}]
        return INCONCLUSIVE, basis
    if one["outcome"] == "NO_INCIDENT_INDICATED":
        return "NOTHING_TO_RATE", basis
    inc, why = b1["incident_grounds"], []
    if "violation" in inc:
        why.append("a verified policy violation")
    if "failed_recovery" in inc:
        why.append("a failed or irreversible recovery")
    ind = _ir_anomaly_independent(one)
    if "exposure" in inc and ind:
        why.append("an exposure with an independent verified anomaly")
    if why:
        basis["severity_basis"] = why
        return "SEVERITY_HIGH", basis
    alerting = [g for g in _ir_ground(one, "anomaly") if g.get("action") == "ALERT"]
    if "exposure" in inc:
        basis["severity_basis"] = ["an exposure" + (" (the anomaly rests on the exposure's evidence: not "
                                                    "independent)" if _ir_ground(one, "anomaly") else "")]
        return "SEVERITY_MEDIUM", basis
    if alerting:
        basis["severity_basis"] = ["an anomaly Anomaly Detection alerts on"]
        return "SEVERITY_MEDIUM", basis
    basis["severity_basis"] = [f"a finding without incident ({', '.join(b1['finding_grounds'])})"]
    return "SEVERITY_LOW", basis


def _ir_pending_block(one):
    return [g for g in _ir_ground(one, "violation") if g.get("action") == "BLOCK"]


def _ir3(a, s, states, pkg, deps):
    one = deps["IR-1"]
    b1 = one["basis"]
    ms = _ct_measures(a, _rk_built(pkg, "IR-3"), states)
    basis = {"measures": ms, "deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "resolution": IR_NO_RESOLUTION, "not_calculated": _rk_not_built(pkg, "IR-3")}
    if one["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "IR-1", "check": None,
                                            "detail": "IR-1 is not answered: no incident state can be given"}]
        return INCONCLUSIVE, basis
    stopped = bool(_ir_ground(one, "deny_rule_fired"))
    if one["outcome"] != "INCIDENT_INDICATED":
        if stopped:
            basis["contained_by"] = [g["sources"] for g in _ir_ground(one, "deny_rule_fired")]
            return "CONTAINED", basis
        return "NO_INCIDENT_STATE", basis
    ongoing = []
    for g in _ir_pending_block(one):
        ongoing.append(f"a denied operation is still pending: {g['source']} {g['label']} proposes BLOCK")
    for g in _ir_ground(one, "failed_recovery"):
        ongoing.append(f"{g['source']} {g['label']}: restoration did not return a viable state")
    if b1["outbound_after_secret"] and "exposure" in b1["incident_grounds"]:
        ongoing.append("outbound calls after a secret was read: " + ", ".join(b1["outbound_after_secret"]))
    basis["ongoing_because"] = ongoing
    if ongoing:
        return "ONGOING", basis
    if stopped and "exposure" not in b1["incident_grounds"]:
        basis["contained_by"] = [g["sources"] for g in _ir_ground(one, "deny_rule_fired")]
        return "CONTAINED", basis
    why = []
    if "exposure" in b1["incident_grounds"]:
        why.append("a secret or sensitive path was handled; whether it left the session is not shown (no outbound "
                   "call after it was observed, and no containment record exists)")
    if _ir_ground(one, "violation"):
        why.append("the violating operation is not shown stopped and no block is pending")
    basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "incident_containment_status",
                                        "check": None, "detail": "still uncertain: " + "; ".join(why)}]
    return INCONCLUSIVE, basis


def _ir_intervention_grounds(one):
    out = [f"pending block: {g['source']} {g['label']}" for g in _ir_pending_block(one)]
    out += [f"failed recovery: {g['source']} {g['label']}" for g in _ir_ground(one, "failed_recovery")]
    if "exposure" in one["basis"]["incident_grounds"]:
        out += [f"exposure with an independent anomaly: {g['source']} {g['label']}"
                for g in _ir_anomaly_independent(one)]
    return out


def _ir4(a, s, states, pkg, deps, upstream):
    one, three = deps["IR-1"], deps["IR-3"]
    b1 = one["basis"]
    ms = _ct_measures(a, _rk_built(pkg, "IR-4"), states)
    basis = {"telemetry": ms, "telemetry_deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "evidence_quality": b1["evidence_quality"],
             "verified_upstream": [k for k, v in b1["upstream"].items() if v["class"] not in ("ABSENT", "NOT_VERIFIED")],
             "not_verified": b1["not_verified"], "independent_grounds": [g["kind"] for g in b1["grounds"]
                                                                         if g["independent"]],
             "correlated_grounds": [g["kind"] for g in b1["grounds"] if not g["independent"]],
             "confidence": _ir_confidence(upstream),
             "confidence_rule": "the lowest confidence of the verified Runtime Monitoring, Anomaly Detection, Policy "
                                "Enforcement and Interaction Discovery findings, else 0.8 x the evidence level",
             "not_calculated": _rk_not_built(pkg, "IR-4")}
    if basis["evidence_quality"] == "DEGRADED" or basis["telemetry_deviating"]:
        basis["certainty"] = "reduced: partial evidence lowers confidence and does not erase a ground"
    if one["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "IR-1", "check": None,
                                            "detail": "IR-1 is not answered: no intervention can be justified"}]
        return INCONCLUSIVE, basis
    if one["outcome"] != "INCIDENT_INDICATED":
        return "NOT_JUSTIFIED", basis
    grounds = _ir_intervention_grounds(one)
    basis["intervention_grounds"] = grounds
    if grounds and three["outcome"] != "CONTAINED":
        return "INTERVENTION_JUSTIFIED", basis
    return "ATTENTION_JUSTIFIED", basis


def _ir_precedents(memory):
    out = []
    for x in (memory or {}).get("established", [])[:10]:
        p = x.get("payload") or {}
        out.append({"record_id": x["record_id"], "label": p.get("label"), "category": p.get("category"),
                    "session_id": p.get("session_id"), "status": x.get("status"),
                    "use": "comparison only, never proof"})
    return out


def _ir5(a, s, states, pkg, deps, memory):
    one, two, four = deps["IR-1"], deps["IR-2"], deps["IR-4"]
    ms = _ct_measures(a, _rk_built(pkg, "IR-5"), states)
    prec = _ir_precedents(memory)
    basis = {"oversight": ms, "oversight_deviating": [m["metric"] for m in ms if m["verdict"] == DEVIATES],
             "precedents": prec, "recurrence": [p["record_id"] for p in prec if p["label"] in ("INCIDENT", "WATCH")],
             "recurrence_note": "validated earlier incidents on this tenant: comparison only, no effect on the answer",
             "authority": IR_AUTHORITY, "executed": [], "not_calculated": _rk_not_built(pkg, "IR-5")}
    if four["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "IR-4", "check": None,
                                            "detail": "IR-4 is not answered: no response beyond the fallback"}]
        return INCONCLUSIVE, basis
    if four["outcome"] == "INTERVENTION_JUSTIFIED":
        blocks = _ir_pending_block(one)
        if blocks:
            ops = sorted({x for g in blocks for x in g["sources"] if str(x).startswith("proposed:")})
            basis.update(requires_approval=True, escalated_to_person=False,
                         containment=f"block the denied operation{'s' if len(ops) != 1 else ''} "
                                     f"{', '.join(ops) or ''} after a person approves; nothing is blocked before",
                         approval_basis="a verified violation whose operation Policy Enforcement proposes to block")
            return "REQUEST_APPROVAL", basis
        basis.update(requires_approval=False, escalated_to_person=True,
                     containment="a person decides containment: " + "; ".join(four["basis"]["intervention_grounds"]))
        return "ESCALATE_TO_PERSON", basis
    basis.update(requires_approval=False, escalated_to_person=False)
    if four["outcome"] == "ATTENTION_JUSTIFIED":
        return "ALERT", basis
    if one["outcome"] == "FINDING_WITHOUT_INCIDENT":
        return ("ALERT" if two["outcome"] == "SEVERITY_MEDIUM" else "WATCH"), basis
    return "NO_RESPONSE", basis


def observe_incident_response(assessments, session, exposure=None, upstream=None, memory=None):
    """{question_id: assessment} -> {question_id: answer}. exposure: tools.check_exposure's result; memory:
    {established, verified_pending} incident_outcome records."""
    pkg = _ir_pkg()
    states = tuple(pkg["deviation_states"])
    view = _ir_upstream(upstream, pkg)
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        if qid == "IR-1":
            outcome, basis = _ir1(a, session, states, pkg, view, exposure)
        elif qid == "IR-2":
            outcome, basis = _ir2(a, session, states, pkg, out)
        elif qid == "IR-3":
            outcome, basis = _ir3(a, session, states, pkg, out)
        elif qid == "IR-4":
            outcome, basis = _ir4(a, session, states, pkg, out, upstream)
        else:
            outcome, basis = _ir5(a, session, states, pkg, out, memory)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": a["sufficiency"]["status"], "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE or basis.get("interpretation_reasons"):
            out[qid]["because"] = basis.get("interpretation_reasons", [])
    return out


def assess_incident_response(rows, session_id, cfg=None, computed_at=None, exposure=None, upstream=None, memory=None,
                             raw_events=None, native=None):
    """The five questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    assessments = {qid: EA.assess(IR, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[IR]}
    answers = observe_incident_response(assessments, s, exposure, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_ir(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if o == INCONCLUSIVE:
        return _explain(qid, ans)
    if qid == "IR-1":
        if o == "NO_INCIDENT_INDICATED":
            return ("no ground: no exposure, no deny rule fired, no verified violation, anomaly, pending authorization "
                    "or recovery finding; quiet: " + (", ".join(f"{x['cop']} {x['label']}" for x in b["quiet"]) or
                                                      "none")
                    + ("; context: " + ", ".join(f"{x['cop']} {x['label']}" for x in b["context"]) if b["context"]
                       else ""))
        s = "; ".join(f"{g['kind']} ({g['source'] if g['source'] != 'own' else ', '.join(g['metrics'])}"
                      f"{'' if g['independent'] else ', correlated'})" for g in b["grounds"])
        return ("one incident: " if o == "INCIDENT_INDICATED" else "finding without incident: ") + s
    if qid == "IR-2":
        if o == "NOTHING_TO_RATE":
            return "no incident and no finding to rate"
        return (f"{o.split('_')[1]}: {'; '.join(b.get('severity_basis') or [])}; scope agents "
                f"{', '.join(b['agents']) or 'none'}, resources {', '.join(b['resources']) or 'none'}; impact not scored "
                f"(no asset inventory)")
    if qid == "IR-3":
        if o == "ONGOING":
            return "ongoing: " + "; ".join(b["ongoing_because"])
        if o == "CONTAINED":
            return "contained: a deny rule stopped the operation, and nothing was exposed"
        return "no incident, so no incident state"
    if qid == "IR-4":
        if o == "INTERVENTION_JUSTIFIED":
            return "intervention justified by verified upstream findings: " + "; ".join(b["intervention_grounds"])
        if o == "ATTENTION_JUSTIFIED":
            return "attention, not intervention: no verified ground needs a person to act"
        return "no incident: no intervention"
    if o == "REQUEST_APPROVAL":
        return "request a person's approval: " + b["containment"]
    if o == "ESCALATE_TO_PERSON":
        return "escalate to a person: " + b["containment"]
    return {"ALERT": "alert; nothing to authorise", "WATCH": "watch", "NO_RESPONSE": "no response"}[o]


def _ir_limitations(ans):
    from . import decision as D
    out = []
    b1 = ans["IR-1"].get("basis") or {}
    b4 = ans["IR-4"].get("basis") or {}
    pkg = _ir_pkg()
    for k in b1.get("absent", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-1", k, "UPSTREAM_ABSENT",
                                f"no {k} finding in this case: it neither grounds nor rules out an incident"))
    for x in b1.get("not_verified", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-1", x["cop"], "UPSTREAM_NOT_VERIFIED",
                                f"{x['cop']} reads {x['label']}, not verified: it grounds nothing"))
    for x in b1.get("undetermined", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-1", x["cop"], "UPSTREAM_UNDETERMINED",
                                f"{x['cop']} reads {x['label']}: it could not decide"))
    if b1.get("exposure_disputed"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-1", "exposure", EA.CONFLICTING_SOURCES,
                                "the collectors disagree on row(s) carrying the exposure: directly disputed evidence "
                                "does not establish an incident alone", b1["exposure_disputed"]))
    for g in b1.get("correlated", []):
        out.append(D.limitation(D.INFERRED_RELATIONSHIP, "IR-1", g["source"], "CORRELATED_GROUND",
                                f"{g['source']} {g.get('label') or g['kind']} rests only on evidence an earlier ground "
                                f"carries: one ground, not two", g["sources"]))
    if b4.get("evidence_quality") == "DEGRADED":
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-4", "runtime_monitoring", "TELEMETRY_QUALITY",
                                "Runtime Monitoring reads DEGRADED: certainty is reduced, grounds stand"))
    for m in b4.get("telemetry_deviating", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-4", m, "TELEMETRY_QUALITY",
                                f"{m} is in a deviation state: certainty is reduced, grounds stand"))
    if ans["IR-2"]["outcome"] not in (INCONCLUSIVE, "NOTHING_TO_RATE"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "IR-2", "asset_inventory", EA.MISSING_REFERENCE, IR_NO_INVENTORY))
    if ans["IR-1"]["outcome"] in ("INCIDENT_INDICATED", "FINDING_WITHOUT_INCIDENT"):
        out.append(D.limitation(D.NOT_CALCULATED, "IR-3", "incident_resolution_status", D.NOT_CALCULATED,
                                IR_NO_RESOLUTION))
    if ans["IR-1"]["outcome"] != INCONCLUSIVE:
        for qid in IR_QUESTIONS:
            for x in (ans[qid].get("basis") or {}).get("not_calculated", []):
                out.append(D.limitation(D.NOT_CALCULATED, qid, x["metric"], D.NOT_CALCULATED, x["missing_inputs"]))
        for k, v in pkg["not_calculated"].items():
            out.append(D.limitation(D.NOT_CALCULATED, "IR-2", k, D.NOT_CALCULATED, " ".join(v.split())))
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "IR-4", "confidence", "UNCALIBRATED_CONFIDENCE",
                                "the confidence is the legacy rule's formula, not calibrated against outcomes (U-IR6)"))
    if (ans["IR-5"].get("basis") or {}).get("recurrence"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "IR-5", "recurrence", "RECURRENCE_NOT_WEIGHED",
                                "validated earlier incidents are listed; no approved rule weighs recurrence (U-IR4)"))
    return out


def _ir_category(ans, label):
    if label == INCONCLUSIVE:
        return "INSUFFICIENT_EVIDENCE"
    if label == "NO_INCIDENT":
        return "NONE"
    if label == "WATCH":
        return "OBSERVED_ABNORMAL" if set(ans["IR-1"]["basis"]["finding_grounds"]) == {"anomaly"} else \
            "VERIFIED_FINDING"
    return "INCIDENT_INTERVENTION" if ans["IR-4"]["outcome"] == "INTERVENTION_JUSTIFIED" else "INCIDENT_ATTENTION"


def _ir_status(ans, label, category, action):
    """Observed behaviour, verified findings, the incident and the intervention it justifies, kept apart."""
    b1 = ans["IR-1"].get("basis") or {}
    b5 = ans["IR-5"].get("basis") or {}
    return {"category": category, "meaning": IR_CATEGORY[category],
            "observed": {"metrics": b1.get("observed", []), "exposure_refs": b1.get("exposure_refs", []),
                         "disputed": b1.get("exposure_disputed", [])},
            "verified_findings": [{k: g.get(k) for k in ("source", "kind", "label", "finding_id", "independent")}
                                  for g in b1.get("grounds", []) if g["source"] != "own"],
            "context": [{k: x[k] for k in ("cop", "label", "finding_id")} for x in b1.get("context", [])],
            "incident": {"indicated": ans["IR-1"]["outcome"] == "INCIDENT_INDICATED", "count": b1.get("incidents", 0),
                         "grounds": [g["kind"] for g in b1.get("grounds", []) if g["independent"]],
                         "correlated": [g["kind"] for g in b1.get("correlated", [])],
                         "state": ans["IR-3"]["outcome"]},
            "intervention": {"justified": ans["IR-4"]["outcome"] == "INTERVENTION_JUSTIFIED",
                             "response": ans["IR-5"]["outcome"], "action": action,
                             "requires_approval": bool(b5.get("requires_approval")),
                             "escalated_to_person": bool(b5.get("escalated_to_person")),
                             "containment": b5.get("containment"), "executed": [], "authority": IR_AUTHORITY}}


def decide_incident_response(questions):
    """The COP decision from the five answers (INTERPRETATION.md). IR-1 gives the label, IR-2 the severity, IR-5 the
    action and IR-4 the confidence (the legacy rule's formula). Decided by the package unless IR-4 justifies an
    intervention: then the model scopes the incident and drafts the containment plan, and `decided` is False."""
    pkg = _ir_pkg()
    ans = {q: v["answer"] for q, v in questions.items()}
    one, two, four, five = (ans[q]["outcome"] for q in ("IR-1", "IR-2", "IR-4", "IR-5"))
    label = pkg["labels"][one]
    action = pkg["responses"].get(five, "WATCH") if label != INCONCLUSIVE else "WATCH"
    sev = pkg["severity"].get(two, "NONE") if label != INCONCLUSIVE else "NONE"
    conf = (ans["IR-4"].get("basis") or {}).get("confidence", 0.0) if label != INCONCLUSIVE else 0.0
    category = _ir_category(ans, label)
    b1 = ans["IR-1"].get("basis") or {}
    reason = IR_REASON.get(label, "")
    if label != INCONCLUSIVE:
        reason = _explain_ir("IR-5", ans["IR-5"]) + f" ({IR_CATEGORY[category]})"
    drivers = ["IR-1"] if label == INCONCLUSIVE else ["IR-1", "IR-2", "IR-4", "IR-5"]
    trace = _question_trace(questions, _explain_ir, drivers)
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items() for x in v["answer"].get("findings", [])]
    contrary += [f"IR-1: {g['source']} {g.get('label') or g['kind']}: {g['correlated']}" for g in b1.get("correlated", [])]
    contrary += [f"IR-1: {x['cop']} reads {x['label']}: context, not a ground" for x in b1.get("context", [])]
    contrary += [f"IR-1: exposure on disputed row {r}: not a ground alone" for r in b1.get("exposure_disputed", [])]
    if label == "WATCH" and category == "OBSERVED_ABNORMAL":
        contrary.append("IR-1: an anomaly is unusual behaviour, not a violation or an exposure: not an incident")
    status = _ir_status(ans, label, category, action)
    decided = four != "INTERVENTION_JUSTIFIED"
    from ..specs import consumers
    pay = {"answers": {q: x["outcome"] for q, x in ans.items()}, "category": category,
           "grounds": [{k: g.get(k) for k in ("kind", "source", "label", "independent", "sources")}
                       for g in b1.get("grounds", [])],
           "response": five, "decided_by": "package" if decided else "package and model",
           "precedents_read": [p["record_id"] for p in (ans["IR-5"].get("basis") or {}).get("precedents", [])]}
    return {"label": label, "confidence": conf, "severity": sev, "action": action, "action_reason": reason,
            "recipients": list(consumers(IR)) if label in ("INCIDENT", "WATCH") else [],
            "drivers": drivers, "contrary": contrary, "questions": trace, "limitations": _ir_limitations(ans),
            "decided": decided, "memory_payload": pay, "incident_status": status, "decision": label,
            "category": category}


PLAN_VERIFIED, PLAN_UNVERIFIED, PLAN_REJECTED, PLAN_UNAVAILABLE = "VERIFIED", "UNVERIFIED", "REJECTED", "UNAVAILABLE"


def _ir_plan(f, cs):
    """The containment plan's own status, apart from the incident (A-IR1, U-IR1). VERIFIED: the model concluded
    the package's label with the package's action, its finding passed verification, and it gave a plan. UNVERIFIED:
    the same, but with no plan to check. REJECTED: a verifier rejected the model's finding, or it concluded another
    label, or it asked for another (stronger or weaker) action. UNAVAILABLE: no model conclusion at all."""
    want_label = cs.get("decision")
    want_action = (cs.get("incident_status") or {}).get("intervention", {}).get("action")
    detail = (f.get("cop_specific") or {}).get("detail") or {}
    rej = f.get("rejected_candidate") or {}
    concluded = f["label"] if f["status"] == "SUBSTANTIVE" else rej.get("label")
    action = (f.get("proposed_action") or {}).get("action") if f["status"] == "SUBSTANTIVE" else \
        (rej.get("proposed_action") or {}).get("action")
    rec = {"by": "llm", "model_label": concluded, "model_status": f["status"], "model_verified": bool(f.get("verified")),
           "model_action": action, "model_confidence": f.get("confidence") if f["status"] == "SUBSTANTIVE"
           else rej.get("confidence"), "package_label": want_label, "package_action": want_action,
           "scope_entities": detail.get("scope_entities", []), "plan": detail.get("response_proposal"),
           "upstream_basis": detail.get("upstream_basis", []),
           "stop_reason": (f.get("cop_specific") or {}).get("stop_reason")}
    if concluded is None:
        rec.update(status=PLAN_UNAVAILABLE, reason="no model conclusion: " + str(rec["stop_reason"] or f.get("rationale", ""))[:200])
    elif f["status"] != "SUBSTANTIVE":
        rec.update(status=PLAN_REJECTED, reason=f"a verifier rejected the model's {concluded}: "
                                                + str(f.get("rationale", ""))[:240])
    elif concluded != want_label:
        rec.update(status=PLAN_REJECTED, reason=f"the model concluded {concluded}; the verified answers establish "
                                                f"{want_label}")
    elif action != want_action:
        rec.update(status=PLAN_REJECTED, reason=f"the model asked for {action}; the answers support {want_action} "
                                                f"only, and a model never authorises a stronger intervention")
    elif not f.get("verified"):
        rec.update(status=PLAN_REJECTED, reason="the model's finding is not verified")
    elif not rec["plan"]:
        rec.update(status=PLAN_UNVERIFIED, reason="the model concluded the package's incident and response but gave "
                                                  "no containment plan to check")
    else:
        rec.update(status=PLAN_VERIFIED, reason="the model's verified conclusion matches the package's label and "
                                                "response; its plan is recorded for the person who approves")
    return rec


def _ir_plan_limitation(plan):
    from . import decision as D
    if plan["status"] == PLAN_VERIFIED:
        return []
    out = [D.limitation(D.MISSING_EVIDENCE, "IR-5", "containment_plan", "CONTAINMENT_PLAN_NOT_CONFIRMED",
                        f"containment plan {plan['status']}: {plan['reason']}")]
    if plan["status"] == PLAN_REJECTED and plan.get("model_action") not in (None, plan["package_action"]):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "IR-5", "model_action", "MODEL_ACTION_REPLACED",
                                f"the model proposed {plan['model_action']}; the package's response "
                                f"{plan['package_action']} stands"))
    return out


def interpreted_incident_response(f, baseline, plan=None, model_results=None):
    """After verification, for an intervention incident the model was consulted on (D-IR11, A-IR1).

    First call (plan None), on the model's verified finding: when the model concluded the package's INCIDENT with
    the package's IR-5 action and passed verification, its finding is published, with IR-2's severity and the lower
    confidence, and the plan VERIFIED or UNVERIFIED. Otherwise the model's output is set aside and the package's own
    deterministic incident (the baseline) is returned under `cop_specific.verify_package_finding` for the graph to
    verify independently, by the same checks.

    Second call (plan given), on that independently verified deterministic finding: when it passed, the incident
    keeps its label, severity, evidence and deterministic response, the plan is recorded REJECTED or UNAVAILABLE, and
    the model's output is kept as the rejected candidate. When the deterministic evidence itself failed verification
    the finding is INCONCLUSIVE (INCIDENT_UNCONFIRMED) with the answers kept. A finding the package decided is
    returned as it is."""
    import copy
    from .contracts import ORIGIN_LLM
    from . import decision as D
    if plan is not None:
        out = dict(f)
        cs = copy.deepcopy(out.get("cop_specific") or {})
        cs.pop("verify_package_finding", None)
        status = cs.get("incident_status") or {}
        lims = list(cs.get("limitations", [])) + _ir_plan_limitation(plan)
        model_out = plan.get("model_output")
        from .contracts import REJECTED as _REJECTED
        if out.get("status") == _REJECTED:
            # the deterministic incident evidence itself failed the existing verification
            why = out.get("rationale", "")
            status = {**status, "category": "INCIDENT_UNCONFIRMED", "meaning": IR_CATEGORY["INCIDENT_UNCONFIRMED"]}
            lims.append(D.limitation(D.MISSING_EVIDENCE, "IR-1", "incident_evidence", "INCIDENT_EVIDENCE_NOT_VERIFIED",
                                     f"the package's incident finding failed verification: {why}"[:400]))
            cs.update(recipients=[], decision=INCONCLUSIVE)
            out["severity"] = "NONE"
        else:
            out["rejected_candidate"] = model_out
            why = "; ".join(f"{x} {cs['questions'][x]['answer']}" for x in cs.get("drivers", []))
            out["rationale"] = (f"{out['label']} from {why}, verified on the package's deterministic evidence; "
                                f"containment plan {plan['status']}: {plan['reason']}; nothing paused, blocked or "
                                f"contained")[:600]
        plan = dict(plan)
        plan["model_verifier_failures"] = [{"check_id": r.get("check_id"), "reason_code": r.get("reason_code")}
                                           for r in (model_results or []) if r.get("result") == "FAIL"]
        status = {**status, "intervention": {**status.get("intervention", {}), "containment_plan": plan["status"]}}
        cs.update(limitations=lims, incident_status=status, containment_plan=plan, model_check=plan,
                  memory_payload={**(cs.get("memory_payload") or {}), "containment_plan": plan["status"],
                                  "model_check": plan})
        out["cop_specific"] = cs
        return out
    cs = (baseline or {}).get("cop_specific") or {}
    if f.get("origin") != ORIGIN_LLM or not cs.get("questions"):
        return f
    rec = _ir_plan(f, cs)
    rec["accepted"] = rec["status"] in (PLAN_VERIFIED, PLAN_UNVERIFIED)
    rec["action_replaced"] = rec["model_action"] not in (None, rec["package_action"]) and rec["model_label"] == \
        rec["package_label"]
    status = copy.deepcopy(cs.get("incident_status") or {})
    if rec["accepted"]:
        out = dict(f)
        q = copy.deepcopy(cs["questions"])
        q["IR-5"]["basis"] = {**(q["IR-5"]["basis"] or {}), "containment_plan_by_model": rec["plan"],
                              "scope_by_model": rec["scope_entities"]}
        # IR-2 gives the severity and IR-4 the confidence; the model scopes and plans, it does not rate (D-IR20)
        out.update(severity=(baseline or {}).get("severity") or f.get("severity"),
                   confidence=min(f.get("confidence") or 0.0, (baseline or {}).get("confidence") or 0.0))
        status["intervention"] = {**status.get("intervention", {}), "containment_plan": rec["status"]}
        out["cop_specific"] = {**(f.get("cop_specific") or {}), "questions": q, "drivers": cs.get("drivers", []),
                               "limitations": list(cs.get("limitations", [])) + _ir_plan_limitation(rec),
                               "contrary": cs.get("contrary", []), "recipients": cs.get("recipients", []),
                               "memory_payload": {**(cs.get("memory_payload") or {}), "containment_plan": rec["status"],
                                                  "model_check": rec},
                               "incident_status": status, "model_check": rec, "containment_plan": rec,
                               "decision": out["label"], "legacy_reading": cs.get("legacy_reading"),
                               "previous_cycle": cs.get("previous_cycle")}
        return out
    # the model's output is set aside; the package's deterministic incident is verified on its own (graph)
    rej = f.get("rejected_candidate") or {}
    rec["model_output"] = ({k: f.get(k) for k in ("label", "confidence", "claims", "rationale", "status",
                                                    "proposed_action")} if f["status"] == "SUBSTANTIVE"
                           else (rej or None))
    if rec["model_output"]:
        rec["model_output"] = {**rec["model_output"], "reason": rec["reason"], "origin": ORIGIN_LLM}
    pkg = copy.deepcopy({k: v for k, v in (baseline or {}).items() if k != "baseline_note"})
    pkg["cop_specific"] = {**pkg.get("cop_specific", {}), "containment_plan": rec,
                           "stop_reason": rec["stop_reason"] or "containment_plan_" + rec["status"].lower()}
    out = dict(f)
    out["cop_specific"] = {**(f.get("cop_specific") or {}), "verify_package_finding": pkg}
    return out


# ---------------------------------------------------------------- Recovery & Resilience

RR = "recovery_resilience"
RR_QUESTIONS = ("RR-1", "RR-2", "RR-3", "RR-4")
RR_AUTHORITY = ("Recovery & Resilience observes, verifies recovery, watches and alerts; it never executes, requests "
                "or initiates a rollback, restart, block, pause or restoration (specs.permitted_actions NONE, WATCH, "
                "ALERT; agents-harness row 17 'initiate approved recovery' is not granted: U-RR5)")
RR_CATEGORY = {"RECOVERED_VERIFIED": "restoration verified by independent post-restore checks",
               "RECOVERY_INCOMPLETE": "recovery incomplete: some restoration not shown, or failures recurred",
               "RECOVERY_FAILED": "restoration did not return a viable state",
               "IRREVERSIBLE_CHANGE": "an irreversible operation ran; restoration cannot undo it",
               "NO_DISRUPTION": "nothing disrupted the workflow: there is no recovery to judge",
               "NO_RECOVERY_RECORD": "a disruption with no recovery record: whether the system recovered is unknown",
               "APPROVAL_PENDING": "a recovery action awaits a person's approval: nothing has been attempted",
               "NOT_ATTEMPTED": "no recovery action was attempted",
               "RECOVERY_UNCONFIRMED": "a recovery action was attempted but nothing independently confirms the "
                                       "restored state",
               "CONFLICTING_RECORDS": "recovery records disagree about the same restoration",
               "INSUFFICIENT_EVIDENCE": "whether a disruption happened cannot be established",
               "RECOVERED_CHECKPOINT_ONLY": "the checkpoint's state is verified by its post-restore checks; the effect "
                                            "of a disputed irreversible command on the restored resource is not "
                                            "ruled out"}
RR_CHECKED = ("VERIFIED", "FAILED_CHECKS", "PARTIAL_CHECKS", "INTEGRITY_FAILED")
RR_NO_IR = ("Incident Response is downstream of this COP in the current cycle and is not read in it; its earlier-cycle "
            "verified finding is read on the feedback edge as context only (P-RR1)")
RR_IR = "incident_response"
RR_SUSTAINED = ("sustained stability over a monitoring window is not assessed: no monitoring window is approved; the "
                "evidence is successful activity after the restoration")


def _rr_pkg():
    return EA.load_package(RR)


def _rr_ref(r):
    return f"observable:{r.get('observable_id')}"


def _rr_failed(r):
    """requirements._had_failure's failure: success false or a non-zero exit code."""
    return r.get("success") is False or str(r.get("exit_code") if r.get("exit_code") is not None else "0") \
        not in ("0", "0.0")


def _rr_disruptions(session):
    """Rows that disrupted the workflow (D-RR3), each with its kind, split by whether the collectors dispute it."""
    from .tools import IRREVERSIBLE
    out = []
    for r in (session.rows if session else []):
        kinds = [k for k, hit in (("failure", _rr_failed(r) and r.get("observable_type") != "session_end"),
                                  ("stall", bool(r.get("hung_seconds"))),
                                  ("deny_rule", bool(r.get("policy_rule_matched"))
                                   and str(r.get("permission_decision") or "").lower() == "deny"),
                                  ("reverted", bool(r.get("reverted_to_earlier"))),
                                  ("irreversible", bool(IRREVERSIBLE.search(str(r.get("command") or "")))))
                 if hit]
        if kinds:
            out.append({"ref": _rr_ref(r), "kinds": kinds, "at": r.get("occurred_at"), "command": r.get("command"),
                        "disputed": r.get("collectors_agree") is False})
    return out


def _rr_time(rec):
    ex = rec.get("execution") or {}
    return ex.get("at") or rec.get("restored_at")


def _rr_record_state(rec, chk, disputed_refs):
    """One record's state (D-RR5, D-RR6)."""
    ap = str((rec.get("approval") or {}).get("status") or "").upper() or None
    ex = rec.get("execution")
    st = str((ex or {}).get("status") or "").upper() or None
    attempted = st in ("ATTEMPTED", "EXECUTED", "FAILED") or (ex is None and bool(rec.get("restored_at")))
    executed = st == "EXECUTED" or (ex is None and bool(rec.get("restored_at")))
    if not attempted:
        state = {"PENDING": "APPROVAL_PENDING", "REJECTED": "APPROVAL_REJECTED"}.get(ap, "NOT_STARTED")
    elif (ex or {}).get("ref") and ("observable:" + str(ex["ref"]).removeprefix("observable:")) in disputed_refs:
        state = "DISPUTED_EXECUTION"
    elif not chk or not chk.get("checks_total"):
        state = "ATTEMPTED_UNVERIFIED"
    elif chk.get("hash_matches") is False:
        state = "INTEGRITY_FAILED"
    elif chk["checks_passed"] == chk["checks_total"]:
        state = "VERIFIED"
    elif chk["checks_passed"] == 0:
        state = "FAILED_CHECKS"
    else:
        state = "PARTIAL_CHECKS"
    return {"checkpoint_id": rec.get("checkpoint_id"), "action": rec.get("action") or (
                "restore" if rec.get("restored_at") or rec.get("checkpoint_id") else None),
            "recommended_by": rec.get("recommended_by"), "approval": ap, "execution": st or (
                "EXECUTED (legacy record)" if executed else None),
            "attempted": attempted, "executed": executed, "at": _rr_time(rec),
            "expected_outcome": rec.get("expected_outcome"), "disruption_refs": list(rec.get("disruption_refs") or []),
            "checks_passed": (chk or {}).get("checks_passed"), "checks_total": (chk or {}).get("checks_total"),
            "hash_matches": (chk or {}).get("hash_matches"), "state": state}


def _rr_conflicts(states):
    """Checked records on the same checkpoint and time that disagree are conflicting (D-RR6)."""
    groups = {}
    for i, s in enumerate(states):
        if s["state"] in RR_CHECKED:
            groups.setdefault((s["checkpoint_id"], s["at"]), []).append(i)
    bad = []
    for idx in groups.values():
        outcomes = {states[i]["state"] for i in idx}
        if len(outcomes) > 1:
            for i in idx:
                states[i] = dict(states[i], state="CONFLICTING", checked_as=states[i]["state"])
            bad.append([states[i]["checkpoint_id"] for i in idx][0])
    return bad


def _rr1(a, s, states, pkg, records):
    ms = _ct_measures(a, _rk_built(pkg, "RR-1"), states)
    by = {m["metric"]: m for m in ms}
    rows = _rr_disruptions(s)
    undisputed = [d for d in rows if not d["disputed"]]
    disputed = [d for d in rows if d["disputed"]]
    basis = {"measures": ms, "disruptions": undisputed, "disputed": disputed,
             "recorded_restorations": [r.get("checkpoint_id") or r.get("action") for r in (records or [])],
             "kinds": sorted({k for d in undisputed for k in d["kinds"]}),
             "incident_response": RR_NO_IR, "not_calculated": _rk_not_built(pkg, "RR-1")}
    if undisputed or records:
        return "DISRUPTED", basis
    if disputed:
        basis["interpretation_reasons"] = [{
            "code": EA.CONFLICTING_SOURCES, "requirement": "disruption", "check": "collectors_agree",
            "detail": "the only failures are on rows the collectors disagree on (" + ", ".join(d["ref"] for d in disputed)
                      + "): directly disputed evidence does not establish a disruption on its own"}]
        return INCONCLUSIVE, basis
    if by["insights.failed"]["verdict"] in (WITHIN, DOES_NOT_APPLY):
        return "NOT_DISRUPTED", basis
    basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "insights.failed", "check": None,
                                        "detail": f"insights.failed {by['insights.failed'].get('reason') or 'unobserved'}"
                                                  ": whether anything failed is not shown"}]
    return INCONCLUSIVE, basis


def _rr2(a, s, states, pkg, deps, records, checkpoints):
    one = deps["RR-1"]
    ms = _ct_measures(a, _rk_built(pkg, "RR-2"), states)
    rows = one["basis"]["disruptions"] + one["basis"]["disputed"]
    irrev = [d["ref"] for d in rows if "irreversible" in d["kinds"] and not d["disputed"]]
    irrev_disputed = [d["ref"] for d in rows if "irreversible" in d["kinds"] and d["disputed"]]
    disputed_refs = {d["ref"] for d in one["basis"]["disputed"]} | {
        _rr_ref(r) for r in (s.rows if s else []) if r.get("collectors_agree") is False}
    chks = (checkpoints or {}).get("checkpoints") or []
    rec_states = [_rr_record_state(r, chks[i] if i < len(chks) else None, disputed_refs)
                  for i, r in enumerate(records or [])]
    conflicting = _rr_conflicts(rec_states)
    observed = []
    for r in (s.rows if s else []):
        if r.get("reverted_to_earlier") and r.get("collectors_agree") is not False:
            observed.append({"ref": _rr_ref(r), "kind": "work_reverted"})
    srows = [r for r in (s.rows if s else []) if r.get("command") and r.get("collectors_agree") is not False]
    for i, r in enumerate(srows):
        if _rr_failed(r) and any(x["command"] == r["command"] and not _rr_failed(x) for x in srows[i + 1:]):
            observed.append({"ref": _rr_ref(r), "kind": "failed_command_later_succeeded", "command": r["command"]})
    ts = EA.signal_traces.ts
    t_rest = [ts(x["at"]) for x in rec_states if x["attempted"] and x.get("at") and ts(x["at"])]
    t_rest = max(t_rest) if t_rest else None
    effects = []
    for d in rows:
        if "irreversible" in d["kinds"] and d["disputed"]:
            t = ts(d["at"]) if d.get("at") else None
            effects.append({"ref": d["ref"], "command": d["command"], "at": d["at"],
                            "order": ("UNKNOWN" if not (t and t_rest) else "AFTER_RESTORATION" if t > t_rest
                                      else "BEFORE_RESTORATION"),
                            "ruled_out": False,
                            "why": "no recovery record field names the resource the command touched, so its effect "
                                   "on the restored state cannot be ruled out; the collectors dispute the row, so it "
                                   "grounds no irreversible change (R-RR1 A)"})
    basis = {"measures": ms, "irreversible": irrev, "irreversible_disputed": irrev_disputed,
             "unresolved_effects": effects,
             "records_supplied": records is not None, "records": rec_states, "conflicting": conflicting,
             "recovery_observed": observed,
             "recovery_verified": [x["checkpoint_id"] for x in rec_states if x["state"] == "VERIFIED"],
             "not_calculated": _rk_not_built(pkg, "RR-2")}
    if irrev:
        basis["category"] = "IRREVERSIBLE_CHANGE"
        return "IRREVERSIBLE_CHANGE", basis
    if one["outcome"] == "NOT_DISRUPTED":
        basis["category"] = "NO_DISRUPTION"
        return "NOTHING_TO_RESTORE", basis
    why = lambda code, req, detail: [{"code": code, "requirement": req, "check": None, "detail": detail}]
    if one["outcome"] == INCONCLUSIVE and not records:
        basis["category"] = "INSUFFICIENT_EVIDENCE"
        basis["interpretation_reasons"] = why(EA.MISSING_DATA, "RR-1", "no disruption is established, and no "
                                              "recovery record shows one")
        return INCONCLUSIVE, basis
    if records is None:
        basis["category"] = "NO_RECOVERY_RECORD"
        basis["interpretation_reasons"] = why(EA.MISSING_REFERENCE, "recovery_records", "no recovery record store: "
                                              "whether the system returned to a viable state cannot be told")
        return INCONCLUSIVE, basis
    if not records:
        basis["category"] = "NO_RECOVERY_RECORD"
        basis["interpretation_reasons"] = why(EA.MISSING_DATA, "recovery_records", "no recovery was recorded; "
                                              "resumed work or the absence of new errors is not a verified recovery")
        return INCONCLUSIVE, basis
    st = [x["state"] for x in rec_states]
    checked = [x for x in st if x in RR_CHECKED]
    if "INTEGRITY_FAILED" in st or (checked and all(x == "FAILED_CHECKS" for x in checked)):
        basis["category"] = "RECOVERY_FAILED"
        return "NOT_RESTORED", basis
    if checked and len(checked) == len(st) and all(x == "VERIFIED" for x in checked):
        basis["category"] = "RECOVERED_VERIFIED"
        return "RESTORED_VERIFIED", basis
    if "VERIFIED" in checked or "PARTIAL_CHECKS" in checked:
        basis["category"] = "RECOVERY_INCOMPLETE"
        return "PARTIALLY_RESTORED", basis
    cat = ("CONFLICTING_RECORDS" if "CONFLICTING" in st else "APPROVAL_PENDING" if "APPROVAL_PENDING" in st
           else "RECOVERY_UNCONFIRMED" if set(st) & {"ATTEMPTED_UNVERIFIED", "DISPUTED_EXECUTION"}
           else "NOT_ATTEMPTED")
    basis["category"] = cat
    basis["interpretation_reasons"] = why(EA.CONFLICTING_SOURCES if cat == "CONFLICTING_RECORDS" else EA.MISSING_DATA,
                                          "recovery_records", RR_CATEGORY[cat] + " (" + ", ".join(
                                              f"{x['checkpoint_id'] or x['action']}: {x['state']}"
                                              for x in rec_states) + ")")
    return INCONCLUSIVE, basis


def _rr_precedents(memory):
    out = []
    for x in (memory or {}).get("established", [])[:10]:
        p = x.get("payload") or {}
        out.append({"record_id": x["record_id"], "label": p.get("label"), "category": p.get("category"),
                    "session_id": p.get("session_id"), "status": x.get("status"),
                    "use": "comparison only, never proof"})
    return out


def _rr_restored_at(two):
    ts = EA.signal_traces.ts
    t = [ts(x["at"]) for x in two["basis"].get("records", []) if x["state"] in ("VERIFIED", "PARTIAL_CHECKS")
         and x.get("at") and ts(x["at"])]
    return max(t) if t else None


def _rr3(a, s, states, pkg, deps, memory):
    two = deps["RR-2"]
    ms = _ct_measures(a, _rk_built(pkg, "RR-3"), states)
    prec = _rr_precedents(memory)
    basis = {"measures": ms, "precedents": prec,
             "recurrence_across_sessions": [p["record_id"] for p in prec
                                            if p["label"] in ("FAILED_RECOVERY", "PARTIAL_RECOVERY", "IRREVERSIBLE")],
             "recurrence_note": "validated earlier recovery outcomes on this tenant: comparison only, no effect",
             "not_calculated": _rk_not_built(pkg, "RR-3")}
    if two["outcome"] not in ("RESTORED_VERIFIED", "PARTIALLY_RESTORED"):
        return "NO_RECOVERY_TO_FOLLOW", basis
    ts = EA.signal_traces.ts
    t0 = _rr_restored_at(two)
    basis["restored_at"] = t0.isoformat() if t0 else None
    after = [d for d in _rr_disruptions(s) if not d["disputed"] and t0 and d["at"] and ts(d["at"]) and ts(d["at"]) > t0
             and set(d["kinds"]) & {"failure", "stall"}]
    basis["recurring"] = after
    return ("RECURRING" if after else "NOT_RECURRING"), basis


def _rr4(a, s, states, pkg, deps):
    one, two, three = deps["RR-1"], deps["RR-2"], deps["RR-3"]
    ms = _ct_measures(a, _rk_built(pkg, "RR-4"), states)
    tele = [m["metric"] for m in ms if m["metric"].startswith("telemetry.") and m["verdict"] == DEVIATES]
    ts = EA.signal_traces.ts
    t0 = _rr_restored_at(two)
    window = [_rr_ref(r) for r in (s.rows if s else []) if t0 and r.get("observable_type") == "tool_request"
              and r.get("success") is True and r.get("collectors_agree") is not False and ts(r.get("occurred_at"))
              and ts(r["occurred_at"]) > t0 and not _rr_failed(r)]
    times = sorted(ts(r["occurred_at"]) for r in (s.rows if s else []) if _rr_ref(r) in window)
    effects = (two.get("basis") or {}).get("unresolved_effects") or []
    basis = {"measures": ms, "telemetry_deviating": tele, "post_recovery_window": window,
             "stability_evidence": {"kind": "POST_RESTORE_ACTIVITY", "successful_steps": len(window),
                                    "span_seconds": (times[-1] - t0).total_seconds() if times and t0 else 0,
                                    "sustained_stability": "NOT_ASSESSED", "why": RR_SUSTAINED},
             "authority": RR_AUTHORITY, "executed_by_this_cop": [], "not_calculated": _rk_not_built(pkg, "RR-4")}
    o2 = two["outcome"]
    if o2 in ("IRREVERSIBLE_CHANGE", "NOT_RESTORED"):
        basis["recommendation"] = "further restoration is recommended; a person decides and acts"
        return "INTERVENTION_REQUIRED", basis
    if o2 == "NOTHING_TO_RESTORE":
        return "NOTHING_TO_CONFIRM", basis
    if o2 == "RESTORED_VERIFIED" and three["outcome"] == "NOT_RECURRING" and window and not tele and not effects:
        return "STABILITY_CONFIRMED", basis
    basis["stability_not_confirmed"] = (
        "failures recurred after the restoration" if three["outcome"] == "RECURRING" else
        "the effect of a disputed irreversible command (" + ", ".join(e["ref"] for e in effects) + ") on the restored "
        "resource is not ruled out" if effects and o2 == "RESTORED_VERIFIED" else
        "no undisputed successful work after the restoration time" if o2 == "RESTORED_VERIFIED" and not window else
        "telemetry in a deviation state: " + ", ".join(tele) if o2 == "RESTORED_VERIFIED" and tele else
        "restoration incomplete" if o2 == "PARTIALLY_RESTORED" else "recovery not established")
    return "MONITORING_REQUIRED", basis


def observe_recovery_resilience(assessments, session, records=None, checkpoints=None, upstream=None, memory=None):
    """{question_id: assessment} -> {question_id: answer}. records: the case's recovery_records (None when there is
    no store); checkpoints: tools.validate_checkpoints's result; upstream: the consumed findings (Behavior
    Analytics); memory: {established, verified_pending} recovery_outcome records."""
    pkg = _rr_pkg()
    states = tuple(pkg["deviation_states"])
    out = {}
    for qid, q in pkg["questions"].items():
        a = assessments[qid]
        if qid == "RR-1":
            outcome, basis = _rr1(a, session, states, pkg, records)
            basis["upstream"] = _rr_upstream(upstream, pkg)
        elif qid == "RR-2":
            outcome, basis = _rr2(a, session, states, pkg, out, records, checkpoints)
        elif qid == "RR-3":
            outcome, basis = _rr3(a, session, states, pkg, out, memory)
        else:
            outcome, basis = _rr4(a, session, states, pkg, out)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": a["sufficiency"]["status"], "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE or basis.get("interpretation_reasons"):
            out[qid]["because"] = basis.get("interpretation_reasons", [])
    return out


def _rr_upstream(upstream, pkg):
    """Behavior Analytics, the only consumed COP, as context (D-RR8): listed with its source items, never a ground."""
    from ..specs import CONSUMES
    out = {}
    for k in CONSUMES[RR]:
        f = (upstream or {}).get(k)
        if f is None:
            out[k] = {"present": False, "class": "ABSENT"}
            continue
        ok = bool(f.get("verified")) and f.get("status") == "SUBSTANTIVE"
        spec = pkg["upstream"].get(k, {})
        cls = ("QUIET" if f.get("label") in spec.get("quiet", []) else "CONTEXT") if ok else "NOT_VERIFIED"
        out[k] = {"present": True, "label": f.get("label"), "finding_id": f.get("finding_id"), "verified": ok,
                  "class": cls, "ground": False, "sources": sorted(rk_source_items(f, upstream)) if ok else [],
                  "why": "context: a behaviour pattern on the same failure evidence, never a recovery ground (D-RR8)"}
    return out


def assess_recovery_resilience(rows, session_id, cfg=None, computed_at=None, records=None, checkpoints=None,
                               upstream=None, memory=None, raw_events=None, native=None):
    """The four questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    sessions = EA._computed(rows, cfg, computed_at)[1]
    s = next(x for x in sessions if x.id == session_id)
    assessments = {qid: EA.assess(RR, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[RR]}
    answers = observe_recovery_resilience(assessments, s, records, checkpoints, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_rr(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if o == INCONCLUSIVE:
        return _explain(qid, ans)
    if qid == "RR-1":
        if o == "NOT_DISRUPTED":
            return "no failure, stall, deny-rule stop, reverted work or recorded restoration"
        return ("disrupted: " + (", ".join(f"{d['ref']} ({'/'.join(d['kinds'])})" for d in b["disruptions"])
                                 or "a recovery record shows a restoration was needed"))
    if qid == "RR-2":
        if o == "IRREVERSIBLE_CHANGE":
            return "irreversible operation on undisputed row(s): " + ", ".join(b["irreversible"])
        if o == "NOTHING_TO_RESTORE":
            return "nothing to restore"
        return (RR_CATEGORY[b["category"]] + ": " + ", ".join(
            f"{x['checkpoint_id'] or x['action']} {x['state']} ({x['checks_passed']}/{x['checks_total']} checks)"
            for x in b["records"]))
    if qid == "RR-3":
        if o == "RECURRING":
            return "failures recurred after the restoration: " + ", ".join(d["ref"] for d in b["recurring"])
        if o == "NOT_RECURRING":
            return "no failure or stall after the restoration time"
        return "no verified restoration to follow"
    if o == "STABILITY_CONFIRMED":
        return (f"post-restore activity succeeded: {len(b['post_recovery_window'])} successful step(s) after the "
                f"restoration, no recurrence, telemetry not deviating; sustained stability not assessed (no approved "
                f"monitoring window)")
    if o == "INTERVENTION_REQUIRED":
        return b["recommendation"]
    if o == "MONITORING_REQUIRED":
        return "keep watching: " + b["stability_not_confirmed"]
    return "nothing to confirm"


def _rr_limitations(ans):
    from . import decision as D
    out = []
    pkg = _rr_pkg()
    b1, b2, b3, b4 = ((ans[q].get("basis") or {}) for q in RR_QUESTIONS)
    for d in b1.get("disputed", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RR-1", d["ref"], EA.CONFLICTING_SOURCES,
                                f"the collectors disagree on {d['ref']} ({'/'.join(d['kinds'])}): not a ground alone",
                                [d["ref"]]))
    if b2.get("irreversible_disputed"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RR-2", "irreversible", EA.CONFLICTING_SOURCES,
                                "an irreversible command on a row the collectors disagree on is listed and grounds "
                                "nothing", b2["irreversible_disputed"]))
    for x in b2.get("records", []):
        if x["state"] in ("ATTEMPTED_UNVERIFIED", "DISPUTED_EXECUTION"):
            out.append(D.limitation(D.MISSING_EVIDENCE, "RR-2", x["checkpoint_id"] or x["action"] or "record",
                                    "RECOVERY_NOT_VERIFIED", f"{x['action'] or 'the action'} was attempted "
                                    f"({x['execution']}); no independent post-restore check confirms the state"))
        if x["state"] in RR_CHECKED and x.get("hash_matches") is None:
            out.append(D.limitation(D.MISSING_EVIDENCE, "RR-2", x["checkpoint_id"] or "record",
                                    "CHECKPOINT_INTEGRITY_NOT_CHECKED", "no checkpoint content was recorded, so its "
                                    "hash cannot be recomputed; the post-restore checks stand"))
    if b2.get("category") == "NO_RECOVERY_RECORD":
        out.append(D.limitation(D.MISSING_EVIDENCE, "RR-2", "recovery_records", EA.MISSING_REFERENCE,
                                "no recovery record: restoration is not shown"))
    for e in b2.get("unresolved_effects", []):
        if ans["RR-2"]["outcome"] in ("RESTORED_VERIFIED", "PARTIALLY_RESTORED"):
            out.append(D.limitation(D.MISSING_EVIDENCE, "RR-2", e["ref"], "IRREVERSIBLE_EFFECT_NOT_RULED_OUT",
                                    f"{e['command']} ({e['order'].lower().replace('_', ' ')}) on a disputed row: its "
                                    "effect on the restored resource is not ruled out; the verification covers the "
                                    "checkpoint only", [e["ref"]]))
    if ans["RR-4"]["outcome"] == "STABILITY_CONFIRMED":
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RR-4", "monitoring_window", "SUSTAINED_STABILITY_NOT_ASSESSED",
                                RR_SUSTAINED))
    if ans["RR-4"]["outcome"] == "MONITORING_REQUIRED" and ans["RR-2"]["outcome"] == "RESTORED_VERIFIED":
        out.append(D.limitation(D.MISSING_EVIDENCE, "RR-4", "post_recovery_window", "STABILITY_NOT_CONFIRMED",
                                b4.get("stability_not_confirmed", "")))
    for m in b4.get("telemetry_deviating", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "RR-4", m, "TELEMETRY_QUALITY",
                                f"{m} is in a deviation state: stability is not confirmed, grounds stand"))
    if b3.get("recurrence_across_sessions"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RR-3", "recurrence", "RECURRENCE_NOT_WEIGHED",
                                "validated earlier recovery outcomes are listed; no approved rule weighs them (U-RR4)"))
    for k, v in (b1.get("upstream") or {}).items():
        if v["class"] == "ABSENT":
            out.append(D.limitation(D.MISSING_EVIDENCE, "RR-1", k, "UPSTREAM_ABSENT",
                                    f"no {k} finding in this case: context only, nothing is grounded on it"))
    for qid in RR_QUESTIONS:
        for x in (ans[qid].get("basis") or {}).get("not_calculated", []):
            out.append(D.limitation(D.NOT_CALCULATED, qid, x["metric"], D.NOT_CALCULATED, x["missing_inputs"]))
    for k, v in pkg["not_calculated"].items():
        out.append(D.limitation(D.NOT_CALCULATED, "RR-2", k, D.NOT_CALCULATED, " ".join(v.split())))
    out.append(D.limitation(D.LIMIT_NOT_APPROVED, "RR-2", "confidence", "UNCALIBRATED_CONFIDENCE",
                            "the confidence is the legacy rule's constant, not calibrated against outcomes (U-RR7)"))
    return out


def _rr_category(ans, label):
    if label == "RECOVERED":
        return "RECOVERED_CHECKPOINT_ONLY" if (ans["RR-2"].get("basis") or {}).get("unresolved_effects") \
            else "RECOVERED_VERIFIED"
    if label == "PARTIAL_RECOVERY":
        return "RECOVERY_INCOMPLETE"
    if label == "FAILED_RECOVERY":
        return "RECOVERY_FAILED"
    if label == "IRREVERSIBLE":
        return "IRREVERSIBLE_CHANGE"
    if ans["RR-1"]["outcome"] == INCONCLUSIVE:
        return "INSUFFICIENT_EVIDENCE"
    return (ans["RR-2"].get("basis") or {}).get("category") or "INSUFFICIENT_EVIDENCE"


def _rr_status(ans, label, category, action):
    """Intervention recommended, approved, attempted and executed; recovery observed and verified; the outcome, kept
    apart (D-RR5)."""
    b2 = ans["RR-2"].get("basis") or {}
    recs = b2.get("records", [])
    name = lambda x: f"{x['action'] or 'restore'}:{x['checkpoint_id']}" if x["checkpoint_id"] else (x["action"] or "?")
    return {"category": category, "meaning": RR_CATEGORY[category],
            "disruption": {"answer": ans["RR-1"]["outcome"],
                           "refs": [d["ref"] for d in (ans["RR-1"].get("basis") or {}).get("disruptions", [])]},
            "intervention": {"recommended": [name(x) for x in recs if x["action"]],
                             "approved": [name(x) for x in recs if x["approval"] in ("APPROVED", "NOT_REQUIRED")],
                             "approval_pending": [name(x) for x in recs if x["approval"] == "PENDING"],
                             "attempted": [name(x) for x in recs if x["attempted"]],
                             "executed_successfully": [name(x) for x in recs if x["executed"]],
                             "executed_by_this_cop": [], "authority": RR_AUTHORITY,
                             "note": "an executed action says the action ran, not that the system recovered"},
            "recovery": {"observed": b2.get("recovery_observed", []), "verified": b2.get("recovery_verified", []),
                         "outcome": label, "recurring": [d["ref"] for d in (ans["RR-3"].get("basis") or {}).get(
                             "recurring", [])],
                         "stability": ans["RR-4"]["outcome"],
                         "verified_scope": ("checkpoint only: unresolved effects not ruled out"
                                            if b2.get("unresolved_effects") else "checkpoint and post-restore checks"),
                         "unresolved_effects": [e["ref"] for e in b2.get("unresolved_effects", [])],
                         "stability_scope": ("post-restore activity; sustained stability not assessed"
                                             if ans["RR-4"]["outcome"] == "STABILITY_CONFIRMED" else None)},
            "action": action}


def rr_earlier_incident(finding, session_id, questions):
    """Incident Response's earlier-cycle finding on the P-RR1 feedback edge (CONNECTIONS.md), read after the four
    answers and never by them: context only. graph.prior_feedback has already withheld anything unverified, from the
    current cycle or without a run_id. This reads it only with a decided_at and an association (the case session, or
    a recovery record's disruption_refs naming it), and only when decided on or after the session's first undisputed
    disruption and before the restoration time it contextualizes. It is never modified (a copy of its identity is
    kept)."""
    if finding is None:
        return {"status": "NOT_DELIVERED", "finding_id": None, "label": None,
                "reason": "no verified earlier-cycle Incident Response finding was delivered (graph feedback)"}
    ts = EA.signal_traces.ts
    ans = {q: v["answer"] for q, v in questions.items()}
    b1, b2 = ans["RR-1"].get("basis") or {}, ans["RR-2"].get("basis") or {}
    ir1 = (((finding.get("cop_specific") or {}).get("questions") or {}).get("IR-1") or {}).get("basis") or {}
    grounds = [g.get("kind") for g in ir1.get("grounds") or []]
    plan = (finding.get("cop_specific") or {}).get("containment_plan") or {}
    own = [u for u in finding.get("upstream_used") or [] if u.get("cop") == RR]
    out = {"finding_id": finding.get("finding_id"), "label": finding.get("label"), "run_id": finding.get("run_id"),
           "decided_at": finding.get("decided_at"), "session_id": finding.get("session_id"),
           "verified": bool(finding.get("verified")), "grounds": grounds,
           "containment_plan": plan.get("status"),
           # IR grounds this COP's FAILED_RECOVERY or IRREVERSIBLE as failed_recovery, PARTIAL_RECOVERY as
           # partial_recovery (IR_INCIDENT_KINDS, IR_FINDING_KINDS): such an incident is never read as confirmation
           "rests_on_this_cop": bool({"failed_recovery", "partial_recovery"} & set(grounds)),
           "this_cop_reading_it_read": [u.get("label") for u in own],
           "use": "context only: names the earlier disruption and its containment plan; never a ground for "
                  "FAILED_RECOVERY, IRREVERSIBLE, PARTIAL_RECOVERY or RECOVERED, and never changes an answer"}
    refs = {str(r) for x in b2.get("records", []) for r in x.get("disruption_refs") or []}
    by_session = bool(session_id) and finding.get("session_id") == session_id
    by_incident = bool(out["finding_id"]) and (out["finding_id"] in refs or f"finding:{out['finding_id']}" in refs)
    out["association"] = "SESSION" if by_session else "INCIDENT" if by_incident else None
    t = ts(out["decided_at"]) if out["decided_at"] else None
    first = [ts(d["at"]) for d in b1.get("disruptions", []) if d.get("at") and ts(d["at"])]
    rest = [ts(x["at"]) for x in b2.get("records", []) if x.get("attempted") and x.get("at") and ts(x["at"])]
    out["first_disruption_at"] = min(first).isoformat() if first else None
    out["restoration_at"] = max(rest).isoformat() if rest else None
    if not finding.get("verified") or finding.get("status") != "SUBSTANTIVE":
        status, why = "REJECTED_UNVERIFIED", "the earlier finding is not a verified substantive reading"
    elif not out["run_id"]:
        status, why = "REJECTED_NO_CYCLE", "the earlier finding names no cycle (run_id)"
    elif not t:
        status, why = "REJECTED_NO_TIMESTAMP", "the earlier finding has no usable decided_at"
    elif not out["association"]:
        status, why = "REJECTED_WRONG_SESSION", (f"the earlier finding belongs to session {out['session_id']}, not "
                                                 f"{session_id}, and no recovery record names it")
    elif first and t < min(first):
        status, why = "REJECTED_STALE", "decided before this session's first disruption: it cannot name it"
    elif not rest:
        status, why = "LISTED_NO_RESTORATION", "no restoration time to contextualize: listed only"
    elif t >= max(rest):
        status, why = "REJECTED_ORDER", "decided at or after the restoration: it does not precede the recovery " \
                                        "observation"
    else:
        status, why = "ACCEPTED_AS_CONTEXT", "verified earlier-cycle incident decided before the restoration"
    out.update(status=status, reason=why)
    return out


def decide_recovery_resilience(questions, earlier=None):
    """The COP decision from the four answers (INTERPRETATION.md). RR-2 gives the label (RR-3 RECURRING turns a
    verified restoration into PARTIAL_RECOVERY), the label the severity and confidence, RR-4 the action. Decided by
    the package; no model."""
    pkg = _rr_pkg()
    ans = {q: v["answer"] for q, v in questions.items()}
    two, three, four = (ans[q]["outcome"] for q in ("RR-2", "RR-3", "RR-4"))
    label = pkg["labels"][two]
    if label == "RECOVERED" and three == "RECURRING":
        label = pkg["recurring_label"]
    sev, conf = pkg["severity"][label], pkg["confidence"][label]
    action = pkg["responses"].get(four, "WATCH")
    category = _rr_category(ans, label)
    reason = _explain_rr("RR-4", ans["RR-4"]) + f" ({RR_CATEGORY[category]})"
    drivers = ["RR-1", "RR-2", "RR-3", "RR-4"]
    trace = _question_trace(questions, _explain_rr, drivers)
    b2 = ans["RR-2"].get("basis") or {}
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items() for x in v["answer"].get("findings", [])]
    contrary += [f"RR-1: {d['ref']} is disputed: not a ground alone" for d in (ans["RR-1"].get("basis") or {}).get(
        "disputed", [])]
    contrary += [f"RR-2: irreversible command on disputed row {r}: not a ground alone"
                 for r in b2.get("irreversible_disputed", [])]
    contrary += [f"RR-2: {x['kind']} at {x['ref']}: recovery observed, not verified" for x in b2.get("recovery_observed",
                                                                                                    [])]
    contrary += [f"RR-1: {k} reads {v['label']}: context, not a ground" for k, v in
                 ((ans["RR-1"].get("basis") or {}).get("upstream") or {}).items() if v.get("present")]
    status = _rr_status(ans, label, category, action)
    status["earlier_incident"] = earlier
    lims = _rr_limitations(ans)
    if not earlier or earlier["status"] != "ACCEPTED_AS_CONTEXT":
        from . import decision as D
        lims.append(D.limitation(D.MISSING_EVIDENCE, "RR-1", RR_IR, "INCIDENT_CONTEXT_NOT_READ",
                                 RR_NO_IR + ": " + ((earlier or {}).get("reason")
                                                    or "no earlier-cycle Incident Response finding was delivered")))
    if earlier and earlier.get("finding_id"):
        contrary.append(f"RR-1: earlier-cycle Incident Response {earlier['label']} ({earlier['status']}): context, "
                        "never a ground")
    from ..specs import consumers
    pay = {"answers": {q: x["outcome"] for q, x in ans.items()}, "category": category,
           "records": [{k: x[k] for k in ("checkpoint_id", "action", "state")} for x in b2.get("records", [])],
           "intervention": status["intervention"] | {"authority": None},
           "recovery": status["recovery"], "decided_by": "package",
           "precedents_read": [p["record_id"] for p in (ans["RR-3"].get("basis") or {}).get("precedents", [])],
           "earlier_incident_context": (earlier or {}).get("finding_id") if (earlier or {}).get("status") ==
           "ACCEPTED_AS_CONTEXT" else None}
    return {"label": label, "confidence": conf, "severity": sev, "action": action, "action_reason": reason,
            "recipients": list(consumers(RR)) if label != INCONCLUSIVE else [],
            "drivers": drivers, "contrary": contrary, "questions": trace, "limitations": lims,
            "decided": True, "memory_payload": pay, "recovery_status": status, "decision": label,
            "category": category}


# ---------------------------------------------------------------- Behavior Analytics

BA = "behavior_analytics"
BA_QUESTIONS = ("BA-1", "BA-2", "BA-3", "BA-4")
BA_AUTHORITY = ("Behavior Analytics observes, classifies, records and passes; it enforces nothing from a behavioural "
                "characterization (agents-harness row 16; specs.permitted_actions NONE, WATCH)")
BA_RECOUNTED = ("insights.retry", "insights.failed")
BA_SCOPE = {"ISOLATED": "this execution only: no earlier ordered session of this agent deviated on the same metric",
            "RECURRING": "recurring across distinct sessions of this agent",
            "WORSENING": "worsening across consecutive sessions of this agent",
            INCONCLUSIVE: "undetermined: the history cannot place this reading in time",
            "NO_CURRENT_PATTERN": "none: no behavioural pattern in this execution"}
BA_PERSISTENCE = ("persistence is not assessed: no time window or persistence threshold is approved (workbook row 185 "
                  "column 5); recurrence and worsening use the approved metric states over all ordered history (U-BA3)")
BA_AD = ("Anomaly Detection reads the same metrics: its earlier finding is context for persistence and never a "
         "separate occurrence (D-BA9)")


def _ba_pkg():
    return EA.load_package(BA)


def _ba_ref(r):
    return f"observable:{r.get('observable_id')}"


def _ba_events(rows, keep_disputed=False):
    """One row per event (tool_use_id, else the row itself), in time order (D-BA3). Rows the collectors dispute are
    left out unless keep_disputed."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r.get("occurred_at") or "", r.get("sequence_num") or 0)):
        if not keep_disputed and r.get("collectors_agree") is False:
            continue
        k = r.get("tool_use_id") or r.get("observable_id")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def ba_recount(rows, cfg, keep_disputed=False):
    """insights.retry and insights.failed over distinct events (signal_metrics.m_retry and m_failed's definitions,
    applied to one row per tool_use_id), judged with the approved thresholds (signal_metrics.judge)."""
    sm = EA.signal_metrics
    ev = _ba_events(rows, keep_disputed)
    failed_cmd, retries = set(), []
    for r in ev:
        if not sm.has(r.get("command")):
            continue
        k = str(r["command"]).strip()
        if k in failed_cmd:
            retries.append(_ba_ref(r))
        if sm.nonzero_exit(r):
            failed_cmd.add(k)
    fails = [_ba_ref(r) for r in ev if r.get("success") is False]
    had_failure = any(r.get("success") is False or sm.nonzero_exit(r) for r in ev)
    th = cfg["thresholds"]
    retry = ({"value": len(retries), "state": sm.judge(len(retries), th["insights.retry"]), "refs": retries}
             if had_failure else {"value": None, "state": "NOT_APPLICABLE", "refs": []})
    return {"insights.retry": retry,
            "insights.failed": {"value": len(fails), "state": sm.judge(len(fails), th["insights.failed"]),
                                "refs": fails},
            "events": len(ev)}


def _ba_disputed(session):
    return {_ba_ref(r) for r in (session.rows if session else []) if r.get("collectors_agree") is False}


def _ba1(a, a2, s, states, pkg, cfg):
    ms = _ct_measures(a, _rk_built(pkg, "BA-1"), states)
    by = {m["metric"]: m for m in ms}
    rows = s.rows if s else []
    rc = ba_recount(rows, cfg)
    with_disputed = ba_recount(rows, cfg, keep_disputed=True)
    recount = []
    for mid in BA_RECOUNTED:
        raw = by[mid]
        x = rc[mid]
        dup = (raw["state"] in states and with_disputed[mid]["state"] not in states)
        disp = (with_disputed[mid]["state"] in states and x["state"] not in states)
        recount.append({"metric": mid, "raw_value": raw["value"], "raw_state": raw["state"], "value": x["value"],
                        "state": x["state"], "events": x["refs"],
                        "deviates": x["state"] in states,
                        "only_duplicates": dup, "only_disputed": disp})
    rec = {x["metric"]: x for x in recount}
    rep = by["insights.repetition"]
    bad = _ba_disputed(s)
    rep_refs = list(rep.get("evidence_refs") or [])
    rep_disputed = rep["verdict"] == DEVIATES and rep_refs and all(r in bad for r in rep_refs)
    rep_dev = rep["verdict"] == DEVIATES and not rep_disputed
    workload = [m for m in _ct_measures(a2, pkg["workload_metrics"], states)]
    stall = by["insights.stalled"]
    basis = {"measures": ms, "recount": recount, "events_counted": rc["events"],
             "repetition_disputed": bool(rep_disputed),
             "workload": workload, "workload_deviating": [m["metric"] for m in workload if m["verdict"] == DEVIATES],
             "stalls": stall["evidence_refs"] if stall["verdict"] == DEVIATES else [],
             "reported_not_judged": [m for m in ms if m["verdict"] == NOT_JUDGED],
             "not_calculated": _rk_not_built(pkg, "BA-1"),
             "event_identity": "one event per tool_use_id; rows the collectors dispute are not counted (D-BA3)"}
    retry_dev, failed_dev = rec["insights.retry"]["deviates"], rec["insights.failed"]["deviates"]
    behind = []
    if retry_dev and failed_dev:
        outcome, behind = "RETRY_AFTER_FAILURE", ["insights.retry", "insights.failed"]
    elif retry_dev or rep_dev:
        outcome = "REPEATED_ACTIONS"
        behind = (["insights.retry"] if retry_dev else []) + (["insights.repetition"] if rep_dev else [])
    elif basis["workload_deviating"]:
        outcome, behind = "WORKLOAD_ONLY", list(basis["workload_deviating"])
    else:
        outcome = None
    basis["behind"] = behind
    basis["behind_states"] = {m: (rec[m]["state"] if m in rec else by.get(m, {}).get("state") or next(
        (w["state"] for w in workload if w["metric"] == m), None)) for m in behind}
    if outcome:
        return outcome, basis
    disputed = [x["metric"] for x in recount if x["only_disputed"]] + (["insights.repetition"] if rep_disputed else [])
    if disputed:
        basis["interpretation_reasons"] = [{
            "code": EA.CONFLICTING_SOURCES, "requirement": m, "check": "collectors_agree",
            "detail": f"{m} deviates only on rows the collectors disagree on: directly disputed evidence does not "
                      f"establish a behavioural pattern"} for m in disputed]
        return INCONCLUSIVE, basis
    judged = [m for m in (by[k] for k in ("insights.repetition", "insights.retry", "insights.failed",
                                          "insights.stalled")) if m["verdict"] in (WITHIN, DEVIATES)]
    judged += [m for m in (by.get("oversight.fast_approvals"),) if m and m["verdict"] in (WITHIN, DEVIATES)]
    if judged:
        return "NO_PATTERN", basis
    basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": m["metric"], "check": None,
                                        "detail": f"{m['metric']} {m.get('reason') or m['verdict']}"}
                                       for m in ms if m["metric"] in pkg["pattern_metrics"]]
    return INCONCLUSIVE, basis


def _ba_history_states(sessions, events, cur, metric, cfg):
    """This operator's other sessions of the same tool (D-BA6), each with its state of metric (insights.retry and
    insights.failed recounted by event as in D-BA3), split by whether they can be ordered against cur."""
    by_sid = {}
    for e in events:
        if e["metric_id"] == metric:
            by_sid[e["session_id"]] = e
    ordered, unordered = [], []
    for x in sessions:
        if x.id == cur.id or x.tool != cur.tool or x.operator != cur.operator:
            continue
        if not x.start:
            unordered.append(x.id)
            continue
        if cur.start and x.start >= cur.start:
            continue
        if metric in BA_RECOUNTED:
            r = ba_recount(x.rows, cfg)[metric]
            st, v = r["state"], r["value"]
        else:
            e = by_sid.get(x.id) or {}
            st, v = e.get("state") or "UNKNOWN", e.get("value")
        ordered.append({"session_id": x.id, "start": x.start, "value": v, "state": st})
    ordered.sort(key=lambda h: h["start"])
    return ordered, sorted(unordered)


def _ba3(a, s, states, pkg, deps, sessions, events, cfg, memory):
    one = deps["BA-1"]
    ms = _ct_measures(a, _rk_built(pkg, "BA-3"), states)
    rank = pkg["state_rank"]
    minimum = cfg["min_sessions_for_baseline"]
    b1 = one["basis"] or {}
    behind = list(b1.get("behind") or [])
    fa = next((m for m in ms if m["metric"] == "oversight.fast_approvals"), None)
    if fa and fa["verdict"] == DEVIATES:
        behind.append("oversight.fast_approvals")
    basis = {"measures": ms, "metrics_followed": behind, "min_sessions_for_baseline": minimum,
             "precedents": _ba_precedents(memory), "persistence": "NOT_ASSESSED", "persistence_why": BA_PERSISTENCE,
             "anomaly_detection": BA_AD,
             "reported_not_judged": [m for m in ms if m["verdict"] == NOT_JUDGED],
             "not_calculated": _rk_not_built(pkg, "BA-3"),
             "definitions": {"same_session": "the same session_id", "distinct_sessions": "different session_ids",
                             "history": "this operator's other sessions of the same tool that started earlier",
                             "consecutive": "those sessions in start order", "window": "none approved: all ordered "
                                                                                       "history is read"}}
    if one["outcome"] == INCONCLUSIVE:
        basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": "BA-1", "check": None,
                                            "detail": "BA-1 is unanswered: there is no current pattern to follow"}]
        return INCONCLUSIVE, basis
    if not behind:
        return "NO_CURRENT_PATTERN", basis
    if s is None or not s.start:
        basis["interpretation_reasons"] = [{"code": EA.INCOMPARABLE_TIMESTAMPS, "requirement": "session start",
                                            "check": "MISSING_TIMESTAMPS",
                                            "detail": "this session's rows carry no timestamp: it cannot be ordered "
                                                      "against the agent's earlier sessions"}]
        basis["missing_timestamps"] = True
        return INCONCLUSIVE, basis
    per = {}
    unordered = set()
    for mid in behind:
        hist, un = _ba_history_states(sessions, events, s, mid, cfg)
        unordered |= set(un)
        cur_state = (b1.get("behind_states") or {}).get(mid) or (fa or {}).get("state")
        observed = [h for h in hist if h["state"] != "UNKNOWN"]
        dev = [h for h in observed if h["state"] in states]
        judged = [rank[h["state"]] for h in observed if h["state"] in rank]
        seq = judged + ([rank[cur_state]] if cur_state in rank else [])
        if dev:
            worse = all(x <= y for x, y in zip(seq, seq[1:])) and len(seq) > 1 and seq[-1] > seq[0]
            res = "WORSENING" if worse else "RECURRING"
        elif len(observed) >= minimum:
            res = "ISOLATED"
        else:
            res = INCONCLUSIVE
        per[mid] = {"current_state": cur_state, "history": hist, "observed": len(observed),
                    "earlier_deviations": [h["session_id"] for h in dev], "ranks_in_order": seq, "reading": res}
    basis["per_metric"] = per
    basis["unordered_history"] = sorted(unordered)
    reads = [p["reading"] for p in per.values()]
    if "WORSENING" in reads:
        return "WORSENING", basis
    if "RECURRING" in reads:
        return "RECURRING", basis
    if all(r == "ISOLATED" for r in reads):
        return "ISOLATED", basis
    basis["interpretation_reasons"] = [{
        "code": EA.INSUFFICIENT_HISTORY, "requirement": m, "check": "min_sessions_for_baseline",
        "detail": f"{m}: {p['observed']} earlier observed session(s), fewer than {minimum}: one reading is not "
                  f"generalized to a tendency"} for m, p in per.items() if p["reading"] == INCONCLUSIVE]
    return INCONCLUSIVE, basis


def _ba2(a, s, states, pkg, sessions, events, cfg):
    ms = _ct_measures(a, _rk_built(pkg, "BA-2"), states)
    judged = [m for m in ms if m["verdict"] in (WITHIN, DEVIATES)]
    dev = [m["metric"] for m in ms if m["verdict"] == DEVIATES]
    own = {}
    if s is not None:
        for mid in ("insights.repetition", "insights.retry", "insights.failed"):
            hist, _ = _ba_history_states(sessions, events, s, mid, cfg)
            vals = [h["value"] for h in hist if h["value"] is not None and h["state"] not in ("UNKNOWN",
                                                                                            "NOT_APPLICABLE")]
            own[mid] = {"n": len(vals), "median": sorted(vals)[len(vals) // 2] if vals else None,
                        "max": max(vals) if vals else None}
    earlier = [x for x in sessions if s is not None and x.id != s.id and x.tool == s.tool
               and x.operator == s.operator and x.start and s.start and x.start < s.start]
    basis = {"measures": ms, "deviating": dev, "own_history": own, "earlier_sessions": len(earlier),
             "min_sessions_for_baseline": cfg["min_sessions_for_baseline"],
             "peer_comparison": "not made: no cohorts or comparable task categories (U-BA4)",
             "reported_not_judged": [m for m in ms if m["verdict"] == NOT_JUDGED],
             "not_calculated": _rk_not_built(pkg, "BA-2")}
    if dev:
        return "OUTSIDE_USUAL_RANGE", basis
    if judged:
        return "WITHIN_USUAL_RANGE", basis
    basis["interpretation_reasons"] = [{"code": EA.MISSING_DATA, "requirement": m["metric"], "check": None,
                                        "detail": f"{m['metric']} {m.get('reason') or m['verdict']}"} for m in ms]
    return INCONCLUSIVE, basis


def _ba_upstream(upstream, pkg):
    """Context & Memory and Workflow Orchestration, the authorised integrity reads (D-BA8), and Runtime Monitoring's
    evidence quality."""
    from ..specs import CONSUMES
    out = {}
    for k in CONSUMES[BA]:
        f = (upstream or {}).get(k)
        if f is None:
            out[k] = {"present": False, "class": "ABSENT"}
            continue
        ok = bool(f.get("verified")) and f.get("status") == "SUBSTANTIVE"
        spec = pkg["upstream"].get(k, {})
        lab = f.get("label")
        cls = ("ALTERED" if lab in spec.get("altered", []) else "QUALITY" if lab in spec.get("quality", [])
               else "QUIET" if lab in spec.get("quiet", []) else "CONTEXT") if ok else "NOT_VERIFIED"
        out[k] = {"present": True, "label": lab, "finding_id": f.get("finding_id"), "verified": ok, "class": cls}
    return out


def _ba4(a, s, states, pkg, upstream):
    ms = _ct_measures(a, _rk_built(pkg, "BA-4"), states)
    view = _ba_upstream(upstream, pkg)
    altered = [dict(v, cop=k) for k, v in view.items() if v["class"] == "ALTERED"]
    basis = {"measures": ms, "upstream": view, "altered_by": altered,
             "unverified": [k for k, v in view.items() if v["class"] == "NOT_VERIFIED"],
             "reported_not_judged": [m for m in ms if m["verdict"] == NOT_JUDGED],
             "not_calculated": _rk_not_built(pkg, "BA-4")}
    return ("CONTEXT_ALTERED" if altered else "NO_ALTERATION_SHOWN"), basis


def _ba_precedents(memory):
    out = []
    for rtype, recs in (memory or {}).items():
        for x in (recs or [])[:10]:
            p = x.get("payload") or {}
            out.append({"record_type": rtype, "record_id": x["record_id"], "label": p.get("label"),
                        "session_id": p.get("session_id"), "status": x.get("status"), "counted": False,
                        "use": "context only: an earlier classification never validates itself or counts as an "
                               "occurrence (D-BA10)"})
    return out


def observe_behavior_analytics(assessments, session, sessions, events, cfg, upstream=None, memory=None):
    """{question_id: assessment} -> {question_id: answer}. sessions and events: the estate's signal_metrics sessions
    and metric events (the agent's own history); upstream: the consumed findings; memory: {record_type: established
    records} for behaviour_baseline, context_integrity_finding and workflow_failure."""
    pkg = _ba_pkg()
    states = tuple(pkg["deviation_states"])
    out = {}
    for qid in BA_QUESTIONS:
        q, a = pkg["questions"][qid], assessments[qid]
        if qid == "BA-1":
            outcome, basis = _ba1(a, assessments["BA-2"], session, states, pkg, cfg)
        elif qid == "BA-2":
            outcome, basis = _ba2(a, session, states, pkg, sessions, events, cfg)
        elif qid == "BA-3":
            outcome, basis = _ba3(a, session, states, pkg, out, sessions, events, cfg, memory)
        else:
            outcome, basis = _ba4(a, session, states, pkg, upstream)
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": a["sufficiency"]["status"], "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
        if outcome == INCONCLUSIVE or basis.get("interpretation_reasons"):
            out[qid]["because"] = basis.get("interpretation_reasons", [])
    return out


def assess_behavior_analytics(rows, session_id, cfg=None, computed_at=None, upstream=None, memory=None,
                              raw_events=None, native=None):
    """The four questions for one session: each assessment with its answer."""
    cfg = cfg or EA.signal_metrics.load_config()
    events, srows = EA.session_inputs(rows, session_id, cfg, computed_at)
    all_events, sessions = EA._computed(rows, cfg, computed_at)
    s = next(x for x in sessions if x.id == session_id)
    assessments = {qid: EA.assess(BA, qid, events, srows, cfg, computed_at, raw_events, native)
                   for qid in EA.QUESTIONS[BA]}
    answers = observe_behavior_analytics(assessments, s, sessions, all_events, cfg, upstream, memory)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}


def _explain_ba(qid, ans):
    b = ans["basis"] or {}
    o = ans["outcome"]
    if o == INCONCLUSIVE:
        return _explain(qid, ans)
    if qid == "BA-1":
        rc = "; ".join(f"{x['metric']} {x['value']} {x['state']} over distinct events (raw {x['raw_value']} "
                       f"{x['raw_state']})" for x in b["recount"])
        if o == "RETRY_AFTER_FAILURE":
            return "failed commands reissued and failing again: " + rc
        if o == "REPEATED_ACTIONS":
            return "repeated actions: " + ", ".join(f"{m} {b['behind_states'][m]}" for m in b["behind"]) + "; " + rc
        if o == "WORKLOAD_ONLY":
            return ("workload outside its usual size (" + ", ".join(b["workload_deviating"]) + ") with no repeated "
                    "or failing action pattern: not a behavioural pattern")
        return "no behavioural pattern measure deviates (" + rc + ")"
    if qid == "BA-2":
        if o == "OUTSIDE_USUAL_RANGE":
            return "outside the estate's usual range: " + ", ".join(b["deviating"])
        return (f"within the estate's usual range; {b['earlier_sessions']} earlier session(s) of this agent; no peer "
                "comparison")
    if qid == "BA-3":
        if o == "NO_CURRENT_PATTERN":
            return "no current pattern to follow over time"
        per = b["per_metric"]
        s = "; ".join(f"{m}: {p['reading']} ({p['observed']} earlier observed, deviations in "
                      f"{', '.join(p['earlier_deviations']) or 'none'})" for m, p in per.items())
        return BA_SCOPE[o] + ": " + s + "; persistence not assessed (no approved window)"
    if o == "CONTEXT_ALTERED":
        return "working from altered context: " + ", ".join(f"{x['cop']} {x['label']}" for x in b["altered_by"])
    return "no verified upstream finding shows altered context"


def _ba_limitations(ans):
    from . import decision as D
    out = []
    pkg = _ba_pkg()
    b1, b2, b3, b4 = ((ans[q].get("basis") or {}) for q in BA_QUESTIONS)
    for x in b1.get("recount", []):
        if x["only_duplicates"]:
            out.append(D.limitation(D.MISSING_EVIDENCE, "BA-1", x["metric"], "DUPLICATE_TELEMETRY",
                                    f"{x['metric']} reads {x['raw_value']} ({x['raw_state']}) over rows, "
                                    f"{x['value']} ({x['state']}) over distinct events: the same event seen by "
                                    "several collectors is one event, not repeated behaviour (D-BA3, U-BA1)",
                                    x["events"]))
        if x["only_disputed"]:
            out.append(D.limitation(D.MISSING_EVIDENCE, "BA-1", x["metric"], EA.CONFLICTING_SOURCES,
                                    f"{x['metric']} deviates only on rows the collectors disagree on"))
    if b1.get("stalls"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "BA-1", "insights.stalled", "STALL_NOT_LABELLED",
                                "a stall is reported; Behavior Analytics has no stall label (U-BA2)", b1["stalls"]))
    if ans["BA-1"]["outcome"] == "WORKLOAD_ONLY":
        out.append(D.limitation(D.MISSING_EVIDENCE, "BA-1", "workload", "WORKLOAD_NOT_A_PATTERN",
                                "a workload outlier alone is not a behavioural pattern: reported, label STABLE"))
    if b3.get("missing_timestamps"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "BA-3", "session_start", "MISSING_TIMESTAMPS",
                                "this session cannot be ordered against the agent's history"))
    if b3.get("unordered_history"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "BA-3", "history", "UNORDERED_HISTORY",
                                "earlier sessions without a start timestamp are listed and not used: "
                                + ", ".join(b3["unordered_history"])))
    if ans["BA-3"]["outcome"] == INCONCLUSIVE and not b3.get("missing_timestamps") and b3.get("per_metric"):
        out.append(D.limitation(D.MISSING_EVIDENCE, "BA-3", "history", EA.INSUFFICIENT_HISTORY,
                                "fewer earlier observed sessions than min_sessions_for_baseline: the reading is not "
                                "generalized"))
    if ans["BA-3"]["outcome"] in ("ISOLATED", "RECURRING", "WORSENING"):
        out.append(D.limitation(D.LIMIT_NOT_APPROVED, "BA-3", "persistence", "PERSISTENCE_NOT_ASSESSED",
                                BA_PERSISTENCE))
    out.append(D.limitation(D.MISSING_EVIDENCE, "BA-2", "peer_comparison", "PEER_COMPARISON_NOT_MADE",
                            "no cohorts or comparable task categories (workbook row 183 column 5, U-BA4)"))
    for k in b4.get("unverified", []):
        out.append(D.limitation(D.MISSING_EVIDENCE, "BA-4", k, "UPSTREAM_NOT_VERIFIED",
                                f"the {k} finding is not verified: listed, alters nothing"))
    for k, v in (b4.get("upstream") or {}).items():
        if v["class"] == "ABSENT":
            out.append(D.limitation(D.MISSING_EVIDENCE, "BA-4", k, "UPSTREAM_ABSENT",
                                    f"no {k} finding in this case: no altered context is shown"))
    for qid in BA_QUESTIONS:
        for x in (ans[qid].get("basis") or {}).get("not_calculated", []):
            out.append(D.limitation(D.NOT_CALCULATED, qid, x["metric"], D.NOT_CALCULATED, x["missing_inputs"]))
    for k, v in pkg["not_calculated"].items():
        out.append(D.limitation(D.NOT_CALCULATED, "BA-1", k, D.NOT_CALCULATED, " ".join(v.split())))
    out.append(D.limitation(D.LIMIT_NOT_APPROVED, "BA-1", "confidence", "UNCALIBRATED_CONFIDENCE",
                            "the confidence is the legacy rule's constant, not calibrated against outcomes (U-BA6)"))
    return out


def decide_behavior_analytics(questions, evidence_scale=1.0):
    """The COP decision from the four answers (INTERPRETATION.md). BA-1 gives the pattern label, BA-4 DRIFTING when
    BA-1 shows none, BA-2 and BA-3 the scope it is published with. Decided by the package; no model."""
    from ..interpret import SEV, RANK
    pkg = _ba_pkg()
    ans = {q: v["answer"] for q, v in questions.items()}
    one, two, three, four = (ans[q]["outcome"] for q in BA_QUESTIONS)
    b1 = ans["BA-1"].get("basis") or {}
    if one == INCONCLUSIVE:
        label = INCONCLUSIVE
    elif pkg["labels"][one] in ("POOR_RECOVERY", "LOOPING"):
        label = pkg["labels"][one]
    elif four == "CONTEXT_ALTERED":
        label = pkg["labels"]["CONTEXT_ALTERED"]
    else:
        label = "STABLE"
    sev = pkg["severity"][label]
    if sev == "from_state":
        sev = max((SEV[st] for st in (b1.get("behind_states") or {}).values() if st in SEV), key=RANK.get,
                  default="NONE")
    conf = round(pkg["confidence"][label] * evidence_scale, 3)
    action = pkg["responses"][label]
    drivers = {"POOR_RECOVERY": ["BA-1"], "LOOPING": ["BA-1"], "DRIFTING": ["BA-4"], "STABLE": ["BA-1", "BA-4"],
               INCONCLUSIVE: ["BA-1"]}[label]
    scope_key = three if label in ("POOR_RECOVERY", "LOOPING") or one == "WORKLOAD_ONLY" else "NO_CURRENT_PATTERN"
    if (label in ("POOR_RECOVERY", "LOOPING") and three == "NO_CURRENT_PATTERN") or label == INCONCLUSIVE:
        scope_key = INCONCLUSIVE
    status = {"pattern": label, "answers": {q: x["outcome"] for q, x in ans.items()},
              "scope": BA_SCOPE.get(scope_key, BA_SCOPE[INCONCLUSIVE]), "time_scope": scope_key,
              "persistent": False, "persistence": "NOT_ASSESSED",
              "events": {x["metric"]: {"raw": x["raw_value"], "distinct_events": x["value"], "state": x["state"]}
                         for x in b1.get("recount", [])},
              "authority": BA_AUTHORITY, "enforced_by_this_cop": []}
    trace = _question_trace(questions, _explain_ba, drivers)
    reason = (f"{label}: " + _explain_ba("BA-1", ans["BA-1"]) + f"; scope {status['scope']}")[:400]
    contrary = [f"{q}: {x['check']} {x['detail']}" for q, v in questions.items() for x in v["answer"].get("findings", [])]
    contrary += [f"BA-1: {x['metric']} raw {x['raw_value']} counts duplicate rows of one event"
                 for x in b1.get("recount", []) if x["only_duplicates"]]
    contrary += [f"BA-3: precedent {p['record_id']} ({p['label']}): context, never counted"
                 for p in (ans["BA-3"].get("basis") or {}).get("precedents", [])]
    from ..specs import consumers
    pay = {"answers": status["answers"], "time_scope": scope_key, "events": status["events"],
           "metric_states": {m["metric"]: m["state"] for m in b1.get("measures", [])
                             if m["metric"] in pkg["pattern_metrics"]},
           "decided_by": "package",
           "precedents_read": [p["record_id"] for p in (ans["BA-3"].get("basis") or {}).get("precedents", [])]}
    return {"label": label, "confidence": conf, "severity": sev, "action": action,
            "action_reason": reason if label != "STABLE" else "", "rationale_head": reason,
            "recipients": list(consumers(BA)) if label != INCONCLUSIVE else [], "drivers": drivers,
            "contrary": contrary, "questions": trace, "limitations": _ba_limitations(ans), "decided": True,
            "memory_payload": pay, "behaviour_status": status, "decision": label}


# ---------------------------------------------------------------- Context & Memory (COP 12, retrieval only)
#
# A dedicated memory retrieval and routing COP (packages/context_memory/DECISIONS.md D-CM1). The workflow: request ->
# scope determination -> memory lookup -> access validation -> provenance validation -> relevance filtering ->
# structured response -> consumption record (D-CM7). It reads through MemoryGateway.read as the requesting COP, so the
# requester's own memory.POLICIES entry is the authorization (D-CM12). It never changes a record, writes no memory and
# draws no governance conclusion from what it returns.

CM = "context_memory"
CM_QUESTIONS = ("CM-1", "CM-2", "CM-3", "CM-4")
CM_BUNDLE = "cm_bundle/1"
ESTABLISHED, VERIFIED_NOT_VALIDATED, UNVERIFIED_OBSERVATION, INSUFFICIENTLY_SUPPORTED = (
    "ESTABLISHED", "VERIFIED_NOT_VALIDATED", "UNVERIFIED_OBSERVATION", "INSUFFICIENTLY_SUPPORTED")
STANDING_RANK = {ESTABLISHED: 0, VERIFIED_NOT_VALIDATED: 1, UNVERIFIED_OBSERVATION: 2, INSUFFICIENTLY_SUPPORTED: 3}
RETRIEVED, PARTIAL, NO_RELEVANT_MEMORY, NO_MEMORY_IN_SCOPE = "RETRIEVED", "PARTIAL", "NO_RELEVANT_MEMORY", \
    "NO_MEMORY_IN_SCOPE"
ACCESS_DENIED, STORAGE_UNAVAILABLE, INVALID_REQUEST = "ACCESS_DENIED", "STORAGE_UNAVAILABLE", "INVALID_REQUEST"
FAILED_RETRIEVAL = (ACCESS_DENIED, STORAGE_UNAVAILABLE, INVALID_REQUEST)
# Exclusion reasons that make a request PARTIAL: something the requester asked for and may need was withheld
# (provenance and integrity are CM-3; entitlement and limit are CM-4). The others are outside what was asked.
WITHHELD_FOR_REQUEST = ("INSTRUCTION_SHAPED_CONTENT", "LIMIT_REACHED")
RETIRED_LABELS = ("CONTEXT_OK", "STALE", "CONTRADICTORY", "CONTEXT_LOSS", "PRECEDENT_FOUND")
CM_USE = {
    ESTABLISHED: "validated precedent: the requesting COP may compare against it; its conclusion is the producing "
                 "COP's, not a finding of this retrieval",
    VERIFIED_NOT_VALIDATED: "verified by the producing COP, not validated by a reviewer: context, not precedent",
    UNVERIFIED_OBSERVATION: "recorded without verification: never evidence",
    INSUFFICIENTLY_SUPPORTED: "provenance cannot be established: never evidence",
}
_CM = {}


def _cm_pkg():
    if "pkg" not in _CM:
        _CM["pkg"] = EA.load_package(CM)
    return _CM["pkg"]


def _cm_get(rec, path):
    cur = rec
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _cm_time(v):
    return EA.signal_traces.ts(v) if v else None


def _cm_after(a, b):
    """a strictly after b; None when either is missing or they cannot be compared."""
    ta, tb = _cm_time(a), _cm_time(b)
    if ta is None or tb is None:
        return None
    try:
        return ta > tb
    except TypeError:
        return None


def cm_request(request):
    """CM-1: the request as supplied, normalized, with the required identifiers it lacks. Nothing is filled in."""
    from .memory import POLICIES
    pkg = _cm_pkg()
    r = dict(request or {})
    req = {k: r.get(k) for k in pkg["request"]["required"] + pkg["request"]["optional"]}
    req["session_scope"] = req.get("session_scope") or "any"
    if req.get("memory_types") is not None:
        req["memory_types"] = list(req["memory_types"])
    missing = [k for k in pkg["request"]["required"] if not req.get(k)]
    problems = [f"{k} not supplied" for k in missing]
    if req.get("requesting_cop") and req["requesting_cop"] not in POLICIES:
        problems.append(f"requesting_cop {req['requesting_cop']!r} is not a COP with a memory policy")
    if req["session_scope"] not in pkg["request"]["session_scopes"]:
        problems.append(f"session_scope {req['session_scope']!r} is not one of {pkg['request']['session_scopes']}")
    if req["session_scope"] != "any" and not req.get("session_id"):
        problems.append(f"session_scope {req['session_scope']} needs a session_id")
    if req.get("max_results") is not None and (not isinstance(req["max_results"], int) or req["max_results"] < 1):
        problems.append(f"max_results {req['max_results']!r} is not a positive integer")
    return req, missing, problems


def cm_scope(req):
    """Scope determination: which memory types the requester may read (its own memory.POLICIES), and the boundaries
    applied to them. A type outside its reads is NOT_ENTITLED and never asked of the gateway."""
    from .memory import POLICIES
    pol = POLICIES[req["requesting_cop"]]
    asked = list(req.get("memory_types") or pol["reads"])
    entitled = [t for t in asked if t in pol["reads"]]
    return {"policy_of": req["requesting_cop"], "policy_reads": list(pol["reads"]), "policy_scope": pol["scope"],
            "asked": asked, "entitled": entitled, "not_entitled": [t for t in asked if t not in pol["reads"]],
            "authorized_tenant": req["tenant_id"], "requested_tenant": req.get("requested_tenant"),
            "agent_scope": req.get("agent_scope") if pol["scope"] == "agent" else None,
            "agent_boundary": "agent" if pol["scope"] == "agent" else "tenant",
            "session_scope": req["session_scope"], "session_id": req.get("session_id")}


def cm_provenance(rec, requester):
    """CM-3 for one record: required and expected provenance, whether its own provenance supports its status, and
    instruction-shaped text in its payload. Nothing missing is filled in (D-CM13)."""
    from .memory import independent_reviewer, VERIFIED
    from .contracts import VALIDATED, UNVALIDATED
    from .tools import scan
    pkg = _cm_pkg()["provenance"]
    status = rec.get("validation_status")
    prov = rec.get("provenance") or {}
    missing_req = [f for f in pkg["required"] if _cm_get(rec, f) in (None, "", [])]
    missing_exp = [f for f in pkg["expected"] if _cm_get(rec, f) in (None, "", [])]
    if status == VALIDATED:
        fb = prov.get("feedback") or {}
        supported = fb.get("outcome") == VALIDATED and independent_reviewer(fb.get("reviewer"))
        why = None if supported else "VALIDATED without feedback from an independent reviewer"
    elif status == VERIFIED:
        supported = prov.get("verified") is not False
        why = None if supported else "VERIFIED while its provenance records verified false"
    elif status == UNVALIDATED:
        supported, why = True, None
    else:
        supported, why = False, f"status {status!r} is not one the gateway returns"
    flags = scan({"payload": rec.get("payload"), "provenance": {k: v for k, v in prov.items() if k != "re_observed"}})
    if missing_req or not supported:
        state = "INSUFFICIENT"
    elif missing_exp:
        state = "PARTIAL"
    else:
        state = "SUPPORTED"
    return {"provenance_status": state, "missing_required": missing_req, "missing_expected": missing_exp,
            "status_supported": supported, "status_problem": why, "instruction_flags": flags}


def _cm_standing(status, prov_state):
    from .memory import VERIFIED
    from .contracts import VALIDATED
    if prov_state == "INSUFFICIENT":
        return INSUFFICIENTLY_SUPPORTED
    return {VALIDATED: ESTABLISHED, VERIFIED: VERIFIED_NOT_VALIDATED}.get(status, UNVERIFIED_OBSERVATION)


def _cm_view(rec, req, pv):
    """One returned record: its own fields unchanged, with provenance, standing, cycle, session and independence."""
    prov = rec.get("provenance") or {}
    pay = rec.get("payload") or {}
    standing = _cm_standing(rec.get("validation_status"), pv["provenance_status"])
    sess = pay.get("session_id") or prov.get("session_id")
    rel = "SESSION_UNKNOWN" if not sess or not req.get("session_id") else \
        "SAME_SESSION" if sess == req["session_id"] else "CROSS_SESSION"
    if not prov.get("run_id") or not req.get("run_id"):
        cycle = "CYCLE_UNKNOWN"
    else:
        cycle = "CURRENT_CYCLE" if prov["run_id"] == req["run_id"] and prov.get("case_id") == req["case_id"] \
            else "PREVIOUS_CYCLE"
    own = rec.get("owner_cop") == req["requesting_cop"]
    return {"record_id": rec.get("record_id"), "record_type": rec.get("record_type"), "decision_type": rec.get("record_type"),
            "owner_cop": rec.get("owner_cop"), "tenant_id": rec.get("tenant_id"), "agent_scope": rec.get("agent_scope"),
            "validation_status": rec.get("validation_status"), "standing": standing,
            "created_at": rec.get("created_at"), "expires_at": rec.get("expires_at"),
            "event_time": prov.get("evaluated_at"), "session_id": sess, "case_id": prov.get("case_id"),
            "finding_id": prov.get("finding_id"), "run_id": prov.get("run_id"),
            "source_evidence": list(prov.get("lineage") or []),
            "versions": {k: prov.get(k) for k in ("verifier_registry", "contract_version", "metric_version")},
            "feedback": prov.get("feedback"), "label": pay.get("label"), "payload": rec.get("payload"),
            **pv, "session_relation": rel, "cycle": cycle, "independent_of_requester": not own,
            "may_corroborate": standing == ESTABLISHED and not own,
            "use": ("own earlier output: context only, never independent verification of the current finding; "
                    if own else "") + CM_USE[standing],
            "matching_basis": [], "disagrees_with": []}


def cm_relevance(view, req):
    """Relevance filtering for one record (D-CM12 order of checks after integrity): event time against as_of, since
    and until; session scope; the request's entities. Returns (reason or None, matching basis)."""
    t = view["event_time"]
    if req.get("as_of") and _cm_after(t, req["as_of"]):
        return "FUTURE_EVENT", [f"event {t} after the request's decision time {req['as_of']}"]
    if req.get("since") and _cm_after(req["since"], t):
        return "OUTSIDE_TIME_RANGE", [f"event {t} before since {req['since']}"]
    if req.get("until") and _cm_after(t, req["until"]):
        return "OUTSIDE_TIME_RANGE", [f"event {t} after until {req['until']}"]
    scope = req["session_scope"]
    if scope == "same_session" and view["session_relation"] != "SAME_SESSION":
        return "OUT_OF_SESSION_SCOPE", [f"session {view['session_id']}, not the request's {req.get('session_id')}"]
    if scope == "cross_session" and view["session_relation"] != "CROSS_SESSION":
        return "OUT_OF_SESSION_SCOPE", [f"session {view['session_id']}: the request asks for other sessions"]
    basis = []
    for field, want in (req.get("entities") or {}).items():
        want = list(want) if isinstance(want, (list, tuple, set)) else [want]
        have = (view["payload"] or {}).get(field)
        if have is None:
            have = view.get(field)
        vals = list(have) if isinstance(have, (list, tuple, set)) else [have]
        hit = sorted({str(v) for v in vals if v in want})
        if not hit:
            return "NOT_RELEVANT", [f"{field} {have!r} matches none of {want}"]
        basis.append(f"{field} matches {', '.join(hit)}")
    return None, basis or ["SCOPE_ONLY: no entities named; every in-scope record is relevant by scope"]


def _cm_identity(view):
    pay = view["payload"] or {}
    for k in _cm_pkg()["event_identity_keys"]:
        if pay.get(k):
            return k, str(pay[k])
    return None


def _cm_rank(v):
    t = _cm_time(v["event_time"])
    return (STANDING_RANK[v["standing"]], -(t.timestamp()) if t else float("inf"), str(v["record_id"]))


def _cm_limit(kind, subject, code, detail, refs=()):
    from . import decision as D
    return D.limitation({"m": D.MISSING_EVIDENCE, "l": D.LIMIT_NOT_APPROVED, "n": D.NOT_CALCULATED}[kind], "CM-4",
                        subject, code, detail, refs)


def retrieve_memory(gateway, request, retrieved_at=None):
    """The retrieval workflow for one request (D-CM7). gateway: a memory.MemoryGateway, or None. Returns the
    structured bundle; never raises for a denied or failed read; never writes a memory record."""
    from .memory import MemoryAccessDenied
    from .contracts import stable_id
    pkg = _cm_pkg()
    req, missing, problems = cm_request(request)
    clock = getattr(gateway, "clock", None)
    at = retrieved_at or (clock().isoformat() if callable(clock) else None)
    bid = stable_id(CM_BUNDLE, json.dumps(req, sort_keys=True, default=str), at)
    b = {"contract": CM_BUNDLE, "bundle_id": bid, "package_version": pkg["version"], "request": req,
         "retrieved_at": at, "scope": None, "retrieval_status": None, "records": [], "excluded": [],
         "not_entitled": [], "disagreements": [], "missing": [], "limitations": [], "source_references": [],
         "absence_is_evidence": False, "denied": None, "storage_error": None, "stages": [],
         "operations": {**{k: v["by"] for k, v in pkg["operations"].items()},
                        **{k: "UNAVAILABLE" for k in pkg.get("unavailable_operations", {})}},
         "gateway_excluded": 0, "in_scope_count": 0}
    stage = lambda name, inp, out, failure=None: b["stages"].append(
        {"stage": name, "input": inp, "output": out, "failure": failure})
    stage("request", {"supplied": sorted(k for k, v in (request or {}).items() if v not in (None, "", []))},
          {"missing": missing, "problems": problems}, INVALID_REQUEST if problems else None)
    if problems:
        b["retrieval_status"] = INVALID_REQUEST
        b["missing"] = [{"item": k, "why": "required request identifier not supplied"} for k in missing]
        b["limitations"].append(_cm_limit("m", "request", INVALID_REQUEST, "; ".join(problems)))
        return _cm_finish(gateway, b)
    sc = cm_scope(req)
    b["scope"] = sc
    b["not_entitled"] = [{"record_type": t, "reason": "NOT_ENTITLED",
                          "detail": f"{req['requesting_cop']}'s memory policy does not read {t}"} for t in sc["not_entitled"]]
    stage("scope_determination", {"asked": sc["asked"], "policy_of": sc["policy_of"]},
          {"entitled": sc["entitled"], "not_entitled": sc["not_entitled"], "agent_boundary": sc["agent_boundary"]},
          ACCESS_DENIED if not sc["entitled"] else None)
    if not sc["entitled"]:
        b["retrieval_status"] = ACCESS_DENIED
        b["limitations"].append(_cm_limit("m", "scope", ACCESS_DENIED, "no requested memory type is readable by "
                                          f"{req['requesting_cop']}: " + ", ".join(sc["not_entitled"])))
        return _cm_finish(gateway, b)
    if gateway is None:
        b["retrieval_status"], b["storage_error"] = STORAGE_UNAVAILABLE, "no MemoryGateway is bound to this run"
        stage("memory_lookup", {"types": sc["entitled"]}, None, STORAGE_UNAVAILABLE)
        b["limitations"].append(_cm_limit("m", "memory_store", STORAGE_UNAVAILABLE, b["storage_error"]))
        return _cm_finish(gateway, b)
    try:
        got = gateway.read(req["requesting_cop"], req["case_id"], req["tenant_id"], tuple(sc["entitled"]),
                           sc["agent_scope"] or req.get("agent_scope"), requested_tenant=req.get("requested_tenant"),
                           include_excluded=True)
    except MemoryAccessDenied as e:
        b["retrieval_status"], b["denied"] = ACCESS_DENIED, str(e)
        stage("memory_lookup", {"types": sc["entitled"]}, None, ACCESS_DENIED)
        stage("access_validation", {"requested_tenant": req.get("requested_tenant")}, None, str(e))
        b["limitations"].append(_cm_limit("m", "access", ACCESS_DENIED, f"the gateway denied the read: {e}"))
        return _cm_finish(gateway, b)
    except Exception as e:                                        # the store could not be read
        b["retrieval_status"], b["storage_error"] = STORAGE_UNAVAILABLE, f"{type(e).__name__}: {e}"[:300]
        stage("memory_lookup", {"types": sc["entitled"]}, None, STORAGE_UNAVAILABLE)
        b["limitations"].append(_cm_limit("m", "memory_store", STORAGE_UNAVAILABLE,
                                          f"the memory store could not be read ({b['storage_error']}); this is not "
                                          "evidence that no memory exists"))
        return _cm_finish(gateway, b)
    raw = got["established"] + got["verified_pending"] + got["unverified"]
    gx = got.get("excluded_records") or []
    b["gateway_excluded"], b["in_scope_count"] = got.get("excluded", 0), len(raw) + len(gx)
    stage("memory_lookup", {"types": sc["entitled"], "agent_scope": sc["agent_scope"]},
          {"returned_by_gateway": len(raw), "withheld_by_gateway": len(gx)})
    stage("access_validation", {"tenant": req["tenant_id"], "requested_tenant": req.get("requested_tenant")},
          {"tenant_isolation": "gateway: other tenants' records are never visible, not even as a count",
           "expired_or_rejected": len(gx)})
    for x in gx:
        b["excluded"].append({**x, "detail": {"EXPIRED": "past its retention (expires_at); the gateway never returns "
                                              "it", "REJECTED": "rejected by an independent reviewer"}.get(
                                                  x["reason"], "status the gateway does not return")})
    views, held = [], []
    for rec in raw:
        pv = cm_provenance(rec, req["requesting_cop"])
        v = _cm_view(rec, req, pv)
        if pv["instruction_flags"]:
            held.append({"record_id": v["record_id"], "record_type": v["record_type"], "owner_cop": v["owner_cop"],
                         "validation_status": v["validation_status"], "reason": "INSTRUCTION_SHAPED_CONTENT",
                         "detail": "payload carries instruction-shaped text: " + ", ".join(pv["instruction_flags"][:3])})
            continue
        views.append(v)
    stage("provenance_validation", {"records": len(raw)},
          {"supported": sum(v["provenance_status"] == "SUPPORTED" for v in views),
           "partial": sum(v["provenance_status"] == "PARTIAL" for v in views),
           "insufficient": sum(v["provenance_status"] == "INSUFFICIENT" for v in views),
           "instruction_shaped": len(held)})
    b["excluded"] += held
    kept = []
    for v in views:
        why, basis = cm_relevance(v, req)
        if why:
            b["excluded"].append({"record_id": v["record_id"], "record_type": v["record_type"],
                                  "owner_cop": v["owner_cop"], "validation_status": v["validation_status"],
                                  "reason": why, "detail": "; ".join(basis), "event_time": v["event_time"]})
            continue
        v["matching_basis"] = basis
        kept.append(v)
    groups = {}
    for v in kept:
        ident = _cm_identity(v)
        if ident:
            groups.setdefault((v["record_type"], v["owner_cop"]) + ident, []).append(v)
    dup = set()
    for key, vs in groups.items():
        if len(vs) < 2:
            continue
        vs = sorted(vs, key=lambda v: (STANDING_RANK[v["standing"]], v["created_at"] or "", str(v["record_id"])))
        for d in vs[1:]:
            dup.add(d["record_id"])
            b["excluded"].append({"record_id": d["record_id"], "record_type": d["record_type"],
                                  "owner_cop": d["owner_cop"], "validation_status": d["validation_status"],
                                  "reason": "DUPLICATE_OF", "duplicate_of": vs[0]["record_id"],
                                  "detail": f"same {key[2]} {key[3]} as {vs[0]['record_id']}: one event, recorded "
                                            "again (own status kept)"})
    kept = sorted((v for v in kept if v["record_id"] not in dup), key=_cm_rank)
    limit = req.get("max_results")
    if limit is not None and len(kept) > limit:
        for v in kept[limit:]:
            b["excluded"].append({"record_id": v["record_id"], "record_type": v["record_type"],
                                  "owner_cop": v["owner_cop"], "validation_status": v["validation_status"],
                                  "reason": "LIMIT_REACHED", "detail": f"max_results {limit} reached"})
        kept = kept[:limit]
    elif limit is None:
        b["limitations"].append(_cm_limit("l", "max_results", "NO_APPROVED_LIMIT",
                                          "no retrieval limit is approved and the request named none: nothing was "
                                          "truncated (U-CM8)"))
    stage("relevance_filtering", {"candidates": len(views), "entities": req.get("entities"),
                                  "session_scope": req["session_scope"], "as_of": req.get("as_of")},
          {"relevant": len(kept), "excluded": {r: sum(x["reason"] == r for x in b["excluded"])
                                               for r in sorted({x["reason"] for x in b["excluded"]})}})
    by = {}
    for v in kept:
        if v["session_id"] and v["label"] is not None:
            by.setdefault((v["record_type"], v["session_id"]), []).append(v)
    for (rt_, sess), vs in sorted(by.items()):
        if len({str(v["label"]) for v in vs}) > 1:
            ids = [v["record_id"] for v in vs]
            for v in vs:
                v["disagrees_with"] = [i for i in ids if i != v["record_id"]]
            b["disagreements"].append({"record_type": rt_, "session_id": sess,
                                       "records": [{"record_id": v["record_id"], "label": v["label"],
                                                    "validation_status": v["validation_status"],
                                                    "owner_cop": v["owner_cop"]} for v in vs],
                                       "resolution": "NOT_RESOLVED: the records are returned with their own "
                                                     "statuses; interpreting them belongs to the requesting COP"})
    b["records"] = kept
    b["source_references"] = [f"memory:{v['record_id']}" for v in kept]
    for v in kept:
        if v["missing_expected"]:
            b["limitations"].append(_cm_limit("m", v["record_id"], "PROVENANCE_FIELDS_NOT_RECORDED",
                                              "not recorded: " + ", ".join(v["missing_expected"]),
                                              [f"memory:{v['record_id']}"]))
        if not v["event_time"]:
            b["limitations"].append(_cm_limit("m", v["record_id"], "EVENT_TIME_NOT_RECORDED",
                                              "no event time: the record could not be checked against the "
                                              "request's decision time", [f"memory:{v['record_id']}"]))
    insufficient = [v for v in kept if v["standing"] == INSUFFICIENTLY_SUPPORTED]
    withheld = [x for x in b["excluded"] if x["reason"] in WITHHELD_FOR_REQUEST]
    if insufficient or withheld or b["not_entitled"]:
        b["retrieval_status"] = PARTIAL
    elif kept:
        b["retrieval_status"] = RETRIEVED
    else:
        b["retrieval_status"] = NO_MEMORY_IN_SCOPE if b["in_scope_count"] == 0 else NO_RELEVANT_MEMORY
    if not kept:
        b["missing"].append({"item": ",".join(sc["entitled"]),
                             "why": "no record in scope" if b["in_scope_count"] == 0 else "no relevant record"})
    for t in b["not_entitled"]:
        b["missing"].append({"item": t["record_type"], "why": "NOT_ENTITLED"})
    stage("structured_response", {"records": len(kept)},
          {"retrieval_status": b["retrieval_status"], "insufficiently_supported": len(insufficient),
           "disagreements": len(b["disagreements"])})
    return _cm_finish(gateway, b)


def _cm_answers(b):
    """The four question answers for one bundle (METRICS.yaml questions)."""
    st = b["retrieval_status"]
    recs = b["records"]
    integrity = [x for x in b["excluded"] if x["reason"] == "INSTRUCTION_SHAPED_CONTENT"]
    one = "REQUEST_INCOMPLETE" if st == INVALID_REQUEST else "REQUEST_COMPLETE"
    if st == INVALID_REQUEST:
        two = INCONCLUSIVE
    elif st in (ACCESS_DENIED, STORAGE_UNAVAILABLE):
        two = st
    elif recs:
        two = "RELEVANT_MEMORY_FOUND"
    else:
        two = NO_MEMORY_IN_SCOPE if b["in_scope_count"] == 0 else NO_RELEVANT_MEMORY
    checked = len(recs) + len(integrity)
    bad = sum(v["standing"] == INSUFFICIENTLY_SUPPORTED for v in recs) + len(integrity)
    three = "NOTHING_TO_VALIDATE" if st in FAILED_RETRIEVAL or not checked else \
        "ALL_ATTRIBUTABLE" if not bad else "NONE_ATTRIBUTABLE" if bad == checked else "PARTLY_ATTRIBUTABLE"
    four = "UNAVAILABLE" if st in FAILED_RETRIEVAL else "PARTIAL" if st == PARTIAL else \
        "COMPLETE" if st == RETRIEVED else "NOTHING_RETURNABLE"
    return {"CM-1": one, "CM-2": two, "CM-3": three, "CM-4": four}


def _cm_finish(gateway, b):
    pkg = _cm_pkg()
    b["label"] = pkg["retrieval_status"][b["retrieval_status"]]
    b["answers"] = _cm_answers(b)
    if b["retrieval_status"] in (NO_MEMORY_IN_SCOPE, NO_RELEVANT_MEMORY):
        b["limitations"].append(_cm_limit("m", "memory", "ABSENCE_NOT_EVIDENCE",
                                          "no relevant record was found; that is not evidence that no such event "
                                          "occurred"))
    for t in b["not_entitled"]:
        b["limitations"].append(_cm_limit("m", t["record_type"], "NOT_ENTITLED", t["detail"]))
    b["buckets"] = {k: [v["record_id"] for v in b["records"] if v["standing"] == s] for k, s in (
        ("established", ESTABLISHED), ("verified_pending", VERIFIED_NOT_VALIDATED),
        ("unverified", UNVERIFIED_OBSERVATION), ("insufficiently_supported", INSUFFICIENTLY_SUPPORTED))}
    req = b["request"]
    reason = (f"for {req.get('requesting_cop')}: returned {b['source_references'][:20]}; excluded "
              f"{len(b['excluded'])}; not entitled {[t['record_type'] for t in b['not_entitled']]}")[:600]
    logged = None
    if gateway is not None and hasattr(gateway, "log"):
        try:
            gateway.log(CM, req.get("case_id"), req.get("tenant_id"), "retrieve",
                        ",".join((b["scope"] or {}).get("entitled") or []), b["bundle_id"], b["retrieval_status"],
                        reason)
            logged = "cop_memory_access_log"
        except Exception as e:                                    # the log shares the failing store
            logged = None
            b["limitations"].append(_cm_limit("m", "consumption_record", "NOT_LOGGED",
                                              f"the retrieval could not be logged: {type(e).__name__}"))
    b["consumption_record"] = {"logged_to": logged, "operation": "retrieve", "cop": CM, "outcome": b["retrieval_status"],
                               "delivered_to": req.get("requesting_cop"), "record_ids": [v["record_id"] for v in b["records"]]}
    b["stages"].append({"stage": "consumption_record", "input": {"bundle_id": b["bundle_id"]},
                        "output": b["consumption_record"], "failure": None if logged or gateway is None else "NOT_LOGGED"})
    return b


def runtime_request(cop, case, run_id=None, memory_types=None, purpose=None):
    """The request the runtime makes for a COP: only identifiers the case and run already hold."""
    return {"requesting_cop": cop, "tenant_id": case.tenant_id, "case_id": case.case_id,
            "behavioural_question": BY_KEY[cop].behavioural_question if cop in BY_KEY else None,
            "agent_scope": case.agent_scope, "session_id": case.session_id, "run_id": run_id,
            "memory_types": list(memory_types) if memory_types else None, "as_of": case.computed_at,
            "session_scope": "any", "purpose": purpose}


def bundle_view(b):
    """A bundle without record payloads (they travel in the memory view's buckets)."""
    out = {k: v for k, v in b.items() if k != "records"}
    out["records"] = [{k: v for k, v in r.items() if k != "payload"} for r in b["records"]]
    return out


def memory_buckets(b, shape):
    """The memory view a COP already consumes, built from its bundle: the gateway's three buckets in the gateway's
    order, keeping only the records the bundle returns with that standing (D-CM18)."""
    by = {v["record_id"]: v for v in b["records"]}
    out = {}
    for k in ("established", "verified_pending", "unverified", "insufficiently_supported"):
        out[k] = [shape(by[i]) for i in sorted(b["buckets"][k], key=lambda i: (by[i]["created_at"] or "", i))]
    return out


def assess_context_memory(bundles):
    """{requester: bundle} -> {question_id: {assessment, answer}}. Each question's answer is that of the request
    that sets the COP label (the worst retrieval status, METRICS.yaml label_order); every request's answers are in
    its basis."""
    pkg = _cm_pkg()
    order = pkg["label_order"]
    if not bundles:
        lead = None
    else:
        lead = min(bundles, key=lambda r: (order.index(bundles[r]["label"]), r))
    out = {}
    for qid in CM_QUESTIONS:
        q = pkg["questions"][qid]
        per = {r: {"answer": b["answers"][qid], "retrieval_status": b["retrieval_status"], "bundle_id": b["bundle_id"]}
               for r, b in sorted(bundles.items())}
        outcome = bundles[lead]["answers"][qid] if lead else INCONCLUSIVE
        refs = sorted({x for b in bundles.values() for x in b["source_references"]})
        basis = {"per_request": per, "lead_request": lead}
        if lead:
            lb = bundles[lead]
            basis.update({"CM-1": lambda: {"request": lb["request"], "missing": lb["missing"] if lb["retrieval_status"]
                                           == INVALID_REQUEST else []},
                          "CM-2": lambda: {"scope": lb["scope"], "returned": lb["source_references"],
                                           "excluded": [{k: x.get(k) for k in ("record_id", "reason", "detail",
                                                                               "duplicate_of")} for x in lb["excluded"]],
                                           "not_entitled": lb["not_entitled"], "denied": lb["denied"],
                                           "storage_error": lb["storage_error"], "absence_is_evidence": False},
                          "CM-3": lambda: {"records": [{k: v[k] for k in ("record_id", "validation_status", "standing",
                                                                         "provenance_status", "missing_required",
                                                                         "missing_expected", "status_problem",
                                                                         "independent_of_requester", "may_corroborate",
                                                                         "cycle", "session_relation")}
                                                       for v in lb["records"]],
                                           "disagreements": lb["disagreements"]},
                          "CM-4": lambda: {"retrieval_status": lb["retrieval_status"], "buckets": lb["buckets"],
                                           "missing": lb["missing"], "retrieved_at": lb["retrieved_at"],
                                           "package_version": lb["package_version"]}}[qid]())
        a = {"question": q["question"].strip(), "presence": {"status": "PRESENT" if lead else "ABSENT", "items": [],
                                                              "fields": {}},
             "reliability": {"status": "RELIABLE", "checks": [], "blocking_checks": [], "verified_checks": []},
             "findings": [], "sufficiency": {"status": "SUFFICIENT" if lead else "INSUFFICIENT",
                                             "reasons": [] if lead else [{"code": "NO_REQUEST", "requirement": None,
                                                                          "detail": "no COP requested memory"}],
                                             "not_assessed": [], "missing_requirements": []},
             "evidence_used": refs}
        ans = {"outcome": outcome, "sufficiency": a["sufficiency"]["status"], "basis": basis, "findings": [],
               "not_assessed": [], "evidence_refs": refs}
        if outcome not in q["outcomes"] and outcome != INCONCLUSIVE:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"assessment": a, "answer": ans}
    return out


def _explain_cm(qid, ans):
    o, b = ans["outcome"], ans["basis"]
    per = "; ".join(f"{r} {x['answer']}" for r, x in b["per_request"].items())
    return f"{o} ({per})" if per else o


def decide_context_memory(questions, bundles):
    """The COP reading from its requests (INTERPRETATION.md): the worst retrieval label across them. Action NONE;
    nothing is judged about what the records say. Decided by the package; no model."""
    from . import decision as D
    from ..specs import consumers
    pkg = _cm_pkg()
    order = pkg["label_order"]
    labels = [b["label"] for b in bundles.values()]
    label = min(labels, key=order.index) if labels else INCONCLUSIVE
    trace = {}
    for qid, q in questions.items():
        a, an = q["assessment"], q["answer"]
        trace[qid] = {"question": a["question"], "answer": an["outcome"], "sufficiency": a["sufficiency"]["status"],
                      "explanation": _explain_cm(qid, an), "basis": an["basis"], "metrics": [], "checks_consumed": [],
                      "raw_evidence_refs": an["evidence_refs"][:20],
                      "sufficiency_reasons": a["sufficiency"]["reasons"], "not_assessed": [],
                      "drives_label": qid in ("CM-2", "CM-4")}
    lims = [x for b in bundles.values() for x in b["limitations"]]
    for m, e in pkg["mapping"].items():
        note = " ".join((e.get("note") or "").split())
        if "NOT_CALCULATED" in note:
            lims.append(D.limitation(D.NOT_CALCULATED, "CM-2", m, D.NOT_CALCULATED, note))
    for m, v in pkg["not_calculated"].items():
        if m not in pkg["mapping"]:
            lims.append(D.limitation(D.NOT_CALCULATED, "CM-2", m, D.NOT_CALCULATED, " ".join(v.split())))
    lims.append(D.limitation(D.LIMIT_NOT_APPROVED, "CM-4", "confidence", "UNCALIBRATED_CONFIDENCE",
                             "the confidence is the previous rule's CONTEXT_OK constant, not calibrated (U-CM6)"))
    lims.append(D.limitation(D.MISSING_EVIDENCE, "CM-2", "semantic_relevance", "SEMANTIC_RETRIEVAL_UNAVAILABLE",
                             "no approved semantic-search capability: relevance is deterministic matching only (U-CM4)"))
    status = {r: {"retrieval_status": b["retrieval_status"], "label": b["label"], "bundle_id": b["bundle_id"],
                  "returned": [v["record_id"] for v in b["records"]], "excluded": len(b["excluded"]),
                  "disagreements": len(b["disagreements"])} for r, b in sorted(bundles.items())}
    head = (f"{label}: " + "; ".join(f"{r} {s['retrieval_status']} ({len(s['returned'])} returned, {s['excluded']} "
                                     f"excluded)" for r, s in status.items())
            + "; retrieval only, nothing interpreted")[:400]
    contrary = [f"{r}: {d['record_type']} records about {d['session_id']} disagree; returned unresolved"
                for r, b in bundles.items() for d in b["disagreements"]]
    return {"label": label, "confidence": pkg["confidence"].get(label, 0.0), "severity": pkg["severity"].get(label, "NONE"),
            "action": pkg["responses"].get(label, "NONE"), "action_reason": "", "rationale_head": head,
            "recipients": [r for r in consumers(CM) if r in bundles], "drivers": ["CM-2", "CM-4"],
            "contrary": contrary, "questions": trace, "limitations": lims, "decided": True, "memory_payload": None,
            "retrieval_status": status, "decision": label}
