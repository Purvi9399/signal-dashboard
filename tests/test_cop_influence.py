"""Upstream and earlier-cycle findings that change a downstream reading, and the
cases where they must not. Upstream findings written as dicts are test inputs in
the published-finding shape, as in test_runtime_graph.py."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E, synthetic as S
from cops.runtime.graph import _payload, _rule_ctx, run_case, run_cop
from cops.runtime.rules import RULES


def delegated_spike(sid, linked=True, parent="main"):
    """Heavy session with two subagent lanes and three MCP calls. A linked lane records its parent; with
    parent=None the link rests on the handoff's timing alone, which Interaction Discovery reads as partial (U5)."""
    s = S.Session(sid, 9).start(40000).tools(["WebFetch", "Bash"] * 3)
    for a in ("sub-a", "sub-b"):
        if linked:
            s.add(observable_type="subagent_start", agent_id=a)
    p = {"parent_agent_id": parent} if linked and parent else {}
    for i in range(3):
        s.add(observable_type="tool_request", tool_name="Read", agent_id="sub-a", tool_use_id=f"{sid}-a{i}",
              tool_arguments={"i": i}, tool_duration_ms=200, success=True, **p)
        s.add(observable_type="tool_request", tool_name="Grep", agent_id="sub-b", tool_use_id=f"{sid}-b{i}",
              tool_arguments={"i": i}, tool_duration_ms=200, success=True, **p)
        s.add(observable_type="mcp_request", tool_name="jira_search", mcp_server="jira",
              tool_use_id=f"{sid}-m{i}", tool_arguments={"i": i}, success=True)
    return s.full_instrumentation().end()


def resm(case_id, rows, sid, cops=("runtime_monitoring", "interaction_discovery", "resource_management")):
    out = run_case(E.make_runtime(case_id, sid, rows, {}, None), cops=list(cops))
    return out, out["results"]["resource_management"]["finding"]


def earlier(cop, label, verified=True, run_id="eval-earlier-cycle", **extra):
    return {"finding_id": f"f-{cop}-earlier", "cop": cop, "label": label, "status": "SUBSTANTIVE",
            "verified": verified, "lineage": [], "claims": [], "run_id": run_id,
            "decided_at": "2026-10-28T00:00:00Z", "case_id": "earlier", "session_id": "earlier", **extra}


@pytest.fixture(scope="module")
def attributed():
    return resm("inf-attr", E._estate(delegated_spike("dx")), "dx")


# ---------------------------------------------------------------- Interaction Discovery -> Resource Management

def test_verified_interaction_graph_attributes_consumption(attributed):
    out, f = attributed
    idf = out["findings"]["interaction_discovery"]
    assert idf["verified"] and idf["label"] != "PATH_PARTIAL"
    a = f["cop_specific"]["attribution"]
    assert f["label"] == "EXCESSIVE" and a["status"] == "ATTRIBUTED"
    assert a["source_finding"] == idf["finding_id"]
    assert a["subagent_lanes"] == ["sub-a", "sub-b"] and a["parallel_lanes"] == ["sub-a", "sub-b"]
    assert a["mcp_calls"] == {"jira": 3} and a["calls_by_actor"]["sub-a"] == 3
    assert {"cop": "interaction_discovery", "label": idf["label"], "finding_id": idf["finding_id"],
            "counted_as": "attribution of consumption"} in f["upstream_used"]
    assert any(c.get("upstream_cop") == "interaction_discovery" for c in f["claims"])
    assert out["findings"]["resource_management"]["verified"]


def test_a_link_inferred_from_timing_does_not_attribute_consumption():
    out, f = resm("inf-timed", E._estate(delegated_spike("dt", parent=None)), "dt")
    assert out["findings"]["interaction_discovery"]["cop_specific"]["questions"]["ID-2"]["answer"] \
        == "PARTIALLY_RECONSTRUCTED"
    assert f["cop_specific"]["attribution"]["status"] == "UNATTRIBUTED"


def test_missing_interaction_finding_leaves_consumption_unattributed(attributed):
    _, with_id = attributed
    _, f = resm("inf-noid", E._estate(delegated_spike("dx")), "dx",
                cops=("runtime_monitoring", "resource_management"))
    a = f["cop_specific"]["attribution"]
    assert f["label"] == "EXCESSIVE" and a["status"] == "UNATTRIBUTED"
    assert a["reason"] == "no verified Interaction Discovery finding"
    assert a["subagent_lanes"] == 2 and a["mcp_servers"] == 1
    assert f["confidence"] == round(with_id["confidence"] * 0.6 / 0.9, 3)
    assert not any(u["cop"] == "interaction_discovery" for u in f["upstream_used"])


def test_unverified_interaction_finding_is_not_attribution(attributed):
    out, _ = attributed
    rows = E._estate(delegated_spike("dx"))
    up = {k: out["findings"][k] for k in ("runtime_monitoring", "interaction_discovery")}
    up["interaction_discovery"] = {**up["interaction_discovery"], "verified": False}
    r = run_cop(E.make_runtime("inf-unv", "dx", rows, {}, None), "resource_management", upstream=up)
    assert r["finding"]["cop_specific"]["attribution"]["status"] == "UNATTRIBUTED"


def test_partial_interaction_path_leaves_consumption_unattributed():
    """The new jira route stands as the label; the unlinked lanes keep the graph partial (ID-2), and that
    answer, not the label, is what stops attribution."""
    out, f = resm("inf-partial", E._estate(delegated_spike("dp", linked=False)), "dp")
    idf = out["findings"]["interaction_discovery"]
    assert idf["label"] == "NEW_ROUTE" and "PATH_PARTIAL" in idf["cop_specific"]["also_held"]
    assert idf["cop_specific"]["questions"]["ID-2"]["answer"] != "FULLY_RECONSTRUCTED"
    a = f["cop_specific"]["attribution"]
    assert a["status"] == "UNATTRIBUTED" and "partial (ID-2" in a["reason"]


def test_single_lane_session_is_not_affected_by_interaction_evidence():
    rows = E._estate(E.spike_session("sx", 9))
    _, with_id = resm("inf-single-1", rows, "sx")
    _, without = resm("inf-single-2", rows, "sx", cops=("runtime_monitoring", "resource_management"))
    for f in (with_id, without):
        assert f["label"] == "EXCESSIVE" and f["cop_specific"]["attribution"]["status"] == "NOT_NEEDED"
    assert with_id["confidence"] == without["confidence"]


# ---------------------------------------------------------------- Workload Optimization -> Resource Management (earlier cycle)

def test_workload_recommendation_is_advisory_only(attributed):
    out, plain = attributed
    rows = E._estate(delegated_spike("dx"))
    up = {k: out["findings"][k] for k in ("runtime_monitoring", "interaction_discovery")}
    rec = earlier("workload_optimization", "REBALANCE", proposed_action={"action": "RECOMMEND"})
    ro = run_cop(E.make_runtime("inf-adv", "dx", rows, {}, None), "resource_management",
                 upstream=up, history={"workload_optimization": rec})
    r = ro["finding"]
    assert ro["proposed_action"]["action"] == out["findings"]["resource_management"]["proposed_action"]["action"]
    adv = r["cop_specific"]["advisory"]
    assert adv["finding_id"] == rec["finding_id"] and adv["run_id"] == rec["run_id"]
    assert adv["recommendation"] == "RECOMMEND" and adv["applied"] is False
    for k in ("label", "confidence", "severity"):
        assert r[k] == plain[k]
    assert r["cop_specific"]["consumption"] == plain["cop_specific"]["consumption"]
    assert "advisory" not in plain["cop_specific"]


# ---------------------------------------------------------------- Anomaly Detection -> Behavior Analytics (earlier cycle)

CM_LOSS = {"finding_id": "f-cm", "cop": "context_memory", "label": "CONTEXT_LOSS", "status": "SUBSTANTIVE",
           "verified": True, "lineage": ["tool_call:x"], "claims": []}


def ba(case_id, upstream=None, history=None):
    rows = E._estate(S.normal_session("n9", 9))
    out = run_cop(E.make_runtime(case_id, "n9", rows, {}, None), "behavior_analytics",
                  upstream=upstream, history=history)
    return {**out["finding"], "proposed": out["proposed_action"]["action"]}


def test_verified_earlier_anomaly_marks_a_recurring_pattern():
    hist = {"anomaly_detection": earlier("anomaly_detection", "ANOMALOUS")}
    without = ba("inf-ba-0", {"context_memory": CM_LOSS})
    f = ba("inf-ba-1", {"context_memory": CM_LOSS}, hist)
    p = f["cop_specific"]["pattern_persistence"]
    assert f["label"] == "DRIFTING" and p["status"] == "RECURRING"
    assert p["earlier_finding"]["run_id"] == "eval-earlier-cycle" and p["baseline_updated"] is False
    for k in ("label", "confidence", "severity", "proposed"):
        assert f[k] == without[k]
    assert without["cop_specific"]["pattern_persistence"]["status"] == "NO_VERIFIED_HISTORY"


def test_earlier_anomaly_absent_now_is_not_recurring():
    f = ba("inf-ba-2", history={"anomaly_detection": earlier("anomaly_detection", "ANOMALOUS")})
    assert f["label"] == "STABLE"
    assert f["cop_specific"]["pattern_persistence"]["status"] == "NOT_RECURRING"


def test_unverified_or_quiet_earlier_anomaly_is_not_pattern_evidence():
    unv = ba("inf-ba-3", {"context_memory": CM_LOSS},
             {"anomaly_detection": earlier("anomaly_detection", "ANOMALOUS", verified=False)})
    assert unv["cop_specific"]["pattern_persistence"]["status"] == "NO_VERIFIED_HISTORY"
    quiet = ba("inf-ba-4", {"context_memory": CM_LOSS}, {"anomaly_detection": earlier("anomaly_detection", "NORMAL")})
    assert quiet["cop_specific"]["pattern_persistence"]["status"] == "NO_EARLIER_DEVIATION"


# ---------------------------------------------------------------- Incident Response inputs

def ir_state(case_id, upstream, history=None):
    rows = E._estate(S.normal_session("n9", 9))
    rt = E.make_runtime(case_id, "n9", rows, {}, None)
    return rt, run_cop(rt, "incident_response", upstream=upstream, history=history)


def ir_rule(rt, st):
    return RULES["incident_response"](_rule_ctx(rt, st, [c for c in st["tool_calls"] if c["requested_by"] == "rules"]))


def test_incident_rule_counts_classification_and_resource_findings():
    up = {"classification_tagging": {**CM_LOSS, "finding_id": "f-ct", "cop": "classification_tagging",
                                     "label": "SECURITY"},
          "resource_management": {**CM_LOSS, "finding_id": "f-rm", "cop": "resource_management",
                                  "label": "EXCESSIVE"}}
    rt, st = ir_state("inf-ir-1", up)
    f = ir_rule(rt, st)
    used = {u["cop"]: u["finding_id"] for u in f["upstream_used"]}
    assert used["classification_tagging"] == "f-ct" and used["resource_management"] == "f-rm"
    assert {c.get("upstream_cop") for c in f["claims"]} >= {"classification_tagging", "resource_management"}
    rt2, st2 = ir_state("inf-ir-2", {k: {**v, "verified": False} for k, v in up.items()})
    assert not ir_rule(rt2, st2)["upstream_used"]


def test_earlier_risk_assessment_reaches_incident_response_verified_only():
    risk = earlier("risk_mitigation", "MATERIAL")
    rt, st = ir_state("inf-ir-3", {}, {"risk_mitigation": risk})
    assert ir_rule(rt, st)["cop_specific"]["earlier_risk_assessment"]["finding_id"] == risk["finding_id"]
    assert _payload(rt, st, "investigate")["earlier_cycle_findings"][0]["run_id"] == risk["run_id"]
    rt2, st2 = ir_state("inf-ir-4", {}, {"risk_mitigation": {**risk, "verified": False}})
    assert ir_rule(rt2, st2)["cop_specific"]["earlier_risk_assessment"] is None
    assert _payload(rt2, st2, "investigate")["earlier_cycle_findings"] == []
    assert st2["feedback_view"][0]["reason"] == "unverified; this edge delivers verified findings only"
