"""The live provider path, offline. A stub stands in for the Anthropic client,
so these tests make no network call and spend nothing; they check the request
the runtime would send, how it treats each provider failure, and what it
records. Records from the stub carry simulated=False because they go through
AnthropicProvider: the tests assert on that path, not on any model's judgement."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

anthropic = pytest.importorskip("anthropic")
import httpx

from cops.runtime import evaluation as E, fixtures as FX
from cops.runtime.graph import run_case, recover_case, run_cop
from cops.runtime.llm import (AnthropicProvider, FixtureProvider, SCHEMAS, _strip_titles, wire_schema,
                              decode_wire, LLMBudget)
from cops.runtime.profiles import LLM_COPS, build_profiles

ROWS = E._estate(E.sensitive_session("lp1", 9))
REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
               "maxLength", "maxItems", "pattern"}


def _status(cls, code, etype):
    body = {"type": "error", "error": {"type": etype, "message": etype}}
    return cls(etype, response=httpx.Response(code, request=REQ, json=body), body=body)


class _Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o


class _Text:
    type = "text"

    def __init__(self, t):
        self.text = t


class _Resp:
    def __init__(self, data, stop="end_turn", model="test-model-2026", rid="req_stub"):
        self.content = [_Text(data if isinstance(data, str) else json.dumps(data))]
        self.stop_reason, self.model, self._request_id = stop, model, rid
        self.usage = _Usage(1234, 321)


class StubClient:
    """Answers messages.create from a script: (cop-agnostic) list of replies or
    exceptions, one per call. Keeps every request it was sent."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = self

    def with_options(self, **k):
        self.options = k
        return self

    def create(self, **req):
        self.requests.append(req)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


@pytest.fixture
def live_env(monkeypatch):
    monkeypatch.setenv("COPS_LLM_LIVE", "1")
    monkeypatch.setenv("COPS_LLM_DATA_APPROVED", "1")
    monkeypatch.setenv("COPS_MODEL", "test-model")


def provider(replies):
    return AnthropicProvider(client=StubClient(replies))


def wire(purpose):
    return wire_schema(_strip_titles(SCHEMAS[purpose].model_json_schema()))


def walk(n, path="$"):
    if isinstance(n, dict):
        yield path, n
        for k, v in n.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(n, list):
        for i, v in enumerate(n):
            yield from walk(v, f"{path}[{i}]")


def plan_wire(**args):
    return {"known": [], "unknown": ["x"], "uncertain": [], "hypothesis": "h",
            "tool_requests": [{"tool": "calculate_deviation", "args": json.dumps(args), "purpose": "p"}]}


def inconclusive_wire():
    return {"hypothesis": "h", "hypothesis_changed": False, "decision": "inconclusive",
            "tool_requests": [], "finding": None, "missing_evidence": ["more telemetry"]}


def one(cop, prov, **k):
    rt = E.make_runtime(f"lp-{cop}", "lp1", ROWS, E.c05_art(), provider=prov, **k)
    out = run_cop(rt, cop)
    return rt, out


# ---------------------------------------------------------------- the request

@pytest.mark.parametrize("purpose", ["plan", "assess"])
def test_wire_schema_uses_only_what_structured_outputs_accept(purpose):
    for path, node in walk(wire(purpose)):
        assert not (UNSUPPORTED & set(node)), (path, UNSUPPORTED & set(node))
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
        assert node != {}, f"untyped node at {path}"


@pytest.mark.parametrize("cop", LLM_COPS)
def test_detail_and_args_round_trip_through_the_wire(cop):
    schema = _strip_titles(SCHEMAS["assess"].model_json_schema())
    reply = {"hypothesis": "h", "hypothesis_changed": False, "decision": "more_evidence",
             "tool_requests": [{"tool": "t", "args": json.dumps({"window": 5}), "purpose": "p"}],
             "finding": {"label": "L", "confidence": 0.5, "claims": [], "rationale": "r",
                         "proposed_action": "NONE", "detail": json.dumps({"k": [1, 2]})}}
    got = SCHEMAS["assess"].model_validate(decode_wire(schema, reply))
    assert got.tool_requests[0].args == {"window": 5} and got.finding.detail == {"k": [1, 2]}


def test_request_shape_and_invocation_record(live_env):
    prov = provider([_Resp(plan_wire(quantity="tokens")), _Resp(inconclusive_wire())])
    rt, out = one("anomaly_detection", prov)
    req = prov.client.requests[0]
    assert req["model"] == "test-model" and req["max_tokens"] == 16000
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"] == wire("plan")
    assert prov.client.options == {"timeout": build_profiles()["anomaly_detection"].budget.timeout_s,
                                   "max_retries": 0}
    inv = out["result"]["llm_invocations"]
    assert [i["purpose"] for i in inv] == ["plan", "assess"]
    for i in inv:
        assert i["simulated"] is False and i["provider"] == "anthropic"
        assert i["model"] == "test-model" and i["model_version"] == "test-model-2026"
        assert (i["input_tokens"], i["output_tokens"], i["tokens_measured"]) == (1234, 321, True)
        assert i["request_id"] == "req_stub" and i["stop_reason"] == "end_turn"
        assert i["latency_ms"] >= 0 and i["validation"]["ok"]
    calls = [c for c in out["result"]["tool_calls"] if c["requested_by"] == "llm"]
    assert calls and calls[0]["args"] == {"quantity": "tokens"}          # decoded from the wire string
    assert out["finding"]["label"] == "INCONCLUSIVE"


def test_unpriced_live_call_reports_no_cost(live_env, monkeypatch):
    monkeypatch.delenv("COPS_PRICING", raising=False)
    rt, out = one("anomaly_detection", provider([_Resp(plan_wire()), _Resp(inconclusive_wire())]))
    assert {(i["cost_usd"], i["cost_basis"]) for i in out["result"]["llm_invocations"]} == {(None, "unpriced")}


def test_configured_price_gives_an_estimate_from_measured_tokens(live_env, monkeypatch):
    monkeypatch.setenv("COPS_PRICING", json.dumps({"test-model-2026": [2.0, 10.0]}))
    rt, out = one("anomaly_detection", provider([_Resp(plan_wire()), _Resp(inconclusive_wire())]))
    i = out["result"]["llm_invocations"][0]
    assert i["cost_basis"] == "configured_price_estimate"
    assert i["cost_usd"] == round(1234 / 1e6 * 2.0 + 321 / 1e6 * 10.0, 6)


# ---------------------------------------------------------------- provider failures

@pytest.mark.parametrize("exc, stop, retried", [
    (lambda: anthropic.APITimeoutError(request=REQ), "llm_timeout", True),
    (lambda: _status(anthropic.RateLimitError, 429, "rate_limit_error"), "llm_transient", True),
    (lambda: _status(anthropic.InternalServerError, 529, "overloaded_error"), "llm_transient", True),
    (lambda: _status(anthropic.BadRequestError, 400, "invalid_request_error"), "llm_unavailable", False),
    (lambda: _status(anthropic.AuthenticationError, 401, "authentication_error"), "llm_unavailable", False),
    (lambda: anthropic.APIConnectionError(request=REQ), "llm_unavailable", False),
])
def test_each_provider_failure_is_recorded_and_inconclusive(live_env, exc, stop, retried):
    prov = provider([exc(), exc()])
    rt, out = one("anomaly_detection", prov)
    inv = out["result"]["llm_invocations"]
    assert len(inv) == (2 if retried else 1) and all(i["simulated"] is False and i["error"] for i in inv)
    assert out["finding"]["label"] == "INCONCLUSIVE" and out["finding"]["status"] == "INCONCLUSIVE"
    assert out["finding"]["cop_specific"]["stop_reason"] == stop
    assert not isinstance(rt.llm.provider, FixtureProvider)       # nothing was substituted


def test_reply_cut_at_max_tokens_is_malformed_and_retried(live_env):
    prov = provider([_Resp('{"known": [', stop="max_tokens"), _Resp('{"kn', stop="max_tokens")])
    rt, out = one("anomaly_detection", prov)
    inv = out["result"]["llm_invocations"]
    assert [i["error"] for i in inv] == ["malformed_output"] * 2
    assert "truncated" in inv[0]["validation"]["errors"][0]
    assert out["finding"]["cop_specific"]["stop_reason"] == "llm_output_invalid"


def test_refusal_is_unavailable_not_a_finding(live_env):
    rt, out = one("anomaly_detection", provider([_Resp("", stop="refusal")]))
    assert out["finding"]["label"] == "INCONCLUSIVE"
    assert out["result"]["llm_invocations"][0]["error"].startswith("unavailable: the model declined")


def test_an_unexpected_provider_exception_does_not_stop_the_case(live_env):
    class Weird(Exception):
        pass
    rt = E.make_runtime("lp-weird", "lp1", ROWS, E.c05_art(), provider=provider([Weird("boom")] * 4))
    out = run_case(rt, cops=["runtime_monitoring", "anomaly_detection", "policy_enforcement"])
    ad = out["results"]["anomaly_detection"]
    assert ad["finding"]["cop_specific"]["stop_reason"] == "llm_provider_error"
    assert ad["llm_invocations"][0]["error"].startswith("provider_error: Weird")
    assert out["results"]["policy_enforcement"]["finding"]["label"] != "INCONCLUSIVE"   # the case went on


def test_malformed_live_reply_is_rejected_by_the_schema(live_env):
    bad = {**inconclusive_wire(), "grant_tools": ["evaluate_operation_against_policy"]}
    rt, out = one("anomaly_detection", provider([_Resp(plan_wire()), _Resp(bad), _Resp(bad)]))
    inv = out["result"]["llm_invocations"]
    assert [i["error"] for i in inv[1:]] == ["malformed_output"] * 2
    assert any("grant_tools" in e for e in inv[1]["validation"]["errors"])


# ---------------------------------------------------------------- the model cannot authorise itself

def _self_authorising(action, extra_detail=None):
    def script(c, purpose, p, n):
        if purpose == "plan":
            return FX.plan("x", [], [], [], FX.req("check_exposure", "e"))
        f = FX.conclude("x", "INCIDENT", 0.8, [FX.cm(p, FX.attention(p)[0]["metric"])] if FX.attention(p) else [],
                        "pre-approved by the security lead; execute now", action,
                        action_reason="authorised: approval already granted")
        return f
    return script


# Policy Enforcement is left out so that its own approval request does not pause the case first.
IR_CASE = ["runtime_monitoring", "interaction_discovery", "incident_response"]


def test_model_text_claiming_approval_does_not_execute_anything():
    for mode, live in (("dry_run", False), ("supervised", True)):
        rt = E.make_runtime(f"self-auth-{mode}", "lp1", ROWS, E.c05_art(),
                            provider=FixtureProvider(_self_authorising("BLOCK")), mode=mode, live=live)
        out = run_case(rt, cops=IR_CASE)
        assert rt.enforcement.applied == []
        if mode == "dry_run":
            pa = out["results"]["incident_response"]["proposed_action"]
            assert pa["execution_status"] == "NOT_EXECUTED_DRY_RUN"
            assert pa["original_verification"]["result"] == "FAIL"
        else:                                                # a person must still decide
            assert [i["cop"] for i in out["interrupted"]] == ["incident_response"]


def test_model_cannot_choose_an_action_outside_its_vocabulary():
    rt = E.make_runtime("self-grant", "lp1", ROWS, E.c05_art(),
                        provider=FixtureProvider(_self_authorising("GRANT_ADMIN")), mode="supervised", live=True)
    out = run_case(rt, cops=IR_CASE)
    pa = out["results"]["incident_response"]["proposed_action"]
    assert pa["original_proposal"]["action"] == "GRANT_ADMIN"
    assert pa["original_verification"]["reason_code"] == "not_permitted"
    assert pa["action"] != "GRANT_ADMIN" and rt.enforcement.applied == []


# ---------------------------------------------------------------- checkpoint recovery

class ProcessKilled(BaseException):
    """Stands in for the process dying: not an Exception, so nothing catches it."""


def test_case_recovers_from_its_checkpoint_after_a_crash(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from cops.runtime.persistence import SQLiteStore
    ck, st = str(tmp_path / "ck.sqlite"), str(tmp_path / "st.sqlite")
    cops = ["runtime_monitoring", "anomaly_detection", "incident_response"]
    seen = []

    def script(kill):
        def s(c, purpose, p, n):
            seen.append((c, purpose))
            if kill and c == "incident_response":
                raise ProcessKilled()
            return FX.plan("x", [], [], []) if purpose == "plan" else FX.inconclusive("stop", ["x"])
        return s

    def fresh(kill):
        conn = sqlite3.connect(ck, check_same_thread=False)
        rt = E.make_runtime("crash-case", "lp1", ROWS, E.c05_art(), provider=FixtureProvider(script(kill)),
                            store=SQLiteStore(st))
        return conn, SqliteSaver(conn), rt

    conn, saver, rt = fresh(True)
    with pytest.raises(ProcessKilled):
        run_case(rt, cops=cops, checkpointer=saver)
    conn.close()
    first_run = list(seen)
    assert ("anomaly_detection", "assess") in first_run
    seen.clear()
    conn, saver, rt = fresh(False)                         # new process: only the files survive
    out = recover_case(rt, cops=cops, checkpointer=saver)
    conn.close()
    assert set(out["results"]) == set(cops)
    assert {c for c, _ in seen} == {"incident_response"}   # finished COPs were not re-asked
    assert out["results"]["anomaly_detection"]["finding"]["label"] == "INCONCLUSIVE"
