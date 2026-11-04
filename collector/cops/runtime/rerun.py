"""
The approved targeted live re-run (2026-11-04): six LLM cases plus fleet F2.

    python -m cops.runtime.rerun --preflight     # no call: checks config, scope, cap, adapters
    python -m cops.runtime.rerun --simulate      # scripted replies through the same cap guard
    python -m cops.runtime.rerun                 # real calls, after --preflight passes

Real calls need the same settings as live_eval.py (COPS_LLM_LIVE, COPS_LLM_DATA_APPROVED,
COPS_MODEL, COPS_PRICING, COPS_LIVE_MAX_TOTAL_USD, provider credentials), and the cap may
not exceed APPROVED_CAP_USD.

The hard cap is enforced on the whole run, before every provider call, retries and
re-assessments included: a call is made only if the money already spent plus that
call's worst-case cost (its estimated input tokens and the full max_output_tokens at
the configured prices) stays within the cap. Otherwise the call is refused and recorded.

Results go to docs/cops/evaluation/rerun_2026-11-04/ and never overwrite the
2026-11-02 live_results.json.
"""

import argparse, json, os, sys, time

from . import live_eval as LE
from . import test_bench as TB
from .bench_cases import FLEET
from .llm import AnthropicProvider, FixtureProvider, ProviderUnavailable, load_pricing
from .profiles import LLM_COPS

APPROVED_CAP_USD = 0.75
CASES = ("workflow_orchestration:abnormal", "classification_tagging:normal",
         "workload_optimization:abnormal", "knowledge_policy_management:abnormal",
         "governance_architecture:abnormal", "governance_architecture:normal")
FLEET_IDS = ("F2",)
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "cops", "evaluation",
                   "rerun_2026-11-04")
CHARS_PER_TOKEN = 2.5          # conservative: real JSON payloads run nearer 3-4 characters a token


class CapGuard:
    """One ledger for the whole run. Every call, retry and re-assessment passes here."""

    def __init__(self, cap, prices, model, max_output_tokens):
        self.cap, self.prices, self.model, self.max_out = cap, prices, model, max_output_tokens
        self.spent, self.calls, self.refused = 0.0, 0, []

    def worst_case(self, system, payload):
        pin, pout = self.prices[self.model]
        tin = (len(system) + len(json.dumps(payload, default=str))) / CHARS_PER_TOKEN
        return (tin * pin + self.max_out * pout) / 1e6

    def allow(self, cop, purpose, system, payload):
        w = self.worst_case(system, payload)
        if self.spent + w > self.cap:
            self.refused.append({"cop": cop, "purpose": purpose, "spent": round(self.spent, 6),
                                 "worst_case": round(w, 6), "cap": self.cap})
            raise ProviderUnavailable(f"total cap: ${self.spent:.4f} spent, next call could cost up to "
                                      f"${w:.4f}, cap ${self.cap:.2f}")
        self.calls += 1

    def charge(self, resp):
        if resp is None or resp.input_tokens is None:
            return
        pin, pout = self.prices[self.model]
        self.spent += ((resp.input_tokens or 0) * pin + (resp.output_tokens or 0) * pout) / 1e6


class CappedProvider:
    def __init__(self, inner, guard):
        self.inner, self.guard = inner, guard
        self.name, self.simulated = inner.name, inner.simulated
        self.model = getattr(inner, "model", "none")

    def complete(self, cop, purpose, system, payload, schema, timeout_s):
        self.guard.allow(cop, purpose, system, payload)
        resp = None
        try:
            resp = self.inner.complete(cop, purpose, system, payload, schema, timeout_s)
            return resp
        finally:
            self.guard.charge(resp)


def preflight():
    """Every condition of the approved run, checked with no provider call."""
    from .actions import DryRunAdapter
    probs, info = [], {}
    cap, live_probs = LE.check_live_config()
    probs += live_probs
    if cap is not None and cap > APPROVED_CAP_USD:
        probs.append(f"COPS_LIVE_MAX_TOTAL_USD={cap} exceeds the approved ${APPROVED_CAP_USD}")
    info["model"] = os.environ.get("COPS_MODEL")
    info["cap_usd"] = cap
    info["max_output_tokens"] = int(os.environ.get("COPS_MAX_OUTPUT_TOKENS") or 16000)
    try:
        prices = load_pricing()
        info["price_per_mtok"] = prices.get(info["model"])
    except (ValueError, TypeError):
        prices = {}
    known = {c["id"] for c in LE.CASES}
    bad = [c for c in CASES if c not in known]
    if bad or len(CASES) != 6:
        probs.append(f"scope: unknown or wrong number of cases: {bad or len(CASES)}")
    if [f["id"] for f in FLEET if f["id"] in FLEET_IDS] != list(FLEET_IDS):
        probs.append("scope: fleet F2 not found")
    info["scope"] = list(CASES) + [f"fleet:{x}" for x in FLEET_IDS]
    f2 = next(f for f in FLEET if f["id"] == "F2")
    info["fleet_F2_llm_cops"] = [c for c in f2["cops"] if c in LLM_COPS]
    # action effects: every runtime the rerun builds uses the dry-run adapter only
    from .evaluation import make_runtime
    b = LE.CASES[0]["build"]("preflight")
    rt = make_runtime("preflight", "preflight", b["rows"], b["art"], provider=None)
    adapter = getattr(rt.enforcement, "dry_run_adapter", None)
    if not isinstance(adapter, DryRunAdapter):
        probs.append(f"action adapter is {type(adapter).__name__}, not DryRunAdapter")
    info["action_adapter"] = type(adapter).__name__
    info["per_case_budget"] = LE.LIVE_BUDGET.as_dict()
    if info.get("price_per_mtok"):
        g = CapGuard(cap or APPROVED_CAP_USD, prices, info["model"], info["max_output_tokens"])
        info["worst_case_per_call_usd_at_20k_char_payload"] = round(g.worst_case("", "x" * 20000), 4)
    return probs, info


def run(live):
    prices = load_pricing() if live else {"simulated": (2.0, 10.0)}
    model = os.environ.get("COPS_MODEL") if live else "simulated"
    cap = float(os.environ.get("COPS_LIVE_MAX_TOTAL_USD") or APPROVED_CAP_USD) if live else APPROVED_CAP_USD
    max_out = int(os.environ.get("COPS_MAX_OUTPUT_TOKENS") or 16000)
    guard = CapGuard(min(cap, APPROVED_CAP_USD), prices, model, max_out)

    def provider():
        inner = AnthropicProvider() if live else LE.offline_provider()
        if not live:
            inner.model = "simulated"
        return CappedProvider(inner, guard)

    t0 = time.time()
    cases = []
    for cid in CASES:
        case = next(c for c in LE.CASES if c["id"] == cid)
        r = LE.run_one(case, provider(), LE.LIVE_BUDGET)
        r["result_source"] = "live_new_call" if live else "simulated"
        cases.append(r)
    fleet = []
    for sc in FLEET:
        if sc["id"] not in FLEET_IDS:
            continue
        case = {"id": f"fleet:{sc['id']}", "build": sc["build"]}
        o, rt, err, wall = TB.run_investigation(case, provider(), LE.LIVE_BUDGET, cops=sc["cops"])
        res = o.get("results") or {}
        inv = [i for v in res.values() for i in v["llm_invocations"]]
        per = TB._per_cop(sc, res)
        checks = TB.fleet_checks(sc, per, o.get("consumption", [])) if not err else \
            [{"check": "ran", "passed": False, "actual": err[-400:]}]
        fleet.append({"id": sc["id"], "title": sc["title"], "event": sc["event"], "cops": sc["cops"],
                      "source": "live_new_call" if live else "simulated", "per_cop": per, "checks": checks,
                      "error": err, "wall_ms": wall, "consumption": o.get("consumption", []),
                      "usage": TB._usage(inv),
                      "verdict": "PASS" if not err and all(c["passed"] for c in checks) else "FAIL"})
    problems = LE.verify_records(cases, live)
    return {"live": live, "model": model if live else None, "cap_usd": guard.cap,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "wall_s": round(time.time() - t0, 1),
            "ledger": {"spent_usd": round(guard.spent, 6), "calls_allowed": guard.calls,
                       "calls_refused": guard.refused},
            "record_problems": problems, "cases": cases, "fleet": fleet}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    a = ap.parse_args(argv)
    if a.preflight or not a.simulate:
        probs, info = preflight()
        print(json.dumps(info, indent=1, default=str))
        if probs:
            print("preflight FAILED; nothing was called:\n  - " + "\n  - ".join(probs))
            return 2
        print("preflight passed")
        if a.preflight:
            return 0
    out = run(live=not a.simulate)
    os.makedirs(OUT, exist_ok=True)
    name = "rerun_results.json" if out["live"] else "rerun_simulated.json"
    with open(os.path.join(OUT, name), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out["ledger"]), "problems:", out["record_problems"])
    for r in out["cases"]:
        print(f"  {r['id']:40s} {r['label']} ({r['status']}) expected {r['expected']}")
    for f in out["fleet"]:
        print(f"  fleet {f['id']}: {f['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
