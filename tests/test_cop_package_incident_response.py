"""Incident Response package: contract, metric-to-question mapping, harness against ground truth, the observation and
interpretation layers, grounds and correlation, severity, state, intervention and authority, the model read-back,
memory, previous-cycle feedback and the decision record. Synthetic data; every model reply is a scripted fixture
(simulated). No paid model call."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.incident_response import harness as H
from cops.runtime import cop_package as P, evidence_assessment as EA
from cops.runtime.contracts import ORIGIN_LLM
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS, UNSEQUENCED
from cops.runtime.graph import INTERPRETED
from cops.specs import BY_KEY, CONSUMES, FEEDBACK, consumers

COP = "incident_response"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
# COPS- implementation 1.xlsx, COMPLETE_DELEGATION_DATA row 104, and metric_registry.json consumers
BUILT = ("insights.secrets", "insights.sensitive_unasked", "insights.blocked", "insights.outside",
         "interaction.subagent_lanes", "interaction.mcp_servers", "access.first_seen_mcp", "insights.unattributed",
         "insights.failed", "insights.reverted", "insights.retry", "insights.stalled", "telemetry.collector_coverage",
         "telemetry.source_agreement", "telemetry.turn_linkage", "telemetry.lane_linkage", "telemetry.unknown_share",
         "insights.no_oversight", "oversight.fast_approvals", "oversight.declined_inferred")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


def res(bench, name):
    return bench["results"][name]


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_registry_metric_is_mapped_and_unbuilt_ones_are_not_calculated():
    pkg = EA.load_package(COP)
    unbuilt = {m for m in pkg["mapping"] if m.startswith("incident_")}
    assert len(pkg["mapping"]) == 34 and len(unbuilt) == 14 and set(pkg["mapping"]) - unbuilt == set(BUILT)
    assert all(pkg["mapping"][m]["note"].startswith("NOT_CALCULATED") for m in unbuilt)
    for q in pkg["questions"].values():
        assert all(r["kind"] == "OPTIONAL" for r in q["requires"] if r["metric"] in unbuilt)
    req = {r["metric"] for q in pkg["questions"].values() for r in q["requires"] if r["kind"] == "REQUIRED"}
    assert req == set(BY_KEY[COP].metric_keys)
    assert set(pkg["not_calculated"]) == set(BY_KEY[COP].missing_metrics)


def test_only_the_containment_plan_uses_the_model_and_the_tool_is_wired():
    ops = EA.load_package(COP)["operations"]
    assert [k for k, v in ops.items() if v["by"] == "llm"] == ["containment_plan"]
    assert DETERMINISTIC_TOOLS[COP] == ("check_exposure", "assess_incident_questions")
    assert "assess_incident_questions" in TOOLS and "assess_incident_questions" in UNSEQUENCED
    assert COP in EA.PACKAGED and COP in INTERPRETED


def test_every_package_file_exists():
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills", "cops-incident-response",
                                       "SKILL.md"))


# ---------------------------------------------------------------- harness against ground truth

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], [(r["scenario"], r["expectation"], r["got"]) for r in bench["failed"]][:5]
    assert len(bench["results"]) == len(H.truth()) == 39


def test_every_scripted_model_call_is_simulated(bench):
    for name, r in bench["results"].items():
        assert all(x.get("simulated") is True for x in r["llm_invocations"]), name


# ---------------------------------------------------------------- the five readings

@pytest.mark.parametrize("name,label,category", [
    ("normal_no_incident", "NO_INCIDENT", "NONE"),
    ("isolated_anomaly_watch", "WATCH", "OBSERVED_ABNORMAL"),
    ("approval_required_without_violation", "WATCH", "VERIFIED_FINDING"),
    ("exposure_attention", "INCIDENT", "INCIDENT_ATTENTION"),
    ("verified_violation_intervention", "INCIDENT", "INCIDENT_INTERVENTION"),
    # A-IR1: a verified deterministic incident is kept when no model is available; only incident evidence that fails
    # verification is INCIDENT_UNCONFIRMED
    ("violation_without_model", "INCIDENT", "INCIDENT_INTERVENTION"),
    ("r1_incident_evidence_fails_verification", "INCONCLUSIVE", "INCIDENT_UNCONFIRMED"),
    ("incomplete_telemetry_insufficient", "INCONCLUSIVE", "INSUFFICIENT_EVIDENCE"),
])
def test_categories_are_kept_apart(bench, name, label, category):
    f = res(bench, name)["cop"]["finding"]
    assert (f["label"], f["cop_specific"]["incident_status"]["category"]) == (label, category)


def test_only_intervention_incidents_call_the_model(bench):
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        if answers(f)["IR-4"] == "INTERVENTION_JUSTIFIED":
            assert r["llm_invocations"] or f["cop_specific"].get("stop_reason") == "llm_unavailable", name
        else:
            assert not r["llm_invocations"], name


def test_correlated_findings_are_one_incident(bench):
    f = res(bench, "correlated_findings_counted_once")["cop"]["finding"]
    st = f["cop_specific"]["incident_status"]["incident"]
    assert st["count"] == 1 and st["grounds"] == ["exposure"] and st["correlated"] == ["pending_authorization"]
    f = res(bench, "simultaneous_findings_one_incident")["cop"]["finding"]
    assert f["cop_specific"]["incident_status"]["incident"]["count"] == 1


# ---------------------------------------------------------------- authority

def test_the_package_never_proposes_pause_block_or_instruct(bench):
    for name, r in bench["results"].items():
        f, pa = r["cop"]["finding"], r["cop"]["result"]["proposed_action"]
        assert f["proposed_action"]["action"] not in ("PAUSE", "BLOCK", "INSTRUCT"), name
        assert not str(pa.get("execution_status") or "").startswith("EXECUTED"), name
        assert f["cop_specific"].get("incident_status", {}).get("intervention", {}).get("executed", []) == [], name


def _model_finding(label, action, status="SUBSTANTIVE", plan="plan"):
    return {"origin": ORIGIN_LLM, "label": label, "status": status, "verified": status == "SUBSTANTIVE",
            "confidence": 0.9, "severity": "NONE", "proposed_action": {"action": action, "reason": "x"},
            "cop_specific": {"detail": {"scope_entities": ["s1"], "response_proposal": plan}}}


def test_read_back_publishes_only_a_matching_plan_and_hands_back_the_package_incident(bench):
    base = res(bench, "verified_violation_intervention")["cop"]["finding"]
    baseline = {"cop_specific": copy.deepcopy(base["cop_specific"]), "severity": "HIGH", "confidence": 0.8,
                "label": "INCIDENT", "origin": "computational"}
    baseline["cop_specific"]["decision"] = "INCIDENT"
    ok = P.interpreted_incident_response(_model_finding("INCIDENT", "REQUEST_APPROVAL"), baseline)
    assert ok["label"] == "INCIDENT" and ok["severity"] == "HIGH" and ok["confidence"] == 0.8
    assert ok["cop_specific"]["containment_plan"]["status"] == "VERIFIED"
    noplan = P.interpreted_incident_response(_model_finding("INCIDENT", "REQUEST_APPROVAL", plan=None), baseline)
    assert noplan["cop_specific"]["containment_plan"]["status"] == "UNVERIFIED"
    # anything else: the model's output is set aside and the package's own incident goes back for verification
    for model, why in ((_model_finding("INCIDENT", "BLOCK"), "stronger intervention"),
                       (_model_finding("NO_INCIDENT", "NONE"), "concluded NO_INCIDENT")):
        out = P.interpreted_incident_response(model, baseline)
        pkg = out["cop_specific"]["verify_package_finding"]
        assert pkg["label"] == "INCIDENT" and pkg["origin"] == "computational"
        assert pkg["cop_specific"]["containment_plan"]["status"] == "REJECTED"
        assert why in pkg["cop_specific"]["containment_plan"]["reason"]
        assert pkg["cop_specific"]["containment_plan"]["model_output"]["label"] == model["label"]
    gone = P.interpreted_incident_response({**_model_finding("INCONCLUSIVE", "WATCH", status="INCONCLUSIVE"),
                                            "cop_specific": {"stop_reason": "llm_unavailable"}}, baseline)
    assert gone["cop_specific"]["verify_package_finding"]["cop_specific"]["containment_plan"]["status"] == "UNAVAILABLE"
    plain = {"origin": "computational", "label": "WATCH"}
    assert P.interpreted_incident_response(plain, baseline) is plain


def test_an_incident_is_kept_whatever_the_model_does_unless_its_own_evidence_fails(bench):
    for name, plan in (("r1_verified_incident_model_unavailable", "UNAVAILABLE"),
                       ("r1_verified_incident_plan_rejected", "REJECTED"), ("r1_model_claims_no_incident", "REJECTED"),
                       ("r1_model_requests_block", "REJECTED"), ("verified_violation_intervention", "VERIFIED")):
        f = res(bench, name)["cop"]["finding"]
        assert (f["label"], f["severity"], f["proposed_action"]["action"], f["verified"]) == \
            ("INCIDENT", "HIGH", "REQUEST_APPROVAL", True), name
        assert f["cop_specific"]["containment_plan"]["status"] == plan, name
    f = res(bench, "r1_incident_evidence_fails_verification")["cop"]["finding"]
    assert f["label"] == "INCONCLUSIVE" and answers(f)["IR-1"] == "INCIDENT_INDICATED"


# ---------------------------------------------------------------- decision influence

def test_evidence_changes_change_the_answers(bench):
    v = res(bench, "evidence_change_changes_decision")["variants"]
    assert v["no_secret"]["label"] == "NO_INCIDENT"
    assert answers(v["outbound"])["IR-3"] == "ONGOING"
    assert answers(v["violation"])["IR-4"] == "INTERVENTION_JUSTIFIED"
    assert v["deny_rule"]["label"] == "WATCH" and v["cli_only"]["label"] == "INCONCLUSIVE"


# ---------------------------------------------------------------- memory and feedback

def test_memory_and_feedback_change_nothing(bench):
    m = res(bench, "memory_precedent")
    assert H._same(m["cop"]["finding"], m["without_memory"])
    assert m["remembered"]["written"] is True
    fb = res(bench, "previous_cycle_feedback_not_circular")
    assert H._same(fb["cycle2"]["finding"], fb["cycle2_without"])
    assert "risk_mitigation" not in CONSUMES[COP] and "risk_mitigation" in FEEDBACK[COP]


def test_record_holds_the_answers_when_written(bench):
    r = res(bench, "exposure_attention")["cop"]
    assert r["result"]["memory_write"]["written"] is True
    assert r["finding"]["cop_specific"]["memory_payload"]["answers"] == answers(r["finding"])


# ---------------------------------------------------------------- topology and decision record

def test_topology_document_matches_runtime_edges():
    ok, detail = H._topology_matches()
    assert ok, detail
    assert set(consumers(COP)) == {"risk_mitigation", "governance_intelligence"}


def test_decision_records_are_valid(bench):
    for name, r in bench["results"].items():
        assert r["cop"]["result"]["decision_problems"] == [], name
