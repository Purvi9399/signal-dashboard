"""
First activation scenario: debugging sessions routed to RM, BA, AD and Recovery & Resilience.

    python -m cops.runtime.activation_scenario --out ../docs/cops/evaluation/activation   # from collector/

Each variant is a coding-agent session written as observables rows with the collector schema
fields the metrics read (collector/observables_schema.sql):

    test failures      command, exit_code, success          (insights.failed, insights.retry)
    test progression   the same test command over time, its exit_code going 1 -> 0
    code changes       tool_name Edit + file_path, file_modify with content_hash, reverted_to_earlier
    telemetry          collector, contributing, collectors_agree, turn_id
                       (telemetry.collector_coverage, source_agreement, turn_linkage, unknown_share)

Variants
    normal_debugging       one failing test run, an edit, a passing run
    repeated_failures      the same failing test command reissued with edits put back each time
    missing_evidence       commands without exit codes or outcomes, one collector only
    concurrent             repeated failures plus a token spike: AD and R&R eligible together
    consultation           edits put back but tests pass: R&R eligible, BA not; R&R consults BA

What is simulated: the session rows are synthetic (labelled), the estate is the synthetic
baseline of 7 earlier ordinary debugging sessions, Anomaly Detection has no model (UnavailableProvider) and stops at
llm_unavailable. Every COP run is the real LangGraph COP subgraph in dry-run. No model call,
no database, no action outside the sandbox.
"""

import argparse
import json
import os

from . import activation as A
from . import router as R
from . import synthetic as S
from . import evaluation as E

VERSION = "activation-scenario-1"
REVIEWER = "cops-owner@tenant-a.example"       # fixed test identity approving the slice's mapping
PYTEST = "pytest tests/test_api.py -q"


def _session(sid, day, collectors=("local_hooks", "otel"), tokens=1000, outcomes=True):
    s = S.Session(sid, day, collectors=collectors).start(tokens)
    r = s.add(observable_type="tool_request", tool_name="Read", file_path="/ws/app.py",
              tool_arguments={"p": "app.py"}, collectors_agree=True, outside_workspace=False)
    if outcomes:
        r["success"] = True
    return s


def _test(s, ok, with_outcome=True):
    k = dict(observable_type="tool_request", tool_name="Bash", command=PYTEST, tool_arguments={"cmd": PYTEST},
             tool_duration_ms=900)
    if with_outcome:
        k.update(exit_code=0 if ok else 1, success=ok)
    return s.add(**k)


def _edit(s, n, reverted=False):
    e = s.add(observable_type="tool_request", tool_name="Edit", file_path="/ws/app.py",
              tool_arguments={"p": "app.py", "edit": n}, success=True)
    s.add(observable_type="file_modify", collector="fs_watcher", file_path="/ws/app.py",
          file_change_kind="modify", content_hash="h0" if reverted else f"h{n}",
          reverted_to_earlier=True if reverted else None,
          occurred_at=e["occurred_at"].replace(":00Z", ":02Z"))


def normal_debugging(sid="dbg-normal", day=9):
    s = _session(sid, day)
    _test(s, False)
    _edit(s, 1)
    _test(s, True)
    return s.end()


def repeated_failures(sid="dbg-repeat", day=9, tokens=1000):
    s = _session(sid, day, tokens=tokens)
    _test(s, False)
    for n in range(1, 5):
        _edit(s, n, reverted=n > 1)
        _test(s, False)
    return s.end()


def missing_evidence(sid="dbg-missing", day=9):
    s = _session(sid, day, collectors=("otel",), outcomes=False)
    for n in range(3):
        _test(s, None, with_outcome=False)
        s.add(observable_type="tool_request", tool_name="Edit", file_path="/ws/app.py",
              tool_arguments={"p": "app.py", "edit": n})
    return s.end()


def concurrent(sid="dbg-concurrent", day=9):
    return repeated_failures(sid, day, tokens=40000)


def consultation(sid="dbg-consult", day=9):
    s = _session(sid, day)
    _edit(s, 1)
    _edit(s, 2, reverted=True)
    _test(s, True)
    return s.end()


def debugging_estate(n=7):
    """The baseline: n earlier sessions of ordinary debugging by the same operator and tool."""
    return [r for d in range(1, n + 1) for r in normal_debugging(f"base{d}", d)]


VARIANTS = {"normal_debugging": normal_debugging, "repeated_failures": repeated_failures,
            "missing_evidence": missing_evidence, "concurrent": concurrent, "consultation": consultation}


def with_tenant(rows, tenant=S.TENANT):
    return [{**r, "tenant_id": tenant} for r in rows]


def approved_registry():
    reg = A.MappingRegistry()
    b = A.default_bundle()
    reg.propose(b, "cops-maintainer@tenant-a.example", A.HUMAN, "first slice of the activation contract")
    reg.approve(b.version, REVIEWER, A.HUMAN)
    reg.activate(b.version)
    return reg


def factory(case_id, sid, rows):
    return E.make_runtime(case_id, sid, rows, {}, None, mode="dry_run")


def run_variant(name, registry=None, version=None, log=None, router=None, **kw):
    reg = registry or approved_registry()
    rt = router or R.Router(reg, factory, log=log, version=version, **kw)
    rows = with_tenant(VARIANTS[name]())
    out = rt.route(rows, estate_rows=with_tenant(debugging_estate()))
    return out[0], rt, reg


def summarise(out):
    recs = out["records"]
    last = {}
    for r in recs:
        last[r.cop] = r.transition
    return {"verdicts": {r.cop: r.transition for r in recs if r.transition in (A.ELIGIBLE, A.NOT_ELIGIBLE, A.UNKNOWN)},
            "final": last,
            "labels": {c: res["finding"]["label"] for c, res in out["results"].items()},
            "waves": [dict(r.detail).get("wave") for r in recs if r.transition == A.ACTIVATED],
            "concurrent": {r.cop: list(dict(r.detail).get("concurrent_with") or ()) for r in recs
                           if r.transition == A.ACTIVATED}}


def trace(name, out, reg):
    recs = out["records"]
    return {"scenario": name, "version": VERSION, "case_id": out["case_id"],
            "investigation_id": out["investigation_id"], "session_id": out["session_id"],
            "tenant_id": out["tenant_id"], "mapping_version": out["mapping_version"],
            "simulated": {"session_rows": "synthetic, collector schema fields", "estate": "synthetic: 7 earlier ordinary debugging sessions",
                          "anomaly_detection_model": "none (UnavailableProvider): stops at llm_unavailable",
                          "mode": "dry_run", "approver": f"{REVIEWER} (fixed test identity)"},
            "summary": summarise(out), "chain_verified": R.ActivationLog.verify_chain(
                type("L", (), {"of": lambda self, i: recs})(), out["investigation_id"]),
            "records": R.records_json(recs),
            "mapping": reg.to_dict()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    reg = approved_registry()
    with open(os.path.join(a.out, "mapping.json"), "w") as f:
        json.dump({"bundle": reg.active().to_dict(), "registry": reg.to_dict(),
                   "validation_errors": A.validate_bundle(reg.active())}, f, indent=1, default=str)
    for name in VARIANTS:
        out, _, _ = run_variant(name, registry=reg)
        t = trace(name, out, reg)
        # replay with the pinned version must reproduce every record but wall time
        again, _, _ = run_variant(name, registry=reg, version=out["mapping_version"])
        t["replay"] = {"pinned_version": out["mapping_version"],
                       "identical": [r.canonical() for r in again["records"]] == [r.canonical() for r in out["records"]]}
        with open(os.path.join(a.out, f"{name}.json"), "w") as f:
            json.dump(t, f, indent=1, default=str)
        s = t["summary"]
        print(f"{name:18s} {s['verdicts']} labels={s['labels']} replay={t['replay']['identical']}")


if __name__ == "__main__":
    main()
