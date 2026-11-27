"""
Deterministic readings for all eighteen COPs.

For the eight deterministic COPs these ARE the governing decision. For the
ten LLM COPs they are the computational baseline: recorded beside the model's
finding, never substituted for it silently, and never counted as a verifier
of it (the verifiers in verifiers.py check claims against data instead).

Each rule receives one context:
    spec      the COPReadSpec
    events    usable MetricEvents (INVALID rows withheld)
    picture   evidence.assess() output
    upstream  {cop: finding dict} for the COPs this spec consumes, verified or not
    tools     {tool name: {"tool_call_id", "status", "result"}} from this run
    memory    the memory buckets retrieved for this COP
    case      CaseData

and returns the fields of a Finding. Labels come from the spec's own
vocabulary or INCONCLUSIVE. No threshold is introduced here: metric states
come from metrics_config.json, and every other cut-off is read from an
authoritative artifact (risk register, quota, policy store) or the reading is
reported as unreachable.

Existing interpreters in cops/interpret.py are reused for the eight COPs that
have them, so their tested behaviour carries over unchanged.
"""

import functools
import types

from ..interpret import RuleInterpreter, SEV, RANK
from ..models import INCONCLUSIVE
from .contracts import (Claim, CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE, CLAIM_TOOL_OBSERVATION, CLAIM_UPSTREAM,
                        CLAIM_MEMORY_PRECEDENT, CLAIM_ABSENCE, ORIGIN_RULES,
                        SUBSTANTIVE, MISSING_DEPENDENCY, ATTENTION_STATES)
from ..interpret import EVIDENCE_LEVEL
from ..specs import BY_KEY, SPEC_CONSUMES
from .cop_package import aggregate_runtime_monitoring, decide_audit_traceability, decide_interaction_discovery, \
    decide_policy_enforcement, decide_anomaly_detection, decide_resource_management, decide_classification_tagging, decide_risk_mitigation, decide_incident_response, decide_recovery_resilience, decide_behavior_analytics, AT_CLEAN, CEILING, DECIDED_WITHOUT_MODEL

_RI = RuleInterpreter()
HOT = ("EXCEPTION", "CRITICAL")

# Which tools each COP's deterministic path calls, in order.
DETERMINISTIC_TOOLS = {
    "runtime_monitoring": ("get_collector_breakdown", "check_collector_expectations", "assess_runtime_evidence"),
    "interaction_discovery": ("build_interaction_graph", "compare_known_routes", "assess_interaction_questions"),
    "policy_enforcement": ("evaluate_session_operations", "check_entitlements", "assess_policy_questions"),
    "resource_management": ("compute_consumption", "get_quota", "assess_resource_questions"),
    "behavior_analytics": ("compute_sequence_statistics", "compare_behaviour_history", "assess_behaviour_questions"),
    "recovery_resilience": ("get_recovery_records", "validate_checkpoints", "assess_recovery_questions"),
    "audit_traceability": ("reconstruct_trace", "check_decision_linkage", "assess_audit_questions"),
    "agent_lifecycle_management": ("get_agent_registry", "detect_configuration_changes"),
    # LLM COPs: the tools their computational baseline reads, so it is
    # computed from the same sources the model can ask for.
    "workflow_orchestration": ("get_task_milestones", "analyse_execution_transitions"),
    "anomaly_detection": ("assess_anomaly_questions",),
    "context_memory": ("get_context_snapshots", "compare_instruction_versions",
                       "inspect_context_references", "verify_context_provenance"),
    "incident_response": ("check_exposure", "assess_incident_questions"),
    "classification_tagging": ("query_taxonomy", "inspect_event_metadata", "assess_classification_questions"),
    "risk_mitigation": ("read_risk_register", "calculate_risk_components", "assess_risk_questions"),
    "governance_intelligence": ("query_aggregate_report",),
    "workload_optimization": ("calculate_usage_rates", "inspect_repeated_operations"),
    "knowledge_policy_management": ("get_policy_versions", "compare_policy_documents",
                                    "inspect_rule_dependencies", "check_authorisation_records"),
    "governance_architecture": ("query_coverage_matrix", "validate_cop_relationships"),
}


def F(label, confidence, action, reason, rationale, claims=(), severity="NONE",
      status=SUBSTANTIVE, missing_deps=(), cop_specific=None, upstream_used=()):
    return {"label": label, "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "action": action, "action_reason": reason, "rationale": rationale,
            "claims": [c.to_dict() if isinstance(c, Claim) else c for c in claims],
            "severity": severity, "status": status,
            "missing_dependencies": list(missing_deps), "cop_specific": cop_specific or {},
            "upstream_used": list(upstream_used)}


def mclaim(e, statement=None):
    return Claim(statement or f"{e.metric_id} is {e.state} (value {e.value}, threshold {e.threshold})",
                 CLAIM_METRIC_STATE, metric_id=e.metric_id, asserted_state=e.state,
                 evidence_refs=list(e.evidence_refs[:10]) or [f"metric:{e.metric_id}"],
                 origin=ORIGIN_RULES)


def tclaim(tools, tool, path, value, statement):
    t = tools.get(tool) or {}
    return Claim(statement, CLAIM_TOOL_OBSERVATION, tool_call_id=t.get("tool_call_id"),
                 result_field=path, asserted_value=value,
                 evidence_refs=[f"tool:{t.get('tool_call_id')}"], origin=ORIGIN_RULES)


def uclaim(cop, f):
    return Claim(f"{cop} concluded {f['label']}", CLAIM_UPSTREAM, upstream_cop=cop,
                 upstream_label=f["label"], evidence_refs=[f"finding:{f.get('finding_id')}"],
                 origin=ORIGIN_RULES)


def result(tools, name):
    t = tools.get(name) or {}
    return t.get("result") or {}


def missing_of(tools, *names):
    out = []
    for n in names:
        r = result(tools, n)
        if "missing_dependency" in r:
            out.append(r["missing_dependency"])
    return out


def verified(up, cop, *labels):
    f = (up or {}).get(cop)
    return f if f and f.get("verified") and f.get("status") == SUBSTANTIVE \
        and (not labels or f["label"] in labels) else None


def independent_upstream(own_metrics, upstream):
    """Split verified upstream findings into independent and correlated.

    An upstream finding is correlated, and does not count as corroboration,
    when its lineage overlaps the metrics this COP already reads, or overlaps
    an upstream finding already counted. Same metric, same evidence: it is one
    signal seen twice, not two signals.
    """
    seen = set(own_metrics)
    indep, corr = [], []
    for cop, f in sorted((upstream or {}).items()):
        if not (f.get("verified") and f.get("status") == SUBSTANTIVE):
            continue
        lin = set(f.get("lineage") or [])
        if not lin:
            corr.append({"cop": cop, "why": "no lineage recorded; cannot show independence"})
        elif lin & seen:
            corr.append({"cop": cop, "why": f"shares {sorted(lin & seen)}"})
        else:
            indep.append({"cop": cop, "lineage": sorted(lin)})
            seen |= lin
    return {"independent": indep, "correlated": corr}


def _legacy_up(upstream):
    """The shape cops/interpret.py rules read upstream decisions in."""
    return {k: types.SimpleNamespace(cop=k, interpretation=f["label"],
                                     action=(f.get("proposed_action") or {}).get("action", "NONE"),
                                     confidence=f.get("confidence", 0.0),
                                     rationale=f.get("rationale", ""),
                                     decision_status=f.get("decision_status"))
            for k, f in (upstream or {}).items()
            if f.get("verified") and f.get("status") == SUBSTANTIVE}


def evidence_conf(upstream):
    rm = verified(upstream, "runtime_monitoring")
    return EVIDENCE_LEVEL.get(rm["label"], 0.9) if rm else 0.9


def _from_legacy(ctx, out, extra_claims=()):
    """Wrap a cops/interpret.py output as a Finding with checkable claims."""
    by = {e.metric_id: e for e in ctx["events"]}
    claims = []
    hot = [e for e in ctx["events"] if e.state in ATTENTION_STATES]
    for e in hot:
        claims.append(mclaim(e))
    for e in ctx["events"]:
        if e.state in ("UNKNOWN", "NOT_APPLICABLE") and e.metric_id == out.get("metric_id"):
            claims.append(mclaim(e))
    claims += list(extra_claims)
    status = SUBSTANTIVE if out["interpretation"] != INCONCLUSIVE else "INCONCLUSIVE"
    f = F(out["interpretation"], out["confidence"], out["action"], out.get("action_reason", ""),
          out.get("rationale", ""), claims, out.get("severity", "NONE"), status)
    if out.get("evidence_confidence") is not None:
        f["cop_specific"]["evidence_confidence"] = out["evidence_confidence"]
    if out.get("request_context"):
        f["cop_specific"]["requested_context"] = out["request_context"]
    return f


# ====================================================================== deterministic 8

def runtime_monitoring(ctx):
    out = _RI.runtime_monitoring(ctx["events"], {}, {"collector_breakdown": [
        {"collector": k, "rows": v} for k, v in
        result(ctx["tools"], "get_collector_breakdown").get("rows_by_collector", {}).items()]}, 0.9)
    exp = result(ctx["tools"], "check_collector_expectations")
    extra = []
    if exp.get("missing"):
        extra.append(tclaim(ctx["tools"], "check_collector_expectations", "missing", exp["missing"],
                            f"expected collectors missing: {', '.join(exp['missing'])}"))
    f = _from_legacy(ctx, out, extra)
    if not ctx["picture"]["judgeable"] and ctx["picture"]["unknown"] + ctx["picture"]["missing"]:
        # nothing about the telemetry could be judged: the evidence is insufficient,
        # never sufficient by default
        f.update(label="INSUFFICIENT", action="ALERT", severity="MEDIUM",
                 rationale=f["rationale"] + "; no telemetry measure could be judged")
        f["cop_specific"]["evidence_confidence"] = EVIDENCE_LEVEL["INSUFFICIENT"]
    f["cop_specific"]["collectors"] = exp
    for e in ctx["events"]:
        if e.metric_id in ctx["spec"].metric_keys and e.value is not None:
            f["claims"].append(Claim(f"{e.metric_id} is {e.value}", CLAIM_METRIC_VALUE, metric_id=e.metric_id,
                                     asserted_value=e.value, evidence_refs=[f"metric:{e.metric_id}"],
                                     origin=ORIGIN_RULES).to_dict())
    _apply_questions(ctx, f)
    return f


RM_RECIPIENTS = tuple(k for k, v in SPEC_CONSUMES.items() if "runtime_monitoring" in v)
RM_AT = {"DEGRADED": ("WATCH", "LOW", "findings stand but carry reduced confidence")}


def _apply_questions(ctx, f):
    """Runtime Monitoring's five answers bound its label (cop_package.aggregate_runtime_monitoring)."""
    got = ctx["tools"].get("assess_runtime_evidence") or {}
    res = got.get("result") or {}
    if not res.get("questions"):
        why = res.get("missing_dependency") or got.get("status")
        f["cop_specific"]["evidence_assessment"] = {
            "assessment_class": "ASSESSMENT_INCONCLUSIVE", "recipients": list(RM_RECIPIENTS),
            "qualifications": [f"question assessment unavailable: {why}"]}
        return
    agg = aggregate_runtime_monitoring(res["questions"], f["label"])
    agg.update(recipients=list(RM_RECIPIENTS), raw_store_supplied=res["raw_store_supplied"],
               capability_supplied=res["capability_supplied"], evaluated_at=res["evaluated_at"])
    f["cop_specific"]["evidence_assessment"] = agg
    for qid, t in agg["questions"].items():
        if t["label_ceiling"] != "SUFFICIENT":
            f["claims"].append(tclaim(ctx["tools"], "assess_runtime_evidence", f"questions.{qid}.answer.outcome",
                                      t["answer"], f"{qid} answered {t['answer']} ({t['contribution']}): "
                                      + "; ".join(t["because"])[:200]).to_dict())
    if agg["label"] != f["label"]:
        action, severity, reason = RM_AT[agg["label"]]
        moved = ", ".join(f"{q} {c}" for q, c in agg["classes"].items() if CEILING[c] == agg["label"])
        f.update(label=agg["label"], action=action, severity=severity, action_reason=reason,
                 rationale=f"evidence {agg['label'].lower()} on the questions ({moved}); "
                           f"threshold reading: {f['rationale']}")
        f["cop_specific"]["evidence_confidence"] = EVIDENCE_LEVEL[agg["label"]]
    elif agg["qualifications"]:
        f["rationale"] += "; not fully assessed: " + ", ".join(
            q for q, c in agg["classes"].items() if c == "ASSESSMENT_INCONCLUSIVE")


def interaction_discovery(ctx):
    """Interaction Discovery's five answers decide its label (cop_package.decide_interaction_discovery)."""
    known = result(ctx["tools"], "compare_known_routes")
    graph = result(ctx["tools"], "build_interaction_graph")
    res = result(ctx["tools"], "assess_interaction_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (ctx["tools"].get("assess_interaction_questions") or {}).get("status")
        return F(INCONCLUSIVE, 0.0, "WATCH", "the interaction questions could not be assessed",
                 f"question assessment unavailable: {why}", status=MISSING_DEPENDENCY,
                 cop_specific={"edges": graph.get("edges", []), "new_routes": known.get("new_routes", [])})
    d = decide_interaction_discovery(res["questions"], evidence_conf(ctx["upstream"]) / EVIDENCE_LEVEL["SUFFICIENT"])
    claims = [tclaim(ctx["tools"], "assess_interaction_questions", f"questions.{qid}.answer.outcome", t["answer"],
                     f"{qid} answered {t['answer']}: {t['explanation']}"[:240]) for qid, t in d["questions"].items()]
    claims += [mclaim(e) for e in ctx["events"] if e.state in ATTENTION_STATES]
    claims += [Claim(f"{e.metric_id} is {e.value}", CLAIM_METRIC_VALUE, metric_id=e.metric_id, asserted_value=e.value,
                     evidence_refs=[f"metric:{e.metric_id}"], origin=ORIGIN_RULES)
               for e in ctx["events"] if e.value is not None]
    id3 = d["questions"]["ID-3"]["basis"]
    for rid in id3.get("memory_records", []):
        claims.append(Claim(f"route record {rid} establishes {', '.join(id3['known_from_memory'])}",
                            CLAIM_MEMORY_PRECEDENT, record_id=rid, evidence_refs=[f"memory:{rid}"],
                            origin=ORIGIN_RULES))
    used = []
    rm = verified(ctx["upstream"], "runtime_monitoring")
    if rm:
        claims.append(uclaim("runtime_monitoring", rm))
        used.append({"cop": "runtime_monitoring", "label": rm["label"], "finding_id": rm.get("finding_id"),
                     "counted_as": "evidence confidence and ID-5 linkage"})
    why = "; ".join(f"{q} {d['questions'][q]['answer']}" for q in d["drivers"])
    rationale = f"{d['label']} from {why}" + (f"; also {', '.join(d['also_held'])}" if d["also_held"] else "")
    routes = sorted(set(d["questions"]["ID-2"]["basis"].get("edge_list", [])) | set(id3.get("new_routes", []))
                    | {f"mcp:{s}" for s in id3.get("new_mcp_servers", [])})
    return F(d["label"], d["confidence"], d["action"], d["action_reason"], rationale, claims, d["severity"],
             cop_specific={"edges": graph.get("edges", []), "new_routes": known.get("new_routes", []),
                           "questions": d["questions"], "drivers": d["drivers"], "also_held": d["also_held"],
                           "contrary": d["contrary"], "limitations": d["limitations"],
                           "recipients": d["recipients"], "memory_payload": {"routes": routes}},
             upstream_used=used)


def policy_enforcement(ctx):
    """Policy Enforcement's five answers decide its label (cop_package.decide_policy_enforcement). The approved
    policy version in force and the entitlement record are the only policy sources; with neither, no rule is
    evaluated and no violation is claimed."""
    t = ctx["tools"]
    ev = result(t, "evaluate_session_operations")
    miss = missing_of(t, "evaluate_session_operations", "check_entitlements")
    res = result(t, "assess_policy_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_policy_questions") or {}).get("status")
        return F(INCONCLUSIVE, 0.0, "WATCH", "the policy questions could not be assessed",
                 f"question assessment unavailable: {why}", status=MISSING_DEPENDENCY, missing_deps=miss)
    d = decide_policy_enforcement(res["questions"])
    q = d["questions"]
    claims = [tclaim(t, "assess_policy_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]) for qid, x in q.items()]
    observed = [] if "missing_dependency" in ev else ev["observed"]
    proposed = [] if "missing_dependency" in ev else ev["proposed"]
    denied = [o for o in observed if o["decision"] == "DENY" and not o["blocked_by_hook"]]
    blocked = [o for o in observed if o["decision"] == "DENY" and o["blocked_by_hook"]]
    need = [o for o in observed + proposed if o["decision"] == "REQUIRE_APPROVAL"]
    no_rule = [o for o in observed + proposed if o["decision"] == "NO_RULE"]
    prop_denied = [o for o in proposed if o["decision"] == "DENY"]
    unent = ((q["PE-2"]["basis"] or {}).get("violations") or [])
    unent = [x["resource"] for x in unent if x["decision"] == "NOT_ENTITLED"]
    ver = ev.get("policy_version")
    for rows, path, text in ((denied, "observed[decision=DENY]", "executed operations denied by"),
                             (prop_denied, "proposed[decision=DENY]", "proposed operations denied by"),
                             (need, "observed+proposed[decision=REQUIRE_APPROVAL]", "operations need approval under"),
                             (no_rule, "observed+proposed[decision=NO_RULE]", "operations match no rule in")):
        if rows:
            claims.append(tclaim(t, "evaluate_session_operations", path, len(rows), f"{len(rows)} {text} {ver}"
                                 + (": " + ", ".join(sorted({r for o in rows for r in o["rules"]}))
                                    if "DENY" in path else "")))
    if unent:
        claims.append(tclaim(t, "check_entitlements", "used_not_entitled", unent,
                             f"MCP servers used without entitlement: {', '.join(unent)}"))
    if d["label"] == "ALLOWED":
        allowed = [o for o in observed if o["decision"] == "ALLOW"]
        claims.append(tclaim(t, "evaluate_session_operations", "observed[decision=ALLOW]", len(allowed),
                             f"{len(allowed)} operations allowed by {ver}"
                             + (f"; {len(need)} needed approval and approval is recorded" if need else "")))
    if not ver:
        # no rule was evaluated: the conditions are recorded as what was observed, not as the decision's basis
        claims += [mclaim(e) for e in ctx["events"] if e.state in ATTENTION_STATES]
    used = []
    four = q["PE-4"]["basis"] or {}
    # an upstream finding is cited only where it changed an answer, so its lineage is not counted as this COP's
    five_reasons = (q["PE-5"]["basis"] or {}).get("interpretation_reasons", [])
    relied = {"runtime_monitoring": "runtime_monitoring" in [x["requirement"] for x in five_reasons],
              "interaction_discovery": (four.get("routes_from") or {}).get("source") == "interaction_discovery"
              and bool(four.get("routes"))}
    for cop, how in (("runtime_monitoring", "PE-5 telemetry sufficiency"),
                     ("interaction_discovery", "PE-4 new routes, evaluated against the policy")):
        f_ = verified(ctx["upstream"], cop)
        if f_ and relied[cop]:
            claims.append(uclaim(cop, f_))
            used.append({"cop": cop, "label": f_["label"], "finding_id": f_.get("finding_id"), "counted_as": how})
    for rid in [p["record_id"] for p in (q["PE-5"]["basis"] or {}).get("precedents", [])]:
        claims.append(Claim(f"enforcement record {rid} is a validated precedent for these operations",
                            CLAIM_MEMORY_PRECEDENT, record_id=rid, evidence_refs=[f"memory:{rid}"],
                            origin=ORIGIN_RULES))
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    rationale = f"{d['label']} from {why}" + (f"; also {', '.join(d['also_held'])}" if d["also_held"] else "")
    rationale += f"; evaluated against policy {ver}; {len(blocked)} attempts stopped by hooks" if ver else \
        "; no approved policy version in force, so no rule was evaluated"
    inconclusive = d["label"] == INCONCLUSIVE
    gaps = {r["requirement"] for x in q.values() for r in (x["basis"] or {}).get("interpretation_reasons", [])
            if r["code"] == "MISSING_REFERENCE"}
    status = SUBSTANTIVE if not inconclusive else MISSING_DEPENDENCY if gaps else "INCONCLUSIVE"
    pe5 = q["PE-5"]["basis"] or {}
    f = F(d["label"], d["confidence"], d["action"], d["action_reason"], rationale, claims, d["severity"], status,
          missing_deps=miss,
          cop_specific={"policy_version": ver, "denied": denied, "proposed": proposed, "approval_needed": need,
                        "blocked_by_hook": blocked, "questions": q, "drivers": d["drivers"],
                        "also_held": d["also_held"], "contrary": d["contrary"], "limitations": d["limitations"],
                        "recipients": d["recipients"],
                        "memory_payload": {"policy_version": ver, "decisions": [
                            {k: o.get(k) for k in ("action", "resource", "decision", "rules")}
                            for o in denied + prop_denied + need + no_rule][:20],
                            "routes": [x["route"] for x in (q["PE-4"]["basis"] or {}).get("routes", [])],
                            "precedents_read": [p["record_id"] for p in pe5.get("precedents", [])]}},
          upstream_used=used)
    if inconclusive and not gaps:
        f["reason_codes"] = ["MISSING_EVIDENCE"]
    return f


def resource_management(ctx):
    """Resource Management's five answers decide its label (cop_package.decide_resource_management). The quota
    record is the only entitlement source: consumption above the estate's normal is EXCESSIVE or INEFFICIENT,
    and UNAUTHORIZED only when the session's tokens exceed the quota's token_budget."""
    t = ctx["tools"]
    q_ = result(t, "get_quota")
    use = result(t, "compute_consumption")
    miss = missing_of(t, "get_quota")
    res = result(t, "assess_resource_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_resource_questions") or {}).get("status")
        return F(INCONCLUSIVE, 0.0, "WATCH", "the resource questions could not be assessed",
                 f"question assessment unavailable: {why}", status=MISSING_DEPENDENCY, missing_deps=miss)
    d = decide_resource_management(res["questions"], evidence_conf(ctx["upstream"]) / EVIDENCE_LEVEL["SUFFICIENT"])
    q = d["questions"]
    claims = [tclaim(t, "assess_resource_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]) for qid, x in q.items()]
    claims += [mclaim(e) for e in ctx["events"] if e.state in ATTENTION_STATES]
    if "missing_dependency" not in q_ and q_.get("token_budget") is not None and use.get("tokens") is not None:
        claims.append(tclaim(t, "compute_consumption", "tokens", use["tokens"],
                             f"session used {use['tokens']} tokens against a quota of {q_['token_budget']}"))
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    p = (q["RS-1"]["basis"] or {}).get("permitted") or {}
    rationale = f"{d['label']} from {why}" + (
        f"; {p['tokens']} tokens against token_budget {p['token_budget']}" if p.get("status") in
        ("WITHIN_QUOTA", "ABOVE_QUOTA") else "; permission not evaluated (no quota compared): a reading above the "
                                            "estate's normal is not consumption above an allowance")
    status = SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE"
    f = F(d["label"], d["confidence"], d["action"], d["action_reason"], rationale, claims, d["severity"], status,
          missing_deps=miss,
          cop_specific={"questions": q, "drivers": d["drivers"], "also_held": d["also_held"],
                        "contrary": d["contrary"], "limitations": d["limitations"], "recipients": d["recipients"],
                        "memory_payload": d["memory_payload"], "decision": d["decision"], "consumption": use,
                        "evidence_confidence": evidence_conf(ctx["upstream"])})
    rm = verified(ctx["upstream"], "runtime_monitoring")
    if rm:
        f["upstream_used"].append({"cop": "runtime_monitoring", "label": rm["label"], "finding_id": rm.get("finding_id"),
                                   "counted_as": "evidence quality, not corroboration"})
    _attribute_consumption(ctx, f, (q["RS-4"]["basis"] or {}).get("attribution"))
    adv = ctx.get("feedback", {}).get("workload_optimization")
    if adv:
        f["cop_specific"]["advisory"] = {
            "from": "workload_optimization", "finding_id": adv.get("finding_id"), "run_id": adv.get("run_id"),
            "label": adv.get("label"), "verified": bool(adv.get("verified")),
            "recommendation": (adv.get("proposed_action") or {}).get("action"), "applied": False,
            "note": "earlier-cycle recommendation, advisory only: no budget, quota, permission or "
                    "allocation is changed by it"}
    return f


CONSUMPTION_READINGS = ("EXCESSIVE", "INEFFICIENT", "UNAUTHORIZED")


def _attribute_consumption(ctx, f, at):
    """Where the session's consumption sits: RS-4's attribution (cop_package._rs4), from Interaction
    Discovery's verified graph and recorded links only.

    Consumption metrics are session totals. When the session has subagent lanes or MCP
    servers, a reading above bounds is attributable only through a verified, complete
    interaction graph whose delegation links are recorded; without one the reading stands but its
    confidence is discounted as for degraded evidence and the attribution is UNATTRIBUTED."""
    cs = f["cop_specific"]
    if at is None:
        # RS-4 was not answered (a blocking check failed): nothing is attributed
        cs["attribution"] = {"status": "NOT_ASSESSED", "reason": "RS-4 could not be answered"}
        return
    if at["status"] == "NOT_NEEDED":
        cs["attribution"] = dict(at)
        return
    if f["label"] not in CONSUMPTION_READINGS:
        cs["attribution"] = {"status": "NOT_NEEDED", "reason": f"{f['label']}: nothing above bounds to attribute"}
        return
    cs["attribution"] = dict(at)
    if at["status"] == "UNATTRIBUTED":
        f["confidence"] = round(f["confidence"] * EVIDENCE_LEVEL["DEGRADED"] / EVIDENCE_LEVEL["SUFFICIENT"], 3)
        f["rationale"] += (f"; consumption spans {at['subagent_lanes']} subagent lane(s) and {at['mcp_servers']} MCP "
                           f"server(s) and cannot be attributed: {at['reason']}")
        return
    idf = verified(ctx["upstream"], "interaction_discovery")
    f["claims"].append(uclaim("interaction_discovery", idf).to_dict())
    f["upstream_used"] = list(f.get("upstream_used") or []) + [
        {"cop": "interaction_discovery", "label": idf["label"], "finding_id": idf.get("finding_id"),
         "counted_as": "attribution of consumption"}]
    f["rationale"] += (f"; attributed through Interaction Discovery ({idf['label']}): "
                       f"{len(at['subagent_lanes'])} subagent lane(s), {len(at['parallel_lanes'])} running alongside "
                       f"another subagent, {sum(at['mcp_calls'].values())} of "
                       f"{sum(at['calls_by_actor'].values())} calls to MCP servers")


def _with_pattern_persistence(rule):
    """Behaviour Analytics' reading, then whether a deviation Anomaly Detection verified in
    an earlier cycle recurs in it. The earlier finding is evidence about persistence only:
    it changes no label, confidence or baseline."""
    @functools.wraps(rule)
    def wrapped(ctx):
        return _pattern_persistence(ctx, rule(ctx))
    return wrapped


def _pattern_persistence(ctx, f):
    prior = verified(ctx.get("feedback"), "anomaly_detection")
    cs = f["cop_specific"]
    if prior is None:
        cs["pattern_persistence"] = {"status": "NO_VERIFIED_HISTORY"}
        return f
    ref = {"finding_id": prior.get("finding_id"), "run_id": prior.get("run_id"),
           "decided_at": prior.get("decided_at"), "label": prior["label"]}
    same = bool(prior.get("session_id")) and prior.get("session_id") == ctx["case"].session_id
    if prior["label"] != "ANOMALOUS":
        st = "NO_EARLIER_DEVIATION"
    elif same:
        # D-BA9: Anomaly Detection read this same session's metrics; reading them again is not a recurrence
        st = "SAME_EVENTS"
    elif f["label"] in ("LOOPING", "POOR_RECOVERY", "DRIFTING", "OVER_EXPLORING"):
        st = "RECURRING"
        f["rationale"] += (f"; Anomaly Detection verified a deviation in an earlier cycle "
                           f"({prior.get('run_id')}) and the pattern is present again")
    elif f["label"] == "STABLE":
        st = "NOT_RECURRING"
        f["rationale"] += f"; the deviation verified in an earlier cycle ({prior.get('run_id')}) is not present now"
    else:
        st = "UNDETERMINED"
    ref["session_id"] = prior.get("session_id")
    cs["pattern_persistence"] = {"status": st, "earlier_finding": ref,
                                 "baseline_updated": False,
                                 # D-BA9: context for persistence only, never a separate occurrence
                                 "counted_as_occurrence": False}
    if isinstance(cs.get("behaviour_status"), dict):
        cs["behaviour_status"]["anomaly_detection_feedback"] = cs["pattern_persistence"]
    return f


@_with_pattern_persistence
def behavior_analytics(ctx):
    # the legacy rule's metric inputs (specs.metric_keys); named here so activation._rule_reads still finds them
    f = _behavior_analytics_legacy(ctx, ("insights.repetition", "insights.retry", "insights.failed",
                                         "insights.stalled", "oversight.fast_approvals"))
    _ba_questions(ctx, f)
    return f


def _ba_questions(ctx, f):
    """Behavior Analytics' four answers (cop_package.decide_behavior_analytics). BA-1 decides the pattern label over
    distinct, undisputed events, BA-4 DRIFTING when there is none, BA-2 and BA-3 the scope it is published with. The
    legacy reading stays the computational baseline. Decided without a model
    (packages/behavior_analytics/INTERPRETATION.md)."""
    t = ctx["tools"]
    res = result(t, "assess_behaviour_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_behaviour_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    scale = min(1.0, evidence_conf(ctx["upstream"]) / 0.9)
    d = decide_behavior_analytics(res["questions"], scale)
    q = d["questions"]
    legacy = {"label": f["label"], "confidence": f["confidence"], "action": f.get("action"),
              "severity": f.get("severity"), "status": f.get("status")}
    lims = list(d["limitations"])
    if (legacy["label"], legacy["action"]) != (d["label"], d["action"]):
        from .decision import limitation, LIMIT_NOT_APPROVED
        lims.append(limitation(LIMIT_NOT_APPROVED, "BA-1", "legacy_reading", "PREVIOUS_READING_DIFFERS",
                               f"the legacy rule read {legacy['label']} {legacy['severity']} {legacy['action']}; the "
                               f"package's answers read {d['label']} {d['severity']} {d['action']} (DECISIONS.md)"))
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=lims,
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"], decision=d["decision"],
                             behaviour_status=d["behaviour_status"], legacy_reading=legacy)
    claims = [tclaim(t, "assess_behaviour_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    why = "; ".join(f"{x} {q[x]['answer']}" for x in q)
    f.update(label=d["label"], confidence=d["confidence"], action=d["action"], severity=d["severity"],
             action_reason=d["action_reason"], claims=claims + f["claims"],
             status=SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE",
             rationale=(f"{d['label']} from {why}; scope: {d['behaviour_status']['scope']}; nothing enforced by this "
                        f"COP; legacy reading: {legacy['label']} ({f['rationale']})")[:600])
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def _behavior_analytics_legacy(ctx, keys):
    """The legacy behaviour reading. It stays the computational baseline (packages/behavior_analytics/DECISIONS.md)."""
    m = {e.metric_id: e for e in ctx["events"]}
    up = ctx["upstream"]
    seq = result(ctx["tools"], "compute_sequence_statistics")
    rep, retry, failed = m.get("insights.repetition"), m.get("insights.retry"), m.get("insights.failed")
    stalled = m.get("insights.stalled")
    judged = [e for e in (rep, retry, failed, stalled, m.get("oversight.fast_approvals"))
              if e is not None and e.state in ("NORMAL",) + ATTENTION_STATES]
    claims, used = [], []
    loss = verified(up, "context_memory", "CONTEXT_LOSS", "CONTRADICTORY")
    drift = verified(up, "workflow_orchestration", "DRIFTING", "UNEXPECTED_BRANCH")
    loops = [e for e in (rep, retry) if e is not None and e.state in HOT]
    scale = min(1.0, evidence_conf(up) / 0.9)
    if loss or drift:
        src = loss or drift
        cop = "context_memory" if loss else "workflow_orchestration"
        used.append({"cop": cop, "label": src["label"], "finding_id": src.get("finding_id"),
                     "counted_as": "behavioural input (authorised read of integrity finding)"})
        claims.append(uclaim(cop, src))
    if retry is not None and retry.state in HOT and failed is not None and failed.state in HOT:
        claims += [mclaim(retry), mclaim(failed)]
        if seq.get("failed_calls") is not None:
            claims.append(tclaim(ctx["tools"], "compute_sequence_statistics", "failed_calls",
                                 seq["failed_calls"], f"{seq['failed_calls']} failed calls in the sequence"))
        return F("POOR_RECOVERY", 0.8 * scale, "WATCH",
                 "failed commands reissued unchanged and failing again: the agent is not recovering",
                 f"retry={retry.value}, failed={failed.value}", claims, "MEDIUM", upstream_used=used)
    if loops:
        claims += [mclaim(e) for e in loops]
        return F("LOOPING", 0.75 * scale, "WATCH", "the same calls repeat within turns",
                 "; ".join(f"{e.metric_id}={e.value}" for e in loops), claims,
                 max((SEV[e.state] for e in loops), key=RANK.get), upstream_used=used)
    if loss or drift:
        return F("DRIFTING", 0.6 * scale, "WATCH",
                 "an upstream integrity finding shows the agent working from altered context",
                 f"{(loss or drift)['label']} from {'context_memory' if loss else 'workflow_orchestration'}",
                 claims, "LOW", upstream_used=used)
    if not judged:
        return F(INCONCLUSIVE, 0.0, "WATCH", "no behavioural measure could be judged",
                 "unknown or not applicable: " + ", ".join(
                     f"{e.metric_id}={e.state}" for e in (rep, retry, failed, stalled) if e is not None),
                 [mclaim(e) for e in (rep, retry, failed) if e is not None], status="INCONCLUSIVE")
    return F("STABLE", 0.75 * scale, "NONE", "", f"{len(judged)} behaviour measures within bounds",
             [mclaim(e) for e in judged], upstream_used=used,
             cop_specific={"unreachable": {"OVER_EXPLORING": "needs exploration_rate, not computed"}})


def recovery_resilience(ctx):
    # the legacy rule's metric inputs (specs.metric_keys); named here so activation._rule_reads still finds them
    f = _recovery_resilience_legacy(ctx, ("insights.reverted", "insights.retry", "insights.failed"))
    _rr_questions(ctx, f)
    return f


def _rr_questions(ctx, f):
    """Recovery & Resilience's four answers (cop_package.decide_recovery_resilience). RR-2 decides the label from
    restoration verified by independent post-restore checks, RR-3 from recurrence after it, RR-4 the action. The
    legacy reading stays the computational baseline. Every reading is decided without a model
    (packages/recovery_resilience/INTERPRETATION.md)."""
    t = ctx["tools"]
    res = result(t, "assess_recovery_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_recovery_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    # P-RR1: Incident Response's earlier-cycle finding, read after the answers and never by them (context only)
    from .cop_package import rr_earlier_incident
    earlier = rr_earlier_incident((ctx.get("feedback") or {}).get("incident_response"), ctx["case"].session_id,
                                  res["questions"])
    d = decide_recovery_resilience(res["questions"], earlier)
    q = d["questions"]
    legacy = {"label": f["label"], "confidence": f["confidence"], "action": f.get("action"),
              "severity": f.get("severity"), "status": f.get("status")}
    lims = list(d["limitations"])
    if (legacy["label"], legacy["action"]) != (d["label"], d["action"]):
        from .decision import limitation, LIMIT_NOT_APPROVED
        lims.append(limitation(LIMIT_NOT_APPROVED, "RR-2", "legacy_reading", "PREVIOUS_READING_DIFFERS",
                               f"the legacy rule read {legacy['label']} {legacy['severity']} {legacy['action']}; the "
                               f"package's answers read {d['label']} {d['severity']} {d['action']} (DECISIONS.md)"))
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=lims,
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"], decision=d["decision"],
                             recovery_status=d["recovery_status"], category=d["category"], legacy_reading=legacy,
                             earlier_incident=earlier,
                             # D-RR12: RECOVERED is the quiet label, but after a disruption it is a recovery outcome
                             # worth remembering (graph.validate_memory_update's remember_quiet)
                             remember_quiet=d["label"] == "RECOVERED")
    claims = [tclaim(t, "assess_recovery_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    upd = dict(label=d["label"], confidence=d["confidence"], action=d["action"], severity=d["severity"],
               action_reason=d["action_reason"], claims=claims + f["claims"],
               rationale=(f"{d['label']} from {why}; nothing rolled back, restarted, blocked or restored by this COP; "
                          f"legacy reading: {legacy['label']} ({f['rationale']})")[:600])
    if legacy["status"] != MISSING_DEPENDENCY or d["label"] != INCONCLUSIVE:
        # no record store: the legacy MISSING_DEPENDENCY reading and its missing dependency stand, with the answers
        upd["status"] = SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE"
    f.update(**upd)
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def _recovery_resilience_legacy(ctx, keys):
    """The legacy recovery reading. It stays the computational baseline (packages/recovery_resilience/DECISIONS.md)."""
    m = {e.metric_id: e for e in ctx["events"]}
    recs = result(ctx["tools"], "get_recovery_records")
    cps = result(ctx["tools"], "validate_checkpoints")
    miss = missing_of(ctx["tools"], "get_recovery_records")
    rows = ctx["case"].session.rows if ctx["case"].session else []
    from .tools import IRREVERSIBLE
    irrev = [r for r in rows if IRREVERSIBLE.search(str(r.get("command") or ""))]
    hot = [e for e in (m.get(k) for k in keys) if e is not None and e.state in HOT]
    if irrev:
        return F("IRREVERSIBLE", 0.85, "ALERT", "an irreversible operation ran; restoration cannot undo it",
                 f"{len(irrev)} irreversible commands",
                 [Claim(f"irreversible command {r.get('command')!r}", CLAIM_ABSENCE,
                        evidence_refs=[f"observable:{r['observable_id']}"]) for r in irrev[:3]], "HIGH")
    if miss:
        return F(INCONCLUSIVE, 0.0, "WATCH" if hot else "NONE",
                 "no recovery record exists, so whether the system returned to a viable state "
                 "cannot be told", "failure signals: " + (", ".join(f"{e.metric_id}={e.value}" for e in hot) or "none"),
                 [mclaim(e) for e in hot], status=MISSING_DEPENDENCY, missing_deps=miss)
    out = cps.get("checkpoints", [])
    if not out:
        return F(INCONCLUSIVE, 0.0, "NONE", "no recovery in this case", "recovery records empty",
                 status="INCONCLUSIVE")
    bad_hash = [c for c in out if c["hash_matches"] is False]
    full = [c for c in out if c["checks_total"] and c["checks_passed"] == c["checks_total"]]
    none_ = [c for c in out if c["checks_total"] and c["checks_passed"] == 0]
    claims = [tclaim(ctx["tools"], "validate_checkpoints", f"checkpoints[{i}].checks_passed",
                     c["checks_passed"], f"{c['checkpoint_id']}: {c['checks_passed']}/{c['checks_total']} "
                     f"post-restore checks passed") for i, c in enumerate(out)]
    if bad_hash or len(none_) == len(out):
        return F("FAILED_RECOVERY", 0.85, "ALERT", "restoration did not return a viable state",
                 f"{len(bad_hash)} checkpoints failed integrity; {len(none_)} restores passed no checks",
                 claims, "HIGH")
    if len(full) == len(out):
        return F("RECOVERED", 0.85, "NONE", "", f"{len(full)} restores passed every check", claims)
    return F("PARTIAL_RECOVERY", 0.8, "WATCH", "some post-restore checks failed",
             f"{len(full)}/{len(out)} restores fully passed", claims, "MEDIUM")


def audit_traceability(ctx):
    """Audit & Traceability's five answers decide its label (cop_package.decide_audit_traceability)."""
    t = ctx["tools"]
    link = result(t, "check_decision_linkage")
    tr = result(t, "reconstruct_trace")
    res = result(t, "assess_audit_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_audit_questions") or {}).get("status")
        return F(INCONCLUSIVE, 0.0, "WATCH", "the audit questions could not be assessed",
                 f"question assessment unavailable: {why}", status=MISSING_DEPENDENCY,
                 cop_specific={"trace": tr, "decision_linkage": link.get("decisions", [])})
    d = decide_audit_traceability(res["questions"], evidence_conf(ctx["upstream"]) / EVIDENCE_LEVEL["SUFFICIENT"])
    q = d["questions"]
    claims = [tclaim(t, "assess_audit_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]) for qid, x in q.items()]
    if q["AT-1"]["answer"] == "ACTOR_NOT_ESTABLISHED":
        claims.append(tclaim(t, "reconstruct_trace", "effects_without_actor", tr.get("effects_without_actor"),
                             q["AT-1"]["explanation"][:240]))
    if tr.get("unattributed_effects"):
        claims.append(tclaim(t, "reconstruct_trace", "unattributed_effects", tr["unattributed_effects"],
                             f"{len(tr['unattributed_effects'])} disk changes are attributed to no step"))
    if tr.get("lanes_without_parent"):
        claims.append(tclaim(t, "reconstruct_trace", "lanes_without_parent", tr["lanes_without_parent"],
                             f"{len(tr['lanes_without_parent'])} sub-agent lanes have no parent step"))
    if tr.get("steps_without_turn"):
        claims.append(tclaim(t, "reconstruct_trace", "steps_without_turn", tr["steps_without_turn"],
                             f"{tr['steps_without_turn']} steps have no turn id; their order still reconstructs "
                             f"the chain"))
    unres = q["AT-4"]["basis"].get("unresolvable") if q["AT-4"]["answer"] != INCONCLUSIVE else None
    if unres:
        claims.append(tclaim(t, "check_decision_linkage", "decisions[unresolvable]", len(unres),
                             f"{len(unres)} upstream decisions cite evidence that does not resolve to a row in "
                             f"this case"))
    claims += [mclaim(e) for e in ctx["events"] if e.state in ATTENTION_STATES]
    claims += [Claim(f"{e.metric_id} is {e.value}", CLAIM_METRIC_VALUE, metric_id=e.metric_id, asserted_value=e.value,
                     evidence_refs=[f"metric:{e.metric_id}"], origin=ORIGIN_RULES)
               for e in ctx["events"] if e.value is not None]
    five = q["AT-5"]["basis"]
    for rid in five.get("memory_records", []):
        claims.append(Claim(f"trace gap record {rid} records {', '.join(five['recurring'])} before",
                            CLAIM_MEMORY_PRECEDENT, record_id=rid, evidence_refs=[f"memory:{rid}"],
                            origin=ORIGIN_RULES))
    used = []
    for cop, how in (("runtime_monitoring", "evidence confidence, AT-2 RM-3 linkage and AT-3 reliability"),
                     ("interaction_discovery", "AT-2 ID-2 reconstruction and AT-5 ID-5 identities"),
                     ("policy_enforcement", "AT-4 decision evidence linkage")):
        f_ = verified(ctx["upstream"], cop)
        if f_:
            claims.append(uclaim(cop, f_))
            used.append({"cop": cop, "label": f_["label"], "finding_id": f_.get("finding_id"), "counted_as": how})
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    rationale = f"{d['label']} from {why}" + (f"; also {', '.join(d['also_held'])}" if d["also_held"] else "")
    f = F(d["label"], d["confidence"], d["action"], d["action_reason"], rationale, claims, d["severity"],
          "INCONCLUSIVE" if d["label"] == INCONCLUSIVE else SUBSTANTIVE,
          cop_specific={"trace": tr, "decision_linkage": link.get("decisions", []), "questions": q,
                           "drivers": d["drivers"], "also_held": d["also_held"], "contrary": d["contrary"],
                        "recipients": d["recipients"], "memory_payload": {"gaps": d["gap_codes"]}},
          upstream_used=used)
    if d["label"] == INCONCLUSIVE:
        f["reason_codes"] = ["MISSING_EVIDENCE"]
    return f


def agent_lifecycle_management(ctx):
    reg = result(ctx["tools"], "get_agent_registry")
    ch = result(ctx["tools"], "detect_configuration_changes")
    m = {e.metric_id: e for e in ctx["events"]}
    up = ctx["upstream"]
    miss = missing_of(ctx["tools"], "get_agent_registry")
    new_vals = ch.get("new_values", {})
    claims, used = [], []
    route = verified(up, "interaction_discovery", "NEW_ROUTE")
    if route:
        used.append({"cop": "interaction_discovery", "label": "NEW_ROUTE",
                     "finding_id": route.get("finding_id"), "counted_as": "connectivity change input"})
        claims.append(uclaim("interaction_discovery", route))
    fs = m.get("access.first_seen_mcp")
    if fs is not None and fs.state in ATTENTION_STATES:
        claims.append(mclaim(fs))
    if new_vals:
        claims.append(tclaim(ctx["tools"], "detect_configuration_changes", "new_values", new_vals,
                             "configuration values not seen before: " +
                             "; ".join(f"{k}={v}" for k, v in new_vals.items())))
    if miss:
        if not ch.get("history_sessions"):
            return F(INCONCLUSIVE, 0.0, "WATCH", "no registry and no history for this agent",
                     "cannot tell new from unregistered", claims, status=MISSING_DEPENDENCY,
                     missing_deps=miss, upstream_used=used)
        if new_vals:
            return F(INCONCLUSIVE, 0.3, "WATCH",
                     "configuration changed; without a registry an approved change cannot be told "
                     "from drift", "", claims, "LOW", status=MISSING_DEPENDENCY, missing_deps=miss,
                     upstream_used=used, cop_specific={"new_values": new_vals})
        return F("UNCHANGED", 0.6, "NONE", "", "no change against this agent's history "
                 "(no registry to compare against)", claims, missing_deps=miss, upstream_used=used)
    entry = reg.get("entry")
    if not reg.get("registered"):
        return F("NEW", 0.85, "ALERT", "an agent with no registry entry is operating", "",
                 claims + [tclaim(ctx["tools"], "get_agent_registry", "registered", False,
                                  "no registry entry for this agent")], "MEDIUM", upstream_used=used)
    current = ch.get("current", {})
    unapproved = {}
    approved = {(c["change"].get("field"), c["change"].get("value")) for c in reg.get("approved_changes", [])}
    allowed_map = {"tool_version": entry.get("approved_versions", []), "model": entry.get("approved_models", []),
                   "mcp_servers": entry.get("approved_mcp_servers", [])}
    for k, allowed in allowed_map.items():
        for v in current.get(k, []):
            if v not in allowed and (k, v) not in approved:
                unapproved.setdefault(k, []).append(v)
    changed = {k: v for k, v in new_vals.items() if k in allowed_map}
    if unapproved:
        claims.append(tclaim(ctx["tools"], "detect_configuration_changes", "current", current,
                             "configuration outside the registry: " +
                             "; ".join(f"{k}={v}" for k, v in unapproved.items())))
        return F("DRIFTED", 0.85, "ALERT", "configuration outside what the registry approves",
                 "", claims, "MEDIUM", upstream_used=used, cop_specific={"unapproved": unapproved})
    if changed:
        return F("CHANGED", 0.8, "WATCH", "an approved configuration change took effect", "",
                 claims, "LOW", upstream_used=used)
    return F("UNCHANGED", 0.85, "NONE", "", "configuration matches the registry", claims,
             upstream_used=used)


# ====================================================================== LLM COP baselines

def anomaly_detection(ctx):
    up = ctx["upstream"]
    rm = verified(up, "runtime_monitoring")
    out = _RI.anomaly_detection(ctx["events"], _legacy_up(up), {}, evidence_conf(up))
    used = []
    if out.get("request_context"):
        # the baseline does not take the second pass; the investigation loop does
        out = {**out, "action": "WATCH"}
    if rm:
        used.append({"cop": "runtime_monitoring", "label": rm["label"], "finding_id": rm.get("finding_id"),
                     "counted_as": "evidence quality, not corroboration"})
        if rm["label"] == "INSUFFICIENT" and out["interpretation"] == "NORMAL":
            # Runtime Monitoring says the telemetry is not there. Deviation metrics
            # being quiet on missing telemetry is not normality.
            out = {**out, "interpretation": "INDETERMINATE", "action": "WATCH", "confidence": 0.2,
                   "action_reason": "telemetry insufficient per Runtime Monitoring; quiet deviation "
                                    "metrics on missing evidence are not evidence of normal behaviour"}
    ba = verified(up, "behavior_analytics", "LOOPING", "POOR_RECOVERY")
    if ba:
        ind = independent_upstream(ctx["spec"].metric_keys, {"behavior_analytics": ba})
        used.append({"cop": "behavior_analytics", "label": ba["label"], "finding_id": ba.get("finding_id"),
                     "counted_as": "independent" if ind["independent"] else
                     "correlated, not counted: " + ind["correlated"][0]["why"]})
    f = _from_legacy(ctx, out, [uclaim("runtime_monitoring", rm)] if rm else [])
    f["upstream_used"] = used
    _ad_questions(ctx, f)
    return f


def _ad_questions(ctx, f):
    """Anomaly Detection's five answers (cop_package.decide_anomaly_detection). With no deviation, or with
    evidence or a baseline too short to judge, AD-5 decides the label and the finding says it needs no model.
    With a deviation the threshold reading above stays the computational baseline and the model interprets."""
    t = ctx["tools"]
    res = result(t, "assess_anomaly_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_anomaly_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    d = decide_anomaly_detection(res["questions"], evidence_conf(ctx["upstream"]) / EVIDENCE_LEVEL["SUFFICIENT"])
    q = d["questions"]
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=d["limitations"],
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"])
    if not d["decided"]:
        return
    claims = [tclaim(t, "assess_anomaly_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    f.update(label=d["label"], confidence=d["confidence"], action=d["action"], severity=d["severity"],
             action_reason=d["action_reason"], status=SUBSTANTIVE, claims=claims + f["claims"],
             rationale=f"{d['label']} from {why}; threshold reading: {f['rationale']}"[:600])
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def incident_response(ctx):
    up = ctx["upstream"]
    out = _RI.incident_response(ctx["events"], _legacy_up(up), {}, evidence_conf(up))
    used, claims = [], []
    for cop in ("anomaly_detection", "policy_enforcement", "runtime_monitoring", "recovery_resilience",
                "workflow_orchestration", "interaction_discovery", "classification_tagging",
                "resource_management"):
        f = verified(up, cop)
        if f:
            used.append({"cop": cop, "label": f["label"], "finding_id": f.get("finding_id"),
                         "counted_as": "input"})
            if f["label"] not in ("NORMAL", "SUFFICIENT", "ALLOWED", "CONTAINED", "RECOVERED",
                                  BY_KEY[cop].interpretations[0]):
                claims.append(uclaim(cop, f))
    rr = verified(up, "recovery_resilience", "FAILED_RECOVERY", "IRREVERSIBLE")
    if rr and out["interpretation"] != "INCIDENT":
        out = {**out, "interpretation": "INCIDENT", "severity": "HIGH", "action": "ESCALATE",
               "action_reason": "recovery from a failure did not succeed"}
    pe = verified(up, "policy_enforcement", "VIOLATION")
    if pe and (pe.get("proposed_action") or {}).get("action") == "BLOCK":
        out = {**out, "action": "REQUEST_APPROVAL",
               "action_reason": "a denied operation is pending; blocking it needs a person's approval"}
    exp = result(ctx["tools"], "check_exposure")
    if exp.get("outbound_after_secret"):
        claims.append(tclaim(ctx["tools"], "check_exposure", "outbound_after_secret",
                             exp["outbound_after_secret"], "outbound calls after a secret was read"))
    ind = independent_upstream(ctx["spec"].metric_keys, up)
    f = _from_legacy(ctx, out, claims)
    f["upstream_used"] = used
    f["cop_specific"]["independence"] = ind
    risk = verified(ctx.get("feedback"), "risk_mitigation")
    f["cop_specific"]["earlier_risk_assessment"] = {
        "finding_id": risk.get("finding_id"), "run_id": risk.get("run_id"),
        "decided_at": risk.get("decided_at"), "label": risk["label"]} if risk else None
    _ir_questions(ctx, f)
    return f


def _ir_questions(ctx, f):
    """Incident Response's five answers (cop_package.decide_incident_response). IR-1 decides the label from incident
    grounds counted once; IR-5 the response. The legacy reading above stays the computational baseline. Decided
    without a model unless IR-4 justifies an intervention: then the model scopes the incident and drafts the
    containment plan, and interpreted_incident_response reads its conclusion back (INTERPRETATION.md)."""
    t = ctx["tools"]
    res = result(t, "assess_incident_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_incident_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    d = decide_incident_response(res["questions"])
    q = d["questions"]
    legacy = {"label": f["label"], "confidence": f["confidence"], "action": f.get("action"),
              "severity": f.get("severity")}
    earlier = f["cop_specific"].get("earlier_risk_assessment")
    previous = {"risk_mitigation": earlier, "use": "context only, never counted as a current-cycle ground"} \
        if earlier else None
    lims = list(d["limitations"])
    if (legacy["label"], legacy["action"]) != (d["label"], d["action"]):
        from .decision import limitation, LIMIT_NOT_APPROVED
        lims.append(limitation(LIMIT_NOT_APPROVED, "IR-1", "legacy_reading", "PREVIOUS_READING_DIFFERS",
                               f"the legacy rule read {legacy['label']} {legacy['severity']} {legacy['action']}; the "
                               f"package's answers read {d['label']} {d['severity']} {d['action']} (DECISIONS.md)"))
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=lims,
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"], decision=d["decision"],
                             incident_status=d["incident_status"], category=d["category"], legacy_reading=legacy,
                             previous_cycle=previous)
    claims = [tclaim(t, "assess_incident_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    status = SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE"
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    upd = dict(label=d["label"], confidence=d["confidence"], action=d["action"], severity=d["severity"],
               action_reason=d["action_reason"])
    if not d["decided"]:
        # the model scopes the intervention; the package's label, severity and response are the baseline it is held to
        f.update(**upd, claims=claims + f["claims"])
        return
    # the legacy claims (metrics, consumed findings, outbound calls) stay: they are checkable statements; IR-1's
    # basis says which consumed findings are grounds and which are context
    f.update(**upd, status=status, claims=claims + f["claims"],
             rationale=(f"{d['label']} from {why}; correlated findings counted once; nothing paused, blocked or "
                        f"contained; legacy reading: {legacy['label']} ({f['rationale']})")[:600])
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def classification_tagging(ctx):
    out = _RI.classification_tagging(ctx["events"], {}, {}, evidence_conf(ctx["upstream"]))
    tax = result(ctx["tools"], "query_taxonomy")
    f = _from_legacy(ctx, out)
    if "missing_dependency" in tax:
        f["missing_dependencies"] = [tax["missing_dependency"]]
    else:
        f["cop_specific"]["taxonomy_version"] = tax.get("version")
    _ct_questions(ctx, f)
    return f


def _ct_questions(ctx, f):
    """Classification & Tagging's five answers (cop_package.decide_classification_tagging). One candidate family,
    no family, or too little evidence: CT-5 decides the label and the finding says it needs no model. Two or more
    candidate families: the legacy precedence reading above stays the computational baseline and the model
    interprets the event against the approved taxonomy (INTERPRETATION.md)."""
    t = ctx["tools"]
    res = result(t, "assess_classification_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_classification_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    d = decide_classification_tagging(res["questions"], evidence_conf(ctx["upstream"]) / EVIDENCE_LEVEL["SUFFICIENT"])
    q = d["questions"]
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=d["limitations"],
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"], decision=d["decision"])
    if not d["decided"]:
        return
    claims = [tclaim(t, "assess_classification_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    status = SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE"
    f.update(label=d["label"], confidence=d["confidence"], action=d["action"], severity=d["severity"],
             action_reason=d["action_reason"], status=status, claims=claims + f["claims"],
             rationale=(f"{d['label']} from {why}; kind only, severity and response belong elsewhere; "
                        f"threshold reading: {f['rationale']}")[:600])
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def context_memory(ctx):
    t = ctx["tools"]
    miss = missing_of(t, "get_context_snapshots")
    if miss:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no context snapshots exist for this session",
                 status=MISSING_DEPENDENCY, missing_deps=miss)
    cmp_ = result(t, "compare_instruction_versions").get("comparisons", [])
    refs = result(t, "inspect_context_references")
    prov = result(t, "verify_context_provenance").get("snapshots", [])
    lost_breach = [b for b in refs.get("breaches", []) if not b["constraint_in_active_context"]]
    changed = [c for c in cmp_ if c["changed"]]
    bad_prov = [p for p in prov if p["hash_matches"] is False]
    if lost_breach:
        return F("CONTEXT_LOSS", 0.8, "WATCH",
                 "an action breached a constraint the active context no longer carried",
                 f"{len(lost_breach)} breaches of dropped constraints",
                 [tclaim(t, "inspect_context_references", "breaches[constraint_in_active_context=false]",
                         len(lost_breach), f"constraint {b['constraint']} breached at {b['ref']} after it "
                         f"left the context") for b in lost_breach[:3]], "MEDIUM",
                 cop_specific={"lost_constraints": sorted({b["constraint"] for b in lost_breach})})
    if changed:
        return F("CONTRADICTORY", 0.7, "WATCH", "the same constraint has different text across snapshots",
                 "", [tclaim(t, "compare_instruction_versions", "comparisons[changed]", len(changed),
                             f"{len(changed)} snapshot transitions changed a constraint")], "LOW")
    if bad_prov:
        return F("STALE", 0.6, "WATCH", "a snapshot's content does not match its recorded provenance",
                 "", [tclaim(t, "verify_context_provenance", "snapshots[hash_matches=false]",
                             len(bad_prov), f"{len(bad_prov)} snapshots fail provenance")], "LOW")
    return F("CONTEXT_OK", 0.7, "NONE", "", f"{len(prov)} snapshots consistent, no dropped-constraint breach",
             [tclaim(t, "inspect_context_references", "breaches", len(refs.get("breaches", [])),
                     "no breach of a dropped constraint")])


def workflow_orchestration(ctx):
    t = ctx["tools"]
    m = {e.metric_id: e for e in ctx["events"]}
    st = m.get("insights.stalled")
    if st is not None and st.state in HOT:
        return F("STALLED", 0.8, "ALERT", "execution left hanging", "", [mclaim(st)],
                 SEV[st.state])
    ms = result(t, "get_task_milestones")
    if "missing_dependency" in ms:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no task plan: progress toward the intended state "
                 "cannot be measured", status=MISSING_DEPENDENCY, missing_deps=[ms["missing_dependency"]])
    claims = [tclaim(t, "get_task_milestones", "reached", ms["reached"],
                     f"{ms['reached']} of {ms['total']} milestones reached")]
    if ms["total"] and ms["reached"] == ms["total"]:
        return F("NORMAL", 0.75, "NONE", "", "every milestone reached", claims)
    # Unreached milestones: stalled, drifting or simply unfinished. The rules
    # cannot tell which; that is what the investigation is for.
    return F(INCONCLUSIVE, 0.3, "WATCH", "milestones unreached; drift and incompleteness look the "
             "same from counts alone", "", claims, "LOW", status="INCONCLUSIVE")


def risk_mitigation(ctx):
    f = _risk_mitigation_legacy(ctx)
    _rk_questions(ctx, f)
    return f


def _risk_mitigation_legacy(ctx):
    """The legacy register reading. It stays the computational baseline (packages/risk_mitigation/DECISIONS.md)."""
    t = ctx["tools"]
    reg = result(t, "read_risk_register")
    comp = result(t, "calculate_risk_components")
    if "missing_dependency" in reg:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no risk register: impact and thresholds are undefined",
                 status=MISSING_DEPENDENCY, missing_deps=[reg["missing_dependency"]],
                 cop_specific={"components": comp})
    th = reg.get("thresholds") or {}
    if "EMERGING" not in th or "MATERIAL" not in th:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "risk register defines no EMERGING/MATERIAL thresholds",
                 status=MISSING_DEPENDENCY, missing_deps=[{"dependency": "risk_register.thresholds",
                                                          "required_for": "risk label",
                                                          "how_to_supply": "add thresholds to the register"}])
    impact = max([r.get("impact", 0) for r in reg.get("risks", [])
                  if r.get("min_sensitivity_tier", 0) <= comp.get("max_sensitivity_tier", 0)] or [0])
    signals = len(comp.get("independent_verified_signals", []))
    irrev = 1 if comp.get("irreversible_operations") else 0
    score = impact * (signals + irrev)
    label = "MATERIAL" if score >= th["MATERIAL"] else "EMERGING" if score >= th["EMERGING"] else "LOW"
    action = {"MATERIAL": "ESCALATE", "EMERGING": "WATCH", "LOW": "NONE"}[label]
    claims = [tclaim(t, "calculate_risk_components", "independent_verified_signals",
                     comp.get("independent_verified_signals"),
                     f"independent verified signals: {comp.get('independent_verified_signals')}")]
    f = F(label, 0.75, action, f"risk score {score} against register thresholds {th}",
          f"impact {impact} x (independent signals {signals} + irreversible {irrev}); "
          f"correlated not counted: {comp.get('correlated_not_counted')}",
          claims, {"MATERIAL": "HIGH", "EMERGING": "MEDIUM", "LOW": "NONE"}[label],
          cop_specific={"score": score, "components": comp, "register_version": reg.get("version")})
    return f


def _rk_questions(ctx, f):
    """Risk Mitigation's four answers (cop_package.decide_risk_mitigation). RK-2's register band decides the label
    from risk-bearing, independent, undisputed signals; the legacy reading above stays the computational baseline.
    Every reading is decided without a model (packages/risk_mitigation/INTERPRETATION.md)."""
    t = ctx["tools"]
    res = result(t, "assess_risk_questions")
    if not res.get("questions"):
        why = res.get("missing_dependency") or (t.get("assess_risk_questions") or {}).get("status")
        f["cop_specific"]["question_assessment"] = f"unavailable: {why}"
        return
    d = decide_risk_mitigation(res["questions"])
    q = d["questions"]
    legacy = {"label": f["label"], "confidence": f["confidence"], "action": f.get("action"),
              "score": f["cop_specific"].get("score")}
    f["cop_specific"].update(questions=q, drivers=d["drivers"], contrary=d["contrary"], limitations=d["limitations"],
                             recipients=d["recipients"], memory_payload=d["memory_payload"],
                             decided_without_model=d["decided"], decision=d["decision"], risk_status=d["risk_status"],
                             legacy_reading=legacy, previous_reading=d["previous_reading"])
    claims = [tclaim(t, "assess_risk_questions", f"questions.{qid}.answer.outcome", x["answer"],
                     f"{qid} answered {x['answer']}: {x['explanation']}"[:240]).to_dict() for qid, x in q.items()]
    why = "; ".join(f"{x} {q[x]['answer']}" for x in d["drivers"])
    if d["label"] == INCONCLUSIVE and d["register_problem"]:
        # the register or its thresholds are missing: the legacy MISSING_DEPENDENCY reading stands, with the answers
        f["claims"] = claims + f["claims"]
        f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL
        return
    status = SUBSTANTIVE if d["label"] != INCONCLUSIVE else "INCONCLUSIVE"
    f.update(label=d["label"], confidence=d["confidence"] if d["label"] != INCONCLUSIVE else 0.0, action=d["action"],
             severity=d["severity"], action_reason=d["action_reason"], status=status, claims=claims + f["claims"],
             rationale=(f"{d['label']} from {why}; observed, inferred and verified risk kept apart; controls "
                        f"recommended, never applied; legacy reading: {f['label']} ({f['rationale']})")[:600])
    f["cop_specific"]["stop_reason"] = DECIDED_WITHOUT_MODEL


def governance_intelligence(ctx):
    t = ctx["tools"]
    rep = result(t, "query_aggregate_report")
    if "missing_dependency" in rep:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no decision history to aggregate",
                 status=MISSING_DEPENDENCY, missing_deps=[rep["missing_dependency"]])
    quiet = {"NORMAL", "SUFFICIENT", "ALLOWED", "CONTAINED", "COMPLETE", "UNCHANGED", "STABLE",
             "NO_INCIDENT", "UNCLASSIFIED", "EFFICIENT", "CONTEXT_OK", "LOW", "CURRENT", "ADEQUATE",
             "RECOVERED", "NO_PATTERN"}
    blind = [r for r in rep["by_cop_label"] if r["label"] in (INCONCLUSIVE, "INSUFFICIENT", "INDETERMINATE",
                                                             "UNCERTAIN") and r["sessions"] > 1]
    recur = [r for r in rep["by_cop_label"] if r["label"] not in quiet | {INCONCLUSIVE, "INSUFFICIENT",
             "INDETERMINATE", "UNCERTAIN"} and r["sessions"] > 1]
    claim = lambda rows, what: tclaim(t, "query_aggregate_report", "by_cop_label", len(rows),
                                      f"{len(rows)} {what} across more than one session")
    if blind:
        return F("BLIND_SPOT", 0.65, "WATCH", "the same COP repeatedly cannot decide",
                 "; ".join(f"{r['cop']}={r['label']} in {r['sessions']} sessions" for r in blind[:4]),
                 [claim(blind, "undecidable readings")], "LOW")
    if recur:
        return F("RECURRING", 0.65, "WATCH", "the same finding recurs",
                 "; ".join(f"{r['cop']}={r['label']} in {r['sessions']} sessions" for r in recur[:4]),
                 [claim(recur, "attention readings")], "LOW")
    return F("NO_PATTERN", 0.6, "NONE", "", f"{rep['decisions']} decisions, nothing recurs",
             [claim([], "recurring readings")],
             cop_specific={"unreachable": {"CONCENTRATED": "needs repeat_actor_concentration"}})


def _state_of(case, metric_id, value):
    """A value's state on a metric's configured thresholds (metrics_config.json)."""
    th = case.cfg["thresholds"].get(metric_id, {})
    up = th.get("direction", "above") == "above"
    for st in ("critical", "exception", "warning"):
        if st in th and ((value >= th[st]) if up else (value <= th[st])):
            return st.upper()
    return "NORMAL"


def workload_optimization(ctx):
    t = ctx["tools"]
    m = {e.metric_id: e for e in ctx["events"]}
    rep = result(t, "inspect_repeated_operations")
    dur, repm = m.get("insights.duration_outlier"), m.get("insights.repetition")
    claims = []
    if dur is not None and dur.state in HOT:
        return F("BOTTLENECK", 0.7, "WATCH", "steps far beyond the estate's usual duration", "",
                 [mclaim(dur)], SEV[dur.state])
    if repm is not None and repm.state in ATTENTION_STATES and (rep.get("redundant_read_calls", 0)
                                                               + rep.get("redundant_command_calls", 0)):
        claims = [mclaim(repm), tclaim(t, "inspect_repeated_operations", "redundant_read_calls",
                                       rep["redundant_read_calls"],
                                       f"{rep['redundant_read_calls']} redundant reads")]
        return F("REDUNDANT", 0.7, "WATCH", "identical work repeated", "", claims, "LOW")
    # insights.repetition counts a repeat only when the whole argument payload is identical
    # within one turn, so repeats whose arguments differ in an incidental field read 0 there.
    # The tool compares by target and checks nothing changed on disk in between. Judged on
    # insights.repetition's own configured thresholds; no new threshold.
    unchanged = rep.get("unchanged_repeat_reads", 0) + rep.get("unchanged_repeat_commands", 0)
    rstate = _state_of(ctx["case"], "insights.repetition", unchanged) if unchanged else "NORMAL"
    if rstate in ATTENTION_STATES:
        claims = [tclaim(t, "inspect_repeated_operations", "unchanged_repeat_reads",
                         rep["unchanged_repeat_reads"], f"{rep['unchanged_repeat_reads']} reads repeated "
                         f"with nothing written in between"),
                  tclaim(t, "inspect_repeated_operations", "unchanged_repeat_commands",
                         rep["unchanged_repeat_commands"], f"{rep['unchanged_repeat_commands']} commands "
                         f"repeated with nothing written in between")]
        return F("REDUNDANT", 0.65, "WATCH", "identical work repeated with nothing changed between",
                 f"{unchanged} unchanged repeats ({rstate} on the insights.repetition thresholds); "
                 f"insights.repetition itself reads {repm.value if repm else 'nothing'}", claims, "LOW")
    judged = [e for e in ctx["events"] if e.state in ("NORMAL",) + ATTENTION_STATES]
    if not judged:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no workload measure could be judged",
                 status="INCONCLUSIVE")
    return F("EFFICIENT", 0.6, "NONE", "", f"{len(judged)} workload measures within bounds",
             [mclaim(e) for e in judged[:4]],
             cop_specific={"unreachable": {"OVERPROVISIONED": "needs capacity data",
                                           "UNDERPROVISIONED": "needs capacity data"}})


def knowledge_policy_management(ctx):
    t = ctx["tools"]
    pv = result(t, "get_policy_versions")
    if "missing_dependency" in pv:
        return F(INCONCLUSIVE, 0.0, "NONE", "", "no policy store", status=MISSING_DEPENDENCY,
                 missing_deps=[pv["missing_dependency"]])
    cmp_ = result(t, "compare_policy_documents")
    deps = result(t, "inspect_rule_dependencies")
    auth = result(t, "check_authorisation_records")
    unprec = [c for c in cmp_.get("conflicts", []) if not c["precedence_declared"]]
    if unprec:
        return F("CONFLICT", 0.8, "WATCH", "rules in force contradict each other without precedence", "",
                 [tclaim(t, "compare_policy_documents", "conflicts", len(unprec),
                         f"{len(unprec)} conflicting rule pairs in {cmp_['version']}: " +
                         ", ".join("/".join(c["rules"]) for c in unprec[:3]))], "MEDIUM",
                 cop_specific={"conflicts": unprec})
    unauth = [v for v in auth.get("versions", []) if v["version"] == pv.get("in_force")
              and not v["authorisation_record"]]
    if deps.get("broken_dependencies") or unauth:
        return F("UPDATE_REQUIRED", 0.75, "WATCH", "the policy in force has broken dependencies or "
                 "no authorisation record", "",
                 [tclaim(t, "inspect_rule_dependencies", "broken_dependencies",
                         len(deps.get("broken_dependencies", [])), "broken rule dependencies")], "LOW")
    pe = verified(ctx["upstream"], "policy_enforcement")
    used_v = (pe or {}).get("cop_specific", {}).get("policy_version")
    if used_v and used_v != pv.get("in_force"):
        return F("STALE", 0.7, "WATCH", "enforcement evaluated a version that is not in force", "",
                 [uclaim("policy_enforcement", pe)], "LOW")
    return F("CURRENT", 0.75, "NONE", "", f"policy {pv.get('in_force')} consistent and authorised",
             [tclaim(t, "compare_policy_documents", "conflicts", 0, "no conflicting rules")])


def governance_architecture(ctx):
    """Two levels: active gaps (something observed in this case that no metric or COP
    covers) and latent gaps (in the wider architecture). A latent item is a GAP only when
    it is a required responsibility with no coverage; an unconsumed metric alone is not."""
    t = ctx["tools"]
    cov = result(t, "query_coverage_matrix")
    uncovered = []
    for f_ in cov.get("unowned_fields", []):
        uncovered.append({"kind": "field", "name": f_, "owner": None})
    for ty in cov.get("unhandled_observable_types", []):
        uncovered.append({"kind": "observable_type", "name": ty, "owner": None})
    estate = cov.get("estate_gap_assessment", [])
    latent_required = [x for x in estate if x.get("required_by_spec") and not x.get("alternative_controls")]
    levels = {"active": [u["name"] for u in uncovered],
              "latent_required": [x["metric"] for x in latent_required],
              "latent_observations": [{"metric": x["metric"], "classification": x["classification"],
                                       "documented_consumers": x["documented_consumers"]}
                                      for x in estate if x not in latent_required]}
    claims, used = [], []
    at = verified(ctx["upstream"], "audit_traceability")
    if at:
        q = (at.get("cop_specific") or {}).get("questions") or {}
        levels["audit"] = {"label": at["label"], "finding_id": at.get("finding_id"),
                           "unclean_answers": {k: v["answer"] for k, v in q.items()
                                               if v["answer"] not in AT_CLEAN},
                           "untraced": ((q.get("AT-4") or {}).get("basis") or {}).get("not_traced", [])}
        used.append({"cop": "audit_traceability", "label": at["label"], "finding_id": at.get("finding_id"),
                     "counted_as": "audit gaps of the case, owned by Audit & Traceability, and what it cannot trace"})
        claims.append(uclaim("audit_traceability", at))
    if uncovered or latent_required:
        if uncovered:
            claims.append(tclaim(t, "query_coverage_matrix", "unowned_fields+unhandled_observable_types",
                                 [u["name"] for u in uncovered],
                                 "active gap: signals in this case no metric reads and no COP owns: " +
                                 ", ".join(u["name"] for u in uncovered)))
        if latent_required:
            claims.append(tclaim(t, "query_coverage_matrix", "estate_gap_assessment",
                                 [x["metric"] for x in latent_required],
                                 "latent gap: required responsibilities no COP covers"))
        gi = verified(ctx["upstream"], "governance_intelligence", "BLIND_SPOT")
        label = "STRUCTURAL_CHANGE_RECOMMENDED" if gi else "GAP"
        return F(label, 0.8, "WATCH", "a responsibility no executable rule or COP covers", "",
                 claims, "MEDIUM", cop_specific={"uncovered_responsibilities": uncovered, "gap_levels": levels,
                                                 "estate_metrics_read_by_no_cop": cov.get("metrics_read_by_no_cop", [])},
                 upstream_used=used)
    note = ""
    if levels["latent_observations"]:
        note = ("; latent, not gaps: " + ", ".join(
            f"{x['metric']} ({x['classification']})" for x in levels["latent_observations"]))
    return F("ADEQUATE", 0.6, "NONE", "", "no active gap and no uncovered required responsibility" + note,
             [tclaim(t, "query_coverage_matrix", "unowned_fields", [], "no unowned field")] + claims,
             upstream_used=used,
             cop_specific={"gap_levels": levels,
                           "estate_metrics_read_by_no_cop": cov.get("metrics_read_by_no_cop", []),
                           "edge_differences": len(result(t, "validate_cop_relationships").get("edge_differences", []))})


RULES = {k: globals()[k] for k in DETERMINISTIC_TOOLS}
