"""The COPS dashboard export (collector/cops/dashboard_export.py).

Builds the three contract files from the recorded sources in the repository and checks
what the prototype relies on: all 18 COPs, edge metadata with a source or an explicit
UNDEFINED/unavailable marker, decisions with the four steps, sessions with several
decisions, the required examples, and no SQL claimed. No model is called."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops import dashboard_export as DX
from cops.specs import SPECS

EDGE_FIELDS = ("why_relevant", "interpretation", "requirement", "evidence_role", "source_collector",
               "retrieval_tool", "condition", "spec_version", "evidence_source", "code_refs", "limitations")


@pytest.fixture(scope="module")
def built():
    net = DX.build_network()
    dec = DX.build_decisions()
    dec.pop("_files")
    DX.annotate_exercised(net, dec)
    return net, dec, DX.build_retrieval()


def _marked(v):
    return isinstance(v, dict) and ("unavailable" in v or "undefined" in v)


def test_contracts(built):
    net, dec, ret = built
    assert (net["contract"], dec["contract"], ret["contract"]) == ("cops-network/1", "cops-decisions/1",
                                                                   "cops-retrieval/1")


def test_all_18_cops_from_specs(built):
    net, _, _ = built
    assert len(net["cops"]) == 18
    assert {c["id"] for c in net["cops"]} == {s.key for s in SPECS}
    for c in net["cops"]:
        assert c["behavioural_question"] and c["spec_version"]


def test_hierarchy_is_not_invented(built):
    net, _, _ = built
    h = net["hierarchy"]
    if h["status"] == "MISSING":
        assert h["missing_contract"] and h["placeholder_file"] == "docs/cops/cop_hierarchy.json"
        assert not h.get("levels")
    else:
        assert h["status"] == "DOCUMENTED" and h["source_document"]


def test_metric_edges_carry_source_or_marker(built):
    net, _, _ = built
    assert len(net["metric_edges"]) == 60
    for e in net["metric_edges"]:
        assert e["metric_id"] and e["cop_id"]
        assert e["relationships"], e["id"]
        for r in e["relationships"]:
            assert r["type"] in ("observes", "interprets", "validates", "triggers", "acts")
            assert r["source"]
        for f in EDGE_FIELDS:
            assert f in e, (e["id"], f)
            v = e[f]
            assert v not in (None, "", []) or f == "limitations", (e["id"], f)
            if isinstance(v, dict) and not _marked(v) and f in ("why_relevant", "interpretation", "evidence_role",
                                                                 "condition"):
                assert v.get("source"), (e["id"], f)


def test_undefined_interpretation_names_the_contract(built):
    net, _, _ = built
    und = [e for e in net["metric_edges"] if "undefined" in (e["interpretation"] or {})]
    assert und
    assert all(isinstance(e["interpretation"]["undefined"], str) and e["interpretation"]["undefined"] for e in und)


def test_cop_edges_and_exercised(built):
    net, _, _ = built
    kinds = {e["kind"] for e in net["cop_edges"]}
    assert {"consumes", "referral"} <= kinds
    ex = [e for e in net["cop_edges"] if e["kind"] == "referral"]
    assert ex and all(e["exercised"]["count"] > 0 for e in ex)
    assert any(e["exercised"]["count"] for e in net["metric_edges"])


def test_every_decision_has_four_steps(built):
    _, dec, _ = built
    assert len(dec["decisions"]) >= 90
    for d in dec["decisions"]:
        for k in ("trigger", "investigation", "determination", "action"):
            assert d[k], (d["decision_id"], k)
        assert d["source"]["file"] and d["execution_mode"]
        assert d["evidence_coverage"]["state"] in ("COMPLETE", "PARTIAL", "ABSENT", "UNAVAILABLE")
        a = d["action"]
        for k in ("proposed", "selected", "authority", "execution"):
            assert k in a
        assert d["investigation"]["sql_retrieval"]["performed"] is False


def test_sessions_with_several_decisions(built):
    _, dec, _ = built
    multi = {k: v for k, v in dec["sessions"].items() if len(v) > 1}
    assert any(k.startswith("scenario:successful/") for k in multi)
    assert any(k.startswith("harbor/") for k in multi)


def _one(dec, f):
    hits = [d for d in dec["decisions"] if f(d)]
    assert hits
    return hits[0]


def test_required_examples(built):
    _, dec, _ = built
    ad = _one(dec, lambda d: d["source"].get("case") == "anomaly_detection:abnormal")
    assert ad["finding"]["label"] == "ANOMALOUS"
    pe = _one(dec, lambda d: d["source"].get("case") == "policy_enforcement:abnormal")
    assert pe["action"]["proposed"]["action"] == "BLOCK"
    assert pe["action"]["authority"]["stage"] == "REJECTED_BY_AUTHORITY"
    assert pe["action"]["authority"]["selected_authorised"] is False      # proposal and final kept apart
    wo = _one(dec, lambda d: d["source"].get("variant") == "successful" and d["cop"] == "workflow_orchestration")
    assert wo["finding"]["label"] == "STALLED" and wo["trigger"]["kind"] == "cop_referral"


def test_operational_scenario_referral_revision_and_alert(built):
    _, dec, _ = built
    s = [d for d in dec["decisions"] if d["source"].get("variant") == "successful"]
    r0 = _one(dec, lambda d: d in s and d["source"]["run_key"].startswith("root:") and d["source"]["run_key"].endswith(":r0"))
    refs = [a for a in r0["resolution_attempts"] if a["kind"] == "cop_referral"]
    assert {a["receiver"] for a in refs} == {"runtime_monitoring", "behavior_analytics"}
    r1 = _one(dec, lambda d: d in s and d["source"]["run_key"].startswith("root:") and d["source"]["run_key"].endswith(":r1"))
    ch = r1["determination"]["changed"]
    assert ch["from"]["support"]["support"] != ch["to"]["support"]["support"]
    act = _one(dec, lambda d: d in s and d["source"].get("phase") == "act")
    assert act["action"]["execution"]["status"] == "EXECUTED_SANDBOX"
    assert act["action"]["gateway"]["history"][-1] == "VERIFIED"
    assert {"relation": "acts_on", "decision_id": r1["decision_id"]} in act["related"]


def test_gateway_lifecycles(built):
    _, dec, _ = built
    for rec, need in (("alert", "DELIVERED"), ("request_approval", "AWAITING_APPROVAL"),
                      ("hold_tool_call", "AWAITING_APPROVAL")):
        d = _one(dec, lambda x: x["source"].get("recording") == rec)
        hist = d["action"]["gateway"]["actions"][0]["history"]
        assert need in hist and hist[-1] == "VERIFIED"


def test_no_watch_substitution(built):
    """The export reports the runtime's selected action; it never rewrites it."""
    _, dec, _ = built
    bench = json.load(open(DX.SOURCES["bench"]))
    by_case = {c["id"]: c for c in bench["cases"]}
    for d in dec["decisions"]:
        if d["source"]["kind"] == "bench":
            sel = ((by_case[d["source"]["case"]]["trace"].get("triage") or {}).get("action_selection") or {})
            if sel.get("selected"):
                assert d["action"]["selected"]["typed_action"] == sel["selected"]


def test_retrieval_audit_claims_no_runtime_sql(built):
    _, _, ret = built
    assert all(a["reaches_database"] is False for a in ret["consumer_assessment"].values())
    assert set(ret["consumer_assessment"]) >= {"governance_intelligence", "behavior_analytics", "context_memory",
                                              "audit_traceability", "knowledge_policy_management",
                                              "agent_lifecycle_management", "policy_enforcement",
                                              "anomaly_detection", "resource_management"}


def test_export_is_deterministic(tmp_path):
    DX.main(["--out", str(tmp_path / "a")])
    DX.main(["--out", str(tmp_path / "b")])
    for f in ("network.json", "decisions.json", "retrieval.json"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()
