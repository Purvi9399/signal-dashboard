"""
COPS dashboard export: the repeatable data behind the standalone COPS dashboard prototype.

    python3 -m cops.dashboard_export --out ../cops-dashboard/public/data     # from collector/

Writes three files, each with a `contract` version and the commit and sha256 of every
source file it read:

    network.json     COPs, Signal metrics, COP-to-metric relationships and COP-to-COP edges,
                     generated from the specifications and runtime code (contract cops-network/1)
    decisions.json   one record per recorded COP decision, from the 54-case bench, the
                     operational scenario traces, the local gateway recordings and the Harbor
                     traces (contract cops-decisions/1)
    retrieval.json   the evidence-access audit and the Evidence Query Service catalogue
                     (contract cops-retrieval/1)

Rules this export follows:
    - nothing is written by hand: every value comes from a source file or from code, and
      each record names the file and path it came from
    - where a source has no value the field is {"unavailable": "<why>"}; where a semantic
      contract does not exist the field is {"undefined": "<missing contract>"}
    - no model is called and no COP is re-run; recorded traces are read as they are
    - relationship types are derived by fixed rules, listed in network.json
      `relationship_rules`, never from COP or metric names
"""

import argparse
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # collector/
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_metrics                                                         # noqa: E402
from cops import specs as SP                                                  # noqa: E402
from cops.authority import TAXONOMY                                           # noqa: E402
from cops.runtime import support as SU, triage as TR, verifiers as VF         # noqa: E402
from cops.runtime import rules as RU, referrals as RF, requirements as RQ     # noqa: E402
from cops.runtime.contracts import CONTRACT_VERSION                           # noqa: E402
from cops.runtime.persistence import decision_id as make_decision_id          # noqa: E402
from cops.runtime.profiles import build_profiles, allowlists                  # noqa: E402
from cops.runtime.evidence_query import ASSESSMENT, QUERY_TYPES, DATA_ACCESS_AUDIT  # noqa: E402

NETWORK_CONTRACT = "cops-network/1"
DECISIONS_CONTRACT = "cops-decisions/1"
RETRIEVAL_CONTRACT = "cops-retrieval/1"
ACTIVATION_CONTRACT = "cops-activation/1"

EVAL = os.path.join(REPO, "docs", "cops", "evaluation")
SOURCES = {
    "mapping_doc": os.path.join(REPO, "docs", "cops", "metric_cop_mappings.json"),
    "topology_doc": os.path.join(REPO, "docs", "cops", "cops_topology.json"),
    "metrics_config": os.path.join(HERE, "metrics_config.json"),
    "bench": os.path.join(EVAL, "test_bench_results.json"),
}
SCENARIO_DIR = os.path.join(EVAL, "scenario")
GATEWAY_DIR = os.path.join(EVAL, "gateway")
HARBOR_DIR = os.path.join(EVAL, "harbor")
ACTIVATION_DIR = os.path.join(EVAL, "activation")
HIERARCHY = os.path.join(REPO, "docs", "cops", "cop_hierarchy.json")

ATTENTION = ("WARNING", "EXCEPTION", "CRITICAL")
ACTION_WORDS = ("ALERT", "WATCH", "REQUEST_APPROVAL", "REQUEST_CONTEXT", "PASS_TO_COP", "BLOCK", "INSTRUCT",
                "ESCALATE")


# ====================================================================== helpers

def rel(p):
    return os.path.relpath(p, REPO)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_head():
    try:
        return subprocess.check_output(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None


def code_ref(obj):
    """path:line of a function or class."""
    try:
        f = inspect.getsourcefile(obj)
        line = inspect.getsourcelines(obj)[1]
        return f"{rel(f)}:{line}"
    except (TypeError, OSError):
        return None


def line_of(path, needle):
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if needle in line:
                return f"{rel(path)}:{n}"
    return rel(path)


def U(why):
    return {"unavailable": why}


def UNDEF(contract):
    return {"undefined": contract}


def load(p):
    with open(p) as f:
        return json.load(f)


def src(path, pointer):
    return {"file": rel(path), "pointer": pointer}


# ====================================================================== network

def _mc01_metrics():
    text = inspect.getsource(VF._raw)
    return sorted(set(re.findall(r'metric_id == "([^"]+)"', text)))


def _component_for(cop, metric):
    """The VS-01 evidence component of this COP that weighs this metric, if any."""
    for c in SU.profile(cop):
        if c["kind"] == "metrics" and metric in (c.get("metrics") or ()):
            return c
    return None


def _requirement(comp):
    """support.C: required = 'all' | 'quiet' | 'attention' | None."""
    if comp is None:
        return {"value": "UNDEFINED", "basis": "no VS-01 component weighs this metric for this COP",
                "missing_contract": "support.profile component for this COP-metric pair"}
    r = comp.get("required")
    if r == "all":
        return {"value": "required", "basis": f"VS-01 component {comp['name']} required for every label"}
    if r in ("quiet", "attention"):
        return {"value": "conditional", "basis": f"VS-01 component {comp['name']} required only for "
                                                 f"the COP's {r} label"}
    return {"value": "optional", "basis": f"VS-01 component {comp['name']} is not required for any label"}


def _thresholds(cfg, metric):
    t = cfg["thresholds"].get(metric)
    return t if t else U("no threshold block in metrics_config.json")


def build_network():
    cfg = load(SOURCES["metrics_config"])
    mapping = {(p["cop"], p["metric_id"]): p for p in load(SOURCES["mapping_doc"])["pairs"]}
    topo = load(SOURCES["topology_doc"])
    means = defaultdict(list)
    for e in topo["edges"]:
        means[(e["cop"], e["metric"])].append(e)
    profiles = build_profiles()
    allow = allowlists(profiles)
    mc01 = set(_mc01_metrics())
    metric_rows = {m[0]: m for m in signal_metrics.METRICS}
    spec_path = inspect.getsourcefile(SP)
    mapping_rel = rel(SOURCES["mapping_doc"])
    interp_note = ("extracted from cops/interpret.py at commit 6a5c942 (docs/cops/metric_cop_mappings.json); "
                   "interpret.py changed since in 3987b35 (adds INCONCLUSIVE). Re-extract before relying on it.")

    cops = []
    for s in SP.SPECS:
        p = profiles[s.key]
        cops.append({
            "id": s.key, "name": s.name, "behavioural_question": s.behavioural_question,
            "spec_version": s.version, "classification": p.classification, "cadence": s.cadence,
            "interpretations": list(s.interpretations), "permitted_actions": list(s.permitted_actions),
            "metric_keys": list(s.metric_keys), "missing_metrics": list(s.missing_metrics),
            "tools": list(allow[s.key]), "deterministic_tools": list(RU.DETERMINISTIC_TOOLS.get(s.key, ())),
            "approval_actions": list(p.approval_actions),
            "prompt_version": p.prompt_version,
            "consumes": list(SP.CONSUMES[s.key]),
            "referral_receiver": s.key in RF.SCOPE_RECEIVERS,
            "specialist": TR.SPECIALISTS.get(s.key, {}).get("receiver") if s.key in TR.SPECIALISTS else None,
            "evidence_components": [{k: c.get(k) for k in ("name", "kind", "relevance", "weight", "required",
                                                           "source", "metrics", "artifact", "tools", "upstream")}
                                    for c in SU.profile(s.key)],
            "source": {"spec": line_of(spec_path, f'key="{s.key}"'), "profile": code_ref(build_profiles),
                       "support_profile": code_ref(SU.profile)},
        })

    metrics = []
    for mid in signal_metrics.METRIC_IDS + ["telemetry.unknown_share"]:
        row = metric_rows.get(mid)
        metrics.append({
            "id": mid, "category": mid.split(".")[0],
            "name": row[1] if row else "Share of metrics that could not be judged",
            "computed_by": (row[2].__name__ if row else "compute (telemetry.unknown_share)"),
            "code_ref": code_ref(row[2]) if row else code_ref(signal_metrics.compute),
            "basis": row[3] if row else "always", "required_fields": row[4] if row else [],
            "applies_when": row[5] if row else "always",
            "collector_source": "Signal observables (collector/observables_schema.sql), computed by "
                                "signal_metrics.compute",
            "thresholds": _thresholds(cfg, mid),
            "recomputed_by_mc01": mid in mc01,
            "consumed_by": [s.key for s in SP.SPECS if mid in s.metric_keys],
        })

    edges = []
    for s in SP.SPECS:
        cls = profiles[s.key].classification
        for mid in s.metric_keys:
            mp = mapping.get((s.key, mid)) or {}
            comp = _component_for(s.key, mid)
            rels = [{"type": "observes", "rule": "metric is in spec.metric_keys",
                     "source": line_of(spec_path, f'key="{s.key}"')}]
            rule = mp.get("interpretation_rule") or ""
            has_rule = mp.get("role") not in (None, "UNDEFINED")
            if has_rule:
                rels.append({"type": "interprets", "rule": "the COP's rule interpreter reads the metric "
                                                           "(mapping role is not UNDEFINED)",
                             "source": f"{mapping_rel} pairs[cop={s.key},metric={mid}].role={mp['role']}"})
            if comp is not None:
                rels.append({"type": "validates", "rule": "a VS-01 evidence component weighs the metric",
                             "source": f"{code_ref(SU.profile)} component {comp['name']}"})
            if has_rule and (mp.get("role", "").startswith("driver")):
                rels.append({"type": "triggers", "rule": "an attention state of the metric drives the COP's "
                                                         "label (mapping role driver)",
                             "source": f"{mapping_rel} pairs[cop={s.key},metric={mid}].role={mp['role']}"})
            acts = [w for w in ACTION_WORDS if re.search(rf"\b{w}\b", rule)]
            if has_rule and acts:
                rels.append({"type": "acts", "rule": "the interpretation rule names an action or hand-off "
                                                     "that follows from the metric",
                             "actions": acts,
                             "source": f"{mapping_rel} pairs[cop={s.key},metric={mid}].interpretation_rule"})
            doc_means = means.get((s.key, mid)) or []
            ev = mp.get("evidence_requirements") or {}
            mat = ev.get("field_materiality_for_this_cop") or {}
            limitations = []
            if isinstance(mat, dict):
                limitations += [f"field {k} is {v} for this COP" for k, v in mat.items() if v == "BLOCKING"]
            if ev.get("if_basis_absent"):
                limitations.append(f"basis {ev.get('basis')} absent: {ev['if_basis_absent']}")
            per_pair = (mp.get("verifier_requirements") or {}).get("per_pair_check")
            if per_pair and str(per_pair).startswith("UNDEFINED"):
                limitations.append(per_pair)
            if cls == "llm":
                limitations.append("LLM COP: the metric reaches the model in its evidence picture; no "
                                   "per-metric interpretation contract exists in its prompt "
                                   "(profiles.PROMPTS names no metric id)")
            edges.append({
                "id": f"{s.key}|{mid}", "metric_id": mid, "cop_id": s.key,
                "relationships": rels,
                "why_relevant": ({"text": doc_means[0]["means"], "context_changes": doc_means[0].get("context_changes"),
                                  "source": f"{rel(SOURCES['topology_doc'])} edges[cop={s.key},metric={mid}].means",
                                  "status": "documented (target-state doc, not code)"}
                                 if doc_means else UNDEF("no documented meaning for this pair "
                                                         "(cops_topology.json edges[].means)")),
                "interpretation": ({"text": rule, "rule_source": mp.get("rule_source"), "note": interp_note,
                                    "source": f"{mapping_rel} pairs[cop={s.key},metric={mid}].interpretation_rule"}
                                   if has_rule else UNDEF(rule or "no rule interpreter reads this metric for "
                                                                  "this COP; no per-metric interpretation contract")),
                "requirement": _requirement(comp),
                "evidence_role": ({"value": comp["relevance"], "weight": comp["weight"],
                                   "source": f"{code_ref(SU.C)} RELEVANCE"} if comp else
                                  UNDEF("no VS-01 component: relevance not defined")),
                "source_collector": {"computed_by": metric_rows[mid][2].__name__ if mid in metric_rows else
                                     "signal_metrics.compute", "basis": ev.get("basis") or
                                     (metric_rows[mid][3] if mid in metric_rows else "always"),
                                     "required_fields": metric_rows[mid][4] if mid in metric_rows else [],
                                     "events": ev.get("events")},
                "retrieval_tool": ({"tool": "get_metric_rows", "source": "cops/runtime/tools.py TOOLS"}
                                   if "get_metric_rows" in allow[s.key] else
                                   {"tool": "graph.load_inputs (CaseData.case_events)",
                                    "source": code_ref(SP.topological_order) and "collector/cops/runtime/graph.py"}),
                "condition": {"thresholds": _thresholds(cfg, mid), "source": f"{rel(SOURCES['metrics_config'])} "
                                                                             f"thresholds.{mid}"},
                "spec_version": {"spec": s.version, "support": SU.SUPPORT_VERSION,
                                 "thresholds": TR.THRESHOLDS_VERSION, "contract": CONTRACT_VERSION},
                "evidence_source": {"metric_row": f"metric_events (session, {mid})",
                                    "recomputed_by_mc01": mid in mc01, "mc01_source": code_ref(VF._raw)},
                "code_refs": [line_of(spec_path, f'key="{s.key}"'), code_ref(metric_rows[mid][2])
                              if mid in metric_rows else code_ref(signal_metrics.compute)] +
                             ([mp["rule_source"]] if mp.get("rule_source") else []),
                "limitations": limitations or [],
            })

    cop_edges = []
    for k, ups in SP.CONSUMES.items():
        for u in ups:
            cop_edges.append({"from": u, "to": k, "kind": "consumes", "executed": True,
                              "rule": "the case graph runs the consumer after the producer and hands it "
                                      "the producer's published finding",
                              "source": line_of(spec_path, "CONSUMES = {")})
    for k, ds in SP.DECLARED_DOWNSTREAM.items():
        for d in ds:
            if k not in SP.CONSUMES.get(d, ()):
                cop_edges.append({"from": k, "to": d, "kind": "declared_handoff", "executed": False,
                                  "rule": "named in spec.downstream_cops but not consumed (specs.edge_report)",
                                  "source": line_of(spec_path, f'key="{k}"')})
    for k, sp in TR.SPECIALISTS.items():
        for c in sp.get("support_cops", ()):
            cop_edges.append({"from": k, "to": c, "kind": "specialist_support", "executed": False,
                              "rule": "triage.SPECIALISTS support_cops: the COP a FAIL_OUT referral goes to",
                              "source": line_of(inspect.getsourcefile(TR), f'"{k}": dict(')})
    ref_src = line_of(inspect.getsourcefile(RF), "SCOPE_RECEIVERS =")

    hierarchy = load(HIERARCHY) if os.path.exists(HIERARCHY) else None
    if not hierarchy or hierarchy.get("status") != "DOCUMENTED":
        hierarchy = {"status": "MISSING",
                     "missing_contract": "the documented seven-primary-COP hierarchy (primary, secondary, "
                                         "tertiary). Supply it as docs/cops/cop_hierarchy.json with status "
                                         "DOCUMENTED, the source document and its levels.",
                     "searched": ["signal-dashboard: all branches and git history (git log --all -G "
                                  "'primary cop|tertiary')", "docs/cops/*.md, *.json, *.html",
                                  "images in the repository", "/mnt/project-files"],
                     "not_reachable": ["malhar-sovereign/project-signal (no access from this session)"],
                     "placeholder_file": rel(HIERARCHY), **(hierarchy or {})}
        hierarchy["status"] = "MISSING"
    else:
        known = {sp.key for sp in SP.SPECS}
        named = [c for lv in hierarchy.get("levels") or [] for c in lv.get("cops") or []]
        bad = sorted(set(named) - known)
        if bad or not hierarchy.get("source_document"):
            raise SystemExit(f"cop_hierarchy.json: unknown COP ids {bad} or no source_document")
        hierarchy["unplaced"] = sorted(known - set(named))

    return {
        "contract": NETWORK_CONTRACT,
        "relationship_rules": {
            "observes": "metric is in the COP's spec.metric_keys (cops/specs.py)",
            "interprets": "the COP's rule interpreter reads the metric: role in docs/cops/metric_cop_mappings.json "
                          "is not UNDEFINED",
            "validates": "a VS-01 evidence component of the COP weighs the metric (runtime/support.profile)",
            "triggers": "the metric's attention state drives the COP's label: mapping role 'driver'",
            "acts": "the interpretation rule names an action or hand-off that follows from the metric",
            "requirement": "from the VS-01 component's required field: all -> required, quiet/attention -> "
                           "conditional, none -> optional",
            "evidence_role": "the VS-01 component's relevance: direct, supporting or contextual",
        },
        "versions": {"contract": CONTRACT_VERSION, "support": SU.SUPPORT_VERSION, "triage": TR.TRIAGE_VERSION,
                     "thresholds": TR.THRESHOLDS_VERSION,
                     "metrics_config_sha256": sha(SOURCES["metrics_config"])},
        "hierarchy": hierarchy,
        "cops": cops, "metrics": metrics, "metric_edges": edges, "cop_edges": cop_edges,
        "referral_scope": {"receivers": list(RF.SCOPE_RECEIVERS), "source": ref_src},
        "action_taxonomy": {k: v.to_dict() for k, v in TAXONOMY.items()},
    }


# ====================================================================== decisions

def coverage(suff):
    """Evidence coverage from the runtime's evidence sufficiency record. Separate from VS-01 support."""
    if not suff or not suff.get("items"):
        return {"state": "UNAVAILABLE", "basis": "no evidence sufficiency record in this trace"}
    items = suff["items"]
    missing = [i for i in items if i["state"].startswith("MISSING")]
    unverified = [i for i in items if i["state"] == "PRESENT_BUT_UNVERIFIED"]
    validated = [i for i in items if i["state"] == "RETRIEVED_AND_VALIDATED"]
    req_missing = [i for i in missing if i.get("kind") == "REQUIRED"]
    metrics = [i for i in items if i.get("kind") == "METRIC"]
    metrics_ok = [i for i in metrics if i["state"] == "RETRIEVED_AND_VALIDATED"]
    if req_missing or suff.get("blocks_all_labels") or (metrics and not metrics_ok and not validated):
        state = "ABSENT"
        basis = ("required evidence missing: " + ", ".join(i["item"] for i in req_missing)) if req_missing \
            else "no metric evidence was retrieved and validated"
    elif missing or unverified:
        state = "PARTIAL"
        basis = "; ".join(x for x in (
            ("missing: " + ", ".join(i["item"] for i in missing)) if missing else "",
            ("present but unverified: " + ", ".join(i["item"] for i in unverified)) if unverified else "") if x)
    else:
        state, basis = "COMPLETE", "every listed item retrieved and validated or not applicable"
    return {"state": state, "basis": basis, "counts": {"validated": len(validated), "missing": len(missing),
                                                        "unverified": len(unverified), "items": len(items)},
            "rule": "ABSENT if a REQUIRED item is missing or nothing was validated; PARTIAL if any item is "
                    "missing or unverified; else COMPLETE (cops/dashboard_export.coverage)"}


def _hot(signals, cfg):
    out = []
    for x in signals or []:
        if x.get("state") in ATTENTION:
            out.append({"metric": x["metric"], "state": x["state"], "value": x.get("value"),
                        "thresholds": cfg["thresholds"].get(x["metric"])})
    return out


def _signals_from_detail(det):
    out = []
    for i in (det.get("sufficiency") or {}).get("items") or []:
        if i.get("kind") == "METRIC":
            out.append({"metric": i["item"].split(":", 1)[1], "state": None, "evidence": i.get("evidence"),
                        "sufficiency": i["state"]})
    return out


def _tools_from_detail(det):
    return [{"tool": t["tool"], "by": t.get("requested_by"), "status": t.get("status"),
             "tool_call_id": t.get("tool_call_id"), "args": t.get("args"), "result": t.get("result"),
             "rejection": t.get("rejection_reason"), "latency_ms": t.get("latency_ms")}
            for t in det.get("tool_calls") or []]


def _retrievals(cop, tools, sessions, tenant, data_source, effect_of):
    """One retrieval-attempt record per tool call that read data."""
    out = []
    for t in tools:
        refs = []
        r = t.get("result")
        if isinstance(r, dict):
            refs = [x for x in json.dumps(r).split('"') if x.startswith(("observable:", "metric:", "record:"))][:10]
        out.append({"requesting_cop": cop, "query_or_tool": t["tool"],
                    "reason": t.get("purpose") or (f"requested by {t.get('by')}" if t.get("by") else
                                                   U("the trace records no purpose for this call")),
                    "scope": {"session_ids": sessions, "agent": U("tool calls are scoped to the case session"),
                              "tenant_id": tenant},
                    "data_source": data_source, "sql_backed": False,
                    "returned_refs": refs, "status": t.get("status"),
                    "latency_ms": t.get("latency_ms") if t.get("latency_ms") is not None else
                    U("latency not recorded in this trace"),
                    "effect_on_determination": effect_of(t)})
    return out


def _effect(support_items):
    def fn(t):
        hit = [s for s in support_items or [] if t["tool"] in json.dumps(s)]
        return ({"cited_in_support": [s.get("kind") or s.get("name") for s in hit]} if hit
                else {"cited_in_support": [], "note": "not named in the support record"})
    return fn


def _action_block(pa, triage, typed, gateway=None, approval_events=None, receipt=None):
    tri = triage or {}
    routing = tri.get("routing") or {}
    sel = tri.get("action_selection") or {}
    life = (pa or {}).get("lifecycle") or {}
    tx = TAXONOMY.get(typed) if typed else None
    orig = (pa or {}).get("original_proposal") or {}
    fa = (pa or {}).get("final_authority") or {}
    return {
        "proposed": {"action": orig.get("action") or sel.get("original_proposal") or U("no proposal recorded"),
                     "reason": orig.get("reason") or U("no proposal reason recorded"),
                     "authority_tier": orig.get("authority_tier")},
        "selected": {"typed_action": sel.get("selected") or typed, "by": "triage action selection",
                     "reason": sel.get("reason") or U("no selection reason recorded"), "cause": sel.get("cause"),
                     "suppressed_on_pass": sel.get("suppressed_on_pass")},
        "final_action": {"typed_action": typed or life.get("final_typed_action"),
                         "by": "authority check (final typed action)"},
        "route": {"tier": routing.get("tier"), "reason": routing.get("primary_reason"),
                  "rules_fired": routing.get("rules_fired"), "next_step": routing.get("next_step"),
                  "human_needed": routing.get("human_needed")},
        "authority": {"selected_authorised": life.get("authorised") if "authorised" in life else fa.get("authorised", (pa or {}).get("authorised")),
                      "final_authorised": fa.get("authorised", (pa or {}).get("authorised")),
                      "reason": (pa or {}).get("authority_reason") or life.get("authority_reason") or "",
                      "checks": life.get("checks") or fa.get("checks"), "stage": life.get("stage"),
                      "final_action": life.get("final_typed_action") or fa.get("action"),
                      "substitute": life.get("substitute")},
        "requires_approval": (pa or {}).get("requires_approval"),
        "approval": (pa or {}).get("approval"),
        "delivery": ({"target": tx.target, "observable_result": tx.observable_result, "verified_by": tx.verified_by,
                      "execution": tx.execution, "approval_rule": tx.approval,
                      "source": "cops/authority.py TAXONOMY"} if tx else U(f"no taxonomy entry for {typed}")),
        "execution": {"status": (pa or {}).get("execution_status") or life.get("execution_status"),
                      "lifecycle_stage": life.get("stage"), "mode": life.get("mode")},
        "receipt": receipt or life.get("dry_run_receipt") or (pa or {}).get("execution_outcome") or
        U("no execution receipt: nothing was executed"),
        "gateway": gateway or U("no gateway record: dry run or no action"),
        "approval_events": approval_events or [],
        "human_task": tri.get("human_task"),
        "referral": tri.get("referral"),
    }


def _attempts(det_tools, suff, action, missing_evidence, referral_events=None):
    """Resolution attempts that have a runtime event behind them."""
    out = []
    retrievable = {i["item"]: i for i in (suff or {}).get("items") or [] if i.get("retrieve_with")}
    called = {t["tool"] for t in det_tools}
    for item, i in retrievable.items():
        hit = [t for t in i["retrieve_with"] if t in called]
        if hit:
            out.append({"kind": "collector_or_tool_call", "for": item, "tools": hit, "state_after": i["state"],
                        "event": "tool_calls"})
    for e in referral_events or []:
        out.append({"kind": "cop_referral", **e})
    ref = action.get("referral")
    if ref and not referral_events:
        out.append({"kind": "cop_referral", "receiver": ref.get("receiver"), "status": ref.get("status"),
                    "reason": ref.get("reason"), "event": "triage.referral (opened by the router)"})
    if (action.get("selected") or {}).get("typed_action") == "REQUEST_CONTEXT":
        out.append({"kind": "request_for_context", "items": [m.get("source") or m.get("metric") or m
                                                             for m in missing_evidence or []],
                    "event": "selected action REQUEST_CONTEXT"})
    if action.get("human_task"):
        out.append({"kind": "human_escalation", **{k: action["human_task"].get(k) for k in ("kind", "question")},
                    "event": "triage.human_task"})
    return out


def _support(vs):
    vs = vs or {}
    if not vs:
        return U("no VS-01 record")
    return {k: vs.get(k) for k in ("decision", "basis", "score", "against", "unknown", "limitations",
                                   "dimensions", "corroborating_sources", "thresholds", "hard_checks",
                                   "graded_checks", "components", "telemetry_reliability")}


def _quotient(triage):
    hq = (triage or {}).get("perceived_handling_quotient") or {}
    if not hq:
        return U("no handling quotient recorded")
    return {"value": hq.get("value"), "components": {k: (v or {}).get("value") for k, v in
                                                     (hq.get("components") or {}).items()}}


def bench_decisions(cfg):
    path = SOURCES["bench"]
    d = load(path)
    out = []
    for n, c in enumerate(d["cases"]):
        t = c["trace"]
        sid = c["id"].replace(":", "-").replace("_", "")[:24]            # test_bench.run_case
        receipt = t.get("receipt") or ((t.get("authority") or {}).get("lifecycle") or {}).get("dry_run_receipt")
        case_id = (receipt or {}).get("case_id")
        did = make_decision_id(f"eval-{case_id}", sid, c["cop"]) if case_id else f"bench:{c['id']}"
        tri = t.get("triage") or {}
        auth = t.get("authority") or {}
        pa = {"original_proposal": auth.get("original_proposal"), "lifecycle": auth.get("lifecycle"),
              "final_authority": {"authorised": auth.get("authorised"), "action": auth.get("final")},
              "authority_reason": auth.get("reason"), "execution_status": auth.get("execution_status"),
              "requires_approval": ((tri.get("routing") or {}).get("supporting_metrics") or {}).get("approval_required")}
        action = _action_block(pa, tri, t.get("typed_action"), receipt=receipt)
        tools = [{"tool": x["tool"], "by": x.get("by"), "status": x.get("status"), "tool_call_id": x.get("tool_call_id"),
                  "result": x.get("result")} for x in t.get("tool_results") or []]
        supp = (tri.get("evidence_support") or {})
        es = t.get("interpretation") or {}
        mode = {"recorded_reply_replay": "recorded_llm_replay", "deterministic_rerun": "deterministic"}.get(
            c.get("result_source"), c.get("result_source"))
        base = f"cases[{n}]"
        out.append({
            "decision_id": did,
            "decision_id_source": ("persistence.decision_id(eval-<receipt.case_id>, session, cop), the runtime's "
                                   "formula" if case_id else "no receipt: bench case id used"),
            "cop": c["cop"], "source": {**src(path, base), "kind": "bench", "case": c["id"]},
            "execution_mode": mode,
            "mode_detail": {"result_source": c.get("result_source"), "llm_usage": c.get("usage"),
                            "note": "recorded model replies replayed; no new call" if mode == "recorded_llm_replay"
                            else "deterministic COP re-run on the synthetic estate"},
            "session_ids": [sid], "session_source": "test_bench.py: sid = case id with ':' -> '-', '_' removed, "
                                                    "first 24 chars; matches receipt.target",
            "timestamp": {"value": d.get("generated"), "kind": "bench run generated",
                          "note": "the bench records no per-decision timestamp"},
            "trigger": {"id": f"bench:{c['id']}", "kind": "evaluation_case",
                        "description": c.get("title"), "description_source": "bench_cases.py case title",
                        "observed": _hot(t.get("signals"), cfg) or [],
                        "all_signals": t.get("signals"),
                        "at": U("no event time recorded for bench cases"),
                        "condition": ("attention state at or above the metrics_config threshold"
                                      if _hot(t.get("signals"), cfg) else
                                      "no metric in an attention state: the bench ran this case directly"),
                        "sessions": [sid]},
            "finding": {**(t.get("decision") or {}), "rationale": es.get("rationale"),
                        "rule_baseline": es.get("rule_baseline"), "gated_label": es.get("gated_label"),
                        "rejected_candidate": es.get("rejected_candidate")},
            "evidence_coverage": coverage(t.get("sufficiency")),
            "investigation": {
                "metrics_inspected": t.get("signals"),
                "records": [i for i in (t.get("sufficiency") or {}).get("items") or [] if i.get("kind") in
                            ("REQUIRED", "OPTIONAL", "NOT_APPLICABLE")],
                "tools": tools, "llm_steps": [{k: x.get(k) for k in ("purpose", "simulated", "model", "hypothesis",
                                                                     "decision")} for x in t.get("llm") or []],
                "cops_consulted": [i for i in (t.get("sufficiency") or {}).get("items") or []
                                   if i.get("kind") == "UPSTREAM"],
                "cops_run_in_case": c.get("cops_run"),
                "evidence_requested": t.get("missing_evidence"),
                "missing_dependencies": t.get("missing_dependencies"),
                "sql_retrieval": {"performed": False, "basis": "no runtime tool reaches a database adapter; "
                                                               "tools read CaseData in memory (retrieval.json audit)"},
                "unavailable": [i for i in (t.get("sufficiency") or {}).get("items") or []
                                if i["state"].startswith("MISSING")],
                "retrievals": _retrievals(c["cop"], tools, [sid], "tenant-a",
                                          "synthetic estate in memory (CaseData), bench_cases.py",
                                          _effect(supp.get("supporting"))),
            },
            "determination": {
                "label": (t.get("decision") or {}).get("label"), "support": _support(t.get("verification_support")),
                "graded": {"outcome": tri.get("outcome"), "reason": tri.get("outcome_reason"),
                           "score": supp.get("score"), "meaning": supp.get("meaning"), "rule": supp.get("rule")},
                "supporting": supp.get("supporting"), "limiting": supp.get("limiting"),
                "contradictions": supp.get("contradictions"),
                "quotient": _quotient(tri), "claims": t.get("claims"), "verifier_results": t.get("verification"),
                "changed": ({"from": es.get("rejected_candidate") or es.get("gated_label"),
                             "to": (t.get("decision") or {}).get("label"),
                             "why": "candidate rejected by a verifier" if es.get("rejected_candidate")
                             else "label gated by the evidence precheck"}
                            if (es.get("rejected_candidate") or es.get("gated_label")) else None),
            },
            "action": action,
            "resolution_attempts": _attempts(tools, t.get("sufficiency"), action, t.get("missing_evidence")),
            "benchmark": {"expected": c.get("expected"), "verdict": c.get("verdict"),
                          "note": "the benchmark's expected label, never given to the COP"},
        })
    return out


def _detail_decision(det, kind, path, pointer, mode, sessions, trigger, timestamp, action_pa=None, gateway=None,
                     approval_events=None, baseline_sessions=None, referral_events=None, changed=None,
                     extra_source=None, cfg=None):
    f = det.get("finding") or {}
    tri = det.get("triage") or {}
    supp = tri.get("evidence_support") or {}
    pa = action_pa or det.get("proposed_action") or {}
    typed = pa.get("typed_action")
    tools = _tools_from_detail(det)
    action = _action_block(pa, tri, typed, gateway=gateway, approval_events=approval_events,
                           receipt=pa.get("execution_outcome"))
    return {
        "decision_id": det.get("decision_id"), "decision_id_source": "recorded run (persistence.decision_id)",
        "cop": det.get("cop"), "source": {**src(path, pointer), "kind": kind, **(extra_source or {})},
        "execution_mode": mode,
        "mode_detail": {"classification": det.get("classification"), "llm_invocations": det.get("llm_invocations"),
                        "stop_reason": det.get("stop_reason")},
        "session_ids": sessions,
        "baseline_session_ids": baseline_sessions or [],
        "timestamp": timestamp,
        "trigger": trigger,
        "finding": {k: f.get(k) for k in ("finding_id", "label", "status", "confidence", "verified", "rationale",
                                          "severity", "baseline")},
        "evidence_coverage": coverage(det.get("sufficiency")),
        "investigation": {
            "metrics_inspected": _signals_from_detail(det),
            "records": [i for i in (det.get("sufficiency") or {}).get("items") or []
                        if i.get("kind") in ("REQUIRED", "OPTIONAL", "NOT_APPLICABLE")],
            "tools": tools,
            "llm_steps": det.get("llm_invocations"),
            "cops_consulted": det.get("upstream_view"),
            "evidence_requested": f.get("missing_evidence"),
            "missing_dependencies": f.get("missing_dependencies"),
            "sql_retrieval": {"performed": False, "basis": "no runtime tool reaches a database adapter; tools read "
                                                           "CaseData in memory (retrieval.json audit)"},
            "unavailable": [i for i in (det.get("sufficiency") or {}).get("items") or []
                            if i["state"].startswith("MISSING")],
            "retrievals": _retrievals(det.get("cop"), tools, sessions, det.get("tenant_id"),
                                      "rows held in memory by CaseData", _effect(supp.get("supporting"))),
        },
        "determination": {
            "label": f.get("label"), "support": _support(det.get("verification_support")),
            "graded": {"outcome": tri.get("outcome"), "reason": tri.get("outcome_reason"), "score": supp.get("score"),
                       "meaning": supp.get("meaning"), "rule": supp.get("rule")},
            "supporting": supp.get("supporting"), "limiting": supp.get("limiting"),
            "contradictions": supp.get("contradictions"), "quotient": _quotient(tri),
            "claims": f.get("claims"), "verifier_results": det.get("verifier_results"),
            "changed": changed,
        },
        "action": action,
        "resolution_attempts": _attempts(tools, det.get("sufficiency"), action, f.get("missing_evidence"),
                                         referral_events),
    }


def scenario_decisions(cfg):
    out = []
    for fn in sorted(os.listdir(SCENARIO_DIR)):
        path = os.path.join(SCENARIO_DIR, fn)
        d = load(path)
        variant = d["summary"]["variant"]
        tr = d["trace"]
        idx = {e["seq"]: i for i, e in enumerate(tr)}
        details = next((e for e in tr if e["stage"] == "run_details"), None)
        if details is None:
            continue
        di = idx[details["seq"]]
        tel = next(e for e in tr if e["stage"] == "telemetry_ingested")
        sessions = sorted({e.get("session_id") for e in tr if e["stage"] == "agent_event" and e.get("session_id")})
        req = {e["investigation_event"]["referral_id"]: e["investigation_event"] for e in tr
               if e["stage"] == "request_for_support"}
        resp = {e["referral_id"]: e for e in tr if e["stage"] == "referral_response"}
        answered = {e["investigation_event"]["referral_id"]: e["investigation_event"] for e in tr
                    if e["stage"] == "receiver_answered"}
        recv_dec = {e["receiver_decision_id"]: e for e in resp.values()}
        revised = [e for e in tr if e["stage"] == "revised_evidence_score"]
        not_opened = [e.get("investigation_event") or e for e in tr if e["stage"] == "referral_not_opened"]
        initial = next((e for e in tr if e["stage"] == "initial_finding"), None)
        runs = details["runs"]
        mode = "scripted_simulation"
        for j, det in enumerate(runs):
            rk = det.get("run_key") or ""
            ptr = f"trace[{di}].runs[{j}]"
            is_root = rk.startswith("root:")
            if is_root:
                trig = {"id": f"scenario:{variant}:telemetry_ingested", "kind": "telemetry_ingested",
                        "description": "telemetry rows for the session were ingested and the investigation started",
                        "observed": [{"metric": i["item"].split(":", 1)[1], "sufficiency": i["state"]}
                                     for i in (det.get("sufficiency") or {}).get("items") or []
                                     if i.get("kind") == "METRIC"],
                        "telemetry": {k: tel.get(k) for k in ("collectors", "rows_for_session", "baseline_sessions",
                                                              "tokens_reported", "tool_calls_dropped_by_collector")},
                        "at": tel["at"], "condition": U("the scenario starts the investigation directly; no trigger "
                                                       "rule is recorded"),
                        "sessions": sessions, "source": src(path, f"trace[{idx[tel['seq']]}]")}
                refs = [{"receiver": r["receiver"], "referral_id": rid, "reason": r.get("reason"),
                         "round": r.get("round"), "depth": r.get("depth"), "requested_at": r.get("at"),
                         "status": (resp.get(rid) or answered.get(rid) or {}).get("status"),
                         "label": (resp.get(rid) or answered.get(rid) or {}).get("label"),
                         "delivered_as": (resp.get(rid) or {}).get("delivered_as"),
                         "elapsed_s": (resp.get(rid) or answered.get(rid) or {}).get("elapsed_s"),
                         "checks": (resp.get(rid) or answered.get(rid) or {}).get("checks"),
                         "receiver_decision_id": (resp.get(rid) or {}).get("receiver_decision_id"),
                         "event": "request_for_support / referral_response"}
                        for rid, r in req.items() if r.get("cop") == det.get("cop")] if rk.endswith(":r0") else []
                refs += [{"receiver": e.get("receiver"), "status": "NOT_OPENED", "reason": e.get("reason"),
                          "event": "referral_not_opened"} for e in not_opened
                         if e.get("cop") == det.get("cop") and rk.endswith(":r0")]
                changed = None
                if rk.endswith(":r1") and initial and revised:
                    rv = revised[-1]
                    changed = {"from": {"label": initial.get("label"), "support": d["summary"]["initial"]},
                               "to": {"label": rv.get("label"), "support": d["summary"]["revised"]},
                               "resolution": rv.get("resolution"), "reason": rv.get("resolution_reason"),
                               "component_changes": rv.get("component_changes"),
                               "source": src(path, f"trace[{idx[rv['seq']]}]")}
                at = (initial or {}).get("at") if rk.endswith(":r0") else (revised[-1]["at"] if revised else None)
            else:
                rid = next((x for x, r in resp.items() if r.get("receiver_decision_id") == det.get("decision_id")), None)
                rq = req.get(rid) or next((r for r in req.values() if r.get("receiver") == det.get("cop")), {})
                trig = {"id": f"referral:{rq.get('referral_id')}", "kind": "cop_referral",
                        "description": f"{rq.get('cop')} asked {det.get('cop')} for support",
                        "observed": [{"reason": rq.get("reason")}], "at": rq.get("at") or U("not recorded"),
                        "condition": rq.get("reason") or U("no reason recorded"), "sessions": sessions,
                        "source": src(path, f"trace[{idx[[e for e in tr if e['stage']=='request_for_support' and e['investigation_event'].get('referral_id')==rq.get('referral_id')][0]['seq']]}]")
                        if rq else U("no matching referral request")}
                changed = None
                own = [(x, r) for x, r in req.items() if r.get("cop") == det.get("cop")] if rk.endswith(":r0") else []
                refs = [{"receiver": r["receiver"], "referral_id": x, "reason": r.get("reason"),
                         "round": r.get("round"), "depth": r.get("depth"), "requested_at": r.get("at"),
                         "status": (answered.get(x) or {}).get("status"), "label": (answered.get(x) or {}).get("label"),
                         "event": "request_for_support / receiver_answered"} for x, r in own]
                refs += [{"receiver": e.get("receiver"), "status": "NOT_OPENED", "reason": e.get("reason"),
                          "event": "referral_not_opened"} for e in not_opened
                         if e.get("cop") == det.get("cop") and rk.endswith(":r0")]
                at = (answered.get(rq.get("referral_id")) or {}).get("at")
            out.append(_detail_decision(
                det, "scenario", path, ptr, mode, sessions, trig,
                {"value": at, "kind": "trace event time"} if at else U("no event time for this run"),
                baseline_sessions=details.get("baseline_session_ids"), referral_events=refs, changed=changed,
                extra_source={"variant": variant, "run_key": rk}, cfg=cfg))
        # the act phase: its own decision, on the investigation's final state
        act = details.get("act")
        if act:
            ex = next((e for e in tr if e["stage"] == "action_execution"), {})
            oc = next((e for e in tr if e["stage"] == "outcome_confirmation"), {})
            fin = next((e for e in tr if e["stage"] == "investigation_final"), {})
            appr = [e for e in tr if e["stage"] in ("approval_requested", "approval_decided", "review_task_opened")]
            gw = {"gateway_action_id": ex.get("gateway_action_id"), "history": ex.get("gateway_history"),
                  "endpoint_received": ex.get("endpoint_received"), "detail": ex.get("detail"),
                  "outcome_confirmation": {k: oc.get(k) for k in ("gateway_state", "observed", "alerts_at_endpoint",
                                                                  "review_task_state")},
                  "source": src(path, f"trace[{idx[ex['seq']]}]") if ex else None}
            trig = {"id": f"scenario:{variant}:investigation_final", "kind": "investigation_final",
                    "description": "the act phase entered the action half of the graph with the investigation's "
                                   "final state", "observed": [{k: fin.get(k) for k in ("tier", "selected_action",
                                                                                        "resolution")}],
                    "at": fin.get("at"), "condition": fin.get("routing_reason"), "sessions": sessions,
                    "source": src(path, f"trace[{idx[fin['seq']]}]") if fin else None}
            rec = _detail_decision(act, "scenario", path, f"trace[{di}].act", "scripted_simulation + real local gateway "
                                   "execution", sessions, trig, {"value": ex.get("at"), "kind": "action_execution "
                                                                                                  "event time"},
                                   gateway=gw, approval_events=appr,
                                   baseline_sessions=details.get("baseline_session_ids"),
                                   extra_source={"variant": variant, "run_key": act.get("run_key"), "phase": "act"},
                                   cfg=cfg)
            rec["action"]["execution"]["status"] = ex.get("execution_status") or rec["action"]["execution"]["status"]
            out.append(rec)
    _link_scenario(out)
    return out


def _link_scenario(recs):
    """Related decisions inside one scenario run, from run keys and referral ids only."""
    by_var = {}
    for r in recs:
        by_var.setdefault(r["source"]["variant"], []).append(r)
    for rs in by_var.values():
        key = {r["source"]["run_key"]: r for r in rs}
        ref_owner = {}
        for r in rs:
            for a in r["resolution_attempts"]:
                if a.get("kind") == "cop_referral" and a.get("referral_id"):
                    ref_owner[a["referral_id"]] = r
        for r in rs:
            rk, rel = r["source"]["run_key"], []
            if rk.endswith(":r1"):
                prev = key.get(rk[:-3] + ":r0")
                if prev:
                    rel.append({"relation": "reassesses", "decision_id": prev["decision_id"]})
            if rk.endswith(":act"):
                base = rk[:-4]
                fin = key.get(base[:-3] + ":r1") or key.get(base)
                if fin:
                    rel.append({"relation": "acts_on", "decision_id": fin["decision_id"]})
            rid = (r["trigger"].get("id") or "").removeprefix("referral:")
            if r["trigger"].get("kind") == "cop_referral" and rid in ref_owner:
                rel.append({"relation": "answers_referral_from", "decision_id": ref_owner[rid]["decision_id"]})
            for a in r["resolution_attempts"]:
                if a.get("kind") == "cop_referral" and a.get("referral_id"):
                    for o in rs:
                        if o["trigger"].get("id") == f"referral:{a['referral_id']}":
                            rel.append({"relation": "referred_to", "decision_id": o["decision_id"],
                                        "cop": o["cop"], "run_key": o["source"]["run_key"]})
            r["related"] = rel or [{"relation": "none", "note": "no linked run in this scenario"}]


def gateway_decisions(cfg):
    out = []
    if not os.path.isdir(GATEWAY_DIR):
        return out
    for fn in sorted(os.listdir(GATEWAY_DIR)):
        path = os.path.join(GATEWAY_DIR, fn)
        d = load(path)
        det = d["detail"]
        acts = d["gateway"]["actions"]
        gw = {"actions": [{k: a.get(k) for k in ("action_id", "typed_action", "state", "target")} |
                          {"history": [h["state"] for h in a.get("history") or []]} for a in acts],
              "approvals": d["gateway"]["approvals"], "holds": d["gateway"]["holds"],
              "endpoint_received": len(d["endpoint_received"]), "source": src(path, "gateway")}
        trig = {"id": f"gateway:{d['recording']}", "kind": "local_gateway_recording",
                "description": f"recorded {d['recording']} lifecycle (gateway_recordings.py)",
                "observed": [e for e in d["events"] if e["step"] == "agent_tool_call_submitted"] or [],
                "at": U("recordings use the evaluation clock; no event time"), "condition": U("the recording runs "
                                                                                             "the case directly"),
                "sessions": [d["session_id"]], "source": src(path, "events")}
        out.append(_detail_decision(det, "gateway_recording", path, "detail", d["execution_mode"], [d["session_id"]],
                                    trig, {"value": det.get("decided_at"), "kind": "evaluation clock (fixed)"},
                                    gateway=gw, approval_events=d["events"],
                                    extra_source={"recording": d["recording"]}, cfg=cfg))
    return out


def harbor_decisions(cfg):
    out = []
    for key in ("h01", "h02"):
        path = os.path.join(HARBOR_DIR, f"{key}_trace.json")
        if not os.path.exists(path):
            continue
        d = load(path)
        rep = d["trial"]
        for j, r in enumerate(d["records"]):
            det = r.get("detail")
            if not det:
                continue
            act = r.get("act_detail") or {}
            gid = r["execution"].get("gateway_action_id")
            gw = ({"gateway_action_id": gid, "history": r["execution"].get("gateway_history"),
                   "source": src(path, f"records[{j}].execution")} if gid else None)
            hot = [m for m in d["metrics"] if m["state"] in ATTENTION]
            trig = {"id": f"harbor:{rep['trial_name']}", "kind": "harbor_trial_ingested",
                    "description": f"Harbor trial {rep['trial_name']} ({rep['task_name']}) ingested by signal_harbor",
                    "observed": [{"metric": m["metric_id"], "state": m["state"], "value": m["value"],
                                  "thresholds": cfg["thresholds"].get(m["metric_id"])} for m in hot],
                    "at": U("the driver records no ingestion time; trial times are in result.json"),
                    "condition": "metric states from signal_metrics on the ingested rows",
                    "sessions": [rep["session_id"]], "trial_id": rep["trial_id"], "source": src(path, "trial")}
            rec = _detail_decision(det, "harbor", path, f"records[{j}].detail",
                                   "real tool execution (scripted model-free Harbor agent); "
                                   + ("no model: LLM COP stopped at llm_unavailable" if det.get("classification") == "llm"
                                      else "deterministic COP"),
                                   [rep["session_id"]], trig, {"value": det.get("decided_at"),
                                                               "kind": "evaluation clock (fixed)"},
                                   action_pa=act.get("proposed_action"), gateway=gw,
                                   baseline_sessions=r.get("baseline_session_ids"),
                                   extra_source={"case": key, "trial": rep["trial_name"]}, cfg=cfg)
            rec["ground_truth"] = next((c for c in d["comparison"]["labels"] if c["cop"] == r["cop"]), None)
            out.append(rec)
    return out


def _estate_of(src):
    """Session ids are unique only inside one estate (one generated or ingested set of sessions)."""
    k = src["kind"]
    if k == "bench":
        return f"bench:{src['case']}"
    if k == "scenario":
        return f"scenario:{src['variant']}"
    if k == "gateway_recording":
        return f"gateway:{src['recording']}"
    return "harbor"


def build_decisions():
    cfg = load(SOURCES["metrics_config"])
    ds = bench_decisions(cfg) + scenario_decisions(cfg) + gateway_decisions(cfg) + harbor_decisions(cfg)
    by_session = defaultdict(list)
    for x in ds:
        x["estate"] = _estate_of(x["source"])
        for s in x["session_ids"]:
            by_session[f"{x['estate']}/{s}"].append(x["decision_id"])
    files = [SOURCES["bench"]] + [os.path.join(SCENARIO_DIR, f) for f in sorted(os.listdir(SCENARIO_DIR))] + \
        ([os.path.join(GATEWAY_DIR, f) for f in sorted(os.listdir(GATEWAY_DIR))] if os.path.isdir(GATEWAY_DIR) else []) + \
        [os.path.join(HARBOR_DIR, f) for f in ("h01_trace.json", "h02_trace.json") if os.path.exists(os.path.join(HARBOR_DIR, f))]
    return {"contract": DECISIONS_CONTRACT,
            "coverage_states": {"COMPLETE": "mandatory evidence present and sufficiently verified",
                                "PARTIAL": "useful evidence exists but important limitations remain",
                                "ABSENT": "required evidence is unavailable",
                                "note": "coverage is read from the runtime's evidence sufficiency record and is "
                                        "separate from VS-01 support"},
            "execution_modes": {"recorded_llm_replay": "model replies recorded in the approved live run, replayed",
                                "deterministic": "deterministic COP re-run",
                                "scripted_simulation": "scripted model replies (FixtureProvider, simulated)",
                                "real tool execution": "real commands in Harbor containers, no model"},
            "decisions": ds,
            "sessions": {k: v for k, v in sorted(by_session.items())},
            "sessions_key": "estate/session_id: session ids repeat across separately generated estates",
            "_files": files}


# ====================================================================== retrieval

def build_retrieval():
    return {"contract": RETRIEVAL_CONTRACT, "data_access_audit": DATA_ACCESS_AUDIT,
            "query_types": QUERY_TYPES, "consumer_assessment": ASSESSMENT}


# ====================================================================== activation

ACTIVATION_STATES = {
    "defined": "the COP has an activation policy in the approved, active mapping version (mapping.json)",
    "eligible": "the router recorded ELIGIBLE for the COP in this case (an ELIGIBLE activation record)",
    "active": "the router recorded ACTIVATED for the COP in this case (an ACTIVATED activation record)",
}


def build_activation():
    """Activation records as the router wrote them (docs/cops/evaluation/activation, written by
    python -m cops.runtime.activation_scenario). Nothing is computed here: no state, edge or
    transition is added that is not in those files."""
    files = []
    mp = os.path.join(ACTIVATION_DIR, "mapping.json")
    if not os.path.exists(mp):
        return {"contract": ACTIVATION_CONTRACT, "status": "MISSING",
                "missing": "run: cd collector && python3 -m cops.runtime.activation_scenario "
                           "--out ../docs/cops/evaluation/activation",
                "states": ACTIVATION_STATES, "mapping": None, "registry": None, "scenarios": [], "_files": files}
    m = load(mp)
    files.append(mp)
    scenarios = []
    for name in sorted(os.listdir(ACTIVATION_DIR)):
        if name == "mapping.json" or not name.endswith(".json"):
            continue
        p = os.path.join(ACTIVATION_DIR, name)
        t = load(p)
        files.append(p)
        scenarios.append({k: t[k] for k in ("scenario", "version", "case_id", "investigation_id", "session_id",
                                            "tenant_id", "mapping_version", "simulated", "summary",
                                            "chain_verified", "replay", "records")}
                         | {"source": rel(p)})
    order = ["normal_debugging", "repeated_failures", "missing_evidence", "concurrent", "consultation"]
    scenarios.sort(key=lambda x: order.index(x["scenario"]) if x["scenario"] in order else len(order))
    return {"contract": ACTIVATION_CONTRACT, "status": "RECORDED", "states": ACTIVATION_STATES,
            "mapping": m["bundle"], "registry": m["registry"], "validation_errors": m["validation_errors"],
            "scenarios": scenarios, "_files": files}


# ====================================================================== main

def _meta(files):
    return {"git_commit": git_head(), "generated_by": "collector/cops/dashboard_export.py",
            "sources": [{"file": rel(p), "sha256": sha(p)} for p in files]}


EXERCISED_RULE = ("a metric edge is exercised when a recorded decision of that COP read the metric with evidence "
                  "OBSERVED or sufficiency RETRIEVED_AND_VALIDATED / PRESENT_BUT_UNVERIFIED; a COP edge is exercised "
                  "when a recorded decision consulted the upstream finding or a referral was sent along it (a request_for_support "
                  "event; a referral the router opened but no loop ran is shown on the decision, not counted here)")


def _consulted_cop(c):
    if c.get("cop"):
        return c["cop"]
    item = c.get("item") or ""
    return item.split(":", 1)[1] if item.startswith("finding:") else None


def annotate_exercised(net, dec):
    m_ex, c_ex = defaultdict(list), defaultdict(list)
    for x in dec["decisions"]:
        inv = x.get("investigation") or {}
        for m in inv.get("metrics_inspected") or []:
            if m.get("evidence") == "OBSERVED" or m.get("sufficiency") in ("RETRIEVED_AND_VALIDATED",
                                                                           "PRESENT_BUT_UNVERIFIED"):
                m_ex[(m["metric"], x["cop"])].append(x["decision_id"])
        for c in inv.get("cops_consulted") or []:
            up = _consulted_cop(c)
            if up and c.get("present", True) is not False:
                c_ex[(up, x["cop"], "consumes")].append(x["decision_id"])
        for a in x.get("resolution_attempts") or []:
            if (a.get("kind") == "cop_referral" and a.get("receiver") and a.get("status") != "NOT_OPENED"
                    and str(a.get("event", "")).startswith("request_for_support")):
                c_ex[(x["cop"], a["receiver"], "referral")].append(x["decision_id"])
    for e in net["metric_edges"]:
        ids = sorted(set(m_ex.get((e["metric_id"], e["cop_id"]), [])))
        e["exercised"] = {"count": len(ids), "decision_ids": ids}
    known = {(e["from"], e["to"], e["kind"]) for e in net["cop_edges"]}
    for e in net["cop_edges"]:
        k = "consumes" if e["kind"] == "consumes" else e["kind"]
        ids = sorted(set(c_ex.get((e["from"], e["to"], k), [])))
        e["exercised"] = {"count": len(ids), "decision_ids": ids}
    for (a, b, k), ids in sorted(c_ex.items()):
        if k == "referral" and (a, b, "referral") not in known:
            net["cop_edges"].append({"from": a, "to": b, "kind": "referral", "executed": True,
                                     "rule": "a recorded runtime referral (request_for_support) from the sender to "
                                             "the receiver", "source": "collector/cops/runtime/referrals.py",
                                     "exercised": {"count": len(set(ids)), "decision_ids": sorted(set(ids))}})
    net["exercised_rule"] = EXERCISED_RULE


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    net = build_network()
    net["meta"] = _meta([SOURCES["mapping_doc"], SOURCES["topology_doc"], SOURCES["metrics_config"],
                         inspect.getsourcefile(SP), inspect.getsourcefile(SU), inspect.getsourcefile(signal_metrics)])
    dec = build_decisions()
    dec["meta"] = _meta(dec.pop("_files"))
    annotate_exercised(net, dec)
    ret = build_retrieval()
    ret["meta"] = _meta([inspect.getsourcefile(sys.modules["cops.runtime.evidence_query"])])
    act = build_activation()
    act["meta"] = _meta(act.pop("_files"))
    for name, obj in (("network.json", net), ("decisions.json", dec), ("retrieval.json", ret),
                      ("activation.json", act)):
        with open(os.path.join(a.out, name), "w") as f:
            json.dump(obj, f, indent=1, default=str, sort_keys=False)
    print(f"cops {len(net['cops'])}  metrics {len(net['metrics'])}  metric edges {len(net['metric_edges'])}  "
          f"cop edges {len(net['cop_edges'])}  hierarchy {net['hierarchy']['status']}")
    print(f"decisions {len(dec['decisions'])}  sessions {len(dec['sessions'])}")
    print(f"activation {act['status']}  scenarios {len(act['scenarios'])}  "
          f"records {sum(len(x['records']) for x in act['scenarios'])}")


if __name__ == "__main__":
    main()
