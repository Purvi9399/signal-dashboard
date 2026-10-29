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

import types

from ..interpret import RuleInterpreter, SEV, RANK
from ..models import INCONCLUSIVE
from .contracts import (Claim, CLAIM_METRIC_STATE, CLAIM_TOOL_OBSERVATION, CLAIM_UPSTREAM,
                        CLAIM_MEMORY_PRECEDENT, CLAIM_ABSENCE, ORIGIN_RULES,
                        SUBSTANTIVE, MISSING_DEPENDENCY, ATTENTION_STATES)
from ..interpret import EVIDENCE_LEVEL

_RI = RuleInterpreter()
HOT = ("EXCEPTION", "CRITICAL")

# Which tools each COP's deterministic path calls, in order.
DETERMINISTIC_TOOLS = {
    "runtime_monitoring": ("get_collector_breakdown", "check_collector_expectations"),
    "interaction_discovery": ("build_interaction_graph", "compare_known_routes"),
    "policy_enforcement": ("evaluate_session_operations", "check_entitlements"),
    "resource_management": ("compute_consumption", "get_quota"),
    "behavior_analytics": ("compute_sequence_statistics", "compare_behaviour_history"),
    "recovery_resilience": ("get_recovery_records", "validate_checkpoints"),
    "audit_traceability": ("reconstruct_trace", "check_decision_linkage"),
    "agent_lifecycle_management": ("get_agent_registry", "detect_configuration_changes"),
    # LLM COPs: the tools their computational baseline reads, so it is
    # computed from the same sources the model can ask for.
    "workflow_orchestration": ("get_task_milestones", "analyse_execution_transitions"),
    "anomaly_detection": (),
    "context_memory": ("get_context_snapshots", "compare_instruction_versions",
                       "inspect_context_references", "verify_context_provenance"),
    "incident_response": ("check_exposure",),
    "classification_tagging": ("query_taxonomy", "inspect_event_metadata"),
    "risk_mitigation": ("read_risk_register", "calculate_risk_components"),
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
    return f


def interaction_discovery(ctx):
    out = _RI.interaction_discovery(ctx["events"], _legacy_up(ctx["upstream"]), {},
                                    evidence_conf(ctx["upstream"]))
    known = result(ctx["tools"], "compare_known_routes")
    graph = result(ctx["tools"], "build_interaction_graph")
    extra = []
    if known.get("new_routes"):
        extra.append(tclaim(ctx["tools"], "compare_known_routes", "new_routes", known["new_routes"],
                            f"routes not seen for this operator before: {', '.join(known['new_routes'][:5])}"))
        if out["interpretation"] == "CONTAINED" and any(r.startswith("mcp:") for r in known["new_routes"]):
            out = {**out, "interpretation": "NEW_ROUTE", "action": "PASS_TO_COP",
                   "action_reason": "a route that did not exist before now does; whether it is "
                                    "permitted is Policy Enforcement's question",
                   "severity": "LOW"}
    f = _from_legacy(ctx, out, extra)
    f["cop_specific"].update({"edges": graph.get("edges", []), "new_routes": known.get("new_routes", [])})
    return f


def policy_enforcement(ctx):
    up = ctx["upstream"]
    base = _RI.policy_enforcement(ctx["events"], _legacy_up(up), {}, evidence_conf(up))
    ev = result(ctx["tools"], "evaluate_session_operations")
    ent = result(ctx["tools"], "check_entitlements")
    miss = missing_of(ctx["tools"], "evaluate_session_operations", "check_entitlements")
    claims, used = [], []
    route = verified(up, "interaction_discovery", "NEW_ROUTE")
    if route:
        used.append({"cop": "interaction_discovery", "label": "NEW_ROUTE",
                     "finding_id": route.get("finding_id"), "counted_as": "input to evaluate"})
        claims.append(uclaim("interaction_discovery", route))
    if "missing_dependency" in ev:
        # No policy store: the condition reading stands, labelled as such. A new
        # route alone is never a violation, and with no rule it is not allowed.
        f = _from_legacy(ctx, base, claims)
        if route and f["label"] == "ALLOWED":
            f.update(label="UNCERTAIN", action="WATCH", confidence=0.3,
                     action_reason="a new route arrived and no policy store exists to evaluate it; "
                                   "a route with no rule is not a permitted one")
        f["missing_dependencies"] = miss
        f["rationale"] += "; conditions only: no policy store, so no rule was evaluated"
        f["upstream_used"] = used
        return f
    denied = [o for o in ev["observed"] if o["decision"] == "DENY" and not o["blocked_by_hook"]]
    blocked = [o for o in ev["observed"] if o["decision"] == "DENY" and o["blocked_by_hook"]]
    need = [o for o in ev["observed"] + ev["proposed"] if o["decision"] == "REQUIRE_APPROVAL"]
    no_rule = [o for o in ev["observed"] + ev["proposed"] if o["decision"] == "NO_RULE"]
    prop_denied = [o for o in ev["proposed"] if o["decision"] == "DENY"]
    unent = ent.get("used_not_entitled", []) if "missing_dependency" not in ent else []
    sev = "NONE"
    if denied or unent:
        label, action, sev = "VIOLATION", "ALERT", "HIGH"
        reason = "an executed operation matched a deny rule in the policy in force"
        if denied:
            claims.append(tclaim(ctx["tools"], "evaluate_session_operations", "observed[decision=DENY]",
                                 len(denied), f"{len(denied)} executed operations denied by "
                                 f"{ev['policy_version']}: " + ", ".join(sorted({r for o in denied for r in o['rules']}))))
        if unent:
            claims.append(tclaim(ctx["tools"], "check_entitlements", "used_not_entitled", unent,
                                 f"MCP servers used without entitlement: {', '.join(unent)}"))
    elif prop_denied:
        label, action, sev = "VIOLATION", "BLOCK", "HIGH"
        reason = "a proposed operation is denied by the policy in force; recommend blocking it"
        claims.append(tclaim(ctx["tools"], "evaluate_session_operations", "proposed[decision=DENY]",
                             len(prop_denied), f"{len(prop_denied)} proposed operations denied by "
                             f"{ev['policy_version']}"))
    elif need and not ev["approval_recorded"]:
        label, action, sev = "APPROVAL_REQUIRED", "REQUEST_APPROVAL", "MEDIUM"
        reason = "the policy requires approval for an operation and none is recorded"
        claims.append(tclaim(ctx["tools"], "evaluate_session_operations",
                             "observed+proposed[decision=REQUIRE_APPROVAL]", len(need),
                             f"{len(need)} operations need approval under {ev['policy_version']}"))
    elif no_rule:
        label, action, sev = "UNCERTAIN", "WATCH", "LOW"
        reason = "operations with no matching rule: not shown to be permitted"
        claims.append(tclaim(ctx["tools"], "evaluate_session_operations",
                             "observed+proposed[decision=NO_RULE]", len(no_rule),
                             f"{len(no_rule)} operations match no rule in {ev['policy_version']}"))
    else:
        label, action = "ALLOWED", "NONE"
        reason = ""
        claims.append(tclaim(ctx["tools"], "evaluate_session_operations", "observed[decision=ALLOW]",
                             len(ev["observed"]), f"all {len(ev['observed'])} operations allowed by "
                             f"{ev['policy_version']}"))
    return F(label, 0.85 if label != "UNCERTAIN" else 0.4, action, reason,
             f"evaluated against policy {ev['policy_version']}; {len(blocked)} attempts stopped by hooks",
             claims, sev, missing_deps=miss,
             cop_specific={"policy_version": ev["policy_version"], "denied": denied,
                           "proposed": ev["proposed"], "approval_needed": need,
                           "blocked_by_hook": blocked},
             upstream_used=used)


def resource_management(ctx):
    out = _RI.resource_management(ctx["events"], {}, {}, evidence_conf(ctx["upstream"]))
    q = result(ctx["tools"], "get_quota")
    use = result(ctx["tools"], "compute_consumption")
    extra, miss = [], missing_of(ctx["tools"], "get_quota")
    if "missing_dependency" not in q and q.get("token_budget") is not None and use.get("tokens") is not None:
        if use["tokens"] > q["token_budget"]:
            out = {**out, "interpretation": "UNAUTHORIZED", "action": "ALERT", "severity": "HIGH",
                   "action_reason": "consumption above the configured quota"}
        extra.append(tclaim(ctx["tools"], "compute_consumption", "tokens", use["tokens"],
                            f"session used {use['tokens']} tokens against a quota of {q['token_budget']}"))
    f = _from_legacy(ctx, out, extra)
    f["missing_dependencies"] = miss
    f["cop_specific"]["consumption"] = use
    return f


def behavior_analytics(ctx):
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
    m = {e.metric_id: e for e in ctx["events"]}
    recs = result(ctx["tools"], "get_recovery_records")
    cps = result(ctx["tools"], "validate_checkpoints")
    miss = missing_of(ctx["tools"], "get_recovery_records")
    rows = ctx["case"].session.rows if ctx["case"].session else []
    from .tools import IRREVERSIBLE
    irrev = [r for r in rows if IRREVERSIBLE.search(str(r.get("command") or ""))]
    hot = [e for e in (m.get("insights.reverted"), m.get("insights.retry"), m.get("insights.failed"))
           if e is not None and e.state in HOT]
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
    out = _RI.audit_traceability(ctx["events"], _legacy_up(ctx["upstream"]), {},
                                 evidence_conf(ctx["upstream"]))
    link = result(ctx["tools"], "check_decision_linkage")
    tr = result(ctx["tools"], "reconstruct_trace")
    extra = []
    broken = [d for d in link.get("decisions", []) if d["refs"] and d["resolvable"] < d["refs"]]
    if broken:
        extra.append(tclaim(ctx["tools"], "check_decision_linkage", "decisions[unresolvable]",
                            len(broken), f"{len(broken)} upstream decisions cite evidence that does "
                            f"not resolve to a row in this case"))
        if out["interpretation"] in ("COMPLETE", "PARTIAL", INCONCLUSIVE):
            out = {**out, "interpretation": "BROKEN_TRACE", "action": "ALERT", "severity": "MEDIUM",
                   "action_reason": "a decision cites evidence the record cannot produce"}
    f = _from_legacy(ctx, out, extra)
    f["cop_specific"].update({"trace": tr, "decision_linkage": link.get("decisions", [])})
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
    return f


def incident_response(ctx):
    up = ctx["upstream"]
    out = _RI.incident_response(ctx["events"], _legacy_up(up), {}, evidence_conf(up))
    used, claims = [], []
    for cop in ("anomaly_detection", "policy_enforcement", "runtime_monitoring", "recovery_resilience",
                "workflow_orchestration", "interaction_discovery"):
        f = verified(up, cop)
        if f:
            used.append({"cop": cop, "label": f["label"], "finding_id": f.get("finding_id"),
                         "counted_as": "input"})
            if f["label"] not in ("NORMAL", "SUFFICIENT", "ALLOWED", "CONTAINED", "RECOVERED"):
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
    return f


def classification_tagging(ctx):
    out = _RI.classification_tagging(ctx["events"], {}, {}, evidence_conf(ctx["upstream"]))
    tax = result(ctx["tools"], "query_taxonomy")
    f = _from_legacy(ctx, out)
    if "missing_dependency" in tax:
        f["missing_dependencies"] = [tax["missing_dependency"]]
    else:
        f["cop_specific"]["taxonomy_version"] = tax.get("version")
    return f


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
    return F(label, 0.75, action, f"risk score {score} against register thresholds {th}",
             f"impact {impact} x (independent signals {signals} + irreversible {irrev}); "
             f"correlated not counted: {comp.get('correlated_not_counted')}",
             claims, {"MATERIAL": "HIGH", "EMERGING": "MEDIUM", "LOW": "NONE"}[label],
             cop_specific={"score": score, "components": comp, "register_version": reg.get("version")})


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
    t = ctx["tools"]
    cov = result(t, "query_coverage_matrix")
    m = {e.metric_id: e for e in ctx["events"]}
    uncovered = []
    for f_ in cov.get("unowned_fields", []):
        uncovered.append({"kind": "field", "name": f_, "owner": None})
    for ty in cov.get("unhandled_observable_types", []):
        uncovered.append({"kind": "observable_type", "name": ty, "owner": None})
    claims = []
    if uncovered:
        claims.append(tclaim(t, "query_coverage_matrix", "unowned_fields+unhandled_observable_types",
                             [u["name"] for u in uncovered],
                             "signals in this case no metric reads and no COP owns: " +
                             ", ".join(u["name"] for u in uncovered)))
        gi = verified(ctx["upstream"], "governance_intelligence", "BLIND_SPOT")
        label = "STRUCTURAL_CHANGE_RECOMMENDED" if gi else "GAP"
        return F(label, 0.8, "WATCH", "a signal arrives that no executable rule or COP covers", "",
                 claims, "MEDIUM", cop_specific={"uncovered_responsibilities": uncovered,
                                                 "estate_metrics_read_by_no_cop": cov.get("metrics_read_by_no_cop", [])})
    return F("ADEQUATE", 0.6, "NONE", "", "every signal in this case has a metric and a COP",
             [tclaim(t, "query_coverage_matrix", "unowned_fields", [], "no unowned field")],
             cop_specific={"estate_metrics_read_by_no_cop": cov.get("metrics_read_by_no_cop", []),
                           "edge_differences": len(result(t, "validate_cop_relationships").get("edge_differences", []))})


RULES = {k: globals()[k] for k in DETERMINISTIC_TOOLS}
