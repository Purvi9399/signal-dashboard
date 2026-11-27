"""Recovery & Resilience package: contract, metric-to-question mapping, harness against ground truth, the observation
and interpretation layers, the intervention and recovery states, authority, the Incident Response relationship,
memory, topology and the decision record. Synthetic data; no model is called."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.recovery_resilience import harness as H
from cops.runtime import cop_package as P, evidence_assessment as EA
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS
from cops.runtime.profiles import DETERMINISTIC_COPS
from cops.specs import BY_KEY, CONSUMES, FEEDBACK, consumers

COP = "recovery_resilience"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
# metric_registry.json, catalogue metrics with no approved threshold (reported, never judged)
REPORTED = ("recovery_disruption_count", "recovery_failure_rate", "recovery_time", "recovery_workflow_resumption_rate",
            "recovery_retry_recurrence", "recovery_stability_duration")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def res(bench, name):
    return bench["results"][name]


def fin(bench, name):
    return res(bench, name)["cop"]["finding"]


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


def status(bench, name):
    return fin(bench, name)["cop_specific"]["recovery_status"]


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_mapping_counts_and_required_metrics():
    pkg = EA.load_package(COP)
    m = pkg["mapping"]
    nc = {k for k, v in m.items() if str(v.get("note", "")).startswith("NOT_CALCULATED")}
    assert len(m) == 31 and len(nc) == 10
    assert set(REPORTED) <= set(m) and not set(REPORTED) & nc
    req = {r["metric"] for q in pkg["questions"].values() for r in q["requires"]
           if r["kind"] in ("REQUIRED", "REQUIRED_WHEN_APPLICABLE")}
    assert req == set(BY_KEY[COP].metric_keys)
    for q in pkg["questions"].values():
        assert all(r["kind"] == "OPTIONAL" for r in q["requires"] if r["metric"] in nc)
    assert set(pkg["not_calculated"]) == {"recovery_success_after_failure", "irreversible_change"}
    assert pkg["model_role"] == "none" and all(v["by"] != "llm" for v in pkg["operations"].values())


def test_tool_wiring_and_files():
    assert DETERMINISTIC_TOOLS[COP] == ("get_recovery_records", "validate_checkpoints", "assess_recovery_questions")
    assert "assess_recovery_questions" in TOOLS and COP in EA.PACKAGED and COP in DETERMINISTIC_COPS
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills",
                                       "cops-recovery-resilience", "SKILL.md"))


# ---------------------------------------------------------------- harness against ground truth

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], [(r["scenario"], r["expectation"], r["got"]) for r in bench["failed"]][:5]
    assert len(bench["results"]) == len(H.truth()) == 27


def test_no_model_is_called(bench):
    for name, r in bench["results"].items():
        assert not r["llm_invocations"], name


# ---------------------------------------------------------------- labels and categories

@pytest.mark.parametrize("name,label,action,category", [
    ("recovered_verified_stable", "RECOVERED", "NONE", "RECOVERED_VERIFIED"),
    ("partial_recovery", "PARTIAL_RECOVERY", "WATCH", None),
    ("failed_recovery_checks", "FAILED_RECOVERY", "ALERT", "RECOVERY_FAILED"),
    ("irreversible_change", "IRREVERSIBLE", "ALERT", None),
    ("attempted_unconfirmed", "INCONCLUSIVE", "WATCH", "RECOVERY_UNCONFIRMED"),
    ("successful_block_not_recovered", "INCONCLUSIVE", "WATCH", "RECOVERY_UNCONFIRMED"),
    ("approval_pending", "INCONCLUSIVE", "WATCH", "APPROVAL_PENDING"),
    ("no_disruption", "INCONCLUSIVE", "NONE", "NO_DISRUPTION"),
])
def test_readings(bench, name, label, action, category):
    f = fin(bench, name)
    assert (f["label"], f["proposed_action"]["action"]) == (label, action)
    if category:
        assert f["cop_specific"]["recovery_status"]["category"] == category


def test_partial_recovery_stays_distinct_from_complete_recovery(bench):
    for name in ("partial_recovery", "recurrence_after_recovery"):
        f = fin(bench, name)
        assert f["label"] == "PARTIAL_RECOVERY" and f["severity"] == "MEDIUM", name
        assert f["cop_specific"]["recovery_status"]["recovery"]["outcome"] != "RECOVERED", name


# ---------------------------------------------------------------- the seven states

def test_executed_action_is_not_recovery(bench):
    for name in ("attempted_unconfirmed", "successful_block_not_recovered", "adversarial_self_reported_success"):
        s = status(bench, name)
        assert s["intervention"]["executed_successfully"] and not s["recovery"]["verified"], name
        assert fin(bench, name)["label"] != "RECOVERED", name


def test_approval_states_are_kept_apart(bench):
    s = status(bench, "approval_pending")
    assert s["intervention"]["recommended"] and s["intervention"]["approval_pending"]
    assert not s["intervention"]["attempted"] and not s["intervention"]["executed_successfully"]
    s = status(bench, "approval_rejected")
    assert not s["intervention"]["approved"] and not s["intervention"]["attempted"]


def test_missing_records_and_resumption_are_not_recovered(bench):
    for name in ("missing_recovery_records", "empty_records_resumption_observed"):
        f = fin(bench, name)
        assert f["label"] == "INCONCLUSIVE" and answers(f)["RR-2"] == "INCONCLUSIVE", name


# ---------------------------------------------------------------- authority

def test_actions_stay_within_authority_and_nothing_is_executed(bench):
    allowed = set(BY_KEY[COP].permitted_actions)
    assert allowed == {"NONE", "WATCH", "ALERT"}
    for name, r in bench["results"].items():
        s = r["cop"]["finding"]["cop_specific"]["recovery_status"]
        assert s["intervention"]["executed_by_this_cop"] == [], name
        assert not str(r["cop"]["result"]["proposed_action"].get("execution_status") or "").startswith("EXECUTED")


# ---------------------------------------------------------------- Incident Response relationship

def test_incident_does_not_establish_recovery_failure(bench):
    r = res(bench, "incident_does_not_establish_failure")
    assert r["downstream"]["incident_response"]["finding"]["label"] == "INCIDENT"
    assert r["cop"]["finding"]["label"] not in ("FAILED_RECOVERY", "IRREVERSIBLE")


def test_failed_recovery_carries_checkable_grounds_and_is_unchanged_by_ir(bench):
    r = res(bench, "failed_recovery_grounds_incident")
    assert r["cop"]["finding"]["label"] == "FAILED_RECOVERY"
    assert H._same(r["cop"]["finding"], r["without_ir"])
    assert r["artifacts_unchanged"]


def test_this_cop_reads_no_incident_response_finding():
    assert CONSUMES[COP] == ("behavior_analytics",)
    assert "incident_response" not in FEEDBACK.get(COP, ())
    assert set(consumers(COP)) == {"incident_response", "governance_intelligence"}


# ---------------------------------------------------------------- memory, evidence, topology

def test_memory_precedent_changes_nothing(bench):
    r = res(bench, "recurrent_incident_precedent")
    assert r["remembered"]["written"] is True
    assert H._same(r["cop"]["finding"], r["without_memory"])


def test_recovered_is_written_to_memory(bench):
    assert res(bench, "recovered_verified_stable")["cop"]["result"]["memory_write"]["written"] is True


def test_evidence_changes_change_the_decision(bench):
    ok, got = H._variants_ok(res(bench, "evidence_change_changes_decision"))
    assert ok, got


def test_adversarial_text_changes_nothing(bench):
    r = res(bench, "adversarial_instruction_text")
    assert H._same(r["cop"]["finding"], r["without_text"])


def test_topology_document_matches_runtime_edges():
    ok, detail = H._topology_matches()
    assert ok, detail


def test_decision_records_are_valid(bench):
    for name, r in bench["results"].items():
        assert r["cop"]["result"]["decision_problems"] == [], name
