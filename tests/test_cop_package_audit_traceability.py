"""Audit & Traceability package: contract, metric-to-question mapping, harness against ground truth,
deterministic interpretation, memory, COP-to-COP influence and the decision record. Synthetic data,
no model call."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.audit_traceability import harness as H
from cops.runtime import cop_package as P, decision as D, evaluation as E, evidence_assessment as EA
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS
from cops.specs import BY_KEY

COP = "audit_traceability"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
NOT_BUILT = ("audit_raw_reference_resolution_rate", "audit_evidence_integrity", "audit_evidence_retention_coverage",
             "audit_decision_evidence_linkage", "audit_intervention_traceability",
             "audit_policy_version_traceability", "audit_verifier_coverage", "audit_evidence_sufficiency",
             "audit_reconstruction_confidence")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def questions(name):
    """The question tool on the scenario's rows, with no upstream finding and no memory."""
    rt = E.make_runtime(f"at-unit-{name}", "s1", H.BUILD[name](), {})
    return TOOLS["assess_audit_questions"][0](rt.case, {}, {})["questions"]


def decide(q):
    return P.decide_audit_traceability(q)


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_registry_metric_is_mapped_and_the_spec_metrics_are_among_them():
    reg = P._registry()
    assigned = {m for m, r in reg.items() if COP in r.get("consumers", [])}
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == assigned and len(assigned) == 29
    assert set(BY_KEY[COP].metric_keys) <= set(pkg["mapping"])


def test_unbuilt_metrics_are_optional_and_reported_not_calculated():
    pkg = EA.load_package(COP)
    for m in NOT_BUILT:
        assert pkg["mapping"][m].get("note"), m
        kinds = [r["kind"] for q in pkg["questions"].values() for r in q["requires"] if r["metric"] == m]
        assert kinds == [EA.OPTIONAL], m
    q = questions("normal")
    items = {x["requirement"]: x for v in q.values() for x in v["assessment"]["presence"]["items"]}
    assert all(items[m]["state"] == EA.NOT_CALCULATED for m in NOT_BUILT)
    told = {u["metric"]: u["missing_inputs"] for v in q.values() for u in v["answer"]["basis"].get("unassessed", [])
            if u.get("state") == EA.NOT_CALCULATED}
    assert set(told) == set(NOT_BUILT) and all(x.startswith("NOT_CALCULATED.") for x in told.values())


def test_questions_without_workbook_fields_list_none():
    pkg = EA.load_package(COP)["questions"]
    assert pkg["AT-3"]["fields"] == [] and pkg["AT-4"]["fields"] == []


def test_every_operation_is_deterministic_and_the_cop_calls_the_question_tool():
    assert {op["by"] for op in EA.load_package(COP)["operations"].values()} == {"deterministic"}
    assert DETERMINISTIC_TOOLS[COP][-1] == "assess_audit_questions"


def test_every_package_file_exists():
    for f in ("SPEC.md", "METRICS.yaml", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py"):
        assert os.path.exists(os.path.join(PKG, f)), f


def test_each_question_reads_the_metrics_its_contract_names():
    for qid, x in questions("normal").items():
        want = [r["metric"] for r in EA.load_package(COP)["questions"][qid]["requires"]]
        assert [i["requirement"] for i in x["assessment"]["presence"]["items"]] == want


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], bench["failed"]
    assert bench["passed"] == len(bench["expectations"]) > 100


def test_every_scenario_publishes_a_valid_decision(bench):
    for name, r in bench["results"].items():
        res = r["cop"]["result"]
        assert res["decision_problems"] == [], name
        assert [x["id"] for x in res["decision"]["question_findings"]] == [f"AT-{i}" for i in range(1, 6)], name


# ---------------------------------------------------------------- interpretation

def test_one_fact_changes_the_answer_and_the_label():
    """Only the three unexplained disk changes differ between the two worlds."""
    a, b = questions("normal"), questions("effects_without_step")
    assert (a["AT-1"]["answer"]["outcome"], decide(a)["label"]) == ("ATTRIBUTED", "COMPLETE")
    assert (b["AT-1"]["answer"]["outcome"], decide(b)["label"]) == ("CAUSE_NOT_ATTRIBUTED", "BROKEN_TRACE")


def test_the_actor_decides_unattributable_and_severity_does_not():
    known, unknown = decide(questions("effects_without_step")), decide(questions("no_operator"))
    assert (known["label"], unknown["label"]) == ("BROKEN_TRACE", "UNATTRIBUTABLE")
    assert known["severity"] == unknown["severity"] == "LOW"


def test_a_source_disagreement_breaks_the_trace_only_beyond_its_threshold():
    one, many = questions("one_disagreement"), questions("sources_disagree")
    assert (one["AT-3"]["answer"]["outcome"], decide(one)["label"]) == ("DEGRADED", "PARTIAL")
    assert (many["AT-3"]["answer"]["outcome"], decide(many)["label"]) == ("UNRELIABLE", "BROKEN_TRACE")


def test_unrun_checks_are_reported_not_counted_as_degradation():
    b = questions("normal")["AT-3"]["answer"]["basis"]
    assert b["degraded"] == [] and b["unreliable"] == []
    unrun = {u.get("check") for u in b["unassessed"] if u.get("check")}
    assert unrun == {"INCOMPARABLE_TIMESTAMPS", "TIMESTAMP_ORDER", "STALE", "INGEST_LATENCY"}


def test_thin_telemetry_leaves_attribution_undecided_and_keeps_the_partial_reading():
    q = questions("thin_telemetry")
    d = decide(q)
    assert q["AT-1"]["answer"]["outcome"] == "INCONCLUSIVE"
    assert (d["label"], d["also_held"], d["drivers"]) == ("INCONCLUSIVE", ["PARTIAL"], ["AT-1"])


def test_an_actor_missing_on_thin_telemetry_is_still_unattributable():
    q = questions("thin_no_operator")
    assert q["AT-1"]["answer"]["outcome"] == "ACTOR_NOT_ESTABLISHED"
    assert decide(q)["label"] == "UNATTRIBUTABLE"


def test_restating_an_earlier_answer_does_not_add_a_second_reading():
    q = questions("no_operator")
    assert q["AT-5"]["answer"]["basis"]["own_broken"] == [] and decide(q)["also_held"] == []


def test_a_decision_citing_evidence_the_record_cannot_produce_is_broken(bench):
    f = bench["results"]["decision_evidence_unresolvable"]["cop"]["finding"]
    b = f["cop_specific"]["questions"]["AT-4"]["basis"]
    assert b["unresolvable"][0]["unresolvable"] == ["observable:ghost-1", "observable:ghost-2"]
    assert (f["label"], f["severity"]) == ("BROKEN_TRACE", "MEDIUM")


def test_the_label_never_rests_on_instruction_text_in_a_tool_result(bench):
    r = bench["results"]["adversarial_instruction_text"]
    assert r["cop"]["finding"]["label"] == r["without_text"]["label"] == "BROKEN_TRACE"


# ---------------------------------------------------------------- upstream and memory

def test_runtime_monitoring_rm3_changes_the_audit_decision(bench):
    r = bench["results"]["upstream_rm_unlinked"]
    assert (r["linked"]["finding"]["label"], r["cop"]["finding"]["label"]) == ("COMPLETE", "PARTIAL")


def test_the_record_outweighs_an_upstream_reading_and_keeps_it_as_contrary(bench):
    f = bench["results"]["contradictory_upstream"]["cop"]["finding"]
    assert f["label"] == "BROKEN_TRACE"
    assert "AT-2: Interaction Discovery ID-2 answered FULLY_RECONSTRUCTED" in f["cop_specific"]["contrary"]


def test_only_validated_trace_gaps_are_precedent(bench):
    v = bench["results"]["memory_validated_gap"]["cop"]["finding"]["cop_specific"]["questions"]["AT-5"]["basis"]
    u = bench["results"]["memory_unvalidated_gap"]["cop"]["finding"]["cop_specific"]["questions"]["AT-5"]["basis"]
    assert v["recurring"] == ["AT-1:CAUSE_NOT_ATTRIBUTED"] and v["memory_records"]
    assert u["recurring"] == [] and u["pending_in_memory"] == ["AT-1:CAUSE_NOT_ATTRIBUTED"]


def test_trace_gap_records_carry_their_gap_codes(bench):
    r = bench["results"]["effects_without_step"]["cop"]
    assert r["result"]["memory_write"]["written"] is True
    assert r["finding"]["cop_specific"]["memory_payload"]["gaps"] == ["AT-1:CAUSE_NOT_ATTRIBUTED",
                                                                      "AT-5:NOT_DEFENSIBLE"]


def test_governance_architecture_receives_the_finding(bench):
    r = bench["results"]["downstream_governance_architecture"]
    up = r["downstream"]["governance_architecture"]["decision"]["upstream_considered"]
    assert any(u["cop"] == COP and u["finding_id"] == r["cop"]["finding"]["finding_id"] for u in up)


def test_the_validator_rejects_an_answer_outside_the_contract(bench):
    d = copy.deepcopy(bench["results"]["normal"]["cop"]["result"]["decision"])
    d["question_findings"][0]["answer"] = "PROBABLY_FINE"
    assert "AT-1: answer PROBABLY_FINE is not one of its outcomes" in D.validate(d)


def test_old_evidence_degrades_reliability_only_against_an_approved_limit(bench):
    stale = bench["results"]["stale_evidence"]["cop"]["finding"]["cop_specific"]["questions"]["AT-3"]
    fresh = questions("normal")["AT-3"]["answer"]["basis"]["checks"]["STALE"]
    assert (stale["answer"], stale["basis"]["checks"]["STALE"]["outcome"]) == ("DEGRADED", "VERIFIED_FAILED")
    assert fresh["outcome"] == "NOT_EXECUTED"


def test_governance_architecture_reads_what_the_evidence_changed(bench):
    broken = bench["results"]["downstream_governance_architecture"]["ga_rule"]
    clean = bench["results"]["downstream_governance_architecture_complete"]["ga_rule"]
    want = {"AT-1": "CAUSE_NOT_ATTRIBUTED", "AT-5": "NOT_DEFENSIBLE"}
    assert broken["cop_specific"]["gap_levels"]["audit"]["unclean_answers"] == want
    assert clean["cop_specific"]["gap_levels"]["audit"]["unclean_answers"] == {}
    assert broken["label"] == clean["label"]
    assert [u["cop"] for u in broken["upstream_used"]] == [COP]


def test_unapproved_limits_and_unbuilt_metrics_are_structured_limitations(bench):
    lim = bench["results"]["normal"]["cop"]["result"]["decision"]["uncertainty"]["limitations"]
    assert {(x["question"], x["subject"]) for x in lim if x["kind"] == D.LIMIT_NOT_APPROVED} == {("AT-3", "STALE")}
    assert {x["subject"] for x in lim if x["kind"] == D.NOT_CALCULATED} == set(NOT_BUILT)
