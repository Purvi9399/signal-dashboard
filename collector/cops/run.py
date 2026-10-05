"""
Run the active COPS over sessions and show, for each one:

    metric -> COP interpretation -> context requested -> action -> downstream

    python3 -m cops.run                      latest 10 sessions from metric_events
    python3 -m cops.run --session ID
    python3 -m cops.run --local              compute metrics in memory first (no metric_events needed)
    python3 -m cops.run --write              also upsert cop_decisions
    python3 -m cops.run --llm                interpret with COPS_MODEL instead of rules
    python3 -m cops.run --activate interaction_discovery,policy_enforcement
"""

import argparse, json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cops.reader import DashboardMetricReader, SupabaseBackend, MemoryBackend
from cops.interpret import RuleInterpreter, LLMInterpreter
from cops.runner import COPRunner, MemoryStore, SupabaseStore
from cops.specs import ACTIVE, BY_KEY


def show(sid, decisions):
    print(f"\n{'=' * 100}\nsession {sid}")
    for key, d in decisions.items():
        off = [m for m in d.metrics_read if m["state"] not in ("NORMAL", "NOT_APPLICABLE")]
        print(f"\n  {BY_KEY[key].name}  [{d.evaluation_ms} ms{', interpreter skipped' if d.skipped_interpreter else ''}]")
        for m in off[:6]:
            print(f"    metric     {m['metric']:<30} {str(m['value']):>8}  {m['state']}")
        if not off:
            print(f"    metric     all {len(d.metrics_read)} normal or not applicable")
        print(f"    reads as   {d.interpretation}  (confidence {d.confidence}, evidence {d.evidence_confidence})")
        if d.rationale:
            print(f"    because    {d.rationale[:150]}")
        for c in d.context_used:
            print(f"    context    {c['key']}: {c['rows']} rows, {c['ms']} ms {c.get('error', '')}")
        print(f"    action     {d.action}  {d.severity}  {d.action_reason[:110]}")
        if d.downstream_cops:
            print(f"    passes to  {', '.join(d.downstream_cops)}")
        if d.failed:
            print(f"    FAILED     {d.error}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--activate", default="")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    a = ap.parse_args()

    if a.local:
        import signal_metrics, signal_traces
        rows = signal_traces.get("observables", f"select={signal_metrics.COLS}&order=occurred_at.asc")
        events, sessions = signal_metrics.compute(rows)
        backend = MemoryBackend(events, sessions)
    else:
        backend = SupabaseBackend()
    reader = DashboardMetricReader(backend)
    interp = LLMInterpreter() if a.llm else RuleInterpreter()
    active = set(ACTIVE) | {k for k in a.activate.split(",") if k}
    runner = COPRunner(reader, interp, SupabaseStore() if a.write else MemoryStore(), active=active)

    ids = [a.session] if a.session else reader.sessions(a.sessions)
    tally = Counter()
    for sid in ids:
        out = runner.run_session(sid)
        for d in out.values():
            tally[(d.cop, d.interpretation, d.action)] += 1
        if not a.quiet:
            show(sid, out)

    print(f"\n{'=' * 100}\nrun {runner.run_id}: {len(ids)} sessions, mode {runner.mode}, interpreter {interp.name}")
    for (cop, interp_, action), n in sorted(tally.items()):
        print(f"  {cop:<22}{interp_:<16}{action:<14}{n:>4}")


if __name__ == "__main__":
    main()
