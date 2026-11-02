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
                         COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1 and
                         COPS_MODEL are set alongside credentials, so it can never be reached by
                         accident, incur charges silently, or send enterprise
                         data without an explicit decision.
"""

import json, os, time, uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import LLMInvocation, CLAIM_KINDS, stable_id

def load_pricing():
    """Prices per million tokens, from configuration only.

    COPS_PRICING is JSON: {"<model>": [input_usd_per_mtok, output_usd_per_mtok]}.
    No prices are built in, so a cost is never shown for a model whose price
    nobody configured; such calls are recorded with cost_basis "unpriced".
    """
    raw = os.environ.get("COPS_PRICING")
    if not raw:
        return {}
    return {k: (float(v[0]), float(v[1])) for k, v in json.loads(raw).items()}


FIXTURE_MODEL = "fixture-model"

# ---------------------------------------------------------------- schemas

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRequestOut(Strict):
    tool: str
    args: dict = Field(default_factory=dict)
    purpose: str


class ClaimOut(Strict):
    # The descriptions travel to the provider in the wire schema; every claim is checked against them.
    statement: str
    kind: Literal[CLAIM_KINDS]  # type: ignore[valid-type]
    metric_id: Optional[str] = Field(None, description="For metric_state and metric_value: a metric id "
                                     "from the evidence rows.")
    asserted_state: Optional[str] = Field(None, description="For metric_state: the row's state, exactly.")
    asserted_value: Any = Field(None, description="For metric_value and tool_observation: the exact value "
                                "you rely on (the metric row's value, or the value at result_field in the "
                                "tool result). Required for those kinds: a claim without it cannot be "
                                "checked and is rejected.")
    tool_call_id: Optional[str] = Field(None, description="For tool_observation: the tool_call_id of a "
                                        "result in tool_results.")
    result_field: Optional[str] = Field(None, description="For tool_observation: the key in that tool "
                                        "result: dotted for nested keys, with list positions as 'list.0.field' or 'list[0].field'.")
    record_id: Optional[str] = Field(None, description="For memory_precedent: an established record id.")
    upstream_cop: Optional[str] = Field(None, description="For upstream_finding: the upstream COP key.")
    upstream_label: Optional[str] = Field(None, description="For upstream_finding: its label, exactly.")
    evidence_refs: list[str] = Field(default_factory=list, description="Optional extra references, each "
                                     "'metric:<metric_id>' or 'tool:<tool_call_id>'. Leave the list empty "
                                     "rather than adding an empty string.")


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


class ProviderTransient(RuntimeError):
    """Rate limited or overloaded: worth one retry within the call budget."""


class ProviderTruncated(RuntimeError):
    """The reply stopped at the output limit, so its JSON is incomplete."""


@dataclass
class ProviderResponse:
    data: Any                         # parsed JSON (dict) or raw text
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    model_version: str
    measured_cost_usd: Optional[float] = None
    tokens_measured: bool = False
    stop_reason: Optional[str] = None
    request_id: Optional[str] = None


class FixtureProvider:
    """Scripted model for tests. script(cop, purpose, payload, n) -> reply dict.

    The reply may also be an exception instance to raise (timeouts, outages)
    or a string (malformed output). n counts this COP's calls in the case.
    """
    name = "fixture"
    simulated = True

    def __init__(self, script, model=FIXTURE_MODEL, label="scripted"):
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
    """Real provider. Opt-in only; see module docstring.

    The request carries a wire version of the reply schema (see wire_schema):
    the API's structured outputs accept a subset of JSON Schema, so bounds the
    API cannot express are dropped from the wire and still enforced by the
    gateway's pydantic validation of the decoded reply.
    """
    name = "anthropic"
    simulated = False

    def __init__(self, model=None, client=None, max_output_tokens=None, effort=None):
        if os.environ.get("COPS_LLM_LIVE") != "1":
            raise ProviderUnavailable("live LLM calls are off (set COPS_LLM_LIVE=1 to enable)")
        if os.environ.get("COPS_LLM_DATA_APPROVED") != "1":
            raise ProviderUnavailable("sending case data to a provider has not been approved "
                                      "(COPS_LLM_DATA_APPROVED=1)")
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("the anthropic SDK is not installed") from e
        self.model = model or os.environ.get("COPS_MODEL")
        if not self.model:
            raise ProviderUnavailable("no model configured (set COPS_MODEL)")
        self.max_output_tokens = int(max_output_tokens or os.environ.get("COPS_MAX_OUTPUT_TOKENS") or 16000)
        self.effort = effort or os.environ.get("COPS_EFFORT") or None
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def complete(self, cop, purpose, system, payload, schema, timeout_s):
        import anthropic
        wire = wire_schema(schema)
        cfg = {"format": {"type": "json_schema", "schema": wire}}
        if self.effort:
            cfg["effort"] = self.effort
        try:
            resp = self.client.with_options(timeout=timeout_s, max_retries=0).messages.create(
                model=self.model, max_tokens=self.max_output_tokens, system=system,
                messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
                output_config=cfg)
        except anthropic.APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailable(f"connection: {e}") from e
        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            raise ProviderTransient(f"{e.status_code} {getattr(e, 'type', '')}: {e}") from e
        except anthropic.APIStatusError as e:      # 400, 401, 402, 403, 404 and the rest: not retried
            raise ProviderUnavailable(f"{e.status_code} {getattr(e, 'type', '')}: {e}") from e
        rid = getattr(resp, "_request_id", None)
        if resp.stop_reason == "refusal":
            raise ProviderUnavailable(f"the model declined the request (request {rid})")
        usage = dict(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        if resp.stop_reason == "max_tokens":
            raise ProviderTruncated(f"reply stopped at max_tokens={self.max_output_tokens} "
                                    f"(request {rid}, output_tokens {usage['output_tokens']})")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = decode_wire(schema, json.loads(text)) if text else text
        return ProviderResponse(data=data, model_version=resp.model, tokens_measured=True,
                                stop_reason=resp.stop_reason, request_id=rid, **usage)


# ---------------------------------------------------------------- wire schema

_DROP = ("title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
         "multipleOf", "minLength", "maxLength", "maxItems", "pattern")
_JSON_TEXT = "A JSON object, encoded as a string."
_JSON_VALUE = ("Any JSON value (number, string, list, object, true/false or null), encoded as a string, "
               "e.g. \"40.0\", \"[\\\"C1\\\"]\" or \"null\".")


def _untyped(node):
    return isinstance(node, dict) and (node == {} or set(node) <= {"title", "default", "description"})


def _free_object(node):
    return (isinstance(node, dict) and node.get("type") == "object" and not node.get("properties")
            and node.get("additionalProperties", True) is not False)


def wire_schema(schema):
    """The reply schema in the subset the provider's structured outputs accept.

    - numeric, string-length and array-length bounds are dropped (pydantic
      still enforces them on the decoded reply);
    - an open object (tool args, the COP detail) becomes a JSON-encoded string,
      since every object on the wire must set additionalProperties false;
    - an untyped value (asserted_value) becomes a required JSON-encoded string:
      in the first live runs an optional value was left null on tool
      observations, which made the claim uncheckable.
    """
    import copy

    def walk(n):
        if isinstance(n, list):
            return [walk(x) for x in n]
        if not isinstance(n, dict):
            return n
        if _free_object(n):
            return {"type": "string", "description": _JSON_TEXT}
        if _untyped(n):
            return {"type": "string", "description": (n.get("description", "") + " " if n.get("description")
                                                      else "") + _JSON_VALUE}
        out = {k: walk(v) for k, v in n.items() if k not in _DROP}
        if isinstance(n.get("properties"), dict):
            forced = [k for k, v in n["properties"].items() if _untyped(v)]
            if forced:
                out["required"] = list(dict.fromkeys(list(out.get("required", [])) + forced))
        # A dropped bound still holds: say it in words, since the reply is validated against it.
        said = [f"at most {n['maxItems']} items" if "maxItems" in n else "",
                f"minimum {n['minimum']}" if "minimum" in n else "",
                f"maximum {n['maximum']}" if "maximum" in n else "",
                f"at most {n['maxLength']} characters" if "maxLength" in n else ""]
        said = "; ".join(x for x in said if x)
        if said:
            out["description"] = (out.get("description", "") + " " if out.get("description") else "") + \
                f"Must hold: {said}."
        if out.get("type") == "array" and out.get("minItems", 0) > 1:
            out.pop("minItems")
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out

    return walk(copy.deepcopy(schema))


def decode_wire(schema, data):
    """Undo wire_schema on a reply: parse the JSON-encoded open objects. A value
    that does not parse is left as it is, for pydantic to reject."""
    defs = schema.get("$defs", {})

    def resolve(n):
        while isinstance(n, dict) and "$ref" in n:
            n = defs.get(n["$ref"].split("/")[-1], {})
        return n

    def walk(n, v):
        n = resolve(n)
        if (_free_object(n) or _untyped(n)) and isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v                       # an object field that does not parse fails pydantic;
                                               # an untyped one stays a plain string
        if isinstance(v, dict) and "properties" in n:
            return {k: walk(n["properties"].get(k, {}), x) for k, x in v.items()}
        if isinstance(v, list) and "items" in n:
            return [walk(n["items"], x) for x in v]
        if isinstance(v, dict) and "anyOf" in n:
            for alt in n["anyOf"]:
                if "properties" in resolve(alt):
                    return walk(alt, v)
        return v

    return walk(schema, data)


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
    def __init__(self, provider, pricing=None):
        self.provider = provider
        self.pricing = load_pricing() if pricing is None else dict(pricing)

    def _cost(self, model, i, o, measured):
        if measured is not None:
            return measured, "measured"
        base = model.split("+")[0]
        if base not in self.pricing:                 # the API may name a dated version of the requested id
            base = getattr(self.provider, "model", base)
        if base in self.pricing and i is not None and o is not None:
            pi, po = self.pricing[base]
            basis = ("simulated_tokens_x_configured_price" if self.provider.simulated
                     else "configured_price_estimate")
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
            except ProviderTransient as e:
                err, validation = f"transient: {e}", {"ok": False, "errors": ["transient"]}
            except ProviderTruncated as e:
                err, validation = "malformed_output", {"ok": False, "errors": [f"truncated: {e}"]}
            except ProviderUnavailable as e:
                err, validation = f"unavailable: {e}", {"ok": False, "errors": ["unavailable"]}
            except (ValidationError, json.JSONDecodeError, TypeError) as e:
                msg = str(e).splitlines()
                err = "malformed_output"
                validation = {"ok": False, "errors": msg[:6]}
            except Exception as e:                  # anything else the provider raises: recorded, never fatal
                err = f"provider_error: {type(e).__name__}: {e}"
                validation = {"ok": False, "errors": ["provider_error"]}
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
                error=err, timed_out=timed_out,
                tokens_measured=bool(resp and resp.tokens_measured),
                stop_reason=resp.stop_reason if resp else None,
                request_id=resp.request_id if resp else None).to_dict())
            if data is not None:
                return data, records, None
            if err and err.startswith("unavailable"):
                return None, records, "llm_unavailable"
            if err and err.startswith("provider_error"):
                return None, records, "llm_provider_error"
            attempt += 1
            if attempt > budget.max_retries:
                return None, records, ("llm_timeout" if timed_out else
                                       "llm_transient" if err.startswith("transient") else "llm_output_invalid")
