"""
One LLM gateway for every COP.

Every model call goes through LLMGateway.invoke, which:
    checks the investigation's budgets (calls, tokens, time, cost) first;
    sends a typed request to a Provider;
    validates the reply against a pydantic schema that forbids extra fields,
      so a reply that tries to add a permission, a tool or a threshold is
      malformed, not obeyed;
    retries a malformed or timed-out reply within the call budget;
    records an LLMInvocation for every attempt, successful or not.

Providers
    FixtureProvider      scripted replies for deterministic tests. Every record
                         it produces is marked simulated=True, and its token
                         counts are estimates from payload size, not measured.
    UnavailableProvider  always fails, for the no-provider path.
    AnthropicProvider    the real Messages API. Refuses to start unless
                         COPS_LLM_LIVE=1 and COPS_LLM_DATA_APPROVED=1 are set
                         alongside credentials, so it can never be reached by
                         accident, incur charges silently, or send enterprise
                         data without an explicit decision.
"""

import json, os, time, uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import LLMInvocation, CLAIM_KINDS, stable_id

# List prices per million tokens (input, output), Anthropic first-party API.
# Source: claude-api skill model table, cached 2026-10-06. Used only to
# estimate cost; a measured cost from the provider overrides it.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}
DEFAULT_MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------- schemas

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRequestOut(Strict):
    tool: str
    args: dict = Field(default_factory=dict)
    purpose: str


class ClaimOut(Strict):
    statement: str
    kind: Literal[CLAIM_KINDS]  # type: ignore[valid-type]
    metric_id: Optional[str] = None
    asserted_state: Optional[str] = None
    asserted_value: Any = None
    tool_call_id: Optional[str] = None
    result_field: Optional[str] = None
    record_id: Optional[str] = None
    upstream_cop: Optional[str] = None
    upstream_label: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)


class CandidateFindingOut(Strict):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    claims: list[ClaimOut]
    rationale: str
    alternative_explanations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    proposed_action: str
    action_reason: str = ""
    detail: dict = Field(default_factory=dict)      # validated against the COP's own schema


class PlanOut(Strict):
    known: list[str]
    unknown: list[str]
    uncertain: list[str]
    hypothesis: str
    tool_requests: list[ToolRequestOut] = Field(default_factory=list, max_length=4)


class AssessmentOut(Strict):
    hypothesis: str
    hypothesis_changed: bool
    change_reason: str = ""
    decision: Literal["more_evidence", "conclude", "inconclusive"]
    tool_requests: list[ToolRequestOut] = Field(default_factory=list, max_length=4)
    finding: Optional[CandidateFindingOut] = None
    missing_evidence: list[str] = Field(default_factory=list)


SCHEMAS = {"plan": PlanOut, "assess": AssessmentOut}


# ---------------------------------------------------------------- budgets

@dataclass
class LLMBudget:
    """Initial safety limits. Configurable per COP profile and per run."""
    max_llm_calls: int = 4
    max_tool_calls: int = 6
    max_tokens: int = 60_000
    max_seconds: float = 60.0
    max_cost_usd: float = 0.50
    max_retries: int = 1
    timeout_s: float = 30.0

    def as_dict(self):
        return dict(self.__dict__)


class BudgetExhausted(RuntimeError):
    def __init__(self, which):
        super().__init__(which)
        self.which = which


# ---------------------------------------------------------------- providers

class ProviderUnavailable(RuntimeError):
    pass


class ProviderTimeout(RuntimeError):
    pass


@dataclass
class ProviderResponse:
    data: Any                         # parsed JSON (dict) or raw text
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    model_version: str
    measured_cost_usd: Optional[float] = None
    tokens_measured: bool = False


class FixtureProvider:
    """Scripted model for tests. script(cop, purpose, payload, n) -> reply dict.

    The reply may also be an exception instance to raise (timeouts, outages)
    or a string (malformed output). n counts this COP's calls in the case.
    """
    name = "fixture"
    simulated = True

    def __init__(self, script, model=DEFAULT_MODEL, label="scripted"):
        self.script, self.model, self.label = script, model, label
        self.calls = []

    def complete(self, cop, purpose, system, payload, schema, timeout_s):
        n = sum(1 for c in self.calls if c[0] == cop)
        self.calls.append((cop, purpose))
        reply = self.script(cop, purpose, payload, n)
        if isinstance(reply, BaseException):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ProviderResponse(
            data=reply, input_tokens=(len(system) + len(json.dumps(payload, default=str))) // 4,
            output_tokens=len(text) // 4, model_version=f"{self.model}+fixture:{self.label}")


class UnavailableProvider:
    name, model, simulated = "none", "none", True

    def complete(self, *a, **k):
        raise ProviderUnavailable("no LLM provider is configured")


class AnthropicProvider:
    """Real provider. Opt-in only; see module docstring."""
    name = "anthropic"
    simulated = False

    def __init__(self, model=None):
        if os.environ.get("COPS_LLM_LIVE") != "1":
            raise ProviderUnavailable("live LLM calls are off (set COPS_LLM_LIVE=1 to enable)")
        if os.environ.get("COPS_LLM_DATA_APPROVED") != "1":
            raise ProviderUnavailable("sending case data to a provider has not been approved "
                                      "(COPS_LLM_DATA_APPROVED=1)")
        try:
            import anthropic
        except ImportError as e:
            raise ProviderUnavailable("the anthropic SDK is not installed") from e
        self.model = model or os.environ.get("COPS_MODEL") or DEFAULT_MODEL
        self.client = anthropic.Anthropic()

    def complete(self, cop, purpose, system, payload, schema, timeout_s):
        import anthropic
        try:
            resp = self.client.with_options(timeout=timeout_s, max_retries=0).messages.create(
                model=self.model, max_tokens=4000, system=system,
                messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
                output_config={"format": {"type": "json_schema", "schema": schema}})
        except anthropic.APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailable(str(e)) from e
        if resp.stop_reason == "refusal":
            raise ProviderUnavailable("the model declined the request")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return ProviderResponse(data=json.loads(text) if text else text,
                                input_tokens=resp.usage.input_tokens,
                                output_tokens=resp.usage.output_tokens,
                                model_version=resp.model, tokens_measured=True)


def _strip_titles(schema):
    if isinstance(schema, dict):
        schema.pop("title", None)
        for v in schema.values():
            _strip_titles(v)
    elif isinstance(schema, list):
        for v in schema:
            _strip_titles(v)
    return schema


# ---------------------------------------------------------------- gateway

class LLMGateway:
    def __init__(self, provider):
        self.provider = provider

    def _cost(self, model, i, o, measured):
        if measured is not None:
            return measured, "measured"
        base = model.split("+")[0]
        if base in PRICING and i is not None and o is not None:
            pi, po = PRICING[base]
            basis = ("simulated_tokens_x_list_price" if self.provider.simulated
                     else "list_price_estimate")
            return round(i / 1e6 * pi + o / 1e6 * po, 6), basis
        return None, "unpriced"

    def invoke(self, cop, case_id, purpose, prompt_version, system, payload, budget, spent,
               evidence_refs=(), tools_exposed=()):
        """Returns (validated dict or None, [invocation dicts], error or None).

        spent is the investigation's running totals and is mutated:
        {llm_calls, tokens, cost_usd, started}.
        """
        schema_model = SCHEMAS[purpose]
        records, attempt = [], 0
        while True:
            for which, over in (("max_llm_calls", spent["llm_calls"] >= budget.max_llm_calls),
                                ("max_tokens", spent["tokens"] >= budget.max_tokens),
                                ("max_cost_usd", spent["cost_usd"] >= budget.max_cost_usd),
                                ("max_seconds", time.time() - spent["started"] >= budget.max_seconds)):
                if over:
                    return None, records, f"budget_exhausted:{which}"
            spent["llm_calls"] += 1
            t0 = time.perf_counter()
            inv_id = stable_id(case_id, cop, purpose, spent["llm_calls"])
            err, timed_out, data, resp = None, False, None, None
            try:
                resp = self.provider.complete(
                    cop, purpose, system, payload,
                    _strip_titles(schema_model.model_json_schema()), budget.timeout_s)
                raw = resp.data
                if isinstance(raw, str):
                    raw = json.loads(raw)
                data = schema_model.model_validate(raw).model_dump()
                validation = {"ok": True, "errors": []}
            except ProviderTimeout as e:
                err, timed_out, validation = f"timeout: {e}", True, {"ok": False, "errors": ["timeout"]}
            except ProviderUnavailable as e:
                err, validation = f"unavailable: {e}", {"ok": False, "errors": ["unavailable"]}
            except (ValidationError, json.JSONDecodeError, TypeError) as e:
                msg = str(e).splitlines()
                err = "malformed_output"
                validation = {"ok": False, "errors": msg[:6]}
            latency = round((time.perf_counter() - t0) * 1000, 3)
            i = resp.input_tokens if resp else None
            o = resp.output_tokens if resp else None
            model_v = resp.model_version if resp else getattr(self.provider, "model", "none")
            cost, basis = self._cost(model_v, i, o, resp.measured_cost_usd if resp else None)
            spent["tokens"] += (i or 0) + (o or 0)
            spent["cost_usd"] += cost or 0.0
            records.append(LLMInvocation(
                invocation_id=inv_id, cop=cop, case_id=case_id, purpose=purpose,
                provider=self.provider.name, model=getattr(self.provider, "model", "none"),
                model_version=model_v, prompt_version=prompt_version,
                simulated=self.provider.simulated, evidence_refs=list(evidence_refs)[:50],
                tools_exposed=list(tools_exposed), input_tokens=i, output_tokens=o,
                latency_ms=latency, cost_usd=cost, cost_basis=basis,
                structured_response=data, validation=validation, retry_count=attempt,
                error=err, timed_out=timed_out).to_dict())
            if data is not None:
                return data, records, None
            if err and err.startswith("unavailable"):
                return None, records, "llm_unavailable"
            attempt += 1
            if attempt > budget.max_retries:
                return None, records, "llm_timeout" if timed_out else "llm_output_invalid"
