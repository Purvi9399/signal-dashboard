"""Export all COPS validation data as JSON for the integrated dashboard.

Usage:
    python3 export_dashboard_data.py > dashboard_data.json
"""
from __future__ import annotations
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BUILD, "collector_framework"))
sys.path.insert(0, os.path.join(BUILD, "signal_cops", "collector"))

from run_cops_validation import run_cops_for_scenario
from emit_events import emit_scenario, emit_all
from cops.specs import SPECS, BY_KEY, CONSUMES, topological_order


def export():
    cop_specs = []
    for i, key in enumerate(topological_order()):
        s = BY_KEY[key]
        cop_specs.append({
            "key": s.key,
            "name": s.name,
            "question": s.behavioural_question,
            "metric_keys": list(s.metric_keys),
            "missing_metrics": list(s.missing_metrics),
            "downstream": list(s.downstream_cops),
            "consumes": list(s.consumes_decisions_from),
            "permitted_actions": list(s.permitted_actions),
            "interpretations": list(s.interpretations),
            "active": s.active,
            "cadence": getattr(s, 'cadence', 'per_session'),
            "order": i,
        })

    edges = []
    for consumer, upstreams in CONSUMES.items():
        for upstream in upstreams:
            edges.append({"from": upstream, "to": consumer, "type": "consumes"})

    metric_cops = []
    for s in SPECS:
        for mk in s.metric_keys:
            metric_cops.append({"metric": mk, "cop": s.key})

    primary = ["runtime_monitoring", "anomaly_detection", "workflow_orchestration",
               "behavior_analytics", "incident_response", "policy_enforcement",
               "classification_tagging"]
    secondary = ["interaction_discovery", "resource_management", "audit_traceability",
                 "context_memory", "recovery_resilience", "risk_mitigation"]
    tertiary = ["governance_intelligence", "workload_optimization",
                "knowledge_policy_management", "agent_lifecycle_management",
                "governance_architecture"]

    scenario_results = []
    all_events = emit_all()
    for name, events in sorted(all_events.items()):
        result = run_cops_for_scenario(name, events)
        scenario_results.append(result)

    return json.dumps({
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "cops": cop_specs,
        "edges": edges,
        "metric_cops": metric_cops,
        "hierarchy": {"primary": primary, "secondary": secondary, "tertiary": tertiary},
        "order": list(topological_order()),
        "scenarios": scenario_results,
    }, indent=2, default=str)


if __name__ == "__main__":
    print(export())
