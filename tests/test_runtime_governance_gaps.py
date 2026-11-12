"""The four operational-governance gaps closed before the first Harbor trial.

1. A published finding carries its VS-01 record, so the case graph and the referral path
   weigh an upstream finding the same way.
2. The act phase does not re-run the COP: act_cop enters the action half of the graph with
   the decided state; no model or tool call is repeated.
3. Referral timeouts are pre-emptive: the initiator stops waiting at the wall-time deadline
   and a late answer is discarded.
4. A memory: evidence ref resolves only to a record the receiver's own run read.
Offline; model replies are scripted (simulated)."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
sys.path.insert(0, os.path.dirname(__file__))

from cops.runtime import referrals as RF
from cops.runtime.graph import run_cop, act_cop
from test_runtime_referrals import runtime, coding_session


def test_published_finding_carries_its_verification_support():
    rt = runtime("gap-1", coding_session(collectors=("otel",)))
    out = run_cop(rt, "runtime_monitoring")
    vs = out["published"]["verification_support"]
    assert vs == {k: out["finding"]["verification_support"][k] for k in ("decision", "score", "against", "unknown")}
    # and the referral deliverable carries the same record, so both paths read the same thing
    d = RF._deliverable(out, True, "r")
    assert d["verification_support"] == vs


def test_act_phase_reuses_the_decided_state_and_repeats_no_model_or_tool_call():
    rt = runtime("gap-2", coding_session(collectors=("otel",)))
    res = RF.run_investigation(rt, "anomaly_detection")
    last = res["investigation"]["runs"][-1]
    decided = res["states"][last["run_key"]]
    calls_before = len(rt.llm.provider.calls)
    import copy
    rx = copy.copy(rt)
    rx.mode, rx.live_session, rx.run_id = "supervised", True, rt.run_id + ":act"
    out = act_cop(rx, decided)
    assert len(rt.llm.provider.calls) == calls_before                      # no model call
    assert [c["tool_call_id"] for c in out["tool_calls"]] == [c["tool_call_id"] for c in decided["tool_calls"]]
    assert out["finding"]["finding_id"] == decided["finding"]["finding_id"]
    assert out["finding"]["verification_support"] == decided["finding"]["verification_support"]
    assert set(out["node_timings"]) <= {"grade_and_route", "authority_check", "await_approval", "execute_action",
                                        "record_decision", "validate_memory_update", "persist_and_publish"}
    assert out["proposed_action"]["final_authority"]["authorised"] in (True, False)
    assert sum(1 for r in out["verifier_results"] if r["check_id"] == "AU-01") == 2   # original + final, made again


def test_referral_timeout_is_preemptive_and_the_late_answer_is_discarded():
    def slow(cop, run_key):
        if cop == "runtime_monitoring":
            time.sleep(1.5)
    rt = runtime("gap-3", coding_session(collectors=("otel",)))
    t0 = time.monotonic()
    out = RF.run_investigation(rt, "anomaly_detection", run_hook=slow, wall_timeout_s=0.3)
    waited = time.monotonic() - t0
    rm = next(r for r in out["investigation"]["responses"] if r["receiver"] == "runtime_monitoring")
    assert rm["status"] == RF.TIMED_OUT and not rm["admissible"] and rm["delivered_as"] == "not_delivered"
    assert "abandoned" in rm["detail"]
    assert out["investigation"]["final"]["tier"] == "human"
    # the investigation did not wait for the 1.5 s receiver on its referral
    pre = [e for e in out["events"] if e.get("preemptive")]
    assert pre and pre[0]["elapsed_s"] < 1.0
    assert waited < 1.5 + 5                 # generous bound: other COP runs still take their time
    # the reassessment never used Runtime Monitoring
    assert "runtime_monitoring" not in (out["upstream"] or {})


def test_memory_refs_resolve_only_to_records_the_run_read():
    claims = [{"evidence_refs": ["memory:abc"]}]
    assert RF._refs_resolve({}, claims, None, set()) == ["memory:abc"]
    assert RF._refs_resolve({}, claims, None, {"abc"}) == []
    view = {"established": [{"record_id": "abc"}], "excluded": [{"record_id": "zzz"}]}
    assert RF._memory_ids(view) == {"abc"}
    assert RF._refs_resolve({}, [{"evidence_refs": ["memory:zzz"]}], None, RF._memory_ids(view)) == ["memory:zzz"]
