"""Graph behaviour: approvals (B3), deterministic branches (B7), edges (B6),
COP-to-COP consumption and double counting."""
import os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from cops.runtime import evaluation as E, fixtures as FX, synthetic as S
from cops.runtime.graph import run_cop, run_case, resume_case
from cops.runtime.llm import UnavailableProvider
from cops.runtime.persistence import SQLiteStore
from cops.runtime.rules import independent_upstream
from cops.specs import CONSUMES, BY_KEY, topological_order, SPECS
import cops.specs as specs_mod


# ---------------------------------------------------------------- helpers

def det(cop, rows, sid, art=None, upstream=None, **kw):
    rt = E.make_runtime(f"g-{cop}-{sid}", sid, rows, art or {}, None, **kw)
    return run_cop(rt, cop, upstream=upstream)


def pub(out):
    return out["published"]


# ---------------------------------------------------------------- B6 edges

def test_downstream_lists_are_derived_from_consumes():
    for s in SPECS:
        assert tuple(s.consumes_decisions_from) == tuple(CONSUMES[s.key])
        assert set(s.downstream_cops) == {k for k, v in CONSUMES.items() if s.key in v}


def test_topological_order_respects_every_edge_and_rejects_cycles(monkeypatch):
    order = topological_order()
    pos = {k: i for i, k in enumerate(order)}
    assert len(order) == 18
    assert all(pos[p] < pos[c] for c, ps in CONSUMES.items() for p in ps)
    bad = {**CONSUMES, "runtime_monitoring": ("governance_architecture",)}
    monkeypatch.setattr(specs_mod, "CONSUMES", bad)
    with pytest.raises(Exception):
        specs_mod.topological_order()


def test_case_graph_delivers_every_declared_edge():
    rt = E.make_runtime("g-edges", "c01", E._estate(S.normal_session("c01", 9)), {}, None)
    out = run_case(rt)
    seen = {(x["producer"], x["consumer"]) for x in out["consumption"]}
    assert seen == {(p, c) for c, ps in CONSUMES.items() for p in ps}
    assert all(x["consumed_as"] != "absent" for x in out["consumption"])


# ---------------------------------------------------------------- B7 deterministic branches

def test_runtime_monitoring_degraded():
    s = S.Session("rmd", 9).start().tools(S.NORMAL_TOOLS)          # two collectors, no wrapper
    f = pub(det("runtime_monitoring", E._estate(s.end()), "rmd"))
    assert f["label"] == "DEGRADED" and f["verified"]


def test_runtime_monitoring_insufficient_and_anomaly_baseline_indeterminate():
    rows = E._estate(S.Session("thin", 9, collectors=("cli_wrapper",)).start().tools(S.NORMAL_TOOLS).end())
    rm = pub(det("runtime_monitoring", rows, "thin"))
    assert rm["label"] == "INSUFFICIENT" and rm["verified"]
    ad = run_cop(E.make_runtime("g-ad-thin", "thin", rows, {}, None), "anomaly_detection",
                 upstream={"runtime_monitoring": rm})
    # LLM unavailable: the finding is inconclusive; the labelled baseline reads INDETERMINATE
    assert ad["finding"]["label"] == "INCONCLUSIVE"
    assert ad["finding"]["baseline"]["label"] == "INDETERMINATE"


def test_anomaly_baseline_changes_with_runtime_monitoring_verdict():
    rows = E._estate(S.normal_session("n9", 9))
    good = pub(det("runtime_monitoring", rows, "n9"))
    insufficient = {**good, "label": "INSUFFICIENT"}
    a = run_cop(E.make_runtime("g-ad1", "n9", rows, {}, None), "anomaly_detection",
                upstream={"runtime_monitoring": good})
    b = run_cop(E.make_runtime("g-ad2", "n9", rows, {}, None), "anomaly_detection",
                upstream={"runtime_monitoring": insufficient})
    assert a["finding"]["baseline"]["label"] == "NORMAL"
    assert b["finding"]["baseline"]["label"] == "INDETERMINATE"


def test_behavior_analytics_consumes_context_integrity_finding():
    rows = E._estate(S.normal_session("n9", 9))
    plain = pub(det("behavior_analytics", rows, "n9"))
    cm = {"finding_id": "f-cm", "cop": "context_memory", "label": "CONTEXT_LOSS", "status": "SUBSTANTIVE",
          "verified": True, "lineage": ["tool_call:x"], "claims": []}
    fed = pub(det("behavior_analytics", rows, "n9", upstream={"context_memory": cm}))
    unverified = pub(det("behavior_analytics", rows, "n9", upstream={"context_memory": {**cm, "verified": False}}))
    assert plain["label"] == "STABLE"
    assert fed["label"] == "DRIFTING" and fed["upstream_used"][0]["cop"] == "context_memory"
    assert unverified["label"] == "STABLE"                  # unverified upstream is not evidence


PE_RULES = [{"id": "P-read", "action": "read", "resource_pattern": "*", "effect": "allow"},
            {"id": "P-env", "action": "read", "resource_pattern": "*.env", "effect": "require_approval"},
            {"id": "P-ws", "action": "write", "resource_pattern": "/ws/*", "effect": "allow"},
            {"id": "P-prod", "action": "write", "resource_pattern": "/prod/*", "effect": "deny"},
            {"id": "P-exec", "action": "exec", "resource_pattern": "*", "effect": "allow"},
            {"id": "P-tool", "action": "tool", "resource_pattern": "*", "effect": "allow"}]


def test_policy_enforcement_paths():
    rows = E._estate(E.sensitive_session("pe1", 9))
    store = {"policy_store": S.policy_store(PE_RULES)}
    need = pub(det("policy_enforcement", rows, "pe1", store))
    assert need["label"] == "APPROVAL_REQUIRED" and need["proposed_action"]["action"] == "REQUEST_APPROVAL"
    denied = pub(det("policy_enforcement", rows, "pe1", {**store, "proposed_operations": [
        {"action": "write", "resource": "/prod/x"}]}))
    assert denied["label"] == "VIOLATION"
    assert denied["proposed_action"]["requested_action"] == "BLOCK"
    assert denied["proposed_action"]["action"] == "ALERT"                 # dry run substitutes
    assert denied["proposed_action"]["execution_status"] == "NOT_EXECUTED_DRY_RUN"
    s = S.Session("pe2", 9).start().tools(S.NORMAL_TOOLS)
    s.add(observable_type="tool_request", tool_name="Read", file_path="/ws/.env", sensitivity_tier=3,
          tool_arguments={"p": 1}, permission_decision="allow")
    # an Edit with no path matches no /ws/* rule: UNCERTAIN, not ALLOWED
    assert pub(det("policy_enforcement", E._estate(s.full_instrumentation().end()), "pe2", store))["label"] \
        == "UNCERTAIN"
    wide = {"policy_store": S.policy_store(PE_RULES + [
        {"id": "P-any-write", "action": "write", "resource_pattern": "*", "effect": "allow"}])}
    approved = pub(det("policy_enforcement", E._estate(s.rows), "pe2", wide))
    assert approved["label"] == "ALLOWED"                  # the approval was recorded in the session
    none_ = det("policy_enforcement", rows, "pe1", {})
    assert none_["finding"]["missing_dependencies"][0]["dependency"] == "policy_store"
    assert "no policy store" in none_["finding"]["rationale"]


def test_policy_enforcement_never_turns_a_new_route_into_a_violation():
    rows = E._estate(E.sensitive_session("pe3", 9))
    route = {"finding_id": "f-id", "cop": "interaction_discovery", "label": "NEW_ROUTE",
             "status": "SUBSTANTIVE", "verified": True, "lineage": ["access.first_seen_mcp"], "claims": []}
    out = pub(det("policy_enforcement", E._estate(S.normal_session("pe4", 9)), "pe4",
                  upstream={"interaction_discovery": route}))
    assert out["label"] == "UNCERTAIN" and out["proposed_action"]["action"] == "WATCH"


def test_resource_management_alternate_outcomes():
    normal = E._estate(S.normal_session("rm1", 9))
    spike = E._estate(E.spike_session("rm2", 9))
    assert pub(det("resource_management", normal, "rm1"))["label"] == "NORMAL"
    assert pub(det("resource_management", spike, "rm2"))["label"] == "EXCESSIVE"
    over = det("resource_management", normal, "rm1", {"quotas": {S.OPS: {"token_budget": 500, "synthetic": True}}})
    assert over["published"]["label"] == "UNAUTHORIZED"
    noquota = det("resource_management", normal, "rm1")
    assert noquota["finding"]["missing_dependencies"][0]["dependency"] == "budget_or_quota"


def test_recovery_outcomes():
    rows = E._estate(E.recovery_session("rr", 9))
    good = S.recovery_record("k", {"a": 1}, 3, 3)
    assert pub(det("recovery_resilience", rows, "rr", {"recovery_records": [good]}))["label"] == "RECOVERED"
    part = S.recovery_record("k", {"a": 1}, 1, 3)
    assert pub(det("recovery_resilience", rows, "rr", {"recovery_records": [part]}))["label"] == "PARTIAL_RECOVERY"
    tam = S.recovery_record("k", {"a": 1}, 3, 3, tamper=True)
    assert pub(det("recovery_resilience", rows, "rr", {"recovery_records": [tam]}))["label"] == "FAILED_RECOVERY"
    assert det("recovery_resilience", rows, "rr")["finding"]["status"] == "MISSING_DEPENDENCY"


def test_agent_lifecycle_outcomes():
    s = S.Session("al", 9).start().tools(S.NORMAL_TOOLS)
    s.add(observable_type="tool_request", tool_name="mcp__x", mcp_server="x", tool_arguments={"i": 1})
    rows = E._estate(s.full_instrumentation().end())
    reg = lambda changes: {"agent_registry": {"agents": [{"agent": S.OPS, "approved_versions": [],
                                                          "approved_models": [], "approved_mcp_servers": []}],
                                              "approved_changes": changes}}
    assert pub(det("agent_lifecycle_management", rows, "al", reg([])))["label"] == "DRIFTED"
    assert pub(det("agent_lifecycle_management", rows, "al", reg([
        {"agent": S.OPS, "change": {"field": "mcp_servers", "value": "x"}}])))["label"] == "CHANGED"
    assert pub(det("agent_lifecycle_management", rows, "al",
                   {"agent_registry": {"agents": [], "approved_changes": []}}))["label"] == "NEW"


# ---------------------------------------------------------------- B3 nested approvals

def _approval_case(tmp_path, rt_kwargs=None):
    rows = E._estate(E.sensitive_session("ap", 9))
    ck = str(tmp_path / "ck.sqlite")
    st = str(tmp_path / "st.sqlite")

    def fresh():
        conn = sqlite3.connect(ck, check_same_thread=False)
        rt = E.make_runtime("ap-case", "ap", rows, E.c05_art(), FX.Script({}), store=SQLiteStore(st),
                            mode="supervised", live=True, **(rt_kwargs or {}))
        return conn, SqliteSaver(conn), rt
    return fresh, ["runtime_monitoring", "interaction_discovery", "policy_enforcement"]


def test_interrupt_checkpoint_approve_resume_after_restart(tmp_path):
    fresh, cops = _approval_case(tmp_path)
    conn, saver, rt = fresh()
    out = run_case(rt, cops=cops, checkpointer=saver)
    assert [i["cop"] for i in out["interrupted"]] == ["policy_enforcement"]
    assert out["interrupted"][0]["action"] == "BLOCK"
    conn.close()
    conn, saver, rt = fresh()                                       # new process, same files
    out = resume_case(rt, {"decision": "approve", "reviewer": "lead"}, cops=cops, checkpointer=saver)
    pa = out["results"]["policy_enforcement"]["proposed_action"]
    assert out["interrupted"] == [] and pa["execution_status"] == "EXECUTED_MOCK"
    assert pa["approval"]["reviewer"] == "lead"
    assert rt.enforcement.applied == [{"cop": "policy_enforcement", "case_id": "ap-case",
                                       "action": "BLOCK", "target": "ap"}]
    assert len(rt.store.select("cop_decisions")) == 3


def test_reject_and_fail_closed(tmp_path):
    for answer in ({"decision": "reject", "reviewer": "lead"}, "yes", {"decision": "approve"}):
        fresh, cops = _approval_case(tmp_path / str(abs(hash(str(answer)))))
        os.makedirs(tmp_path / str(abs(hash(str(answer)))), exist_ok=True)
        fresh, cops = _approval_case(tmp_path / str(abs(hash(str(answer)))))
        conn, saver, rt = fresh()
        run_case(rt, cops=cops, checkpointer=saver)
        out = resume_case(rt, answer, cops=cops, checkpointer=saver)
        pa = out["results"]["policy_enforcement"]["proposed_action"]
        assert pa["execution_status"] == "APPROVAL_REJECTED", answer       # no reviewer, no approval
        assert rt.enforcement.applied == []
        conn.close()


def test_unconfirmed_outcome_marks_execution_failed(tmp_path):
    from cops.runtime.graph import MockEnforcementGateway
    fresh, cops = _approval_case(tmp_path, {"enforcement": MockEnforcementGateway(unconfirmed=("BLOCK",))})
    conn, saver, rt = fresh()
    run_case(rt, cops=cops, checkpointer=saver)
    out = resume_case(rt, {"decision": "approve", "reviewer": "lead"}, cops=cops, checkpointer=saver)
    r = out["results"]["policy_enforcement"]
    assert r["proposed_action"]["execution_status"] == "EXECUTION_FAILED"
    assert {x["check_id"]: x["result"] for x in r["verifier_results"]}["EX-01"] == "FAIL"


def test_dry_run_never_executes():
    rt = E.make_runtime("dry", "dr", E._estate(E.sensitive_session("dr", 9)), E.c05_art(), None)
    out = run_case(rt, cops=["runtime_monitoring", "interaction_discovery", "policy_enforcement"])
    assert out["interrupted"] == [] and rt.enforcement.applied == []
    assert all(r["proposed_action"]["execution_status"].startswith("NOT_EXECUTED")
               for r in out["results"].values())


# ---------------------------------------------------------------- double counting

def test_shared_lineage_is_correlated_not_independent():
    up = {"anomaly_detection": {"verified": True, "status": "SUBSTANTIVE", "lineage": ["insights.token_outlier"]},
          "resource_management": {"verified": True, "status": "SUBSTANTIVE", "lineage": ["insights.token_outlier"]},
          "policy_enforcement": {"verified": True, "status": "SUBSTANTIVE", "lineage": ["tool_call:p1"]}}
    out = independent_upstream(("insights.secrets",), up)
    assert [x["cop"] for x in out["independent"]] == ["anomaly_detection", "policy_enforcement"]
    assert out["correlated"][0]["cop"] == "resource_management"
    own = independent_upstream(("insights.token_outlier",), up)
    assert [x["cop"] for x in own["independent"]] == ["policy_enforcement"]
