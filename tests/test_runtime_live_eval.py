"""The opt-in live evaluation harness, run offline. Scripted replies only: every
record here is simulated=True, and no provider is called."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import live_eval as L, evaluation as E
from cops.runtime.graph import run_case
from cops.runtime.profiles import LLM_COPS, DETERMINISTIC_COPS
from cops.specs import BY_KEY

LIVE_VARS = ("COPS_LLM_LIVE", "COPS_LLM_DATA_APPROVED", "COPS_MODEL", "COPS_PRICING",
             "COPS_LIVE_MAX_TOTAL_USD", "ANTHROPIC_API_KEY")


def test_three_cases_per_llm_cop_with_expectations_fixed_in_advance():
    by = {}
    for c in L.CASES:
        by.setdefault(c["cop"], []).append(c["kind"])
        assert c["accept"] and set(c["accept"]) <= set(BY_KEY[c["cop"]].interpretations) | {"INCONCLUSIVE"}
    assert by == {k: ["normal", "abnormal", "missing"] for k in LLM_COPS}
    assert all(L.QUIET[c["cop"]] not in c["accept"] for c in L.CASES if c["kind"] != "normal")
    assert L.EXPECTED == {c["id"]: c["accept"] for c in L.CASES}


@pytest.mark.parametrize("cop", LLM_COPS)
def test_only_the_target_cop_is_an_llm_cop_in_its_case(cop):
    cops = L.deterministic_upstream(cop)
    assert cops[-1] == cop and set(cops[:-1]) <= set(DETERMINISTIC_COPS)


def test_offline_run_of_all_thirty_cases_is_simulated_and_verifiable():
    res = L.run_all(live=False)
    assert len(res) == 30 and not any(r.get("error") for r in res)
    assert L.verify_records(res, live=False) == []
    assert L.verify_records(res, live=True)                 # the same records could never pass as live
    t = L.totals(res)
    assert t["simulated_calls"] == t["llm_calls"] > 0 and t["ran"] == 30
    assert all(r["other_llm_calls"] == 0 for r in res)
    assert all(not str(r["execution_status"]).startswith("EXECUTED") for r in res)     # dry run throughout
    # missing-evidence cases never publish the quiet label, whatever the script says
    assert all(r["label"] != L.QUIET[r["cop"]] for r in res if r["kind"] == "missing")


def test_outcome_classes():
    case = next(c for c in L.CASES if c["id"] == "anomaly_detection:abnormal")
    assert L.classify(case, "ANOMALOUS") == "agree"
    assert L.classify(case, "NORMAL") == "missed_finding"
    assert L.classify(case, "INCONCLUSIVE") == "inconclusive"
    normal = next(c for c in L.CASES if c["id"] == "anomaly_detection:normal")
    assert L.classify(normal, "ANOMALOUS") == "false_positive"


def test_live_run_is_refused_without_every_precondition(monkeypatch, tmp_path):
    for k in LIVE_VARS:
        monkeypatch.delenv(k, raising=False)
    assert L.main(["--out", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []                  # nothing was called or written
    for k, v in (("COPS_LLM_LIVE", "1"), ("COPS_LLM_DATA_APPROVED", "1"), ("COPS_MODEL", "m"),
                 ("ANTHROPIC_API_KEY", "x"), ("COPS_LIVE_MAX_TOTAL_USD", "5")):
        monkeypatch.setenv(k, v)
    cap, probs = L.check_live_config()
    assert probs == ["COPS_PRICING has no price for m: the spending cap could not be enforced"]
    monkeypatch.setenv("COPS_PRICING", '{"m": [2, 10]}')
    assert L.check_live_config() == (5.0, [])


def test_total_cap_stops_scheduling_cases(monkeypatch):
    monkeypatch.setattr(L, "AnthropicProvider", lambda: L.offline_provider())
    res = L.run_all(live=True, cap=0.10)                    # below one investigation's own cap
    assert all(r.get("skipped") for r in res) and len(res) == 30


def test_deterministic_cops_make_no_model_calls():
    out = E.e2e_full({})
    for k in DETERMINISTIC_COPS:
        assert out["cops"][k]["llm_calls"] == 0, k
    assert all(out["cops"][k]["llm_calls"] >= 1 for k in LLM_COPS)


def test_fleet_matrix_has_eighteen_rows_and_marks_live_as_not_run():
    from cops.runtime import fleet_report as F
    rs = F.rows(E.run_all(only=["C01", "E2E-full"]), None)
    assert len(rs) == 18 and {r["cop"] for r in rs} == set(BY_KEY)
    assert all(r["implementation"] == "complete" for r in rs)
    md = F.markdown(rs, None)
    assert md.count("\n| `") == 18 and "no live run yet" in md
    assert all("live not run" in line for line in md.splitlines() if "| LLM |" in line)
    assert all(r["offline_llm_calls"] == 0 for r in rs if r["classification"] == "deterministic")


def test_a_provider_that_refuses_every_call_is_never_counted_as_agreement(monkeypatch):
    from cops.runtime.llm import ProviderUnavailable, FixtureProvider
    refuse = lambda: FixtureProvider(lambda *a: ProviderUnavailable("400 credit balance too low"))
    monkeypatch.setattr(L, "AnthropicProvider", refuse)
    res = L.run_all(live=True, cap=100.0)
    ran = [r for r in res if not r.get("skipped")]
    assert len(ran) == L.STOP_AFTER_FAILED_CASES                       # then it stops calling
    assert all(r["outcome"] == "provider_failure" for r in ran)       # including the missing-evidence case
    assert L.totals(res)["agree"] == 0
    assert any("provider call failed" in p for p in L.verify_records(res, live=True))
