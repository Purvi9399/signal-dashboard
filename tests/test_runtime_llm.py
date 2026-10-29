"""The LLM gateway and every LLM COP's wiring and failure modes. All simulated."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E, fixtures as FX
from cops.runtime.llm import (LLMGateway, LLMBudget, FixtureProvider, UnavailableProvider, AnthropicProvider,
                              ProviderTimeout, ProviderUnavailable)
from cops.runtime.profiles import LLM_COPS, DETERMINISTIC_COPS, build_profiles, PROMPTS
from cops.runtime.graph import run_cop
from cops.specs import BY_KEY

ROWS = E._estate(E.sensitive_session("llm1", 9))
RECORD_FIELDS = ("invocation_id", "cop", "case_id", "purpose", "provider", "model", "model_version",
                 "prompt_version", "simulated", "evidence_refs", "tools_exposed", "input_tokens",
                 "output_tokens", "latency_ms", "cost_usd", "cost_basis", "structured_response",
                 "validation", "retry_count", "error", "timed_out")


def rt_with(provider, budget=None, case="llm-case"):
    return E.make_runtime(case, "llm1", ROWS, E.c05_art(), provider=provider, budget=budget)


def one(cop, provider, budget=None):
    rt = rt_with(provider, budget)
    out = run_cop(rt, cop)
    return rt, out["result"], out["finding"]


def test_allocation_is_fixed_ten_and_eight():
    assert len(LLM_COPS) == 10 and len(DETERMINISTIC_COPS) == 8
    assert set(LLM_COPS) | set(DETERMINISTIC_COPS) == set(BY_KEY)
    p = build_profiles()
    assert all(p[k].classification == "llm" for k in LLM_COPS)
    assert all(p[k].classification == "deterministic" and p[k].prompt is None for k in DETERMINISTIC_COPS)


def test_prompts_tools_and_schemas_differ_per_cop():
    p = build_profiles()
    prompts = [PROMPTS[k] for k in LLM_COPS]
    assert len(set(prompts)) == 10
    assert len({p[k].tools for k in LLM_COPS}) == 10
    assert len({p[k].detail_schema for k in LLM_COPS}) == 10


@pytest.mark.parametrize("cop", LLM_COPS)
def test_wiring_through_the_provider_interface(cop):
    seen = []

    def script(c, purpose, payload, n):
        seen.append((c, purpose, payload))
        return FX.plan("look", [], [], []) if purpose == "plan" else FX.inconclusive("stop", ["x"])
    rt, r, f = one(cop, FixtureProvider(script))
    assert [s[0] for s in seen] == [cop, cop] and [s[1] for s in seen] == ["plan", "assess"]
    payload = seen[0][2]
    assert payload["cop"] == cop and payload["behavioural_question"] == BY_KEY[cop].behavioural_question
    assert [t["tool"] for t in payload["tools_available"]] == list(build_profiles()[cop].tools)
    inv = r["llm_invocations"]
    assert len(inv) == 2 and all(set(RECORD_FIELDS) <= set(i) for i in inv)
    assert all(i["simulated"] and i["prompt_version"] == f"{cop}/prompt-1" for i in inv)
    assert all(i["tools_exposed"] == list(build_profiles()[cop].tools) for i in inv)
    assert all(i["cost_basis"] == "simulated_tokens_x_list_price" for i in inv)
    assert f["status"] == "INCONCLUSIVE" and f["cop_specific"]["stop_reason"] == "model_inconclusive"


@pytest.mark.parametrize("cop", LLM_COPS)
def test_unavailable_provider_is_recorded_not_fabricated(cop):
    rt, r, f = one(cop, UnavailableProvider())
    assert f["label"] == "INCONCLUSIVE" and f["status"] == "INCONCLUSIVE" and not f["verified"]
    assert f["cop_specific"]["stop_reason"] == "llm_unavailable"
    assert r["llm_invocations"][0]["error"].startswith("unavailable")
    # the computational baseline is kept, labelled, and not substituted
    assert f["baseline"] is not None and f["baseline"]["origin"] == "computational"
    assert r["proposed_action"]["execution_status"] in ("NOT_EXECUTED_NO_ACTION",)


@pytest.mark.parametrize("cop", LLM_COPS)
def test_timeout_retries_then_stops(cop):
    rt, r, f = one(cop, FixtureProvider(lambda *a: ProviderTimeout("slow")))
    inv = r["llm_invocations"]
    assert [i["retry_count"] for i in inv] == [0, 1] and all(i["timed_out"] for i in inv)
    assert f["cop_specific"]["stop_reason"] == "llm_timeout" and f["label"] == "INCONCLUSIVE"


@pytest.mark.parametrize("cop", LLM_COPS)
def test_malformed_output_retries_then_stops(cop):
    replies = iter(["not json at all", {"known": [], "unknown": [], "uncertain": [], "hypothesis": "h",
                                        "tool_requests": [], "grant_me": "admin"}])
    rt, r, f = one(cop, FixtureProvider(lambda *a: next(replies)))
    inv = r["llm_invocations"]
    assert len(inv) == 2 and not any(i["validation"]["ok"] for i in inv)
    assert f["cop_specific"]["stop_reason"] == "llm_output_invalid"


@pytest.mark.parametrize("cop", LLM_COPS)
def test_prohibited_tool_request_is_refused_and_logged(cop):
    other = next(t for k in LLM_COPS if k != cop for t in build_profiles()[k].tools
                 if t not in build_profiles()[cop].tools)

    def script(c, purpose, payload, n):
        if purpose == "plan":
            return FX.plan("try", [], [], [], FX.req(other, "not mine"), FX.req("drop_tables", "nope"))
        return FX.inconclusive("refused", ["tools"])
    rt, r, f = one(cop, FixtureProvider(script))
    rej = [t for t in r["tool_calls"] if t["status"] == "REJECTED"]
    assert {t["rejection_reason"].split(":")[0] for t in rej} == {"not_in_allowlist", "unknown_tool"}
    assert {e["code"] for e in r["security_events"]} == {"not_in_allowlist", "unknown_tool"}
    # the allowlist is exactly the profile's tools (baseline tools are a subset), and unchanged
    assert rt.tools.allowlist(cop) == sorted(build_profiles()[cop].tools)


def test_llm_call_budget_stops_the_loop():
    def script(c, purpose, payload, n):
        if purpose == "plan":
            return FX.plan("loop", [], [], [], FX.req("calculate_deviation", "x", quantity="tokens"))
        return FX.more("again", False, "", FX.req("calculate_deviation", "x", quantity="actions"))
    rt, r, f = one("anomaly_detection", FixtureProvider(script), LLMBudget(max_llm_calls=3))
    assert len(r["llm_invocations"]) == 3
    assert f["cop_specific"]["stop_reason"] == "budget_exhausted:max_llm_calls"


def test_tool_budget_refuses_extra_requests():
    def script(c, purpose, payload, n):
        if purpose == "plan":
            return FX.plan("many", [], [], [], *[FX.req("analyse_tool_call_sequence", "x")] * 4)
        return FX.inconclusive("done", [])
    rt, r, f = one("anomaly_detection", FixtureProvider(script), LLMBudget(max_tool_calls=2))
    llm_calls = [t for t in r["tool_calls"] if t["requested_by"] == "llm"]
    assert [t["status"] for t in llm_calls].count("OK") == 2
    assert [t["rejection_reason"].split(":")[0] for t in llm_calls if t["status"] == "REJECTED"] == \
        ["budget_exhausted"] * 2


def test_token_and_cost_budgets_are_checked_before_each_call():
    gw = LLMGateway(FixtureProvider(lambda *a: FX.plan("x", [], [], [])))
    spent = {"llm_calls": 0, "tokens": 10**6, "cost_usd": 0.0, "started": 1e18}
    data, recs, err = gw.invoke("anomaly_detection", "c", "plan", "p", "sys", {}, LLMBudget(), spent)
    assert err == "budget_exhausted:max_tokens" and recs == []
    spent = {"llm_calls": 0, "tokens": 0, "cost_usd": 9.0, "started": 1e18}
    assert gw.invoke("anomaly_detection", "c", "plan", "p", "sys", {}, LLMBudget(), spent)[2] == \
        "budget_exhausted:max_cost_usd"


def test_live_provider_cannot_start_by_accident(monkeypatch):
    monkeypatch.delenv("COPS_LLM_LIVE", raising=False)
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider()
    monkeypatch.setenv("COPS_LLM_LIVE", "1")
    monkeypatch.delenv("COPS_LLM_DATA_APPROVED", raising=False)
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider()


@pytest.mark.skipif(not (os.environ.get("COPS_LLM_LIVE") == "1" and os.environ.get("COPS_LLM_DATA_APPROVED") == "1"
                         and os.environ.get("ANTHROPIC_API_KEY")),
                    reason="opt-in live smoke test: needs COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1 and credentials")
def test_live_provider_smoke():                                         # pragma: no cover
    rt, r, f = one("anomaly_detection", AnthropicProvider())
    assert r["llm_invocations"] and not r["llm_invocations"][0]["simulated"]
    assert f["status"] in ("SUBSTANTIVE", "INCONCLUSIVE", "REJECTED_BY_VERIFIER")
