"""
Three fixtures: the same session under three evidence conditions.

An agent reaches an MCP server it has never used, delegates to two subagents,
consumes heavily, and one of them touches a file. Only the evidence changes,
so the audience sees evidence quality moving the outcome rather than a
different story each time.

Metric events match what signal_metrics.py writes, so the COPS interpreters
read them without knowing they came from a file.
"""

import json, pathlib

VER = "2026.10"


def ev(metric_id, state, value=None, threshold=None, direction="above",
       features=None, refs=2, basis=True, name=None):
    return {
        "metric_event_id": f"fx_{metric_id.replace('.', '_')}",
        "metric_id": metric_id,
        "metric_name": name or metric_id.split(".")[-1].replace("_", " "),
        "session_id": None,                      # filled per scenario
        "value": value, "state": state, "threshold": threshold,
        "direction": direction,
        "features": features or {},
        "evidence_refs": [f"obs_{metric_id[:6]}_{i}" for i in range(refs)],
        "source": "signal_metrics", "metric_version": VER,
        "computed_at": "2026-10-13T09:00:00Z",
        "basis_present": basis,
    }


# What is true in every scenario: a new MCP server, heavy consumption, two lanes
COMMON = [
    ev("access.first_seen_mcp", "EXCEPTION", 1, 0, features={"server": "github-mcp"}),
    ev("insights.token_outlier", "CRITICAL", 312400, 50000,
       features={"multiple": 6.2, "tool": "claude-code"}),
    ev("activity.action_rate_ratio", "WARNING", 2.1, 2.0),
    ev("insights.duration_outlier", "NORMAL", 1, 3),
    ev("insights.stalled", "NORMAL", 0, 1),
    ev("insights.failed", "NORMAL", 1, 5),
    ev("insights.retry", "NORMAL", 0, 2),
    ev("insights.blocked", "NORMAL", 0, 1),
    ev("telemetry.source_agreement", "NORMAL", 1.0, 0.9, direction="below"),
    ev("telemetry.unknown_share", "NORMAL", 0.05, 0.3),
    ev("path.transition_novelty", "NORMAL", 0, 2),
]

SCENARIOS = {
    "full_evidence": {
        "note": "everything linked; hooks and OTel both present",
        "metrics": COMMON + [
            ev("telemetry.collector_coverage", "NORMAL", 1.0, 0.8, direction="below",
               features={"present": ["local_hooks", "otel", "fs_watcher"], "missing": []}),
            ev("telemetry.turn_linkage", "NORMAL", 1.0, 0.9, direction="below"),
            ev("telemetry.lane_linkage", "NORMAL", 1.0, 0.9, direction="below"),
            ev("interaction.subagent_lanes", "NORMAL", 2, 4),
            ev("insights.repetition", "NORMAL", 1, 3),
            ev("insights.outside", "NORMAL", 0, 1),
            ev("insights.sensitive_unasked", "NORMAL", 0, 1),
            ev("insights.secrets", "NORMAL", 0, 1),
            ev("insights.unattributed", "NORMAL", 0.02, 0.2),
        ],
        "sources": {},
    },
    "partial_evidence": {
        "note": "subagent lanes ran but their steps cannot be attributed to a lane",
        "metrics": COMMON + [
            ev("telemetry.collector_coverage", "NORMAL", 1.0, 0.8, direction="below",
               features={"present": ["local_hooks", "otel", "fs_watcher"], "missing": []}),
            ev("telemetry.turn_linkage", "NORMAL", 1.0, 0.9, direction="below"),
            ev("telemetry.lane_linkage", "CRITICAL", 0.0, 0.9, direction="below",
               features={"reason": "steps carry no lane id", "lanes_seen": 2}),
            ev("interaction.subagent_lanes", "UNKNOWN", None, None,
               features={"reason": "lane id absent; subagent_start seen twice"}),
            ev("insights.repetition", "NORMAL", 1, 3),
            ev("insights.outside", "NORMAL", 0, 1),
            ev("insights.sensitive_unasked", "NORMAL", 0, 1),
            ev("insights.secrets", "NORMAL", 0, 1),
            ev("insights.unattributed", "WARNING", 0.25, 0.2),
        ],
        "sources": {("local_hooks", "lane_id"): "session_linked",
                    ("otel", "lane_id"): "emitted",
                    ("fs_watcher", "lane_id"): "expected"},
    },
    "blocking_evidence": {
        "note": "a file was touched; which file is unrecoverable",
        "metrics": COMMON + [
            ev("telemetry.collector_coverage", "WARNING", 0.66, 0.8, direction="below",
               features={"present": ["local_hooks", "fs_watcher"], "missing": ["otel"]}),
            ev("telemetry.turn_linkage", "NORMAL", 1.0, 0.9, direction="below"),
            ev("telemetry.lane_linkage", "NORMAL", 1.0, 0.9, direction="below"),
            ev("interaction.subagent_lanes", "NORMAL", 2, 4),
            ev("insights.repetition", "NORMAL", 1, 3),
            ev("insights.outside", "UNKNOWN", None, None, basis=True,
               features={"reason": "file path absent; 3 change steps recorded"}),
            ev("insights.sensitive_unasked", "UNKNOWN", None, None, basis=True,
               features={"reason": "file path absent; cannot read sensitivity"}),
            ev("insights.secrets", "UNKNOWN", None, None, basis=True,
               features={"reason": "no content to scan"}),
            ev("insights.unattributed", "EXCEPTION", 0.6, 0.2,
               features={"reason": "file effects cannot be tied to a step"}),
        ],
        "sources": {("otel", "file_path"): "collector_alive",
                    ("fs_watcher", "file_path"): "session_linked",
                    ("local_hooks", "file_path"): "normalised",
                    ("otel", "sensitivity_tier"): "collector_alive",
                    ("fs_watcher", "sensitivity_tier"): "session_linked",
                    ("local_hooks", "sensitivity_tier"): "normalised"},
    },
}

if __name__ == "__main__":
    out = pathlib.Path("fixtures")
    out.mkdir(exist_ok=True)
    for key, s in SCENARIOS.items():
        sid = f"demo-{key}"
        for m in s["metrics"]:
            m["session_id"] = sid
        doc = {"session_id": sid, "scenario": key, "note": s["note"],
               "metric_events": s["metrics"],
               "evidence_conditions": {f"{k[0]}|{k[1]}": v for k, v in s["sources"].items()}}
        (out / f"{key}.json").write_text(json.dumps(doc, indent=2))
        print(f"  {key:<20}{len(s['metrics']):>3} metric events")
