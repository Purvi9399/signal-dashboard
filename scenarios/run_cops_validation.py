"""Run COPS validation against scenario execution traces.

Synthesizes metric_events from scenario telemetry and runs all active
COPs (Runtime Monitoring, Anomaly Detection, Workflow Orchestration,
Behavior Analytics) using the RuleInterpreter and MemoryBackend.

Usage:
    python3 run_cops_validation.py                # all scenarios
    python3 run_cops_validation.py --scenario b   # one scenario
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BUILD, "collector_framework"))
sys.path.insert(0, os.path.join(BUILD, "signal_cops", "collector"))

from emit_events import emit_scenario, emit_all
from curate.adapter_source_events import iter_adapted_raw_events
from cops.models import MetricEvent
from cops.reader import MemoryBackend, DashboardMetricReader
from cops.runner import COPRunner, MemoryStore
from cops.interpret import RuleInterpreter
from cops.dispatch import LogDispatcher, CompositeDispatcher


def _metric_event(
    session_id: str,
    metric_id: str,
    metric_name: str,
    value: float | None,
    state: str,
    threshold: float | None = None,
    deviation: float | None = None,
    features: dict | None = None,
    evidence_refs: tuple = (),
    context: dict | None = None,
) -> dict:
    return {
        "metric_event_id": str(uuid.uuid4()),
        "metric_id": metric_id,
        "metric_name": metric_name,
        "session_id": session_id,
        "value": value,
        "state": state,
        "threshold": threshold,
        "deviation": deviation,
        "direction": "above",
        "trace_id": None,
        "tool": "claude-code",
        "features": features or {},
        "evidence_refs": evidence_refs,
        "source": "scenario_validation",
        "metric_version": "1",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "basis_present": value is not None,
        "context": context or {},
    }


def _analyze_scenario_events(events: list[dict]) -> dict:
    """Extract telemetry statistics from adapted scenario events."""
    adapted = []
    for event in events:
        for triple in iter_adapted_raw_events(event):
            adapted.append(triple)

    session_ids = set()
    tool_calls = []
    test_runs = []
    file_edits = []
    failures = []

    for collector, fields, raw in adapted:
        sid = fields.get("session_id")
        if sid:
            session_ids.add(sid)
        otype = fields.get("observable_type", "")
        tool_name = fields.get("tool_name", "")

        if otype == "tool_request":
            tool_calls.append(fields)
            cmd = (fields.get("tool_arguments") or {}).get("CommandLine", "") or fields.get("command", "")
            if tool_name == "Bash" and "pytest" in cmd:
                test_runs.append(fields)
            if tool_name in ("write_to_file", "replace_file_content"):
                file_edits.append(fields)
        elif otype == "tool_result":
            if not fields.get("success", True):
                failures.append(fields)

    session_id = next(iter(session_ids)) if session_ids else "unknown"

    # Count test run outcomes from raw events
    test_failures = 0
    test_passes = 0
    for event in events:
        raw = event.get("raw_payload", {})
        if raw.get("hook_event_name") == "PostToolUse":
            tc = raw.get("toolCall", {})
            cmd = (tc.get("args") or {}).get("CommandLine", "")
            if "pytest" in cmd:
                ec = raw.get("exit_code")
                if ec == 0:
                    test_passes += 1
                elif ec is not None:
                    test_failures += 1

    # Detect repetition (same tool+args in sequence)
    repetitions = 0
    prev_key = None
    for tc in tool_calls:
        key = (tc.get("tool_name"), str(tc.get("tool_arguments")))
        if key == prev_key:
            repetitions += 1
        prev_key = key

    # Detect collectors present in the event stream
    collectors = set()
    harbor_events = []
    for collector, fields, raw in adapted:
        collectors.add(collector)
        otype = fields.get("observable_type", "")
        if otype.startswith("container_") or otype.startswith("scan_") or otype.startswith("project_"):
            harbor_events.append(fields)

    if not collectors:
        collectors = {"local_hooks"}

    # Extract signal hints from event stream (attached by emit_events.py)
    signal_hints = {}
    for event in events:
        if "_signal_hints" in event:
            signal_hints = event["_signal_hints"]

    return {
        "session_id": session_id,
        "total_events": len(adapted),
        "tool_calls": len(tool_calls),
        "test_runs": len(test_runs),
        "test_failures": test_failures,
        "test_passes": test_passes,
        "file_edits": len(file_edits),
        "failures": len(failures),
        "repetitions": repetitions,
        "unique_tools": len({tc.get("tool_name") for tc in tool_calls}),
        "collectors": collectors,
        "harbor_events": len(harbor_events),
        "signal_hints": signal_hints,
    }


def synthesize_metrics(stats: dict) -> list[dict]:
    """Build metric_event rows from scenario statistics.

    Uses signal_hints from emit_events.py to produce realistic metric states
    instead of hardcoding everything to NORMAL.
    """
    sid = stats["session_id"]
    hints = stats.get("signal_hints", {})
    metrics = []

    def _hint(key, default=0):
        return hints.get(key, default)

    # --- Runtime Monitoring metrics ---
    collector_count = len(stats["collectors"])
    coverage = min(collector_count / 3.0, 1.0)
    cov_state = "NORMAL" if coverage >= 0.6 else ("WARNING" if coverage >= 0.3 else "EXCEPTION")
    missing = [c for c in ("mcp_proxy", "fs_watcher") if c not in stats["collectors"]]
    metrics.append(_metric_event(
        sid, "telemetry.collector_coverage", "Collector Coverage",
        round(coverage, 2), cov_state, threshold=0.6,
        features={"collectors": sorted(stats["collectors"]),
                  "missing": missing},
        context={"transcript_only": collector_count == 1},
    ))
    metrics.append(_metric_event(
        sid, "telemetry.source_agreement", "Source Agreement",
        0.85 if collector_count >= 2 else 1.0,
        "NORMAL",
        threshold=0.7,
        features={"reason": "" if collector_count >= 2
                  else "single collector, no cross-validation possible"},
    ))
    metrics.append(_metric_event(
        sid, "telemetry.turn_linkage", "Turn Linkage",
        0.95, "NORMAL", threshold=0.7,
    ))

    # Lane linkage — driven by subagent lanes hint
    lanes = _hint("subagent_lanes")
    if lanes > 0:
        lane_val = round(min(lanes / 5.0, 1.0), 2)
        lane_state = "NORMAL" if lanes <= 2 else ("WARNING" if lanes <= 4 else "EXCEPTION")
        metrics.append(_metric_event(
            sid, "telemetry.lane_linkage", "Lane Linkage",
            lane_val, lane_state, threshold=0.5,
            features={"subagent_lanes": lanes},
        ))
    else:
        metrics.append(_metric_event(
            sid, "telemetry.lane_linkage", "Lane Linkage",
            None, "NOT_APPLICABLE",
            features={"reason": "no subagent lanes in this session"},
        ))

    # Unknown share — higher when unattributed actions present
    unattr = _hint("unattributed")
    unknown_share = 0.15 if collector_count == 1 else 0.05
    if unattr > 0:
        unknown_share = min(0.3 + unattr * 0.1, 0.7)
    unk_state = "WARNING" if unknown_share > 0.1 else "NORMAL"
    if unknown_share > 0.3:
        unk_state = "EXCEPTION"
    metrics.append(_metric_event(
        sid, "telemetry.unknown_share", "Unknown Share",
        round(unknown_share, 2), unk_state, threshold=0.1,
        features={"unknown": ["mcp_proxy", "fs_watcher"] if collector_count == 1 else [],
                  "unattributed_actions": unattr},
    ))

    evidence_score = round(coverage * 0.85, 2)
    ev_state = "NORMAL" if evidence_score >= 0.7 else ("WARNING" if evidence_score >= 0.5 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "telemetry.evidence_score", "Evidence Score",
        evidence_score, ev_state, threshold=0.7,
    ))

    # --- Anomaly Detection metrics ---
    tc = stats["tool_calls"]
    token_ratio = tc / 15.0
    tok_state = "NORMAL" if token_ratio < 2.0 else ("WARNING" if token_ratio < 3.0 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.token_outlier", "Token Outlier",
        round(token_ratio, 2), tok_state, threshold=2.0,
        deviation=round(token_ratio / 2.0, 2) if token_ratio > 2.0 else None,
    ))

    # Duration outlier — driven by hint
    dur = _hint("duration_outlier", 1.0)
    dur_state = "NORMAL" if dur < 2.0 else ("WARNING" if dur < 3.5 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.duration_outlier", "Duration Outlier",
        round(dur, 2), dur_state, threshold=2.0,
        deviation=round(dur / 2.0, 2) if dur > 2.0 else None,
    ))

    # Transition novelty — higher when MCP or outside workspace
    novelty = 0.0
    if _hint("first_seen_mcp", False):
        novelty = 0.7
    elif _hint("outside_workspace") > 0:
        novelty = 0.4
    nov_state = "NORMAL" if novelty < 0.5 else ("WARNING" if novelty < 0.7 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "path.transition_novelty", "Transition Novelty",
        round(novelty, 2), nov_state, threshold=0.5,
    ))

    action_rate = round(tc / max(stats["total_events"], 1), 2)
    ar_state = "NORMAL" if action_rate < 2.0 else ("WARNING" if action_rate < 3.0 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "activity.action_rate_ratio", "Action Rate Ratio",
        action_rate, ar_state, threshold=2.0,
    ))

    # First seen MCP — driven by hint
    if _hint("first_seen_mcp", False):
        metrics.append(_metric_event(
            sid, "access.first_seen_mcp", "First Seen MCP",
            1, "EXCEPTION", threshold=1,
            features={"reason": "new MCP server observed for the first time"},
        ))
    else:
        metrics.append(_metric_event(
            sid, "access.first_seen_mcp", "First Seen MCP",
            None, "NOT_APPLICABLE",
            features={"reason": "no MCP servers in scenario"},
        ))

    # --- Behavior Analytics metrics ---
    rep_ratio = stats["repetitions"] / max(tc, 1)
    rep_state = "NORMAL" if rep_ratio < 0.15 else ("WARNING" if rep_ratio < 0.3 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.repetition", "Repetition Ratio",
        round(rep_ratio, 2), rep_state, threshold=0.15,
        deviation=round(rep_ratio / 0.15, 2) if rep_ratio > 0.15 else None,
    ))

    retry_count = max(stats["test_failures"] - 1, 0)
    retry_state = "NORMAL" if retry_count <= 1 else ("WARNING" if retry_count <= 2 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.retry", "Retry Count",
        retry_count, retry_state, threshold=2,
        deviation=round(retry_count / 2.0, 2) if retry_count > 2 else None,
    ))

    # Stalled turns — driven by hint
    stalled = _hint("stalled_turns")
    stall_state = "NORMAL" if stalled == 0 else ("WARNING" if stalled <= 2 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.stalled", "Stalled Turns",
        stalled, stall_state, threshold=2,
        deviation=round(stalled / 2.0, 2) if stalled > 2 else None,
    ))

    fail_rate = stats["test_failures"] / max(stats["test_runs"], 1)
    fail_state = "NORMAL" if fail_rate < 0.5 else ("WARNING" if fail_rate < 0.75 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.failed", "Failed Rate",
        round(fail_rate, 2), fail_state, threshold=0.5,
        deviation=round(fail_rate / 0.5, 2) if fail_rate > 0.5 else None,
    ))

    metrics.append(_metric_event(
        sid, "oversight.fast_approvals", "Fast Approvals",
        0, "NORMAL", threshold=3,
    ))

    # --- Workflow Orchestration metrics ---
    declined = _hint("declined_inferred")
    decl_state = "NORMAL" if declined == 0 else ("WARNING" if declined <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "oversight.declined_inferred", "Declined Inferred",
        declined, decl_state, threshold=1,
    ))

    lanes_val = _hint("subagent_lanes")
    lanes_state = "NORMAL" if lanes_val == 0 else ("WARNING" if lanes_val <= 3 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "interaction.subagent_lanes", "Subagent Lanes",
        lanes_val, lanes_state, threshold=3,
        features={"delegation_chains": lanes_val} if lanes_val > 0 else {},
    ))

    # --- Incident Response / Policy Enforcement metrics ---
    secrets = _hint("secrets_detected")
    sec_state = "NORMAL" if secrets == 0 else ("WARNING" if secrets <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.secrets", "Secrets Detected",
        secrets, sec_state, threshold=1,
        deviation=round(secrets / 1.0, 2) if secrets > 0 else None,
    ))

    sensitive = _hint("sensitive_unasked")
    sens_state = "NORMAL" if sensitive == 0 else ("WARNING" if sensitive <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.sensitive_unasked", "Sensitive Unasked",
        sensitive, sens_state, threshold=1,
        deviation=round(sensitive / 1.0, 2) if sensitive > 0 else None,
    ))

    blocked = _hint("blocked_actions")
    blk_state = "NORMAL" if blocked == 0 else ("WARNING" if blocked <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.blocked", "Blocked Actions",
        blocked, blk_state, threshold=1,
        deviation=round(blocked / 1.0, 2) if blocked > 0 else None,
    ))

    # Outside workspace
    outside = _hint("outside_workspace")
    out_state = "NORMAL" if outside == 0 else ("WARNING" if outside <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.outside", "Outside Workspace",
        outside, out_state, threshold=1,
        deviation=round(outside / 1.0, 2) if outside > 0 else None,
    ))

    # Reverted
    reverted = _hint("reverted")
    rev_state = "NORMAL" if reverted == 0 else ("WARNING" if reverted <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.reverted", "Reverted Changes",
        reverted, rev_state, threshold=1,
    ))

    # --- Interaction Discovery metrics ---
    mcp = _hint("mcp_servers")
    mcp_state = "NORMAL" if mcp == 0 else ("WARNING" if mcp <= 3 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "interaction.mcp_servers", "MCP Servers",
        mcp, mcp_state, threshold=3,
        features={"mcp_count": mcp} if mcp > 0 else {"reason": "no MCP servers in scenario"},
    ))

    # --- Audit Traceability metrics ---
    unattr_val = _hint("unattributed")
    ua_state = "NORMAL" if unattr_val == 0 else ("WARNING" if unattr_val <= 1 else "EXCEPTION")
    metrics.append(_metric_event(
        sid, "insights.unattributed", "Unattributed Actions",
        unattr_val, ua_state, threshold=1,
        deviation=round(unattr_val / 1.0, 2) if unattr_val > 0 else None,
    ))

    return metrics


def run_cops_for_scenario(scenario_name: str, events: list[dict], mode: str = "observe") -> dict:
    """Run all active COPs against one scenario's events."""
    stats = _analyze_scenario_events(events)
    metric_rows = synthesize_metrics(stats)

    backend = MemoryBackend(metric_rows)
    reader = DashboardMetricReader(backend)
    interp = RuleInterpreter()
    store = MemoryStore()
    dispatcher = LogDispatcher()

    target_cops = [
        "runtime_monitoring",
        "anomaly_detection",
        "workflow_orchestration",
        "behavior_analytics",
        "resource_management",
        "interaction_discovery",
        "classification_tagging",
        "policy_enforcement",
        "audit_traceability",
        "incident_response",
        "context_memory",
        "recovery_resilience",
        "risk_mitigation",
        "governance_intelligence",
        "workload_optimization",
        "knowledge_policy_management",
        "agent_lifecycle_management",
        "governance_architecture",
    ]

    runner = COPRunner(reader, interp, store, active=target_cops, mode=mode,
                       dispatcher=dispatcher)
    decisions = runner.run_session(stats["session_id"])

    return {
        "scenario": scenario_name,
        "session_id": stats["session_id"],
        "stats": stats,
        "actions_dispatched": [r.row() for r in runner.action_log],
        "decisions": {
            cop: {
                "interpretation": d.interpretation,
                "action": d.action,
                "severity": d.severity,
                "evidence_status": d.evidence_status,
                "decision_state": d.decision_state,
                "rationale": d.rationale[:200],
                "failed": d.failed,
                "error": d.error,
                "metrics_read": len(d.metrics_read),
                "explanation": d.explanation if hasattr(d, 'explanation') and d.explanation else {
                    "trigger": d.rationale[:100] if d.rationale else "metric state change",
                    "analysis": f"{len(d.metrics_read)} metrics evaluated against {cop} rule",
                    "determination": d.interpretation,
                    "proposed_action": d.action,
                },
                "verification": [
                    {"id": v["id"], "passed": v["passed"]}
                    for v in d.verification if not v["passed"]
                ],
            }
            for cop, d in decisions.items()
        },
    }


def print_decision_table(results: list[dict]):
    """Print a summary table of all COP decisions across scenarios."""
    cops = ["runtime_monitoring", "anomaly_detection",
            "workflow_orchestration", "behavior_analytics",
            "resource_management", "interaction_discovery",
            "classification_tagging", "policy_enforcement",
            "audit_traceability", "incident_response",
            "context_memory", "recovery_resilience",
            "risk_mitigation", "governance_intelligence",
            "workload_optimization", "knowledge_policy_management",
            "agent_lifecycle_management", "governance_architecture"]
    cop_short = {"runtime_monitoring": "RM", "anomaly_detection": "AD",
                 "workflow_orchestration": "WO", "behavior_analytics": "BA",
                 "resource_management": "RS", "interaction_discovery": "ID",
                 "classification_tagging": "CT", "policy_enforcement": "PE",
                 "audit_traceability": "AT", "incident_response": "IR",
                 "context_memory": "CM", "recovery_resilience": "RR",
                 "risk_mitigation": "RK", "governance_intelligence": "GI",
                 "workload_optimization": "WL", "knowledge_policy_management": "KP",
                 "agent_lifecycle_management": "AL", "governance_architecture": "GA"}

    header = f"{'Scenario':<12} {'Events':<8}"
    for c in cops:
        header += f" {cop_short[c]+':interp':<22} {cop_short[c]+':action':<12}"
    print(header)
    print("-" * len(header))

    for r in results:
        line = f"{r['scenario'].upper():<12} {r['stats']['total_events']:<8}"
        for c in cops:
            d = r["decisions"].get(c, {})
            interp = d.get("interpretation", "N/A")
            action = d.get("action", "N/A")
            failed = " FAIL" if d.get("failed") else ""
            line += f" {interp+failed:<22} {action:<12}"
        print(line)

    print()
    # Detail per scenario
    for r in results:
        collectors = sorted(r["stats"]["collectors"])
        harbor_n = r["stats"].get("harbor_events", 0)
        print(f"\n{'='*60}")
        print(f"Scenario {r['scenario'].upper()} — {r['stats']['total_events']} events, "
              f"{r['stats']['tool_calls']} tool calls, "
              f"{r['stats']['test_runs']} test runs "
              f"({r['stats']['test_passes']} pass, {r['stats']['test_failures']} fail)")
        print(f"  Collectors: {', '.join(collectors)}"
              f"  |  Harbor events: {harbor_n}")
        print(f"{'='*60}")
        for cop, d in r["decisions"].items():
            status = "FAILED" if d["failed"] else "OK"
            print(f"\n  {cop_short.get(cop, cop)} [{status}]")
            print(f"    Interpretation: {d['interpretation']}")
            print(f"    Action:         {d['action']}")
            print(f"    Severity:       {d['severity']}")
            print(f"    Evidence:       {d['evidence_status']}")
            print(f"    Decision:       {d['decision_state']}")
            print(f"    Metrics read:   {d['metrics_read']}")
            if d.get("rationale"):
                print(f"    Rationale:      {d['rationale'][:120]}")
            if d.get("error"):
                print(f"    Error:          {d['error']}")
            if d.get("verification"):
                print(f"    Failed checks:  {d['verification']}")

        dispatched = r.get("actions_dispatched", [])
        if dispatched:
            print(f"\n  Actions dispatched: {len(dispatched)}")
            for a in dispatched:
                print(f"    {a['cop']}: {a['action']} → {a['dispatcher']} "
                      f"({'OK' if a['dispatched'] else 'FAILED'})")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run COPS validation against scenarios")
    parser.add_argument("--scenario", "-s", default=None, help="Single scenario (a-e)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    args = parser.parse_args()

    if args.scenario:
        events = emit_scenario(args.scenario)
        results = [run_cops_for_scenario(args.scenario, events)]
    else:
        all_events = emit_all()
        results = [
            run_cops_for_scenario(name, events)
            for name, events in sorted(all_events.items())
        ]

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print_decision_table(results)
