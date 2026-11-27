"""Behavior Analytics package: contract, metric-to-question mapping, harness against ground truth, event recount,
temporal logic, evidence independence (Anomaly Detection, memory), authority, downstream consumption (Anomaly
Detection, Recovery & Resilience) and topology. Synthetic data; no model is called."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.behavior_analytics import harness as H
from cops.packages.recovery_resilience import harness as RRH
from cops.runtime import cop_package as P, evidence_assessment as EA
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS
from cops.runtime.profiles import DETERMINISTIC_COPS
from cops.specs import BY_KEY, CONSUMES, FEEDBACK, FEEDBACK_VERIFIED_ONLY, consumers
import signal_metrics

COP = "behavior_analytics"
REPORTED = ("behavior_action_sequence_frequency", "behavior_tool_usage_distribution",
            "behavior_tool_transition_frequency", "behavior_model_variation", "behavior_repetition_trend",
            "behavior_retry_trend", "behavior_failure_trend")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def fin(bench, name):
    return bench["results"][name]["cop"]["finding"]


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_mapping_counts_and_required_metrics():
    pkg = EA.load_package(COP)
    m = pkg["mapping"]
    nc = {k for k, v in m.items() if str(v.get("note", "")).startswith("NOT_CALCULATED")}
    assert len(m) == 37 and len(nc) == 10
    assert set(REPORTED) <= set(m) and not set(REPORTED) & nc
    req = {r["metric"] for q in pkg["questions"].values() for r in q["requires"]
           if r["kind"] in ("REQUIRED", "REQUIRED_WHEN_APPLICABLE")}
    assert req == set(BY_KEY[COP].metric_keys)
    assert pkg["model_role"] == "none" and COP in DETERMINISTIC_COPS


def test_tool_registered_last():
    assert DETERMINISTIC_TOOLS[COP][-1] == "assess_behaviour_questions" and "assess_behaviour_questions" in TOOLS
    assert COP in EA.PACKAGED


# ---------------------------------------------------------------- harness

def test_harness_meets_ground_truth(bench):
    assert bench["failed"] == [], bench["failed"][:3]
    assert bench["superseded"] == []


# ---------------------------------------------------------------- event identity

def _row(i, **k):
    return dict(observable_id=f"o{i}", tool_use_id=k.pop("uid", f"u{i}"), command="make deploy", exit_code=1,
                success=False, occurred_at=f"2026-10-09T10:0{i}:00Z", **k)


def test_recount_dedupes_one_event_seen_three_times():
    cfg = signal_metrics.load_config()
    rows = [_row(i, uid="same") for i in range(3)]
    rc = P.ba_recount(rows, cfg)
    assert rc["insights.retry"]["value"] == 0 and rc["insights.failed"]["value"] == 1


def test_recount_leaves_out_disputed_rows():
    cfg = signal_metrics.load_config()
    rows = [_row(i, collectors_agree=False) for i in range(3)]
    assert P.ba_recount(rows, cfg)["insights.retry"]["state"] == "NOT_APPLICABLE"
    assert P.ba_recount(rows, cfg, keep_disputed=True)["insights.retry"]["value"] == 2


def test_distinct_events_count_as_the_metric_does():
    cfg = signal_metrics.load_config()
    rc = P.ba_recount([_row(i) for i in range(6)], cfg)
    assert (rc["insights.retry"]["value"], rc["insights.retry"]["state"]) == (5, "CRITICAL")
    assert (rc["insights.failed"]["value"], rc["insights.failed"]["state"]) == (6, "EXCEPTION")


# ---------------------------------------------------------------- temporal logic

def test_one_anomaly_is_never_recurring(bench):
    s = fin(bench, "isolated_anomaly")["cop_specific"]["behaviour_status"]
    assert s["time_scope"] == "ISOLATED" and s["persistent"] is False


def test_worsening_needs_never_decreasing_ranks(bench):
    w = fin(bench, "worsening_trend")["cop_specific"]["questions"]["BA-3"]["basis"]["per_metric"]["insights.retry"]
    assert w["ranks_in_order"] == sorted(w["ranks_in_order"]) and w["reading"] == "WORSENING"
    r = fin(bench, "recovery_then_recurrence")["cop_specific"]["questions"]["BA-3"]["basis"]["per_metric"]
    assert r["insights.retry"]["reading"] == "RECURRING"


def test_unordered_session_is_not_counted_as_earlier(bench):
    b = fin(bench, "unordered_history_session")["cop_specific"]["questions"]["BA-3"]["basis"]
    assert b["unordered_history"] == ["base4"]
    assert "base4" not in [h["session_id"] for h in b["per_metric"]["insights.retry"]["history"]]


# ---------------------------------------------------------------- evidence independence

def test_ad_feedback_never_an_occurrence(bench):
    for n in ("ad_feedback_same_session", "ad_feedback_distinct_session"):
        f = fin(bench, n)
        assert f["cop_specific"]["pattern_persistence"]["counted_as_occurrence"] is False
        assert answers(f) == answers(bench["results"][n]["without_fb"])
    assert fin(bench, "ad_feedback_same_session")["cop_specific"]["pattern_persistence"]["status"] == "SAME_EVENTS"


def test_precedent_never_validates_itself(bench):
    r = bench["results"]["adversarial_memory_precedent"]
    assert answers(r["cop"]["finding"]) == answers(r["without_memory"])
    assert all(p["counted"] is False for p in r["cop"]["finding"]["cop_specific"]["questions"]["BA-3"]["basis"][
        "precedents"])


# ---------------------------------------------------------------- downstream

def test_anomaly_detection_consumes_and_marks_correlated(bench):
    r = bench["results"]["downstream_anomaly_detection"]
    assert any(x["consumer"] == "anomaly_detection" and x["producer"] == COP for x in r["consumption"])
    assert H._ad_reads(r)[0]["counted_as"].startswith("correlated")


def test_recovery_resilience_label_unchanged_by_a_pattern():
    """A world where Recovery & Resilience reads a real recovery label (a record passing 3 of 3 checks) and this COP
    reads a pattern: RR consumes the finding as context and reads the same as without this COP."""
    rows = RRH.sess(*([dict(RRH.FAIL, tool_use_id=f"s1-deploy-{i}", tool_arguments={"cmd": "deploy", "i": i})
                       for i in range(4)] + [RRH.REST, RRH.LATER]))
    art = RRH.at_restore(3)(rows)
    out = RRH._case("ba-rr-pattern", rows, art, cops=["runtime_monitoring", COP, "recovery_resilience"])[0]
    base = RRH._case("ba-rr-nopattern", rows, art, cops=["runtime_monitoring", "recovery_resilience"])[0]
    ba = out["results"][COP]["finding"]
    rr, rr0 = out["results"]["recovery_resilience"]["finding"], base["results"]["recovery_resilience"]["finding"]
    assert ba["label"] in ("LOOPING", "POOR_RECOVERY")
    assert any(x["consumer"] == "recovery_resilience" and x["producer"] == COP
               and x["finding_id"] == ba["finding_id"] for x in out["consumption"])
    view = rr["cop_specific"]["questions"]["RR-1"]["basis"]["upstream"][COP]
    assert view["ground"] is False and view["label"] == ba["label"]
    assert rr["label"] not in ("INCONCLUSIVE",)
    assert (rr["label"], rr["severity"], answers(rr)) == (rr0["label"], rr0["severity"], answers(rr0))


# ---------------------------------------------------------------- topology and authority

def test_topology():
    assert CONSUMES[COP] == ("runtime_monitoring", "context_memory", "workflow_orchestration")
    assert FEEDBACK[COP] == ("anomaly_detection",) and ("anomaly_detection", COP) in FEEDBACK_VERIFIED_ONLY
    assert set(consumers(COP)) == {"anomaly_detection", "recovery_resilience"}
    assert H._topology_matches(None)[0] and H._p_ba1_not_wired(None)[0]


def test_actions_within_authority(bench):
    for name in bench["results"]:
        f = fin(bench, name)
        assert (f.get("proposed_action") or {}).get("action") in ("NONE", "WATCH", "PASS_TO_COP"), name
        assert f["cop_specific"]["behaviour_status"]["enforced_by_this_cop"] == []
