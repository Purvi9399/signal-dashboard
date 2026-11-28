"""The LLM gateway and every LLM COP's wiring and failure modes. All simulated."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E
from cops.runtime import synthetic as S, fixtures as FX
from cops.runtime.llm import (LLMGateway, LLMBudget, FixtureProvider, UnavailableProvider, AnthropicProvider,
                              ProviderTimeout, ProviderUnavailable)
from cops.runtime.profiles import LLM_COPS, DETERMINISTIC_COPS, build_profiles, PROMPTS, PROMPT_REVISION
from cops.runtime.graph import run_cop
from cops.specs import BY_KEY

ROWS = E._estate(E.sensitive_retried_session("llm1", 9))   # a retry, so Anomaly Detection consults the model
RECORD_FIELDS = ("invocation_id", "cop", "case_id", "purpose", "provider", "model", "model_version",
                 "prompt_version", "simulated", "evidence_refs", "tools_exposed", "input_tokens",
                 "output_tokens", "latency_ms", "cost_usd", "cost_basis", "structured_response",
                 "validation", "retry_count", "error", "timed_out")


# Risk Mitigation's package decides every reading without the model (COP 8, packages/risk_mitigation/DECISIONS.md,
# D-RK9), so its provider wiring cannot be exercised here; test_risk_mitigation_does_not_reach_the_provider checks it.
# Context & Memory is retrieval only and, since U-CM5 (purvi 2026-11-28, packages/context_memory/DECISIONS.md
# A-CM1), a deterministic COP; test_context_memory_does_not_reach_the_provider checks it.
MODEL_PATH_COPS = [c for c in LLM_COPS if c != "risk_mitigation"]


def rt_with(provider, budget=None, case="llm-case"):
    # The taxonomy is supplied so Classification reaches its model call: without it the
    # evidence precheck stops that COP before any call (tested in test_runtime_error_paths).
    return E.make_runtime(case, "llm1", ROWS, {**E.c05_art(), "taxonomy": S.taxonomy()},
                          provider=provider, budget=budget)


# COP 9 (2026-11-27): Incident Response's package consults the model only for an incident whose intervention the
# verified upstream findings justify (packages/incident_response/DECISIONS.md D-IR11). Run alone it has no upstream
# and decides without the model, so it is run after Runtime Monitoring and Policy Enforcement (both deterministic):
# Policy Enforcement's verified VIOLATION of the proposed prod write (c05_art) is that justification.
IR_UPSTREAM = ["runtime_monitoring", "policy_enforcement"]


def one(cop, provider, budget=None):
    rt = rt_with(provider, budget)
    if cop == "incident_response":
        from cops.runtime.graph import run_case
        out = run_case(rt, cops=IR_UPSTREAM + [cop])["results"][cop]
        return rt, out, out["finding"]
    out = run_cop(rt, cop)
    return rt, out["result"], out["finding"]


def no_model_conclusion(cop, f):
    """The finding when the model reached no conclusion. A-IR1 (purvi, 2026-11-27): Incident Response keeps its
    verified deterministic incident and records the containment plan UNAVAILABLE; every other COP is INCONCLUSIVE."""
    if cop == "incident_response":
        return (f["label"] == "INCIDENT" and f["verified"] and f["origin"] == "computational"
                and f["cop_specific"]["containment_plan"]["status"] == "UNAVAILABLE")
    return f["label"] == "INCONCLUSIVE" and f["status"] == "INCONCLUSIVE" and not f["verified"]


def test_allocation_is_fixed_nine_and_nine():
    # ten and eight until U-CM5 (purvi 2026-11-28) moved Context & Memory to the deterministic COPs
    assert len(LLM_COPS) == 9 and len(DETERMINISTIC_COPS) == 9
    assert "context_memory" in DETERMINISTIC_COPS and "context_memory" not in LLM_COPS
    assert set(LLM_COPS) | set(DETERMINISTIC_COPS) == set(BY_KEY)
    p = build_profiles()
    assert all(p[k].classification == "llm" for k in LLM_COPS)
    assert all(p[k].classification == "deterministic" and p[k].prompt is None for k in DETERMINISTIC_COPS)


def test_prompts_tools_and_schemas_differ_per_cop():
    p = build_profiles()
    prompts = [PROMPTS[k] for k in LLM_COPS]
    assert len(set(prompts)) == 9
    assert len({p[k].tools for k in LLM_COPS}) == 9
    assert len({p[k].detail_schema for k in LLM_COPS}) == 9


@pytest.mark.parametrize("cop", MODEL_PATH_COPS)
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
    assert all(i["simulated"] and i["prompt_version"] == f"{cop}/prompt-{PROMPT_REVISION.get(cop, 2)}" for i in inv)
    assert all(i["tools_exposed"] == list(build_profiles()[cop].tools) for i in inv)
    assert all(i["cost_basis"] == "unpriced" and i["cost_usd"] is None for i in inv)   # no price configured
    assert no_model_conclusion(cop, f) and f["cop_specific"]["stop_reason"] == "model_inconclusive"


@pytest.mark.parametrize("cop", MODEL_PATH_COPS)
def test_unavailable_provider_is_recorded_not_fabricated(cop):
    rt, r, f = one(cop, UnavailableProvider())
    assert no_model_conclusion(cop, f)
    assert f["cop_specific"]["stop_reason"] == "llm_unavailable"
    assert r["llm_invocations"][0]["error"].startswith("unavailable")
    if cop == "incident_response":
        # A-IR1: the published finding is the package's own verified incident (origin computational); its
        # REQUEST_APPROVAL is recorded, never executed, in dry run
        assert r["proposed_action"]["execution_status"].startswith("NOT_EXECUTED")
        return
    # the computational baseline is kept, labelled, and not substituted
    assert f["baseline"] is not None and f["baseline"]["origin"] == "computational"
    assert r["proposed_action"]["execution_status"] in ("NOT_EXECUTED_NO_ACTION",)


@pytest.mark.parametrize("cop", MODEL_PATH_COPS)
def test_timeout_retries_then_stops(cop):
    rt, r, f = one(cop, FixtureProvider(lambda *a: ProviderTimeout("slow")))
    inv = r["llm_invocations"]
    assert [i["retry_count"] for i in inv] == [0, 1] and all(i["timed_out"] for i in inv)
    assert f["cop_specific"]["stop_reason"] == "llm_timeout" and no_model_conclusion(cop, f)


@pytest.mark.parametrize("cop", MODEL_PATH_COPS)
def test_malformed_output_retries_then_stops(cop):
    replies = iter(["not json at all", {"known": [], "unknown": [], "uncertain": [], "hypothesis": "h",
                                        "tool_requests": [], "grant_me": "admin"}])
    rt, r, f = one(cop, FixtureProvider(lambda *a: next(replies)))
    inv = r["llm_invocations"]
    assert len(inv) == 2 and not any(i["validation"]["ok"] for i in inv)
    assert f["cop_specific"]["stop_reason"] == "llm_output_invalid"


@pytest.mark.parametrize("cop", MODEL_PATH_COPS)
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
    # the allowlist is the profile's tools and the baseline tools (profiles.allowlists), and unchanged;
    # Anomaly Detection's assess_anomaly_questions is a baseline tool the model is not offered
    from cops.runtime.rules import DETERMINISTIC_TOOLS
    assert rt.tools.allowlist(cop) == sorted(set(build_profiles()[cop].tools) | set(DETERMINISTIC_TOOLS[cop]))


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


def test_cost_uses_configured_prices_only(monkeypatch):
    monkeypatch.setenv("COPS_PRICING", '{"fixture-model": [2.0, 10.0]}')
    gw = LLMGateway(FixtureProvider(lambda *a: FX.plan("x", [], [], [])))
    spent = {"llm_calls": 0, "tokens": 0, "cost_usd": 0.0, "started": 1e18}
    data, recs, err = gw.invoke("anomaly_detection", "c", "plan", "p", "s" * 400, {"k": "v"}, LLMBudget(), spent)
    r = recs[0]
    assert r["cost_basis"] == "simulated_tokens_x_configured_price"
    assert r["cost_usd"] == round(r["input_tokens"] / 1e6 * 2.0 + r["output_tokens"] / 1e6 * 10.0, 6)


def test_live_provider_cannot_start_by_accident(monkeypatch):
    monkeypatch.delenv("COPS_LLM_LIVE", raising=False)
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider()
    monkeypatch.setenv("COPS_LLM_LIVE", "1")
    monkeypatch.delenv("COPS_LLM_DATA_APPROVED", raising=False)
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider()
    monkeypatch.setenv("COPS_LLM_DATA_APPROVED", "1")
    monkeypatch.delenv("COPS_MODEL", raising=False)
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider()


@pytest.mark.skipif(not (os.environ.get("COPS_LLM_LIVE") == "1" and os.environ.get("COPS_LLM_DATA_APPROVED") == "1"
                         and os.environ.get("COPS_MODEL") and os.environ.get("ANTHROPIC_API_KEY")),
                    reason="opt-in live smoke test: needs COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1, "
                           "COPS_MODEL and credentials")
def test_live_provider_smoke():                                         # pragma: no cover
    rt, r, f = one("anomaly_detection", AnthropicProvider())
    assert r["llm_invocations"] and not r["llm_invocations"][0]["simulated"]
    assert f["status"] in ("SUBSTANTIVE", "INCONCLUSIVE", "REJECTED_BY_VERIFIER")


def test_risk_mitigation_does_not_reach_the_provider():
    seen = []

    def script(c, purpose, payload, n):
        seen.append((c, purpose))
        return FX.plan("look", [], [], []) if purpose == "plan" else FX.inconclusive("stop", ["x"])
    rt, r, f = one("risk_mitigation", FixtureProvider(script))
    assert seen == [] and r["llm_invocations"] == []
    assert f["cop_specific"]["stop_reason"] == "decided_by_package_without_model"


def test_context_memory_does_not_reach_the_provider():
    seen = []

    def script(c, purpose, payload, n):
        seen.append((c, purpose))
        return FX.plan("look", [], [], []) if purpose == "plan" else FX.inconclusive("stop", ["x"])
    rt, r, f = one("context_memory", FixtureProvider(script))
    assert seen == [] and r["llm_invocations"] == []
    assert f["cop_specific"]["stop_reason"] == "decided_by_package_without_model"
