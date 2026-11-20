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
