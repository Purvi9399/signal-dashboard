"""
One COP run, recorded in full for later explanation.

run_detail(state) copies what a COP run state already holds (finding, evidence
sufficiency, tool calls and results, model invocations, verifier results, VS-01,
triage, proposed action and its lifecycle) into a plain JSON record. Nothing is
computed or summarised here beyond trimming large tool results; the record is
what the COPS dashboard export (cops/dashboard_export.py) reads to explain a
decision. It never calls a model and never re-runs a COP.
"""

import json

MAX_RESULT_BYTES = 4000


def _trim(obj):
    s = json.dumps(obj, default=str)
    if len(s) <= MAX_RESULT_BYTES:
        return obj
    return {"truncated": True, "bytes": len(s), "preview": s[:MAX_RESULT_BYTES]}


def run_detail(state, decision_id=None, run_key=None):
    f = state.get("finding") or {}
    res = state.get("result") or {}
    pa = state.get("proposed_action") or res.get("proposed_action") or {}
    return {
        "record_version": "decision-record-1",
        "decision_id": decision_id, "run_key": run_key,
        "cop": state.get("cop") or f.get("cop"), "case_id": state.get("case_id"),
        "run_id": res.get("run_id"), "session_id": res.get("session_id"), "tenant_id": res.get("tenant_id"),
        "decided_at": res.get("decided_at"), "mode": state.get("mode") or res.get("mode"),
        "classification": state.get("classification") or res.get("classification"),
        "finding": {k: f.get(k) for k in ("finding_id", "label", "status", "confidence", "verified", "rationale",
                                          "severity", "claims", "missing_evidence", "missing_dependencies",
                                          "upstream_used", "qualifications", "lineage", "baseline")},
        "verification_support": f.get("verification_support"),
        "sufficiency": state.get("sufficiency") or res.get("sufficiency"),
        "upstream_view": state.get("upstream_view"),
        "tool_calls": [{**{k: t.get(k) for k in ("tool_call_id", "tool", "args", "requested_by", "status",
                                                 "rejection_reason", "latency_ms", "purpose")},
                        "result": _trim(t.get("result"))} for t in state.get("tool_calls") or []],
        "llm_invocations": [{k: i.get(k) for k in ("invocation_id", "purpose", "provider", "model", "simulated",
                                                   "evidence_refs", "tools_exposed", "latency_ms", "cost_usd",
                                                   "cost_basis")} for i in state.get("llm_invocations") or []],
        "stop_reason": state.get("stop_reason"),
        "verifier_results": [{k: r.get(k) for k in ("check_id", "result", "reason_code", "detail", "effect",
                                                    "subject")} for r in state.get("verifier_results") or []],
        "triage": state.get("triage"),
        "proposed_action": pa,
        "node_timings": state.get("node_timings"),
    }
