"""
One versioned verifier registry.

Every check returns a VerifierResult with check id, version, category,
result, effect, reason code and evidence references. Effects:

    BLOCKS_FINDING   a failure means the candidate cannot be a substantive finding
    BLOCKS_ACTION    a failure means the proposed action cannot proceed
    BLOCKS_MEMORY    a failure means the memory write is refused
    DIAGNOSTIC       recorded only; changes nothing

Independence rule. A check is independent only if it can fail when the
interpreter is wrong. Re-running the same interpreter cannot (RP-01 is
therefore DIAGNOSTIC and marked independent=False). The independent checks
work from different inputs or different code:

    MC-01 recomputes metric values from raw observables with its own code,
          not signal_metrics' functions;
    SC-01 checks each claim against the stored metric row, tool result,
          upstream finding or memory record it cites;
    DS-01 checks the label against what the evidence picture permits;
    DS-02 recomputes evidence independence from lineage.

This replaces the per-COP ad hoc checks. Malhar's V01-V16 and the PR #9
VR01-VR10 could not be read from this session, so no mapping onto them is
claimed; docs/cops/RUNTIME.md lists this as an open item.
"""

import fnmatch, statistics

from ..models import INCONCLUSIVE
from ..authority import TIER, HUMAN_APPROVED_ONLY
from .contracts import (VerifierResult, ORIGIN_LLM, PASS, FAIL, WARN, NOT_RUN, BLOCKS_FINDING, BLOCKS_ACTION,
                        BLOCKS_MEMORY, DIAGNOSTIC, FINDING_STATUSES, CLAIM_KINDS, SUBSTANTIVE,
                        CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE, CLAIM_TOOL_OBSERVATION,
                        CLAIM_MEMORY_PRECEDENT, CLAIM_UPSTREAM, CLAIM_ABSENCE, OBSERVED,
                        NOT_APPLICABLE, ATTENTION_STATES, VALIDATED)
from .rules import independent_upstream
from .memory import POLICIES
from .tools import scan, latest_policy, evaluate_operation
from .requirements import required_unavailable

REGISTRY_VERSION = "2026-10-29.1"

CATEGORIES = {
    "EV": "evidence/schema",
    "ID": "identity/provenance",
    "MC": "independent metric computation",
    "SC": "semantic claim-to-evidence consistency",
    "DS": "decision-state and missing-evidence",
    "PE": "policy and entitlement",
    "AU": "proposed-action authority",
    "EX": "execution outcome",
    "MU": "memory-update validation",
    "RP": "reproducibility diagnostics",
}

# check id -> (version, category prefix, effect, independent, description)
CHECKS = {
    "EV-01": ("1", "EV", BLOCKS_FINDING, True, "label, status, confidence and claim kinds are well formed"),
    "EV-02": ("1", "EV", BLOCKS_FINDING, True, "every cited evidence reference resolves"),
    "ID-01": ("1", "ID", BLOCKS_FINDING, True, "cited metric rows carry provenance; no INVALID row is cited"),
    "ID-02": ("1", "ID", BLOCKS_FINDING, True, "cited tool calls and memory records belong to this case and tenant"),
    "MC-01": ("1", "MC", BLOCKS_FINDING, True, "cited metric values recomputed from raw observables"),
    "SC-01": ("2", "SC", BLOCKS_FINDING, True, "each claim matches the data it cites"),
    "SC-02": ("1", "SC", BLOCKS_FINDING, True, "an attention label has direct evidential support"),
    "DS-01": ("1", "DS", BLOCKS_FINDING, True, "the label is one the evidence state permits"),
    "DS-02": ("1", "DS", BLOCKS_FINDING, True, "no correlated upstream finding counted as independent"),
    "DS-03": ("1", "DS", BLOCKS_FINDING, True, "a quiet model reading is not contradicted by the rule baseline"),
    "PE-01": ("1", "PE", BLOCKS_ACTION, True, "an intervening action has a policy basis"),
    "AU-01": ("1", "AU", BLOCKS_ACTION, True, "the action is permitted for this COP and mode"),
    "AU-02": ("1", "AU", BLOCKS_ACTION, True, "an action needing approval has it before execution"),
    "EX-01": ("1", "EX", BLOCKS_ACTION, True, "an executed action's outcome was observed"),
    "MU-01": ("1", "MU", BLOCKS_MEMORY, True, "a memory write is owned, scoped, sourced and clean"),
    "RP-01": ("1", "RP", DIAGNOSTIC, False, "rule re-run or baseline agreement (not independent)"),
}


def R(check_id, result, reason, detail="", refs=(), subject="", failures=()):
    v, cat, eff, ind, _ = CHECKS[check_id]
    return VerifierResult(check_id, v, CATEGORIES[cat], result, eff, reason, detail,
                          list(refs)[:20], ind, subject, list(failures)).to_dict()


# Why a claim failed. Each code says whether the investigation could repair it
# with evidence that exists (recoverable) or whether the evidence is not there.
CITATION_MALFORMED = "CITATION_MALFORMED"      # a field the claim kind needs is absent
VALUE_MISMATCH = "VALUE_MISMATCH"              # the cited data holds a different value
CITED_SOURCE_UNAVAILABLE = "CITED_SOURCE_UNAVAILABLE"  # the cited tool call failed or holds nothing
UPSTREAM_UNVERIFIED = "UPSTREAM_UNVERIFIED"    # an unverified upstream finding used as support
UPSTREAM_MISMATCH = "UPSTREAM_MISMATCH"        # the upstream finding has another label
PRECEDENT_UNAVAILABLE = "PRECEDENT_UNAVAILABLE"
RECOVERABLE = {CITATION_MALFORMED: True, VALUE_MISMATCH: True, UPSTREAM_MISMATCH: True,
               CITED_SOURCE_UNAVAILABLE: False, UPSTREAM_UNVERIFIED: False, PRECEDENT_UNAVAILABLE: False}
# A non-substantive upstream reading. Citing one with its exact label states that the
# upstream gives nothing to rely on; it is a disclosure, never support.
NON_SUBSTANTIVE_LABELS = (INCONCLUSIVE,)


def _fail(i, x, field_, asserted, observed, code, msg):
    return {"claim": i, "statement": (x.get("statement") or "")[:200], "kind": x.get("kind"),
            "field": field_, "asserted": asserted, "observed": observed, "code": code,
            "recoverable": RECOVERABLE[code], "message": msg}


def describe():
    return [{"check_id": k, "version": v[0], "category": CATEGORIES[v[1]], "effect": v[2],
             "independent": v[3], "description": v[4]} for k, v in CHECKS.items()]


# ---------------------------------------------------------------- helpers

def _match(x, flt):
    k, v = flt.split("=")
    return str(x.get(k)).lower() == v.lower()


def _get_path(obj, path):
    """Resolve a claim's result_field against a tool result.

    Forms: 'a.b', 'list[0].field' or 'list.0.field', 'list[key=value]' (count of matches),
    'a+b' (lists concatenated), 'a+b[key=value]' (count across both lists),
    and two named filters: 'list[unresolvable]', 'list[changed]'.
    """
    if not path or not isinstance(obj, dict):
        return None
    base, _, flt = path.partition("[")
    flt = flt.rstrip("]") if flt else ""
    if "+" in base and "." not in base:
        lists = [obj.get(n) or [] for n in base.split("+")]
        if not flt:
            return [x for lst in lists for x in lst]
        return sum(1 for lst in lists for x in lst if _match(x, flt))
    cur = obj
    if path.startswith("result.") and "result" not in obj:
        path = path[len("result."):]                     # the payload shows each tool call's output under "result"
    segs, i = path.split("."), 0
    while i < len(segs):
        seg = segs[i]
        # a key that itself holds dots (a file path such as /ws/app.py): take the longest key that matches
        if isinstance(cur, dict) and seg.partition("[")[0] not in cur:
            for j in range(len(segs), i + 1, -1):
                joined = ".".join(segs[i:j])
                if joined.partition("[")[0] in cur:
                    seg, i = joined, j - 1
                    break
        i += 1
        name, _, idx = seg.partition("[")
        idx = idx.rstrip("]")
        if isinstance(cur, list) and name.isdigit():       # 'list.0.field', the form models tend to write
            cur = cur[int(name)] if int(name) < len(cur) else None
        else:
            cur = cur.get(name) if isinstance(cur, dict) else None
        if cur is None:
            return None
        if not idx:
            continue
        if "=" in idx:
            return sum(1 for x in cur if _match(x, idx))
        if idx == "unresolvable":
            return sum(1 for x in cur if x.get("refs") and x.get("resolvable", 0) < x["refs"])
        if idx == "changed":
            return sum(1 for x in cur if x.get("changed"))
        k = int(idx)
        cur = cur[k] if k < len(cur) else None
        if cur is None:
            return None
    return cur


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= max(1e-6, 0.01 * abs(float(b)))
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a == b
    return a == b or str(a) == str(b)


# ---------------------------------------------------------------- MC-01 raw recompute
#
# Written independently of signal_metrics.py: it reads raw rows and the
# estate, and shares only the thresholds file (configuration, not logic).

def _raw(case, metric_id):
    s = case.session
    if s is None:
        return None
    rows = s.rows
    if metric_id == "insights.secrets":
        return float(sum(1 for r in rows if r.get("secret_detected")) +
                     sum(1 for x in s.effects if x.get("secret_detected")))
    if metric_id == "insights.blocked":
        return float(sum(1 for r in rows if r.get("policy_rule_matched")))
    if metric_id == "insights.outside":
        return float(sum(1 for r in rows if r.get("outside_workspace")) +
                     sum(1 for x in s.effects if x.get("outside_workspace")))
    if metric_id == "insights.failed":
        return float(sum(1 for r in rows if r.get("success") is False))
    if metric_id == "insights.token_outlier":
        toks = lambda x: sum(float(r.get("total_tokens") or 0) for r in x.rows)
        est = [toks(x) for x in case.sessions if toks(x) > 0]
        if len(est) < case.cfg["min_sessions_for_baseline"]:
            return None
        med = statistics.median(est)
        return round(toks(s) / med, 2) if med else None
    if metric_id == "access.first_seen_mcp":
        mine = {r["mcp_server"] for r in rows if r.get("mcp_server")}
        before = set()
        for x in case.sessions:
            if x.operator == s.operator and (x.start or "") < (s.start or ""):
                before |= {r["mcp_server"] for r in x.rows if r.get("mcp_server")}
        return float(len(mine - before))
    if metric_id == "insights.retry":
        failed, hits = set(), 0
        for r in sorted((r for r in rows if r.get("command")), key=lambda r: r.get("occurred_at") or ""):
            k = str(r["command"]).strip()
            if k in failed:
                hits += 1
            if r.get("exit_code") not in (None, "", 0, "0", "0.0"):
                failed.add(k)
        return float(hits)
    return None


def _state_for(case, metric_id, value):
    t = case.cfg["thresholds"].get(metric_id, {})
    if value is None:
        return "UNKNOWN"
    up = t.get("direction", "above") == "above"
    for st in ("critical", "exception", "warning"):
        if st in t and ((value >= t[st]) if up else (value <= t[st])):
            return st.upper()
    return "NORMAL"


# ---------------------------------------------------------------- the checks

def verify_finding(ctx):
    """Finding-level checks. ctx keys: spec, finding, picture, events, case,
    tool_calls {id: call}, upstream {cop: finding}, memory (gateway), cop,
    baseline (computational baseline finding or None), rule_rerun (label or None)."""
    f, spec, case = ctx["finding"], ctx["spec"], ctx["case"]
    claims = f.get("claims", [])
    rows_by = {r["metric"]: r for r in ctx["picture"]["rows"]}
    events = {e.metric_id: e for e in ctx["events"]}
    calls = ctx["tool_calls"]
    up = ctx["upstream"] or {}
    out = []

    # EV-01
    probs = []
    if f.get("label") not in tuple(spec.interpretations) + (INCONCLUSIVE,):
        probs.append(f"label {f.get('label')!r} not in {spec.key}'s vocabulary")
    if f.get("status") not in FINDING_STATUSES:
        probs.append(f"status {f.get('status')!r}")
    c = f.get("confidence")
    if not isinstance(c, (int, float)) or not 0 <= c <= 1:
        probs.append(f"confidence {c!r}")
    bad = [x.get("kind") for x in claims if x.get("kind") not in CLAIM_KINDS]
    if bad:
        probs.append(f"claim kinds {bad}")
    out.append(R("EV-01", FAIL if probs else PASS, "malformed" if probs else "well_formed", "; ".join(probs)))

    # EV-02 references resolve
    s = case.session
    obs = {r.get("observable_id") for r in (s.rows if s else [])}
    steps = {x["step_id"] for x in (s.steps if s else [])}
    up_ids = {v.get("finding_id") for v in up.values()}
    unresolved = []
    for x in claims:
        for ref in x.get("evidence_refs", []):
            kind, _, rid = str(ref).partition(":")
            ok = ((kind == "observable" and rid in obs) or (kind == "step" and rid in steps)
                  or (kind == "metric" and rid in rows_by) or (kind == "tool" and rid in calls)
                  or (kind == "finding" and rid in up_ids)
                  or (kind == "memory" and ctx.get("memory") is not None))
            if kind in ("obs_acc", "obs_insigh", "obs_telem", "obs_path.", "obs_intera") or ref.startswith("obs_"):
                ok = True                        # fixture-style refs from make_fixtures.py
            if not ok:
                unresolved.append(ref)
    out.append(R("EV-02", FAIL if unresolved else PASS,
                 "unresolved_refs" if unresolved else "refs_resolve",
                 f"{len(unresolved)} unresolved" if unresolved else "", unresolved))

    # ID-01 provenance of cited metric rows
    probs = []
    for x in claims:
        m = x.get("metric_id")
        if not m:
            continue
        row = rows_by.get(m)
        if row is None:
            probs.append(f"{m} was not supplied to {spec.key}")
        elif row["evidence"] == "INVALID":
            probs.append(f"{m} is INVALID ({row['reason']}) and cannot be cited")
        elif row["evidence"] == OBSERVED and not (row.get("source") and row.get("metric_version")):
            probs.append(f"{m} has no provenance")
    out.append(R("ID-01", FAIL if probs else PASS, "provenance_missing" if probs else "provenance_ok",
                 "; ".join(probs)))

    # ID-02 tenant and case scope of tool calls and memory records
    probs = []
    for x in claims:
        tid = x.get("tool_call_id")
        if tid and (tid not in calls or calls[tid]["cop"] != ctx["cop"] or calls[tid]["case_id"] != case.case_id):
            probs.append(f"tool call {tid} is not this COP's call in this case")
        rid = x.get("record_id")
        if rid and ctx.get("memory") is not None:
            try:
                rec = ctx["memory"].get(rid, case.tenant_id, ctx["cop"], case.case_id)
                if rec is None:
                    probs.append(f"memory record {rid} not found")
            except PermissionError as e:
                probs.append(f"memory record {rid}: {e}")
    out.append(R("ID-02", FAIL if probs else PASS, "out_of_scope" if probs else "in_scope", "; ".join(probs)))

    # MC-01 independent recomputation
    checked, mism = [], []
    for x in claims:
        m = x.get("metric_id")
        if not m or x.get("kind") not in (CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE):
            continue
        raw = _raw(case, m)
        ev = events.get(m)
        if raw is None or ev is None or ev.value is None:
            continue
        checked.append(m)
        if not _same(raw, float(ev.value)):
            mism.append(f"{m}: metric row {ev.value}, raw recompute {raw}")
        if x.get("kind") == CLAIM_METRIC_VALUE and x.get("asserted_value") is not None \
                and not _same(float(x["asserted_value"]), raw):
            mism.append(f"{m}: claim asserts {x['asserted_value']}, raw recompute {raw}")
        if x.get("kind") == CLAIM_METRIC_STATE and x.get("asserted_state") and \
                x["asserted_state"] in ATTENTION_STATES + ("NORMAL",) and \
                _state_for(case, m, raw) != x["asserted_state"]:
            mism.append(f"{m}: claim asserts {x['asserted_state']}, raw value {raw} is "
                        f"{_state_for(case, m, raw)}")
    out.append(R("MC-01", FAIL if mism else (PASS if checked else NOT_RUN),
                 "recompute_mismatch" if mism else ("recomputed" if checked else "no_recomputable_claim"),
                 "; ".join(mism) or (f"recomputed {checked}" if checked else
                                     "no cited metric has an independent raw recomputation")))

    # SC-01 each claim against the data it cites. Every failure is recorded with the
    # claim, the cited field, the asserted and the observed value, and a code.
    fl, ok, disclosures = [], 0, 0
    for i, x in enumerate(claims, 1):
        k = x.get("kind")
        m = x.get("metric_id")
        refs = [str(r) for r in x.get("evidence_refs", [])]
        if k in (CLAIM_METRIC_STATE, CLAIM_METRIC_VALUE) and not m:
            named = [r for r in refs if r.startswith("metric:")]
            fl.append(_fail(i, x, None, x.get("asserted_state") if k == CLAIM_METRIC_STATE
                            else x.get("asserted_value"), None, CITATION_MALFORMED,
                            f"claim {i} ({k}) names no metric_id" +
                            (f"; its references name {', '.join(named)} but a reference is not a citation"
                             if named else "; it cites nothing")))
            continue
        if k == CLAIM_TOOL_OBSERVATION and not (x.get("tool_call_id") and x.get("result_field")):
            fl.append(_fail(i, x, x.get("result_field"), x.get("asserted_value"), None, CITATION_MALFORMED,
                            f"claim {i} (tool_observation) has no "
                            + " and no ".join(n for n, v in (("tool_call_id", x.get("tool_call_id")),
                                                             ("result_field", x.get("result_field"))) if not v)))
            continue
        if k in (CLAIM_METRIC_VALUE, CLAIM_TOOL_OBSERVATION) and x.get("asserted_value") is None:
            # no value stated against a cited value that exists: the claim cannot be checked and
            # supports nothing (an asserted null against a null field is an ordinary match below)
            if k == CLAIM_METRIC_VALUE:
                actual, where = (rows_by.get(m) or {}).get("value"), m
            else:
                c0 = calls.get(x.get("tool_call_id"))
                actual = _get_path(c0["result"], x.get("result_field")) if c0 and c0["status"] == "OK" else None
                where = f"{c0['tool'] if c0 else 'tool call ' + str(x.get('tool_call_id'))}.{x.get('result_field')}"
            if actual is not None:
                fl.append({**_fail(i, x, where, None, actual, CITATION_MALFORMED,
                                   f"{where}: no asserted_value (the data holds {actual!r}), so the claim "
                                   f"cannot be checked"), "unchecked": True})
                continue
        if k == CLAIM_METRIC_STATE:
            row = rows_by.get(m)
            if row is None or row.get("state") != x.get("asserted_state"):
                code = CITATION_MALFORMED if x.get("asserted_state") is None and row else VALUE_MISMATCH
                fl.append(_fail(i, x, f"metric:{m}.state", x.get("asserted_state"),
                                row.get("state") if row else None, code,
                                f"{m}: asserted {x.get('asserted_state')}, actual "
                                f"{row.get('state') if row else 'not supplied'}"))
            else:
                ok += 1
        elif k == CLAIM_METRIC_VALUE:
            row = rows_by.get(m)
            if row is None or row.get("value") is None or not _same(x.get("asserted_value"), row["value"]):
                fl.append(_fail(i, x, f"metric:{m}.value", x.get("asserted_value"),
                                row.get("value") if row else None,
                                VALUE_MISMATCH if row and row.get("value") is not None else CITED_SOURCE_UNAVAILABLE,
                                f"{m}: asserted value {x.get('asserted_value')}, actual "
                                f"{row.get('value') if row else 'not supplied'}"))
            else:
                ok += 1
        elif k == CLAIM_TOOL_OBSERVATION:
            call = calls.get(x.get("tool_call_id"))
            if call is None or call["status"] != "OK":
                code = CITATION_MALFORMED if call is None else CITED_SOURCE_UNAVAILABLE
                fl.append(_fail(i, x, f"tool_call:{x.get('tool_call_id')}", x.get("asserted_value"),
                                call["status"] if call else None, code,
                                f"tool call {x.get('tool_call_id')} " +
                                ("is not a call in this investigation" if call is None else
                                 f"returned {call['status']}, so it holds no result to cite")))
                continue
            got = _get_path(call["result"], x.get("result_field"))
            if not _same(x.get("asserted_value"), got):
                fl.append(_fail(i, x, f"{call['tool']}.{x.get('result_field')}", x.get("asserted_value"), got,
                                VALUE_MISMATCH, f"{call['tool']}.{x.get('result_field')}: asserted "
                                f"{x.get('asserted_value')!r}, tool returned {got!r}"))
            else:
                ok += 1
        elif k == CLAIM_UPSTREAM:
            u = up.get(x.get("upstream_cop"))
            if not u or u["label"] != x.get("upstream_label"):
                fl.append(_fail(i, x, f"finding:{x.get('upstream_cop')}.label", x.get("upstream_label"),
                                u["label"] if u else None, UPSTREAM_MISMATCH,
                                f"upstream {x.get('upstream_cop')}: asserted {x.get('upstream_label')}, "
                                f"actual {u['label'] if u else 'absent'}"))
            elif not u.get("verified") and u["label"] in NON_SUBSTANTIVE_LABELS:
                disclosures += 1      # "the upstream could not decide": accurate, and supports nothing
            elif not u.get("verified"):
                fl.append(_fail(i, x, f"finding:{x.get('upstream_cop')}.verified", True, False,
                                UPSTREAM_UNVERIFIED,
                                f"upstream {x.get('upstream_cop')} finding is not verified"))
            else:
                ok += 1
        elif k == CLAIM_MEMORY_PRECEDENT:
            rec = None
            if ctx.get("memory") is not None and x.get("record_id"):
                try:
                    rec = ctx["memory"].get(x["record_id"], case.tenant_id, ctx["cop"], case.case_id)
                except PermissionError:
                    rec = None
            if rec is None:
                fl.append(_fail(i, x, f"memory:{x.get('record_id')}", x.get("record_id"), None,
                                PRECEDENT_UNAVAILABLE, f"precedent {x.get('record_id')} not retrievable"))
            elif rec["validation_status"] != VALIDATED:
                fl.append(_fail(i, x, f"memory:{x.get('record_id')}.validation_status", VALIDATED,
                                rec["validation_status"], PRECEDENT_UNAVAILABLE,
                                f"precedent {x.get('record_id')} is {rec['validation_status']}: "
                                f"an unvalidated observation is not established truth"))
            else:
                ok += 1
        elif k == CLAIM_ABSENCE:
            ok += 1 if x.get("evidence_refs") else 0
    fails = [f_["message"] for f_ in fl]
    malformed = all(f_["code"] == CITATION_MALFORMED for f_ in fl) and fl
    out.append(R("SC-01", FAIL if fl else (PASS if ok else NOT_RUN),
                 ("claim_unchecked" if all(f_.get("unchecked") for f_ in fl) else
                  "claim_malformed" if malformed else "claim_contradicted") if fl else
                 ("claims_supported" if ok else "no_claims"),
                 "; ".join(fails[:6]) + (f" ({disclosures} upstream disclosure claim(s) not counted "
                                         f"as support)" if disclosures and not fl else ""),
                 failures=fl))

    # SC-02 direct support for an attention label
    quiet = spec.interpretations[0] if spec.interpretations else None
    direct = [x for x in claims if x.get("kind") != CLAIM_UPSTREAM]
    attention = f.get("status") == SUBSTANTIVE and f.get("label") not in (quiet, INCONCLUSIVE)
    ups = [up.get(x.get("upstream_cop")) for x in claims if x.get("kind") == CLAIM_UPSTREAM]
    transitive = bool(ups) and all(u and u.get("verified") and u.get("status") == SUBSTANTIVE
                                   and u.get("lineage") for u in ups)
    if attention and not direct and transitive:
        # Support through verified upstream findings that carry their own lineage
        # is traceable to direct evidence; it stands, qualified.
        out.append(R("SC-02", WARN, "transitive_support",
                     "attention label rests on verified upstream findings with recorded lineage"))
    elif attention and not direct:
        out.append(R("SC-02", FAIL, "no_direct_support",
                     "attention label rests only on unverified or untraceable upstream findings, or on nothing"))
    elif attention and ctx.get("evidence_ceiling") is not None and c is not None \
            and c > ctx["evidence_ceiling"] + 0.1:
        out.append(R("SC-02", WARN, "confidence_above_evidence",
                     f"confidence {c} exceeds evidence ceiling {ctx['evidence_ceiling']}; capped"))
    else:
        out.append(R("SC-02", PASS if attention else NOT_RUN,
                     "directly_supported" if attention else "not_attention_label"))

    # DS-01 decision state against the evidence picture
    pic = ctx["picture"]
    tool_ok = any(x.get("kind") == CLAIM_TOOL_OBSERVATION for x in claims)
    probs = []
    if f.get("status") == SUBSTANTIVE:
        if not pic["judgeable"] and not tool_ok and spec.metric_keys:
            probs.append("substantive label with no judgeable metric and no tool observation")
        if f.get("label") == quiet and pic["judgeable"] is False and (pic["unknown"] + pic["missing"]) \
                and not tool_ok:
            probs.append("quiet label on unknown or missing evidence: lack of evidence is not normality")
        # Runtime Monitoring's verdict on the telemetry bounds what a quiet label may claim.
        rm = up.get("runtime_monitoring")
        rm_label = rm["label"] if rm and rm.get("verified") else None
        dark = [r["metric"] for r in pic["rows"] if r["evidence"] in ("UNKNOWN", "MISSING")]
        if f.get("label") == quiet and rm_label == "INSUFFICIENT" and spec.key != "runtime_monitoring":
            probs.append("quiet label while Runtime Monitoring reports the telemetry INSUFFICIENT")
        elif f.get("label") == quiet and dark and rm_label is not None and rm_label != "SUFFICIENT" \
                and spec.key != "runtime_monitoring":
            probs.append(f"quiet label with {', '.join(dark)} unobservable under {rm_label} telemetry")
        # A label backed by a required artifact that is absent is unsupported (runtime/requirements.py).
        gaps = required_unavailable(spec.key, ctx["case"], f.get("label"), quiet) if ctx.get("case") else []
        if gaps:
            probs.append("required evidence unavailable: " + ", ".join(
                f"{g['artifact']} ({g['backs']})" for g in gaps))
        cited = [rows_by.get(x.get("metric_id")) for x in claims if x.get("metric_id")]
        if attention and cited and all(r and r["evidence"] == NOT_APPLICABLE for r in cited):
            probs.append("attention label from NOT_APPLICABLE rows only")
    elif f.get("status") in ("INCONCLUSIVE", "MISSING_DEPENDENCY"):
        if not (f.get("missing_evidence") or f.get("missing_dependencies") or f.get("rationale")):
            out.append(R("DS-01", WARN, "inconclusive_without_reason"))
            probs = None
    if probs is not None:
        out.append(R("DS-01", FAIL if probs else PASS, "state_mismatch" if probs else "state_consistent",
                     "; ".join(probs)))

    # DS-02 correlated evidence counted as independent
    counted = [u for u in f.get("upstream_used", []) if str(u.get("counted_as", "")).startswith("independent")
               or u.get("counted_as") == "corroboration"]
    if counted:
        ind = independent_upstream(spec.metric_keys, {u["cop"]: up[u["cop"]] for u in counted if u["cop"] in up})
        bad = [x["cop"] for x in ind["correlated"]]
        out.append(R("DS-02", FAIL if bad else PASS, "correlated_counted" if bad else "independent",
                     f"counted as independent but correlated: {bad}" if bad else ""))
    else:
        out.append(R("DS-02", NOT_RUN, "no_corroboration_claimed"))

    # DS-03 a quiet label from the model against a substantive non-quiet rule baseline
    base = ctx.get("baseline")
    quiet = spec.interpretations[0]
    if f.get("origin") == ORIGIN_LLM and f.get("label") == quiet and base:
        contra = (base.get("status") == "SUBSTANTIVE" and base.get("label") not in (None, quiet)
                  and base.get("label") in spec.interpretations)
        out.append(R("DS-03", FAIL if contra else PASS, "baseline_contradicts_quiet" if contra else "no_contradiction",
                     f"the rules read {base.get('label')} on the same evidence; a quiet reading must "
                     f"explain that, and cannot be published over it" if contra else ""))
    else:
        out.append(R("DS-03", NOT_RUN, "not_a_quiet_model_reading"))

    # RP-01 diagnostic only
    rr = ctx.get("rule_rerun")
    if rr is not None:
        out.append(R("RP-01", PASS if rr == f.get("label") else WARN,
                     "rerun_agrees" if rr == f.get("label") else "rerun_differs",
                     "same rules re-run; not independent evidence"))
    elif base is not None:
        out.append(R("RP-01", PASS if base.get("label") == f.get("label") else WARN,
                     "baseline_agrees" if base.get("label") == f.get("label") else "baseline_differs",
                     f"computational baseline reads {base.get('label')}; diagnostic only"))
    else:
        out.append(R("RP-01", NOT_RUN, "no_rerun"))
    return out


def au01(spec, a, mode, subject):
    """AU-01 on one action. The graph runs it twice: on the action as the COP
    proposed it, before authority.check substitutes anything, and on the action
    that goes forward. Only the first can see an unauthorised proposal."""
    tier = TIER.get(a)
    if a not in spec.permitted_actions or tier is None:
        return R("AU-01", FAIL, "not_permitted", f"{spec.key} may not {a}", subject=subject)
    if tier in HUMAN_APPROVED_ONLY and mode == "dry_run":
        return R("AU-01", FAIL, "intervene_in_dry_run", f"{a} is recorded, not executed, in dry-run",
                 subject=subject)
    return R("AU-01", PASS, "permitted", subject=subject)


def verify_action(spec, action, mode, case, finding, approval=None, execution=None):
    """Action-level checks: PE-01, AU-01, AU-02, EX-01."""
    out = []
    a = action.get("action", "NONE")
    tier = TIER.get(a)
    out.append(au01(spec, a, mode, "final_action"))
    # PE-01 an intervening or approval action needs a policy basis
    if tier in HUMAN_APPROVED_ONLY or a == "REQUEST_APPROVAL":
        ver = latest_policy(case)
        target = (finding.get("cop_specific") or {}).get("proposed") or []
        if ver is None:
            out.append(R("PE-01", FAIL if tier in HUMAN_APPROVED_ONLY else WARN, "no_policy_store",
                         "no approved policy version to base the action on"))
        elif target:
            ok = [evaluate_operation(ver, t, case.agent_scope)["decision"] in ("DENY", "REQUIRE_APPROVAL")
                  for t in target]
            out.append(R("PE-01", PASS if any(ok) else FAIL,
                         "policy_basis" if any(ok) else "policy_allows_target",
                         f"re-evaluated {len(target)} targets against {ver['version']}"))
        else:
            out.append(R("PE-01", PASS, "policy_basis_from_finding",
                         f"policy {ver['version']} in force"))
    else:
        out.append(R("PE-01", NOT_RUN, "not_intervening"))
    # AU-02
    if action.get("requires_approval"):
        if approval is None:
            out.append(R("AU-02", FAIL, "awaiting_approval"))
        elif approval.get("decision") == "approve":
            out.append(R("AU-02", PASS, "approved", f"by {approval.get('reviewer')}"))
        else:
            out.append(R("AU-02", FAIL, "rejected", f"by {approval.get('reviewer')}"))
    else:
        out.append(R("AU-02", NOT_RUN, "no_approval_needed"))
    # EX-01
    if execution is None:
        out.append(R("EX-01", NOT_RUN, "not_executed"))
    else:
        out.append(R("EX-01", PASS if execution.get("outcome_confirmed") else FAIL,
                     "outcome_confirmed" if execution.get("outcome_confirmed") else "outcome_not_observed",
                     execution.get("detail", "")))
    return out


def verify_memory_update(cop, case, record_type, payload, provenance, status, finding_verified):
    probs = []
    pol = POLICIES.get(cop, {})
    if record_type not in pol.get("writes", ()):
        probs.append(f"{cop} does not own {record_type}")
    if status == "VERIFIED" and not finding_verified:
        probs.append("VERIFIED status claimed for a finding that did not verify")
    if not provenance.get("case_id") or not provenance.get("finding_id"):
        probs.append("provenance lacks case or finding")
    if provenance.get("tenant_id") not in (None, case.tenant_id):
        probs.append("provenance names another tenant")
    flags = scan(payload)
    if flags:
        probs.append(f"payload carries instruction-shaped text: {flags[:2]}")
    return [R("MU-01", FAIL if probs else PASS, "memory_refused" if probs else "memory_ok", "; ".join(probs))]


def blocking(results, effect):
    return [r for r in results if r["result"] == FAIL and r["effect"] == effect]
