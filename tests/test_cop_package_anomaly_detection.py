"""Anomaly Detection package: contract, metric-to-question mapping, harness against ground truth, the label from
the five answers, the model only over a deviation, memory, COP-to-COP influence and the decision record.
Synthetic data; every model reply is scripted (simulated), no provider is called."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.anomaly_detection import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, fixtures as FX, graph as G
from cops.runtime.contracts import stable_id
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import UNSEQUENCED
from cops.specs import BY_KEY

COP = "anomaly_detection"
MAPPED = ("insights.repetition", "insights.retry", "insights.failed", "telemetry.turn_linkage",
          "insights.token_outlier", "insights.duration_outlier", "token_expansion_per_turn",
          "activity.action_rate_ratio", "path.transition_novelty", "access.first_seen_mcp", "interaction.mcp_servers",
          "interaction.subagent_lanes", "telemetry.lane_linkage", "resource_access_novelty_files",
          "tool_use_deviation_per_agent", "task_adjusted_anomaly_score")
NOT_CALCULATED = ("tool_use_deviation_per_agent", "task_adjusted_anomaly_score")
DEVIATES = {"AD-1": "UNUSUALLY_FREQUENT", "AD-2": "SIGNIFICANTLY_DIFFERENT", "AD-3": "RATE_UNUSUAL",
            "AD-4": "UNFAMILIAR"}
QUIET = {"AD-1": "NOT_UNUSUAL", "AD-2": "COMPARABLE", "AD-3": "RATE_WITHIN_BASELINE", "AD-4": "FAMILIAR"}


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


def run(rows, script=None, name="t"):
    rt = E.make_runtime(f"t-ad-{name}", "s1", copy.deepcopy(rows), {}, FX.Script({COP: script}) if script else None)
    out = G.run_case(rt, cops=["runtime_monitoring", COP])
    return out["results"][COP], out["results"]["runtime_monitoring"]["finding"]


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_mapped_metric_and_the_unbuilt_ones_name_their_missing_input():
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == set(MAPPED)
    assert set(BY_KEY[COP].metric_keys) <= set(pkg["mapping"])
    for m in NOT_CALCULATED:
        assert pkg["mapping"][m]["note"].startswith("NOT_CALCULATED") and "Missin" in pkg["mapping"][m]["note"]


def test_labels_and_vocabulary_are_unchanged():
    pkg = EA.load_package(COP)
    assert set(pkg["labels"].values()) <= set(BY_KEY[COP].interpretations) | {"INCONCLUSIVE"}
    assert BY_KEY[COP].interpretations[0] == "NORMAL" and "BLOCK" not in BY_KEY[COP].permitted_actions
    assert pkg["deviation_states"] == ["EXCEPTION", "CRITICAL"]
    assert DETERMINISTIC_TOOLS[COP] == ("assess_anomaly_questions",)


# ---------------------------------------------------------------- harness against the ground truth

def test_harness_meets_every_expectation(bench):
    assert bench["failed"] == [], bench["failed"][:3]
    assert bench["passed"] == len(bench["expectations"]) >= 150


def test_the_label_follows_from_the_five_answers_in_every_scenario(bench):
    """Recomputed here from AD-1 to AD-4 and Runtime Monitoring, independently of cop_package."""
    labels = EA.load_package(COP)["labels"]
    for name, res in bench["results"].items():
        if "cop" not in res:
            continue
        f = res["cop"]["finding"]
        a = answers(f)
        rm = res["others"].get("runtime_monitoring") or {}
        deviates = any(a[q] == DEVIATES[q] for q in DEVIATES)
        if deviates:
            assert a["AD-5"] in ("MEANINGFUL_ANOMALY", "LEGITIMATE_VARIATION", "UNDETERMINED", "INCONCLUSIVE"), name
        elif all(a[q] == QUIET[q] for q in QUIET) and not (rm.get("verified") and rm.get("label") == "INSUFFICIENT"):
            assert a["AD-5"] == "NO_DEVIATION", name
        else:
            assert a["AD-5"] == "UNDETERMINED", name
        rejected = f["status"] == "REJECTED_BY_VERIFIER"
        assert f["label"] == ("INCONCLUSIVE" if rejected else labels[a["AD-5"]]), name


# ---------------------------------------------------------------- deterministic and model operations

def test_no_deviation_is_decided_without_the_model_even_if_one_is_scripted():
    r, _ = run(H.WORLDS["normal"](), H.anomalous(["insights.token_outlier"]), "normal")
    f = r["finding"]
    assert f["label"] == "NORMAL" and f["verified"] and r["llm_invocations"] == []
    assert f["cop_specific"]["stop_reason"] == G.DECIDED_WITHOUT_MODEL and f["cop_specific"]["decided_without_model"]


def test_a_deviation_without_a_model_is_inconclusive_not_anomalous():
    r, _ = run(H.WORLDS["token_spike_without_model"](), None, "nomodel")
    a = answers(r["finding"])
    assert a["AD-2"] == "SIGNIFICANTLY_DIFFERENT" and a["AD-5"] == "INCONCLUSIVE"
    assert r["finding"]["label"] == "INCONCLUSIVE"


def test_a_deviation_is_interpreted_and_the_verified_label_is_read_back_as_ad5():
    r, _ = run(H.WORLDS["token_spike_interpreted"](), H.SCRIPTS["token_spike_interpreted"](), "spike")
    f = r["finding"]
    assert r["llm_invocations"] and all(i["simulated"] for i in r["llm_invocations"])
    assert f["verified"] and f["label"] == "ANOMALOUS" and answers(f)["AD-5"] == "MEANINGFUL_ANOMALY"
    assert f["cop_specific"]["recipients"] == ["incident_response", "risk_mitigation"]


def test_the_package_tool_takes_no_place_in_the_model_call_sequence():
    r, _ = run(H.WORLDS["token_spike_interpreted"](), H.SCRIPTS["token_spike_interpreted"](), "seq")
    calls = r["tool_calls"]
    assert calls[0]["tool"] in UNSEQUENCED
    first_model = next(c for c in calls if c["tool"] not in UNSEQUENCED)
    assert first_model["tool_call_id"] == stable_id(first_model["case_id"], COP, 0, first_model["tool"])


# ---------------------------------------------------------------- unusual is not prohibited

def test_novelty_names_no_violation(bench):
    f = bench["results"]["novelty_is_not_a_violation"]["cop"]["finding"]
    text = str(f["cop_specific"]["questions"]).upper()
    assert "VIOLATION" not in text.replace("LEGITIMATE_VARIATION", "")
    assert f["label"] in BY_KEY[COP].interpretations + ("INCONCLUSIVE",)


# ---------------------------------------------------------------- downstream reads the answers

def test_incident_response_consumes_the_label_ad5_gives(bench):
    ok, got = H.CHECKS["incident_response_reads_ad5"](bench["results"]["downstream_incident_response"])
    assert ok, got
    assert {k: v["ad5"] for k, v in got.items()} == {"normal": "NO_DEVIATION", "interpreted": "MEANINGFUL_ANOMALY",
                                                     "incomplete": "UNDETERMINED"}


# ---------------------------------------------------------------- memory

def test_only_a_validated_observation_is_a_precedent_and_it_decides_nothing(bench):
    res = bench["results"]["memory_precedent_interpreted"]
    prec = res["cop"]["finding"]["cop_specific"]["questions"]["AD-5"]["basis"]["precedents"]
    assert [p["record_id"] for p in prec] == [res["remembered"]["record_id"]]
    assert res["unverified_record"] not in [p["record_id"] for p in prec]
    assert all(p["use"] == "comparison only, never proof" for p in prec)


def test_a_package_normal_writes_no_observation():
    r, _ = run(H.WORLDS["normal"](), None, "mem")
    assert r["memory_write"]["written"] is False


# ---------------------------------------------------------------- decision record

def test_every_finding_carries_a_valid_decision_record_with_five_question_findings(bench):
    for name, res in bench["results"].items():
        if "cop" not in res:
            continue
        d = res["cop"]["result"]["decision"]
        assert [x["id"] for x in d["question_findings"]] == [f"AD-{i}" for i in range(1, 6)], name


# ---------------------------------------------------------------- U-AD2, U-AD3: scoped to Anomaly Detection

def test_the_legitimacy_recheck_and_warning_qualification_belong_to_anomaly_detection_only():
    from cops.runtime import support as SUP
    assert set(P.LEGITIMACY) == {COP}
    for cop in BY_KEY:
        flagged = [c["name"] for c in SUP.profile(cop) if c.get("explainable") or c.get("warning_is_partial")
                   or c["kind"] == "legitimacy"]
        assert flagged == ([] if cop != COP else ["deviation_metrics", "legitimacy_context"]), cop


def test_a_warning_still_opposes_another_cops_quiet_label():
    from cops.runtime import support as SUP
    comp = SUP.C("m", "metrics", "direct", "all", "telemetry", metrics=("insights.token_outlier",))
    ctx = {"rows": [{"metric": "insights.token_outlier", "evidence": "OBSERVED", "state": "WARNING", "value": 4.0}],
           "telemetry_reliability": 1.0}
    assert SUP._metrics(ctx, comp, "quiet", "X")["direction"] == "opposes"
    flagged = dict(comp, warning_is_partial=True)
    assert SUP._metrics(ctx, flagged, "quiet", "NORMAL")["direction"] == "supports"


def test_ds03_is_unchanged_for_other_cops(bench):
    from cops.runtime import live_eval as L
    r = L.run_one(next(c for c in L.CASES if c["id"] == "knowledge_policy_management:abnormal"), L.offline_provider(), None)
    assert r["status"] == "REJECTED_BY_VERIFIER" and "DS-03" in r["verifier_fail"]


def test_one_explained_deviation_does_not_conceal_another(bench):
    res = bench["results"]["explained_tokens_unexplained_retries"]
    ctx = {x["metric"]: x["explained"] for x in res["cop"]["finding"]["cop_specific"]["questions"]["AD-5"]["basis"]["context"]}
    assert ctx["insights.token_outlier"] is True and ctx["insights.retry"] is False
    assert res["cop"]["finding"]["label"] == "INCONCLUSIVE"


@pytest.mark.parametrize("name,label", [("unsupported_legitimacy_claim", "INCONCLUSIVE"),
                                        ("false_normal_against_task_class_peers", "INCONCLUSIVE"),
                                        ("fabricated_context_citation", "INCONCLUSIVE"),
                                        ("unvalidated_legitimate_precedent", "INCONCLUSIVE"),
                                        ("false_anomaly_over_explained_deviation", "INCONCLUSIVE"),
                                        ("legitimate_variation_task_class", "NORMAL"),
                                        ("validated_legitimate_precedent", "NORMAL"),
                                        ("genuine_anomaly_despite_task_class", "ANOMALOUS")])
def test_false_normal_and_false_anomalous_readings(bench, name, label):
    assert bench["results"][name]["cop"]["finding"]["label"] == label


def test_a_verified_legitimate_variation_is_remembered_for_review():
    rows = H._class_world()
    rt = E.make_runtime("t-ad-remember", "s1", rows, {"task_classes": H._classes(rows)},
                        FX.Script({COP: H.SCRIPTS["legitimate_variation_task_class"]()}))
    r = G.run_case(rt, cops=["runtime_monitoring", COP])["results"][COP]
    assert r["finding"]["label"] == "NORMAL" and r["memory_write"]["written"] is True


def test_leave_one_out_reads_the_session_against_the_others(bench):
    """U-AD7, adopted 2026-11-24: the self-including reading is recorded beside the one that decides."""
    f = bench["results"]["baseline_contamination_half_estate"]["cop"]["finding"]
    m = next(x for x in f["cop_specific"]["questions"]["AD-2"]["basis"]["measures"]
             if x["metric"] == "insights.token_outlier")
    assert answers(f)["AD-2"] == "SIGNIFICANTLY_DIFFERENT" and m["value"] == 40.0 and m["state"] == "CRITICAL"
    assert m["baseline"]["median"] == 1000.0 and m["baseline"]["self_inclusive"] == {"sessions": 8, "median": 20500.0}
    b = answers(bench["results"]["baseline_minimum_counts_self"]["cop"]["finding"])
    assert b["AD-2"] == b["AD-3"] == "BASELINE_INSUFFICIENT" and b["AD-5"] == "UNDETERMINED"


def test_leave_one_out_rows_record_the_self_including_reading_and_a_versioned_method():
    rt = E.make_runtime("t-ad-loo", "s1", H.WORLDS["baseline_contamination_half_estate"]())
    loo = {e.metric_id: e for e in P.ad_case_events(rt.case)}
    incl = {e.metric_id: e for e in rt.case.case_events()}
    t = loo["insights.token_outlier"]
    assert (t.value, t.state) == (40.0, "CRITICAL") and incl["insights.token_outlier"].value == 1.95
    assert t.features["baseline_method"] == P.AD_BASELINE_METHOD
    assert t.features["self_inclusive"] == {"value": 1.95, "state": "NORMAL"}
    assert t.source.endswith("/" + P.AD_BASELINE_METHOD)
    # every measure the package does not read leave-one-out is the case's own row, unchanged
    assert all(loo[k] == incl[k] for k in incl if k not in P.AD_SELF_INCLUDING)
    v1 = H.run_scenario("baseline_contamination_half_estate", None)
    v2 = H.run_scenario("baseline_contamination_half_estate", None)
    ver = lambda r: next(x for x in r["cop"]["finding"]["cop_specific"]["questions"]["AD-2"]["basis"]["measures"]
                         if x["metric"] == "insights.token_outlier")["baseline"]["version"]
    assert ver(v1) == ver(v2) and ver(v1).endswith("/" + P.AD_BASELINE_METHOD)


def test_other_cops_keep_the_self_including_reading():
    from cops.runtime import tools as T
    rows = H.WORLDS["baseline_contamination_half_estate"]()
    rt = E.make_runtime("t-ad-scope", "s1", rows)
    # Resource Management joined the leave-one-out reading when purvi approved U-RS1 (2026-11-26, D-RS17);
    # the other COPs that read the measure keep the self-including reading
    for cop in ("risk_mitigation", "workload_optimization"):
        assert "insights.token_outlier" in BY_KEY[cop].metric_keys
        got = {r["metric"]: r["value"] for r in T.t_get_metric_rows(rt.case, {"metric_ids": ["insights.token_outlier"]},
                                                                    {"cop": cop})["rows"]}
        assert got == {"insights.token_outlier": 1.95}
        assert [e.value for e in G._events(rt, cop) if e.metric_id == "insights.token_outlier"] == [1.95]
    for cop in (COP, "resource_management"):
        assert [e.value for e in G._events(rt, cop) if e.metric_id == "insights.token_outlier"] == [40.0]
        got = {r["metric"]: r["value"] for r in T.t_get_metric_rows(rt.case, {"metric_ids": ["insights.token_outlier"]},
                                                                    {"cop": cop})["rows"]}
        assert got == {"insights.token_outlier": 40.0}


def test_mc01_recomputes_with_the_method_the_row_declares():
    from cops.runtime import verifiers as V
    rt = E.make_runtime("t-ad-mc01", "s1", H.WORLDS["baseline_contamination_half_estate"]())
    assert V._raw(rt.case, "insights.token_outlier") == 1.95
    assert V._raw(rt.case, "insights.token_outlier", P.AD_BASELINE_METHOD) == 40.0
    rt4 = E.make_runtime("t-ad-mc01-4", "s1", H.WORLDS["baseline_minimum_counts_self"]())
    assert V._raw(rt4.case, "insights.token_outlier", P.AD_BASELINE_METHOD) is None


def test_compute_reads_each_metric_through_metric_event():
    import signal_metrics as SM
    rows = H.WORLDS["normal"]()
    cfg = SM.load_config()
    events, sessions = SM.compute(rows, cfg, computed_at="2026-10-29T00:00:00Z")
    s = next(x for x in sessions if x.id == "s1")
    est = SM.estate(sessions)
    mine = {e["metric_id"]: e for e in events if e["session_id"] == "s1"}
    for entry in SM.METRICS:
        assert SM.metric_event(s, entry, est, cfg, "2026-10-29T00:00:00Z") == mine[entry[0]]
