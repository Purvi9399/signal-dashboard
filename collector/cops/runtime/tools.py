"""
The tool gateway and the computational tools behind it.

A COP never holds a connection, a query or a file handle. It asks the
gateway for a named tool with arguments; the gateway checks, in order:

    the tool exists                          else REJECTED unknown_tool
    the COP's allowlist names it             else REJECTED not_in_allowlist
    the arguments match the tool's schema    else REJECTED invalid_arguments
    bounds hold (window sizes, ref counts)   else REJECTED out_of_bounds
    scope holds (this case's session/tenant) else REJECTED out_of_scope
    the investigation still has tool budget  else REJECTED budget_exhausted

and records every request, rejected or not. Results come back wrapped as
untrusted data and are scanned for instruction-shaped text; a hit is flagged
on the result and in the audit log. Flagged text is never executed: the
gateway has no tool that changes permissions, policies or thresholds, so
there is nothing for injected text to call.

Every tool is read-only and computes from CaseData. A tool whose source does
not exist returns {"missing_dependency": ...} rather than an answer.
"""

import fnmatch, hashlib, json, re, statistics, time
from collections import Counter, defaultdict

from ..specs import BY_KEY, SPECS, edge_report, CONSUMES
from .contracts import stable_id
from . import memory as M

# ---------------------------------------------------------------- injection scan

INJECTION = [
    re.compile(p, re.I) for p in (
        r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
        r"disregard (your|the) (instructions|policy|rules)",
        r"you are now", r"new instructions?:", r"system prompt",
        r"(grant|give) (yourself|me|the agent) (access|permission|admin)",
        r"(disable|turn off|bypass) (the )?(verifier|policy|check|guard|monitor)",
        r"(add|append) .* to (your|the) (allowlist|tool list)",
        r"set (the )?threshold", r"<\|.*?\|>", r"\bassistant:\s", r"call the tool",
    )
]


def scan(obj):
    text = json.dumps(obj, default=str)
    return sorted({p.pattern for p in INJECTION if p.search(text)})


def missing(dep, required_for, how):
    return {"missing_dependency": {"dependency": dep, "required_for": required_for,
                                   "how_to_supply": how}}


SAFE_ROW = ("observable_id", "observable_type", "occurred_at", "tool_name", "mcp_server",
            "file_path", "command", "exit_code", "success", "sensitivity_tier",
            "secret_detected", "outside_workspace", "policy_rule_matched",
            "permission_decision", "collector", "agent_id", "turn_id", "tool_version", "model")


def _safe(r, extra=()):
    return {k: r.get(k) for k in SAFE_ROW + tuple(extra) if r.get(k) not in (None, "")}


def _sha(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- shared helpers

def _steps(case, lane=None):
    s = case.session
    if not s:
        return []
    lanes = s.trace["lanes"]
    if lane:
        return lanes.get(lane, [])
    return [x for ln in lanes.values() for x in ln]


def _step_view(x):
    return {k: x.get(k) for k in ("step_id", "seq", "step", "tool_name", "failed", "lane",
                                  "parent_step_id", "turn_id", "command", "file_path",
                                  "mcp_server", "duration_ms", "decided_by")}


def _operation(r):
    """What a row asked to do, in policy terms."""
    if r.get("mcp_server"):
        return {"action": "mcp", "resource": r["mcp_server"]}
    t = r.get("tool_name") or ""
    if r.get("command") or t == "Bash":
        return {"action": "exec", "resource": str(r.get("command") or "")}
    if t in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return {"action": "write", "resource": r.get("file_path") or ""}
    if t in ("Read", "Grep", "Glob") or r.get("file_path"):
        return {"action": "read", "resource": r.get("file_path") or ""}
    if t:
        return {"action": "tool", "resource": t}
    return None


def latest_policy(case):
    ps = case.artifact("policy_store")
    if not ps or not ps.get("versions"):
        return None
    approved = [v for v in ps["versions"] if v.get("approved_by")]
    if not approved:
        return None
    return sorted(approved, key=lambda v: v.get("effective", ""))[-1]


def evaluate_operation(version, op, agent="*"):
    """Deny beats require_approval beats allow. No rule is not permission."""
    hits = [r for r in version.get("rules", [])
            if r.get("action") in (op["action"], "*")
            and fnmatch.fnmatch(op.get("resource") or "", r.get("resource_pattern", "*"))
            and r.get("applies_to", "*") in ("*", agent)]
    effects = {r["effect"] for r in hits}
    if "deny" in effects:
        decision = "DENY"
    elif "require_approval" in effects:
        decision = "REQUIRE_APPROVAL"
    elif "allow" in effects:
        decision = "ALLOW"
    else:
        decision = "NO_RULE"
    return {"decision": decision, "rules": [r["id"] for r in hits],
            "policy_version": version.get("version")}


# ---------------------------------------------------------------- tools
# Each: fn(case, args, ctx) -> dict. ctx carries cop, upstream, memory gateway.

def t_get_metric_rows(case, a, ctx):
    spec = BY_KEY[ctx["cop"]]
    ids = [m for m in a.get("metric_ids", spec.metric_keys) if m in spec.metric_keys]
    return {"rows": [e.brief() | {"evidence_refs": list(e.evidence_refs[:10])}
                     for e in case.case_events(ids)]}


# ---- Workflow Orchestration
def t_get_task_milestones(case, a, ctx):
    plan = case.artifact("task_plan")
    if not plan:
        return missing("task_plan", "workflow_orchestration: progress against the declared objective",
                       "a task plan per session: objective and milestones with observable done signals")
    rows = case.session.rows if case.session else []
    out = []
    for m in plan.get("milestones", []):
        sig = m.get("done_signal", {})
        pat = re.compile(sig.get("pattern", "a^"), re.I)
        field_ = sig.get("field", "command")
        hit = next((r for r in rows if pat.search(str(r.get(field_) or ""))
                    and (not sig.get("require_success") or r.get("success") is not False
                         and str(r.get("exit_code") or "0") in ("0", "0.0"))), None)
        out.append({"id": m["id"], "name": m.get("name"), "reached": bool(hit),
                    "evidence_ref": f"observable:{hit['observable_id']}" if hit else None})
    return {"objective": plan.get("objective"), "milestones": out,
            "reached": sum(1 for x in out if x["reached"]), "total": len(out)}


def t_analyse_execution_transitions(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "transitions", "the case session's observables")
    trans = Counter(s.transitions())
    loops = []
    for lane, steps in s.trace["lanes"].items():
        seq = [x["step"] for x in steps]
        runs, cur = [], 1
        for i in range(1, len(seq)):
            if seq[i] == seq[i - 1]:
                cur += 1
            else:
                runs.append((seq[i - 1], cur)); cur = 1
        if seq:
            runs.append((seq[-1], cur))
        pairs = Counter(zip(seq, seq[1:], seq[2:], seq[3:]))
        for (p, q, r_, t_), n in pairs.items():
            if p == r_ and q == t_ and p != q and n >= 2:
                loops.append({"lane": lane, "cycle": f"{p}>{q}", "repeats": n})
        longest = max(runs, key=lambda x: x[1]) if runs else None
        if longest:
            loops.append({"lane": lane, "longest_run": {"step": longest[0], "length": longest[1]}})
    stalls = [r for r in s.rows if r.get("hung_seconds")]
    return {"transitions": {f"{a_}>{b}": n for (a_, b), n in trans.most_common(12)},
            "patterns": loops,
            "stalls": [{"ref": f"observable:{r['observable_id']}", "hung_seconds": r["hung_seconds"]}
                       for r in stalls]}


def t_inspect_delegation_traces(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "delegation", "the case session's observables")
    lanes = s.trace["lanes"]
    subs = {k: v for k, v in lanes.items() if k != "main"}
    handoffs = [x for x in lanes.get("main", []) if x["step"] == "DELEGATE"]
    return {"delegations": len(handoffs), "subagent_lanes": len(subs),
            "unlinked_lanes": sorted(k for k, v in subs.items() if not (v and v[0].get("parent_step_id"))),
            "lane_sizes": {k: len(v) for k, v in subs.items()}}


def _memory_lookup(ctx, rtype):
    g = ctx.get("memory")
    if g is None:
        return missing("memory_gateway", rtype, "a MemoryGateway bound to the case")
    out = g.read(ctx["cop"], ctx["case_id"], ctx["tenant_id"], (rtype,), ctx.get("agent_scope"))
    view = lambda r: {"record_id": r["record_id"], "status": r["validation_status"],
                      "payload": r["payload"], "case_id": (r["provenance"] or {}).get("case_id")}
    return {"record_type": rtype,
            "established": [view(r) for r in out["established"]],
            "verified_pending": [view(r) for r in out["verified_pending"]],
            "unverified_count": len(out["unverified"]),
            "note": "only established records are precedent; unverified observations are not evidence"}


def t_compare_workflow_failures(case, a, ctx):
    return _memory_lookup(ctx, "workflow_failure")


# ---- Anomaly Detection
MAX_TRACE_STEPS = 50


def t_retrieve_trace_window(case, a, ctx):
    lane = a.get("lane", "main")
    start, n = int(a.get("start", 0)), int(a.get("max_steps", 20))
    steps = _steps(case, lane)
    win = steps[start:start + n]
    return {"lane": lane, "start": start, "returned": len(win), "lane_length": len(steps),
            "steps": [_step_view(x) for x in win]}


def t_compare_versioned_baseline(case, a, ctx):
    mid = a["metric_id"]
    hist = [e for e in case.metric_history(mid) if e["value"] is not None
            and e["state"] not in ("UNKNOWN", "NOT_APPLICABLE")]
    cur = next((e for e in case.events if e["session_id"] == case.session_id
                and e["metric_id"] == mid), None)
    vals = sorted(float(e["value"]) for e in hist)
    version = f"{cur['metric_version'] if cur else '?'}#" + _sha(sorted(e["session_id"] for e in hist))[:10]
    out = {"metric_id": mid, "baseline_version": version, "sessions_in_baseline": len(vals),
           "current_value": cur["value"] if cur else None,
           "current_state": cur["state"] if cur else "MISSING"}
    if vals:
        out.update({"median": statistics.median(vals), "max": vals[-1], "min": vals[0]})
    else:
        out["note"] = "no judged history for this metric; no baseline to compare with"
    return out


def t_find_similar_validated_cases(case, a, ctx):
    return _memory_lookup(ctx, "anomaly_observation")


def t_calculate_deviation(case, a, ctx):
    """Raw-data deviation, recomputed from observables, not read from metrics."""
    quantity = a["quantity"]
    def q(s):
        if quantity == "tokens":
            return s.tokens
        if quantity == "actions":
            return float(len(s.actions))
        if quantity == "failures":
            return float(sum(1 for r in s.rows if r.get("success") is False))
        raise ValueError(quantity)
    s = case.session
    if not s:
        return missing("session", "deviation", "the case session")
    others = [q(x) for x in case.other_sessions() if q(x) > 0 or quantity != "tokens"]
    cur = q(s)
    if len(others) < 2:
        return {"quantity": quantity, "current": cur, "n_baseline": len(others),
                "note": "fewer than two comparable sessions; deviation not computed"}
    med = statistics.median(others)
    mean, sd = statistics.mean(others), statistics.pstdev(others)
    return {"quantity": quantity, "current": cur, "n_baseline": len(others),
            "baseline_median": med, "ratio_to_median": round(cur / med, 3) if med else None,
            "z_score": round((cur - mean) / sd, 3) if sd else None}


def t_analyse_tool_call_sequence(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "tool sequence", "the case session")
    names = [r.get("tool_name") for r in s.actions]
    consec, run = 1, 1
    for i in range(1, len(names)):
        run = run + 1 if names[i] == names[i - 1] else 1
        consec = max(consec, run)
    bigrams = Counter(zip(names, names[1:]))
    cmds = Counter(str(r.get("command")).strip() for r in s.rows if r.get("command"))
    fails = [r for r in s.rows if r.get("success") is False or
             (r.get("exit_code") not in (None, "", 0, "0", "0.0"))]
    return {"calls": len(names), "distinct_tools": len(set(names)),
            "max_consecutive_same_tool": consec,
            "top_bigrams": {f"{x}>{y}": n for (x, y), n in bigrams.most_common(5)},
            "repeated_commands": {c: n for c, n in cmds.most_common(5) if n > 1},
            "failed_calls": len(fails),
            "failed_refs": [f"observable:{r['observable_id']}" for r in fails[:10]]}


# ---- Context & Memory
def _snapshots(case):
    snaps = case.artifact("context_snapshots")
    if not snaps:
        return None
    return sorted(snaps, key=lambda x: x.get("at", ""))


def t_get_context_snapshots(case, a, ctx):
    snaps = _snapshots(case)
    if snaps is None:
        return missing("context_snapshots", "context_memory: continuity across compaction",
                       "per-session context snapshots (initial, each compaction, each resume) "
                       "with their constraints and a content hash")
    return {"snapshots": [{"snapshot_id": s["snapshot_id"], "at": s["at"], "kind": s["kind"],
                           "instructions_version": s.get("instructions_version"),
                           "constraint_ids": [c["id"] for c in s.get("constraints", [])]}
                          for s in snaps]}


def t_compare_instruction_versions(case, a, ctx):
    snaps = _snapshots(case)
    if snaps is None:
        return missing("context_snapshots", "instruction comparison", "context snapshots")
    out, prev = [], None
    for s in snaps:
        if prev is not None:
            before = {c["id"]: c for c in prev.get("constraints", [])}
            after = {c["id"]: c for c in s.get("constraints", [])}
            changed = [k for k in set(before) & set(after) if before[k]["text"] != after[k]["text"]]
            out.append({"from": prev["snapshot_id"], "to": s["snapshot_id"], "kind": s["kind"],
                        "lost": sorted(set(before) - set(after)),
                        "added": sorted(set(after) - set(before)), "changed": sorted(changed),
                        "version_changed": prev.get("instructions_version") != s.get("instructions_version")})
        prev = s
    return {"comparisons": out}


def t_inspect_context_references(case, a, ctx):
    """Did any action after a snapshot breach a constraint that snapshot no longer carried?"""
    snaps = _snapshots(case)
    if snaps is None:
        return missing("context_snapshots", "constraint adherence", "context snapshots")
    rows = sorted(case.session.rows if case.session else [], key=lambda r: r.get("occurred_at") or "")
    every = {c["id"]: c for s in snaps for c in s.get("constraints", [])}
    breaches = []
    for cid, c in every.items():
        pref = c.get("forbid_path_prefix")
        if not pref:
            continue
        for r in rows:
            fp = r.get("file_path") or ""
            if fp.startswith(pref) and (r.get("tool_name") in ("Write", "Edit", "MultiEdit")
                                        or r.get("observable_type") in ("file_modify", "file_create")):
                active = [s for s in snaps if s["at"] <= (r.get("occurred_at") or "")]
                carried = bool(active) and any(x["id"] == cid for x in active[-1].get("constraints", []))
                breaches.append({"constraint": cid, "ref": f"observable:{r['observable_id']}",
                                 "at": r.get("occurred_at"), "constraint_in_active_context": carried})
    return {"breaches": breaches, "constraints_checked": len(every)}


def t_verify_context_provenance(case, a, ctx):
    snaps = _snapshots(case)
    if snaps is None:
        return missing("context_snapshots", "provenance", "context snapshots")
    out = []
    for s in snaps:
        rec = (s.get("provenance") or {}).get("hash")
        calc = _sha(s.get("constraints", []))
        out.append({"snapshot_id": s["snapshot_id"], "source": (s.get("provenance") or {}).get("source"),
                    "hash_present": bool(rec), "hash_matches": rec == calc if rec else None})
    return {"snapshots": out}


# ---- Incident Response
def _rows_by_ref(case, refs):
    ids = {r.split(":", 1)[1] for r in refs if str(r).startswith("observable:")}
    rows = case.session.rows if case.session else []
    return [r for r in rows if r.get("observable_id") in ids]


def t_get_incident_evidence(case, a, ctx):
    refs = list(a.get("refs", []))
    return {"rows": [_safe(r) for r in _rows_by_ref(case, refs)], "requested": len(refs)}


def t_get_verified_findings(case, a, ctx):
    up = ctx.get("upstream") or {}
    return {"findings": [{"cop": k, "label": v["label"], "status": v["status"],
                          "verified": v["verified"], "lineage": v.get("lineage", []),
                          "finding_id": v.get("finding_id")}
                         for k, v in up.items() if v.get("verified")],
            "unverified_withheld": sorted(k for k, v in up.items() if not v.get("verified"))}


def t_check_exposure(case, a, ctx):
    rows = case.session.rows if case.session else []
    sec = [r for r in rows if r.get("secret_detected")]
    sens = [r for r in rows if (r.get("sensitivity_tier") or 0) >= 3]
    outbound = [r for r in rows if r.get("tool_name") in ("WebFetch", "WebSearch")
                or re.search(r"\b(curl|wget|scp|nc)\b", str(r.get("command") or ""))]
    after_secret = []
    if sec:
        t0 = min(r.get("occurred_at") or "" for r in sec)
        after_secret = [r for r in outbound if (r.get("occurred_at") or "") >= t0]
    return {"secret_rows": [f"observable:{r['observable_id']}" for r in sec],
            "sensitive_rows": [f"observable:{r['observable_id']}" for r in sens],
            "outbound_after_secret": [f"observable:{r['observable_id']}" for r in after_secret],
            "outbound_calls": len(outbound)}


def t_check_approval_requirements(case, a, ctx):
    ver = latest_policy(case)
    if ver is None:
        return missing("policy_store", "which operations need approval",
                       "an approved, versioned policy store")
    rows = case.session.rows if case.session else []
    ops = [(r, _operation(r)) for r in rows if _operation(r)]
    req = []
    for r, op in ops:
        ev = evaluate_operation(ver, op, case.agent_scope)
        if ev["decision"] in ("REQUIRE_APPROVAL", "DENY"):
            req.append({"ref": f"observable:{r['observable_id']}", **op, **ev})
    asked = any(r.get("observable_type") == "permission_request" for r in rows)
    return {"policy_version": ver.get("version"), "operations_needing_approval": req,
            "approval_seen_in_session": asked}


def t_get_incident_outcomes(case, a, ctx):
    return _memory_lookup(ctx, "incident_outcome")


# ---- Classification & Tagging
def t_query_taxonomy(case, a, ctx):
    tax = case.artifact("taxonomy")
    if not tax or not tax.get("approved"):
        return missing("taxonomy", "classification_tagging: approved family definitions",
                       "an approved, versioned governance-event taxonomy")
    return {"version": tax["version"], "families": tax["families"]}


def t_compare_validated_examples(case, a, ctx):
    return _memory_lookup(ctx, "classification_example")


def t_inspect_event_metadata(case, a, ctx):
    refs = list(a.get("refs", []))
    rows = _rows_by_ref(case, refs) if refs else [
        r for r in (case.session.rows if case.session else [])
        if r.get("secret_detected") or (r.get("sensitivity_tier") or 0) >= 3
        or r.get("outside_workspace") or r.get("policy_rule_matched")]
    return {"rows": [_safe(r) for r in rows[:10]]}


# ---- Risk Mitigation
def t_read_risk_register(case, a, ctx):
    reg = case.artifact("risk_register")
    if not reg:
        return missing("risk_register", "risk_mitigation: impact and thresholds",
                       "an approved risk register with impact scores and EMERGING/MATERIAL thresholds")
    return reg


def t_evaluate_control_prerequisites(case, a, ctx):
    reg = case.artifact("risk_register")
    if not reg:
        return missing("risk_register", "controls", "a risk register listing controls")
    rows = case.session.rows if case.session else []
    collectors = case.session.collectors if case.session else set()
    out = []
    for risk in reg.get("risks", []):
        for c in risk.get("controls", []):
            pre = c.get("prerequisite", {})
            ok = True
            if pre.get("collector"):
                ok = ok and pre["collector"] in collectors
            if pre.get("approval_events"):
                ok = ok and any(r.get("observable_type") == "permission_request" for r in rows)
            out.append({"risk": risk["id"], "control": c["id"], "prerequisites_met": ok,
                        "reversible": c.get("reversible")})
    return {"controls": out}


def t_get_mitigation_outcomes(case, a, ctx):
    return _memory_lookup(ctx, "mitigation_outcome")


IRREVERSIBLE = re.compile(r"(\brm\s+-rf\b|git push (-f|--force)|\bdrop\s+table\b|\btruncate\b)", re.I)


def t_calculate_risk_components(case, a, ctx):
    from .rules import independent_upstream
    up = ctx.get("upstream") or {}
    own = set(BY_KEY[ctx["cop"]].metric_keys)
    indep = independent_upstream(own, up)
    rows = case.session.rows if case.session else []
    irrev = [r for r in rows if IRREVERSIBLE.search(str(r.get("command") or ""))]
    reverted = [r for r in rows if r.get("reverted_to_earlier")]
    sens = max([r.get("sensitivity_tier") or 0 for r in rows] or [0])
    return {"independent_verified_signals": [u["cop"] for u in indep["independent"]],
            "correlated_not_counted": [u["cop"] for u in indep["correlated"]],
            "max_sensitivity_tier": sens,
            "irreversible_operations": [f"observable:{r['observable_id']}" for r in irrev],
            "reverted_changes": len(reverted)}


# ---- Governance Intelligence
def _decisions(case):
    st = case.decision_store
    return st.select("cop_decisions") if st is not None else None


def t_query_aggregate_report(case, a, ctx):
    rows = _decisions(case)
    if rows is None:
        return missing("decision_store", "governance_intelligence: decision trends",
                       "cop_decisions history across sessions")
    days = int(a.get("window_days", 30))
    from datetime import datetime, timedelta
    end = datetime.fromisoformat(case.computed_at.replace("Z", "+00:00"))
    lo = (end - timedelta(days=days)).isoformat()
    rows = [r for r in rows if (r["decided_at"] or "") >= lo and r["tenant_id"] in (None, case.tenant_id)]
    by = Counter((r["cop"], r["interpretation"]) for r in rows)
    sessions = defaultdict(set)
    for r in rows:
        sessions[(r["cop"], r["interpretation"])].add(r["session_id"])
    return {"window_days": days, "decisions": len(rows),
            "by_cop_label": [{"cop": c, "label": l, "count": n, "sessions": len(sessions[(c, l)])}
                             for (c, l), n in by.most_common(25)]}


def t_compare_time_windows(case, a, ctx):
    rows = _decisions(case)
    if rows is None:
        return missing("decision_store", "trend comparison", "cop_decisions history")
    split = a.get("split_at") or case.computed_at
    before = Counter((r["cop"], r["interpretation"]) for r in rows if (r["decided_at"] or "") < split)
    after = Counter((r["cop"], r["interpretation"]) for r in rows if (r["decided_at"] or "") >= split)
    keys = sorted(set(before) | set(after))
    return {"split_at": split, "changes": [{"cop": c, "label": l, "before": before[(c, l)],
                                            "after": after[(c, l)]} for c, l in keys]}


def t_get_confirmed_incidents(case, a, ctx):
    return _memory_lookup(ctx, "incident_outcome")


def t_get_previous_reviews(case, a, ctx):
    return _memory_lookup(ctx, "governance_review")


# ---- Workload Optimization
def t_calculate_usage_rates(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "usage", "the case session")
    mins = s.minutes()
    cost = sum(float(r.get("cost_usd") or 0) for r in s.rows)
    return {"tokens": s.tokens, "actions": len(s.actions), "minutes": round(mins, 2),
            "tokens_per_action": round(s.tokens / len(s.actions), 1) if s.actions else None,
            "actions_per_minute": round(len(s.actions) / mins, 2) if mins else None,
            "recorded_cost_usd": round(cost, 4) if cost else None}


def t_compare_task_classes(case, a, ctx):
    tc = case.artifact("task_classes")
    if not tc:
        return missing("task_classes", "workload_optimization: like-for-like comparison",
                       "a task class per session (e.g. bugfix, feature, refactor)")
    mine = tc.get(case.session_id)
    peers = [x for x in case.other_sessions() if tc.get(x.id) == mine]
    toks = [x.tokens for x in peers if x.tokens]
    acts = [len(x.actions) for x in peers]
    return {"task_class": mine, "peers": len(peers),
            "peer_median_tokens": statistics.median(toks) if toks else None,
            "peer_median_actions": statistics.median(acts) if acts else None,
            "current_tokens": case.session.tokens, "current_actions": len(case.session.actions)}


def t_inspect_repeated_operations(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "repetition", "the case session")
    reads = Counter(r.get("file_path") for r in s.actions if r.get("tool_name") == "Read" and r.get("file_path"))
    cmds = Counter(str(r.get("command")).strip() for r in s.rows if r.get("command"))
    return {"repeated_reads": {k: n for k, n in reads.items() if n > 1},
            "repeated_commands": {k: n for k, n in cmds.items() if n > 1},
            "redundant_read_calls": sum(n - 1 for n in reads.values() if n > 1),
            "redundant_command_calls": sum(n - 1 for n in cmds.values() if n > 1)}


def t_evaluate_previous_changes(case, a, ctx):
    return _memory_lookup(ctx, "optimisation_outcome")


# ---- Knowledge & Policy Management / Policy Enforcement
def t_get_policy_versions(case, a, ctx):
    ps = case.artifact("policy_store")
    if not ps:
        return missing("policy_store", "policy versions",
                       "a versioned policy store with approval records")
    return {"versions": [{"version": v["version"], "effective": v.get("effective"),
                          "approved_by": v.get("approved_by"), "rules": len(v.get("rules", []))}
                         for v in ps["versions"]],
            "in_force": (latest_policy(case) or {}).get("version")}


def t_compare_policy_documents(case, a, ctx):
    ps = case.artifact("policy_store")
    if not ps:
        return missing("policy_store", "policy comparison", "a versioned policy store")
    by = {v["version"]: v for v in ps["versions"]}
    va = by.get(a.get("version_a")) or (ps["versions"][-2] if len(ps["versions"]) > 1 else None)
    vb = by.get(a.get("version_b")) or ps["versions"][-1]
    conflicts = []
    rules = vb.get("rules", [])
    for i, r1 in enumerate(rules):
        for r2 in rules[i + 1:]:
            if r1["action"] == r2["action"] and r1["effect"] != r2["effect"] and (
                    fnmatch.fnmatch(r1["resource_pattern"], r2["resource_pattern"])
                    or fnmatch.fnmatch(r2["resource_pattern"], r1["resource_pattern"])):
                conflicts.append({"rules": [r1["id"], r2["id"]], "action": r1["action"],
                                  "effects": [r1["effect"], r2["effect"]],
                                  "patterns": [r1["resource_pattern"], r2["resource_pattern"]],
                                  "precedence_declared": bool(vb.get("precedence"))})
    diff = None
    if va:
        ra = {r["id"]: r for r in va.get("rules", [])}
        rb = {r["id"]: r for r in rules}
        diff = {"from": va["version"], "to": vb["version"],
                "removed": sorted(set(ra) - set(rb)), "added": sorted(set(rb) - set(ra)),
                "changed": sorted(k for k in set(ra) & set(rb) if ra[k] != rb[k])}
    return {"version": vb["version"], "conflicts": conflicts, "diff": diff}


def t_inspect_rule_dependencies(case, a, ctx):
    ps = case.artifact("policy_store")
    if not ps:
        return missing("policy_store", "rule dependencies", "a versioned policy store")
    v = latest_policy(case) or ps["versions"][-1]
    ids = {r["id"] for r in v.get("rules", [])}
    broken = [{"rule": k, "depends_on": d} for k, deps in (v.get("dependencies") or {}).items()
              for d in deps if d not in ids]
    return {"version": v["version"], "broken_dependencies": broken}


def t_check_authorisation_records(case, a, ctx):
    ps = case.artifact("policy_store")
    if not ps:
        return missing("policy_store", "authorisation records", "a versioned policy store")
    auth = {x["version"] for x in ps.get("authorisations", [])}
    return {"versions": [{"version": v["version"], "approved_by": v.get("approved_by"),
                          "authorisation_record": v["version"] in auth} for v in ps["versions"]]}


def t_evaluate_operation_against_policy(case, a, ctx):
    ver = latest_policy(case)
    if ver is None:
        return missing("policy_store", "policy_enforcement: rule evaluation",
                       "an approved, versioned policy store")
    op = {"action": a["action"], "resource": a.get("resource", "")}
    return {"operation": op, **evaluate_operation(ver, op, case.agent_scope)}


def t_evaluate_session_operations(case, a, ctx):
    ver = latest_policy(case)
    if ver is None:
        return missing("policy_store", "policy_enforcement: rule evaluation",
                       "an approved, versioned policy store")
    rows = case.session.rows if case.session else []
    out = []
    for r in rows:
        op = _operation(r)
        if not op or r.get("observable_type") in ("tool_result", "permission_request"):
            continue
        ev = evaluate_operation(ver, op, case.agent_scope)
        out.append({"ref": f"observable:{r['observable_id']}", **op, **ev,
                    "blocked_by_hook": bool(r.get("policy_rule_matched"))})
    proposed = []
    for p in case.artifact("proposed_operations") or []:
        ev = evaluate_operation(ver, p, case.agent_scope)
        proposed.append({**p, **ev})
    approved = any(r.get("permission_decision") in ("allow", "approved") for r in rows)
    return {"policy_version": ver["version"], "observed": out, "proposed": proposed,
            "approval_recorded": approved}


def t_check_entitlements(case, a, ctx):
    ent = case.artifact("entitlements")
    if not ent:
        return missing("entitlements", "policy_enforcement: entitlement mismatch",
                       "an entitlement record per agent or operator")
    mine = ent.get(case.agent_scope) or ent.get("*") or {}
    rows = case.session.rows if case.session else []
    servers = {r.get("mcp_server") for r in rows if r.get("mcp_server")}
    return {"entitled_mcp_servers": mine.get("mcp_servers", []),
            "used_not_entitled": sorted(s for s in servers if s not in mine.get("mcp_servers", []))}


# ---- Governance Architecture
def t_inspect_cop_catalogue(case, a, ctx):
    return {"cops": [{"key": s.key, "metric_keys": list(s.metric_keys),
                      "missing_metrics": list(s.missing_metrics),
                      "consumes": list(s.consumes_decisions_from)} for s in SPECS],
            "source": "collector/cops/specs.py SPECS"}


def t_query_coverage_matrix(case, a, ctx):
    import signal_metrics
    readers = defaultdict(list)
    for s in SPECS:
        for m in s.metric_keys:
            readers[m].append(s.key)
    all_metrics = list(signal_metrics.METRIC_IDS) + ["telemetry.unknown_share"]
    unread = [m for m in all_metrics if not readers.get(m)]
    # observable fields and types present in the case that no metric reads
    read_fields = {f for _, _, _, _, fields, _ in signal_metrics.METRICS for f in fields}
    read_fields |= {"observable_id", "session_id", "tool", "collector", "contributing",
                    "operator_email", "operator_username", "workspace", "turn_id",
                    "occurred_at", "tool_use_id", "observable_type", "cwd", "total_tokens",
                    "tool_duration_ms", "hung_seconds", "success", "cost_usd", "tool_version",
                    "model", "agent_id", "sequence_num", "ingested_at", "content_hash"}
    known_types = {"session_start", "session_end", "user_prompt", "tool_request", "tool_result",
                   "permission_request", "subagent_start", "subagent_stop", "command",
                   "file_modify", "file_create", "file_delete", "file_rename", "mcp_call",
                   "transcript_appended", "commit"}
    rows = case.session.rows if case.session else []
    fields = Counter(k for r in rows for k, v in r.items() if v not in (None, "", [], {}))
    types = Counter(r.get("observable_type") for r in rows)
    return {"metrics_read_by_no_cop": unread,
            "unowned_fields": sorted(k for k in fields if k not in read_fields),
            "unhandled_observable_types": sorted(t for t in types if t and t not in known_types),
            "metric_readers": {m: readers.get(m, []) for m in all_metrics}}


def t_validate_cop_relationships(case, a, ctx):
    return {"edge_differences": edge_report(), "consumes": {k: list(v) for k, v in CONSUMES.items()}}


def t_get_confirmed_coverage_gaps(case, a, ctx):
    return _memory_lookup(ctx, "coverage_gap")


# ---- deterministic COP tools
def t_get_collector_breakdown(case, a, ctx):
    rows = case.session.rows if case.session else []
    return {"rows_by_collector": dict(Counter(r.get("collector") for r in rows))}


def t_check_collector_expectations(case, a, ctx):
    s = case.session
    want = set(case.cfg["expected_collectors"].get((s.tool if s else "") or "", []))
    got = s.collectors if s else set()
    return {"tool": s.tool if s else None, "expected": sorted(want), "present": sorted(got),
            "missing": sorted(want - got)}


def t_build_interaction_graph(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "interaction graph", "the case session")
    edges = Counter()
    for r in s.rows:
        src = r.get("agent_id") or "main"
        if r.get("mcp_server"):
            edges[(src, f"mcp:{r['mcp_server']}")] += 1
        elif r.get("tool_name") and r.get("observable_type") not in ("tool_result", "permission_request"):
            edges[(src, f"tool:{r['tool_name']}")] += 1
    for lane in s.trace["lanes"]:
        if lane != "main":
            edges[("main", f"agent:{lane}")] += 1
    return {"edges": [{"from": a_, "to": b, "count": n} for (a_, b), n in sorted(edges.items())]}


def t_compare_known_routes(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "routes", "the case session")
    mine = {f"mcp:{r['mcp_server']}" for r in s.rows if r.get("mcp_server")}
    mine |= {f"tool:{r['tool_name']}" for r in s.rows if r.get("tool_name")}
    seen = set()
    for x in case.earlier_sessions():
        if x.operator == s.operator:
            seen |= {f"mcp:{r['mcp_server']}" for r in x.rows if r.get("mcp_server")}
            seen |= {f"tool:{r['tool_name']}" for r in x.rows if r.get("tool_name")}
    mem = _memory_lookup(ctx, "route_record") if ctx.get("memory") else {"established": []}
    for rec in mem.get("established", []):
        seen |= set(rec["payload"].get("routes", []))
    return {"new_routes": sorted(mine - seen), "known_routes": len(seen & mine),
            "history_sessions": len([x for x in case.earlier_sessions() if x.operator == s.operator])}


def t_get_agent_registry(case, a, ctx):
    reg = case.artifact("agent_registry")
    if not reg:
        return missing("agent_registry", "agent_lifecycle_management: approved configuration",
                       "an agent registry with approved versions, models and MCP servers")
    entry = next((x for x in reg.get("agents", []) if x["agent"] == case.agent_scope), None)
    return {"registered": entry is not None, "entry": entry,
            "approved_changes": [c for c in reg.get("approved_changes", []) if c["agent"] == case.agent_scope]}


def t_detect_configuration_changes(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "configuration", "the case session")
    def cfg(rows):
        return {"tool_version": sorted({r["tool_version"] for r in rows if r.get("tool_version")}),
                "model": sorted({r["model"] for r in rows if r.get("model")}),
                "mcp_servers": sorted({r["mcp_server"] for r in rows if r.get("mcp_server")}),
                "machine": sorted({r["machine_id"] for r in rows if r.get("machine_id")})}
    now = cfg(s.rows)
    prev_rows = [r for x in case.earlier_sessions() if x.operator == s.operator for r in x.rows]
    before = cfg(prev_rows)
    changes = {k: sorted(set(now[k]) - set(before[k])) for k in now if prev_rows}
    return {"current": now, "previous": before if prev_rows else None,
            "new_values": {k: v for k, v in changes.items() if v},
            "history_sessions": len([x for x in case.earlier_sessions() if x.operator == s.operator])}


def t_compute_consumption(case, a, ctx):
    r = t_calculate_usage_rates(case, a, ctx)
    s = case.session
    durs = [float(x.get("duration_ms")) for x in s.steps if x.get("duration_ms")] if s else []
    r["step_latency_ms"] = {"median": statistics.median(durs), "max": max(durs)} if durs else None
    r["burn_tokens_per_minute"] = round(s.tokens / r["minutes"], 1) if s and r.get("minutes") else None
    return r


def t_get_quota(case, a, ctx):
    q = case.artifact("quotas")
    if not q:
        return missing("budget_or_quota", "resource_management: UNAUTHORIZED",
                       "a token or cost quota per operator and period")
    mine = q.get(case.agent_scope) or q.get("*")
    if not mine:
        return missing("budget_or_quota", "resource_management: UNAUTHORIZED",
                       f"no quota entry for {case.agent_scope}")
    return mine


def t_compute_sequence_statistics(case, a, ctx):
    return {**t_analyse_tool_call_sequence(case, a, ctx),
            "stalls": [f"observable:{r['observable_id']}" for r in (case.session.rows if case.session else [])
                       if r.get("hung_seconds")]}


def t_compare_behaviour_history(case, a, ctx):
    out = {}
    for mid in ("insights.repetition", "insights.retry", "insights.failed"):
        vals = [float(e["value"]) for e in case.metric_history(mid)
                if e["value"] is not None and e["state"] not in ("UNKNOWN", "NOT_APPLICABLE")]
        out[mid] = {"n": len(vals), "median": statistics.median(vals) if vals else None,
                    "max": max(vals) if vals else None}
    mem = _memory_lookup(ctx, "behaviour_baseline") if ctx.get("memory") else None
    return {"history": out, "validated_baselines": (mem or {}).get("established", [])}


def t_get_recovery_records(case, a, ctx):
    rec = case.artifact("recovery_records")
    if rec is None:
        return missing("recovery_records", "recovery_resilience: restoration outcome",
                       "a record per recovery: checkpoint, restore time, post-restore checks")
    return {"records": rec}


def t_validate_checkpoints(case, a, ctx):
    rec = case.artifact("recovery_records")
    if rec is None:
        return missing("recovery_records", "checkpoint validity", "recovery records")
    out = []
    for r in rec:
        content = r.get("checkpoint_content")
        out.append({"checkpoint_id": r.get("checkpoint_id"),
                    "hash_matches": (_sha(content) == r.get("checkpoint_hash")) if content is not None else None,
                    "checks_passed": sum(1 for c in r.get("post_restore_checks", []) if c.get("passed")),
                    "checks_total": len(r.get("post_restore_checks", []))})
    return {"checkpoints": out}


def t_reconstruct_trace(case, a, ctx):
    s = case.session
    if not s:
        return missing("session", "trace", "the case session")
    acts = [x for x in s.steps if x["step"] not in ("START", "END")]
    subs = {k: v for k, v in s.trace["lanes"].items() if k != "main"}
    return {"steps": len(acts), "steps_without_turn": sum(1 for x in acts if not x.get("turn_id")),
            "lanes": len(subs),
            "lanes_without_parent": sorted(k for k, v in subs.items() if not (v and v[0].get("parent_step_id"))),
            "unattributed_effects": [x.get("observable_id") or x.get("effect_id") for x in s.effects
                                     if x.get("attributed_to") == "unaccounted"][:10]}


def t_check_decision_linkage(case, a, ctx):
    """Do the evidence refs behind upstream findings resolve to rows in this case?"""
    up = ctx.get("upstream") or {}
    ids = {r.get("observable_id") for r in (case.session.rows if case.session else [])}
    steps = {x["step_id"] for x in (case.session.steps if case.session else [])}
    out = []
    for cop, f in up.items():
        refs = sorted({ref for c in f.get("claims", []) for ref in c.get("evidence_refs", [])})
        ok = [r for r in refs if (r.startswith("observable:") and r.split(":", 1)[1] in ids)
              or (r.startswith("step:") and r.split(":", 1)[1] in steps)]
        out.append({"cop": cop, "refs": len(refs), "resolvable": len(ok),
                    "unresolvable": [r for r in refs if r not in ok][:5]})
    return {"decisions": out}


# ---------------------------------------------------------------- registry

def _arr(name, max_len):
    return {"type": "array", "max": max_len}


TOOLS = {
    # name: (fn, schema {arg: spec})
    "get_metric_rows": (t_get_metric_rows, {"metric_ids": _arr("metric_ids", 25)}),
    "get_task_milestones": (t_get_task_milestones, {}),
    "analyse_execution_transitions": (t_analyse_execution_transitions, {}),
    "inspect_delegation_traces": (t_inspect_delegation_traces, {}),
    "compare_workflow_failures": (t_compare_workflow_failures, {}),
    "retrieve_trace_window": (t_retrieve_trace_window, {
        "lane": {"type": "str"}, "start": {"type": "int", "min": 0},
        "max_steps": {"type": "int", "min": 1, "max": MAX_TRACE_STEPS},
        "session_id": {"type": "scope_session"}}),
    "compare_versioned_baseline": (t_compare_versioned_baseline, {
        "metric_id": {"type": "metric", "required": True}}),
    "find_similar_validated_cases": (t_find_similar_validated_cases, {}),
    "calculate_deviation": (t_calculate_deviation, {
        "quantity": {"type": "enum", "values": ("tokens", "actions", "failures"), "required": True}}),
    "analyse_tool_call_sequence": (t_analyse_tool_call_sequence, {}),
    "get_context_snapshots": (t_get_context_snapshots, {}),
    "compare_instruction_versions": (t_compare_instruction_versions, {}),
    "inspect_context_references": (t_inspect_context_references, {}),
    "verify_context_provenance": (t_verify_context_provenance, {}),
    "get_incident_evidence": (t_get_incident_evidence, {"refs": _arr("refs", 10)}),
    "get_verified_findings": (t_get_verified_findings, {}),
    "check_exposure": (t_check_exposure, {}),
    "check_approval_requirements": (t_check_approval_requirements, {}),
    "get_incident_outcomes": (t_get_incident_outcomes, {}),
    "query_taxonomy": (t_query_taxonomy, {}),
    "compare_validated_examples": (t_compare_validated_examples, {}),
    "inspect_event_metadata": (t_inspect_event_metadata, {"refs": _arr("refs", 10)}),
    "read_risk_register": (t_read_risk_register, {}),
    "evaluate_control_prerequisites": (t_evaluate_control_prerequisites, {}),
    "get_mitigation_outcomes": (t_get_mitigation_outcomes, {}),
    "calculate_risk_components": (t_calculate_risk_components, {}),
    "query_aggregate_report": (t_query_aggregate_report, {
        "window_days": {"type": "int", "min": 1, "max": 90}, "tenant_id": {"type": "scope_tenant"}}),
    "compare_time_windows": (t_compare_time_windows, {"split_at": {"type": "str"}}),
    "get_confirmed_incidents": (t_get_confirmed_incidents, {}),
    "get_previous_reviews": (t_get_previous_reviews, {}),
    "calculate_usage_rates": (t_calculate_usage_rates, {}),
    "compare_task_classes": (t_compare_task_classes, {}),
    "inspect_repeated_operations": (t_inspect_repeated_operations, {}),
    "evaluate_previous_changes": (t_evaluate_previous_changes, {}),
    "get_policy_versions": (t_get_policy_versions, {}),
    "compare_policy_documents": (t_compare_policy_documents, {
        "version_a": {"type": "str"}, "version_b": {"type": "str"}}),
    "inspect_rule_dependencies": (t_inspect_rule_dependencies, {}),
    "check_authorisation_records": (t_check_authorisation_records, {}),
    "evaluate_operation_against_policy": (t_evaluate_operation_against_policy, {
        "action": {"type": "enum", "values": ("read", "write", "exec", "mcp", "tool"), "required": True},
        "resource": {"type": "str"}}),
    "evaluate_session_operations": (t_evaluate_session_operations, {}),
    "check_entitlements": (t_check_entitlements, {}),
    "inspect_cop_catalogue": (t_inspect_cop_catalogue, {}),
    "query_coverage_matrix": (t_query_coverage_matrix, {}),
    "validate_cop_relationships": (t_validate_cop_relationships, {}),
    "get_confirmed_coverage_gaps": (t_get_confirmed_coverage_gaps, {}),
    "get_collector_breakdown": (t_get_collector_breakdown, {}),
    "check_collector_expectations": (t_check_collector_expectations, {}),
    "build_interaction_graph": (t_build_interaction_graph, {}),
    "compare_known_routes": (t_compare_known_routes, {}),
    "get_agent_registry": (t_get_agent_registry, {}),
    "detect_configuration_changes": (t_detect_configuration_changes, {}),
    "compute_consumption": (t_compute_consumption, {}),
    "get_quota": (t_get_quota, {}),
    "compute_sequence_statistics": (t_compute_sequence_statistics, {}),
    "compare_behaviour_history": (t_compare_behaviour_history, {}),
    "get_recovery_records": (t_get_recovery_records, {}),
    "validate_checkpoints": (t_validate_checkpoints, {}),
    "reconstruct_trace": (t_reconstruct_trace, {}),
    "check_decision_linkage": (t_check_decision_linkage, {}),
}


class ToolGateway:
    def __init__(self, case, allowlists, memory=None):
        """allowlists: {cop: tuple(tool names)}, from profiles. Frozen copies are
        taken here, so nothing a COP does at run time can widen them."""
        self.case = case
        self.allow = {k: frozenset(v) for k, v in allowlists.items()}
        self.memory = memory
        self.log = []

    def allowlist(self, cop):
        return sorted(self.allow.get(cop, ()))

    def _check_args(self, cop, tool, args):
        schema = TOOLS[tool][1]
        if not isinstance(args, dict):
            return "invalid_arguments", "arguments must be an object"
        extra = set(args) - set(schema)
        if extra:
            return "invalid_arguments", f"unknown arguments {sorted(extra)}"
        for k, spec in schema.items():
            v = args.get(k)
            if v is None:
                if spec.get("required"):
                    return "invalid_arguments", f"{k} is required"
                continue
            t = spec["type"]
            if t == "int":
                if not isinstance(v, int) or isinstance(v, bool):
                    return "invalid_arguments", f"{k} must be an integer"
                if "min" in spec and v < spec["min"] or "max" in spec and v > spec["max"]:
                    return "out_of_bounds", f"{k}={v} outside [{spec.get('min')}, {spec.get('max')}]"
            elif t == "array":
                if not isinstance(v, list):
                    return "invalid_arguments", f"{k} must be a list"
                if len(v) > spec["max"]:
                    return "out_of_bounds", f"{k} has {len(v)} items, limit {spec['max']}"
            elif t == "enum":
                if v not in spec["values"]:
                    return "invalid_arguments", f"{k} must be one of {spec['values']}"
            elif t == "metric":
                if v not in BY_KEY[cop].metric_keys:
                    return "out_of_scope", f"{v} is not a metric {cop} reads"
            elif t == "scope_session":
                if v != self.case.session_id:
                    return "out_of_scope", f"session {v} is outside this case"
            elif t == "scope_tenant":
                if v != self.case.tenant_id:
                    return "out_of_scope", f"tenant {v} is outside this case"
            elif t == "str":
                if not isinstance(v, str):
                    return "invalid_arguments", f"{k} must be a string"
        return None, ""

    def request(self, cop, tool, args, requested_by, budget, ctx):
        """budget: dict with 'tool_calls' used and 'max_tool_calls'. Mutated."""
        t0 = time.perf_counter()
        n = len([x for x in self.log if x["cop"] == cop and x["case_id"] == self.case.case_id])
        call_id = stable_id(self.case.case_id, cop, n, tool)
        rec = {"tool_call_id": call_id, "cop": cop, "case_id": self.case.case_id, "tool": tool,
               "args": args, "requested_by": requested_by, "status": None,
               "rejection_reason": None, "result_summary": None, "injection_flags": [],
               "latency_ms": 0.0}
        reason = None
        if tool not in TOOLS:
            reason = ("unknown_tool", f"no tool named {tool}")
        elif tool not in self.allow.get(cop, ()):
            reason = ("not_in_allowlist", f"{tool} is not on {cop}'s allowlist")
        else:
            code, why = self._check_args(cop, tool, args or {})
            if code:
                reason = (code, why)
            elif budget["tool_calls"] >= budget["max_tool_calls"]:
                reason = ("budget_exhausted", f"{budget['max_tool_calls']} tool calls used")
        if reason:
            rec.update(status="REJECTED", rejection_reason=f"{reason[0]}: {reason[1]}")
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            self.log.append(rec)
            return {**rec, "result": None}
        budget["tool_calls"] += 1
        try:
            result = TOOLS[tool][0](self.case, args or {}, {**ctx, "cop": cop, "memory": self.memory})
            rec["status"] = "MISSING_DEPENDENCY" if "missing_dependency" in result else "OK"
        except Exception as e:                       # a broken tool is a fact, not a crash
            result = {"error": f"{type(e).__name__}: {e}"[:300]}
            rec["status"] = "ERROR"
        rec["injection_flags"] = scan(result)
        rec["result_summary"] = {"keys": sorted(result)[:20], "bytes": len(json.dumps(result, default=str))}
        rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        self.log.append(rec)
        return {**rec, "result": result}
