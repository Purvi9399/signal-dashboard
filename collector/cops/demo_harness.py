"""One session through the harness, phase by phase."""

import sys
from .evidence import EvidenceAvailabilityService
from .authority import check as authority_check
from .decide import finalise
from .gap_registry import GapRegistry
from .harness import COPAgent, Registry
from .harness_configs import default_for

STAGES = ("expected", "collector_alive", "emitted", "ingested",
          "normalised", "session_linked", "usable_by_metrics")


def stages_failing_at(stage):
    out, broken = {}, False
    for s in STAGES:
        if broken:      out[s] = None
        elif s == stage: out[s], broken = False, True
        else:            out[s] = True
    return out


def build_registry():
    r = Registry()
    r.tool("metric_reader", lambda *a, **k: [], read_only=True)
    r.tool("evidence_service", lambda *a, **k: {}, read_only=True)
    r.tool("context_reader", lambda sid: {"lane_path": [{"step": "CHANGE", "failed": False}]},
           read_only=True)
    r.tool("history_retriever", lambda *a, **k: {"baseline_tokens": 50000}, read_only=True)
    r.tool("entitlement_lookup", lambda *a, **k: None, read_only=True)   # nothing supplies it
    r.tool("persistence_store", lambda *a, **k: True, read_only=False)
    r.tool("checkpoint_service", lambda *a, **k: {}, read_only=True)
    r.skill("retrieve_lane_path", "context_reader")
    r.skill("read_metric", "metric_reader")
    r.skill("read_evidence", "evidence_service")
    r.skill("retrieve_history", "history_retriever")
    r.skill("retrieve_entitlement", "entitlement_lookup")
    r.skill("compare_baseline", "history_retriever")
    r.skill("record_decision", "persistence_store")
    r.skill("record_evidence_gap", "persistence_store")
    r.skill("retrieve_collector_breakdown", "checkpoint_service")
    return r


def main(scenario="blocking_evidence", cops=("runtime_monitoring", "resource_management")):
    import json, pathlib
    from .specs import BY_KEY                     # the real specs
    from .interpret import RuleInterpreter
    from .models import MetricEvent

    doc = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                      f"{scenario}.json").read_text())
    conditions = {tuple(k.split("|")): v for k, v in doc["evidence_conditions"].items()}
    probe = lambda src, f: (stages_failing_at(conditions[(src, f)])
                            if (src, f) in conditions else dict.fromkeys(STAGES, True))
    svc = EvidenceAvailabilityService(probe, lambda e: True)
    reg, gaps = build_registry(), GapRegistry()
    rows = {m["metric_id"]: m for m in doc["metric_events"]}

    print(f"\n{scenario.replace('_', ' ').upper()}  —  {doc['note']}\n")
    for cop in cops:
        spec = BY_KEY[cop]
        events = [MetricEvent.from_row(rows[m]) for m in spec.metric_keys if m in rows]
        agent = COPAgent(spec, default_for(cop), RuleInterpreter(), svc, reg,
                         finalise, authority_check, gap_registry=gaps)
        d = agent.run(doc["session_id"], events)
        print(f"{spec.name}")
        for k in ("interpretation", "decision_status", "confidence",
                  "recommended_action", "action_status", "stop_reason",
                  "steps", "tool_calls", "evaluation_ms"):
            print(f"    {k:<20}{d[k]}")
        if d["missing_evidence"]:
            print(f"    {'missing':<20}{', '.join(d['missing_evidence'])}")
        if d["verification_problems"]:
            print(f"    {'rejected':<20}{'; '.join(d['verification_problems'])}")
        print(f"    {'phases':<20}{' '.join(d['trail'])}")
        print()
    gaps.save()


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
