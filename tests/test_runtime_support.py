"""Graded verification support (cops/runtime/support.py).

Every test is deterministic and offline: no model is called. Inputs are evidence in
the shapes the graph passes: metric rows, tool calls, upstream findings, the rule
baseline and verifier results."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

from cops.runtime import support as SUP
from cops.runtime import triage as T
from cops.runtime.verifiers import R, CHECKS
from cops.specs import BY_KEY

AD_METRICS = BY_KEY["anomaly_detection"].metric_keys


def rows(metric_states, evidence=None):
    """metric -> state ('UNKNOWN' and 'MISSING' become evidence states)."""
    out = []
    for m, st in metric_states.items():
        ev = st if st in ("UNKNOWN", "MISSING", "NOT_APPLICABLE") else "OBSERVED"
        out.append({"metric": m, "evidence": ev, "state": None if ev in ("UNKNOWN", "MISSING") else st,
                    "value": 1 if ev == "OBSERVED" else None})
    return out


def quiet_rows(metrics, **override):
    return rows({m: override.get(m, "NORMAL") for m in metrics})


def call(tool, result, status="OK", cid=None):
    return {"tool": tool, "tool_call_id": cid or f"tc-{tool}", "status": status, "result": result,
            "requested_by": "rules"}


def passing(*extra):
    return [R(c, "PASS", "ok") for c in ("EV-01", "EV-02", "ID-01", "ID-02", "MC-01", "SC-01", "SC-02",
                                        "DS-01", "DS-02")] + list(extra)


def finding(label, conf=0.8, claims=(), severity="HIGH"):
    return {"label": label, "status": "SUBSTANTIVE", "confidence": conf, "claims": list(claims),
            "severity": severity}


def assess(cop, f, rws, calls=(), upstream=None, baseline=None, results=None, reliability=0.9, memory=None):
    return SUP.assess(cop, f, results if results is not None else passing(), rws, list(calls), upstream or {},
                      memory or {}, baseline, None, reliability)


def comp(sup, name):
    return next(c for c in sup["components"] if c["name"] == name)


def baseline_cmp(metric, value, lo=1.0, hi=1.0, n=7):
    return call("compare_versioned_baseline", {"metric_id": metric, "sessions_in_baseline": n,
                                               "current_value": value, "min": lo, "max": hi, "median": lo},
                cid=f"tc-cmp-{metric}")


BASE_ANOM = {"label": "ANOMALOUS", "status": "SUBSTANTIVE"}
BASE_NORM = {"label": "NORMAL", "status": "SUBSTANTIVE"}


# ================================================================ structure

def test_hard_and_graded_checks_are_separate_and_cover_every_finding_check():
    hard, graded = set(SUP.HARD_CHECKS), set(SUP.GRADED_FLOOR_CHECKS) | set(SUP.GRADED_CONSISTENCY_CHECKS)
    assert not hard & graded
    finding_checks = {k for k, v in CHECKS.items() if v[2] == "BLOCKS_FINDING" and k != "VS-01"}
    assert finding_checks <= hard | graded, finding_checks - hard - graded
    # mandatory policy and authority conditions stay hard, on the action
    assert {"PE-01", "AU-01", "AU-02"} <= set(SUP.ACTION_HARD_CHECKS)


def test_every_cop_has_explicit_components_and_derived_thresholds():
    prof = SUP.static_profiles()
    assert set(prof) == set(BY_KEY)
    for cop, p in prof.items():
        assert any(c["relevance"] == "direct" for c in p["components"]), cop
        for cls, th in p["thresholds"].items():
            assert 0 < th["provisional_min"] < th["supported_min"] <= 1, (cop, cls, th)
            # derived from the component weights, not chosen per COP
            assert th["supported_min"] == round(SUP.RHO_SUPPORTED * (th["required_weight"]
                                                + th["smallest_corroborating_weight"]) / th["total_weight"], 4)
            assert th["provisional_min"] == round(SUP.RHO_PROVISIONAL * th["required_weight"]
                                                  / th["total_weight"], 4)


def test_the_floors_are_the_runtimes_existing_telemetry_levels():
    from cops.interpret import EVIDENCE_LEVEL
    assert SUP.RHO_SUPPORTED == EVIDENCE_LEVEL["SUFFICIENT"] and SUP.RHO_PROVISIONAL == EVIDENCE_LEVEL["DEGRADED"]


# ================================================================ strong evidence

def test_strong_evidence_is_supported():
    r = quiet_rows(AD_METRICS, **{"insights.token_outlier": "CRITICAL"})
    s = assess("anomaly_detection", finding("ANOMALOUS"), r,
               [baseline_cmp("insights.token_outlier", 40.0)], baseline=BASE_ANOM)
    assert s["decision"] == SUP.SUPPORTED, s["basis"]
    assert s["score"] >= s["thresholds"]["supported_min"] and s["against"] == 0
    assert s["corroborating_sources"] == ["baseline_history", "telemetry"]   # rule baseline is not counted
    assert comp(s, "rule_baseline")["item"]["direction"] == "supports"


def test_the_score_is_the_weighted_sum_it_reports():
    r = quiet_rows(AD_METRICS, **{"insights.token_outlier": "CRITICAL"})
    s = assess("anomaly_detection", finding("ANOMALOUS"), r, [baseline_cmp("insights.token_outlier", 40.0)],
               baseline=BASE_ANOM)
    W = sum(c["weight"] for c in s["components"] if c["item"]["applicable"])
    V = sum(c["weight"] * c["item"]["reliability"] * c["item"]["strength"] for c in s["components"]
            if c["item"]["direction"] == "supports") / W
    assert abs(V - s["score"]) < 1e-3 and abs(s["score"] + s["against"] + s["unknown"] - 1) < 1e-3


# ================================================================ borderline evidence

def test_borderline_evidence_is_provisional_and_names_its_limitations():
    # one WARNING-level deviation for a LOW-severity claim, no baseline comparison, no rule agreement
    r = quiet_rows(AD_METRICS, **{"insights.duration_outlier": "WARNING"})
    s = assess("anomaly_detection", finding("ANOMALOUS", severity="LOW"), r, baseline={"label": "INCONCLUSIVE"})
    assert s["decision"] == SUP.PROVISIONAL, s["basis"]
    assert s["thresholds"]["provisional_min"] <= s["score"] < s["thresholds"]["supported_min"]
    assert any("independent source" in x for x in s["limitations"])


def test_the_same_warning_does_not_carry_a_high_severity_claim():
    """Strength is relative to the severity claimed: a WARNING is a third of a HIGH claim."""
    r = quiet_rows(AD_METRICS, **{"insights.duration_outlier": "WARNING"})
    s = assess("anomaly_detection", finding("ANOMALOUS", severity="HIGH"), r, baseline={"label": "INCONCLUSIVE"})
    assert s["decision"] == SUP.INSUFFICIENT and s["against"] == 0
    assert comp(s, "deviation_metrics")["item"]["strength"] == round(1 / 3, 4)


def test_the_same_borderline_evidence_on_unreliable_telemetry_is_insufficient_not_false():
    r = quiet_rows(AD_METRICS, **{"insights.duration_outlier": "WARNING"})
    s = assess("anomaly_detection", finding("ANOMALOUS", severity="LOW"), r, baseline={"label": "INCONCLUSIVE"},
               reliability=0.3)                                        # INSUFFICIENT telemetry level
    assert s["decision"] == SUP.INSUFFICIENT and s["against"] == 0


# ================================================================ contradictory evidence

def test_evidence_for_the_opposite_rejects_the_candidate():
    r = quiet_rows(AD_METRICS, **{"insights.token_outlier": "CRITICAL"})
    s = assess("anomaly_detection", finding("NORMAL", severity="NONE"), r,
               [baseline_cmp("insights.token_outlier", 40.0)], baseline=BASE_ANOM)
    assert s["decision"] == SUP.REJECTED and "opposite" in s["basis"]
    assert s["against"] >= s["thresholds"]["reject_min"] and s["against"] > s["score"]


def test_a_policy_deny_rejects_allowed():
    ev = {"policy_version": "p1", "approval_recorded": False, "proposed": [],
          "observed": [{"decision": "ALLOW", "rules": ["a"]}, {"decision": "DENY", "rules": ["P-prod"],
                                                                "blocked_by_hook": False}]}
    s = assess("policy_enforcement", finding("ALLOWED", severity="NONE"),
               quiet_rows(BY_KEY["policy_enforcement"].metric_keys), [call("evaluate_session_operations", ev)])
    assert s["decision"] == SUP.REJECTED
    assert comp(s, "policy_evaluation")["item"]["direction"] == "opposes"


def test_a_hard_check_failure_rejects_whatever_the_evidence():
    r = quiet_rows(AD_METRICS, **{"insights.token_outlier": "CRITICAL"})
    res = passing()[:-1] + [R("MC-01", "FAIL", "VALUE_MISMATCH")]
    s = assess("anomaly_detection", finding("ANOMALOUS"), r, [baseline_cmp("insights.token_outlier", 40.0)],
               baseline=BASE_ANOM, results=res)
    assert s["decision"] == SUP.REJECTED and s["basis"].startswith("hard check failed")


# ================================================================ missing evidence

def test_missing_evidence_is_unknown_never_evidence_against():
    s = assess("policy_enforcement", finding("VIOLATION"), quiet_rows(BY_KEY["policy_enforcement"].metric_keys),
               [call("evaluate_session_operations", {"missing_dependency": {"dependency": "policy_store"}},
                     status="MISSING_DEPENDENCY")])
    assert s["decision"] == SUP.INSUFFICIENT and s["required_unknown"] == ["policy_evaluation"]
    assert s["against"] == 0 and comp(s, "policy_evaluation")["item"]["direction"] == "unknown"


def test_low_support_for_anomalous_does_not_prove_normal():
    unknown = rows({m: "UNKNOWN" for m in AD_METRICS})
    a = assess("anomaly_detection", finding("ANOMALOUS"), unknown, baseline={"label": "INCONCLUSIVE"})
    n = assess("anomaly_detection", finding("NORMAL", severity="NONE"), unknown, baseline={"label": "INCONCLUSIVE"})
    assert a["decision"] == SUP.INSUFFICIENT and n["decision"] == SUP.INSUFFICIENT
    assert a["against"] == 0 and n["against"] == 0          # neither is disproved; both are unknown


def test_normal_needs_its_own_support_not_the_absence_of_anomaly():
    # every deviation metric quiet: that supports NORMAL directly ...
    quiet = quiet_rows(AD_METRICS)
    n = assess("anomaly_detection", finding("NORMAL", severity="NONE"), quiet,
               [baseline_cmp("insights.token_outlier", 1.0)], baseline=BASE_NORM)
    assert n["decision"] == SUP.SUPPORTED
    # ... while the same quiet metrics weigh against ANOMALOUS: these metrics define it
    a = assess("anomaly_detection", finding("ANOMALOUS"), quiet, [baseline_cmp("insights.token_outlier", 1.0)],
               baseline=BASE_NORM)
    assert a["decision"] == SUP.REJECTED


def test_a_quiet_sufficient_metric_does_not_oppose_an_attention_label():
    """insights.stalled is sufficient for STALLED, not necessary: quiet, it says nothing."""
    wo = BY_KEY["workflow_orchestration"].metric_keys
    s = assess("workflow_orchestration", finding("STALLED", severity="MEDIUM"),
               quiet_rows(wo, **{"insights.repetition": "EXCEPTION"}),
               [call("get_task_milestones", {"reached": 0, "total": 1}),
                call("analyse_execution_transitions", {"stalls": []})], baseline={"label": "INCONCLUSIVE"})
    assert comp(s, "execution_transitions")["item"]["direction"] == "neutral"
    assert comp(s, "progress_metrics")["item"]["direction"] == "supports"
    assert s["against"] == 0


# ================================================================ false-positive and false-negative risks

def test_false_positive_risk_an_unsupported_model_alarm_does_not_stand():
    """A model says ANOMALOUS with high self-reported confidence, but every deviation
    metric is quiet and inside the versioned baseline: rejected, so no ALERT follows."""
    quiet = quiet_rows(AD_METRICS)
    s = assess("anomaly_detection", finding("ANOMALOUS", conf=0.99), quiet,
               [baseline_cmp("insights.token_outlier", 1.0)], baseline=BASE_NORM)
    assert s["decision"] == SUP.REJECTED


def test_false_negative_risk_a_quiet_reading_over_an_attention_state_does_not_pass():
    """A quiet label while one of the COP's own metrics is in an attention state."""
    s = assess("anomaly_detection", finding("NORMAL", conf=0.95, severity="NONE"),
               quiet_rows(AD_METRICS, **{"path.transition_novelty": "EXCEPTION"}),
               [baseline_cmp("path.transition_novelty", 0.55, 0.0, 0.2)], baseline=BASE_ANOM)
    assert s["decision"] == SUP.REJECTED


def test_false_negative_risk_a_quiet_reading_on_missing_records_does_not_pass():
    wo = BY_KEY["workflow_orchestration"].metric_keys
    s = assess("workflow_orchestration", finding("NORMAL", severity="NONE"), quiet_rows(wo),
               [call("get_task_milestones", {"missing_dependency": {"dependency": "task_plan"}},
                     status="MISSING_DEPENDENCY"),
                call("analyse_execution_transitions", {"stalls": []})], baseline={"label": "INCONCLUSIVE"})
    assert s["decision"] == SUP.INSUFFICIENT and s["required_unknown"] == ["task_milestones"]


def test_self_reported_confidence_is_not_an_input():
    r = quiet_rows(AD_METRICS, **{"insights.duration_outlier": "WARNING"})
    lo = assess("anomaly_detection", finding("ANOMALOUS", conf=0.05), r, baseline={"label": "INCONCLUSIVE"})
    hi = assess("anomaly_detection", finding("ANOMALOUS", conf=0.99), r, baseline={"label": "INCONCLUSIVE"})
    assert (lo["score"], lo["against"], lo["decision"]) == (hi["score"], hi["against"], hi["decision"])


# ================================================================ Risk Mitigation recomputation

REGISTER = {"version": "r1", "thresholds": {"EMERGING": 2, "MATERIAL": 6},
            "risks": [{"id": "hi", "impact": 3, "min_sensitivity_tier": 3}, {"id": "lo", "impact": 1,
                                                                             "min_sensitivity_tier": 0}]}


def risk_calls(tier, signals):
    return [call("read_risk_register", REGISTER),
            call("calculate_risk_components", {"independent_verified_signals": signals,
                                               "max_sensitivity_tier": tier, "irreversible_operations": []})]


def test_risk_label_is_checked_against_the_register_recomputation():
    rm = BY_KEY["risk_mitigation"].metric_keys
    hot = quiet_rows(rm, **{"insights.secrets": "EXCEPTION"})
    ok = assess("risk_mitigation", finding("EMERGING", severity="MEDIUM"), hot, risk_calls(3, ["policy_enforcement"]),
                baseline={"label": "EMERGING", "status": "SUBSTANTIVE"})
    assert comp(ok, "register_score")["item"]["direction"] == "supports"          # 3 x (1+0) = 3 -> EMERGING
    over = assess("risk_mitigation", finding("MATERIAL", severity="HIGH"), hot, risk_calls(3, ["policy_enforcement"]),
                  baseline={"label": "EMERGING", "status": "SUBSTANTIVE"})
    assert comp(over, "register_score")["item"]["strength"] == 0.5               # same class, another label
    low = assess("risk_mitigation", finding("MATERIAL", severity="HIGH"), quiet_rows(rm), risk_calls(0, []),
                 baseline={"label": "LOW", "status": "SUBSTANTIVE"})
    assert low["decision"] == SUP.REJECTED                                         # register says LOW


# ================================================================ triage uses it, quotient stays separate

def _tri_finding(vs, label, action, provisional=False):
    return {"cop": "policy_enforcement", "case_id": "c", "label": label, "status": "SUBSTANTIVE",
            "verified": True, "confidence": 0.85, "origin": "computational", "claims": [],
            "proposed_action": {"action": action, "reason": "r"}, "missing_evidence": [],
            "missing_dependencies": [], "upstream_used": [], "cop_specific": {},
            "verification_support": vs, "provisional": provisional}


def _policy_vs(decision):
    ev = {"policy_version": "p1", "approval_recorded": False, "proposed": [{"decision": "DENY", "rules": ["x"]}],
          "observed": [{"decision": "ALLOW", "rules": ["a"]}]}
    s = assess("policy_enforcement", finding("VIOLATION"), quiet_rows(BY_KEY["policy_enforcement"].metric_keys),
               [call("evaluate_session_operations", ev)])
    return {**s, "decision": decision}


def test_a_provisional_finding_cannot_select_a_permission_action():
    t = T.classify("policy_enforcement", _tri_finding(_policy_vs(SUP.PROVISIONAL), "VIOLATION", "BLOCK", True),
                   passing(), {"items": []}, [], {}, 1.0, {}, None)
    assert t["action_selection"]["selected"] == "ALERT" and "PROVISIONALLY_SUPPORTED" in t["action_selection"]["reason"]
    sup = T.classify("policy_enforcement", _tri_finding(_policy_vs(SUP.SUPPORTED), "VIOLATION", "BLOCK"),
                     passing(), {"items": []}, [], {}, 1.0, {}, None)
    assert sup["action_selection"]["selected"] == "BLOCK_OPERATION"


def test_the_quotient_reads_support_but_is_a_separate_measure():
    vs = _policy_vs(SUP.SUPPORTED)
    a = T.classify("policy_enforcement", _tri_finding(vs, "VIOLATION", "ALERT"), passing(), {"items": []},
                   [], {}, 1.0, {}, None)
    b = T.classify("policy_enforcement", _tri_finding(vs, "VIOLATION", "BLOCK"), passing(), {"items": []},
                   [], {}, 1.0, {}, None, approval_actions=("BLOCK",))
    qa, qb = a["perceived_handling_quotient"], b["perceived_handling_quotient"]
    # the quotient keeps its own formula and reads the support decision only through its band
    assert a["evidence_support"]["score"] == 4 and qa["components"]["evidence_strength"]["value"] == 0.75
    assert qa["components"]["evidence_strength"] == qb["components"]["evidence_strength"]
    assert qa["value"] != qb["value"]          # same evidence, different handling (approval, reversibility)
    assert a["evidence_support"]["derived_from"] == "verification_support"


def test_insufficient_support_after_passing_hard_checks_asks_for_what_is_unknown():
    vs = assess("policy_enforcement", finding("VIOLATION"), quiet_rows(BY_KEY["policy_enforcement"].metric_keys),
                [])                                                      # policy never evaluated
    f = {**_tri_finding(vs, "INCONCLUSIVE", "WATCH"), "status": "REJECTED_BY_VERIFIER", "verified": False,
         "rejected_candidate": {"label": "VIOLATION"}}
    res = passing(R("VS-01", "FAIL", "INSUFFICIENT_SUPPORT"))
    t = T.classify("policy_enforcement", f, res, {"items": []}, [], {}, 1.0, {}, None)
    assert t["outcome"] == "INCONCLUSIVE" and t["action_selection"]["cause"] == "missing_retrievable"
    assert t["action_selection"]["selected"] == "COLLECT_EVIDENCE"


# ================================================================ in the graph

def test_every_finding_in_the_graph_carries_vs01_and_its_support_record():
    from cops.runtime import evaluation as E
    from cops.runtime.bench_cases import DET_CASES
    from cops.runtime.graph import run_case
    from cops.runtime.llm import UnavailableProvider
    from cops.runtime import live_eval as LE
    case = next(c for c in DET_CASES if c["id"] == "policy_enforcement:abnormal")
    b = case["build"]("vsgraph")
    rt = E.make_runtime("vs-graph", "vsgraph", b["rows"], b["art"], provider=UnavailableProvider())
    out = run_case(rt, cops=LE.deterministic_upstream(case["cop"]))
    for cop, r in out["results"].items():
        vs01 = [x for x in r["verifier_results"] if x["check_id"] == "VS-01"]
        assert len(vs01) == 1, cop
        assert r["finding"]["verification_support"]["version"] == SUP.SUPPORT_VERSION
    pe = out["results"]["policy_enforcement"]["finding"]
    assert pe["label"] == "VIOLATION" and pe["verification_support"]["decision"] == SUP.SUPPORTED
