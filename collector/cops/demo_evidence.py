"""
The same event under three evidence conditions.

An agent reaches an MCP server it has never used, delegates to two subagents,
consumes heavily, and one of them touches a file. Only the evidence changes.

    python3 demo_evidence.py            all three
    python3 demo_evidence.py partial    one
"""

import sys
from .evidence import EvidenceAvailabilityService
from .cop_metrics import ASKS, NAMES, ORDER

ALL = ("expected", "collector_alive", "emitted", "ingested",
       "normalised", "session_linked", "usable_by_metrics")
OK = dict.fromkeys(ALL, True)


def stages(**fail):
    """Everything works up to the named stage, which fails; the rest untried."""
    out, broken = {}, False
    for s in ALL:
        if broken:
            out[s] = None
        elif s in fail:
            out[s], broken = False, True
        else:
            out[s] = True
    return out


SCENARIOS = {
    "full": {
        "title": "Full evidence",
        "note": "everything linked; hooks and OTel both present",
        "events": {e: True for e in
                   ("file_access", "mcp_call", "delegation", "model_request", "tool_call")},
        "sources": {},                       # nothing fails
    },
    "partial": {
        "title": "Partial evidence",
        "note": "subagent lanes ran but their steps cannot be attributed to a lane",
        "events": {e: True for e in
                   ("file_access", "mcp_call", "delegation", "model_request", "tool_call")},
        "sources": {("local_hooks", "lane_id"): stages(session_linked=1),
                    ("otel", "lane_id"): stages(emitted=1),
                    ("fs_watcher", "lane_id"): stages(expected=1)},
    },
    "blocking": {
        "title": "Decision-blocking absence",
        "note": "a file was touched; which file is unrecoverable",
        "events": {e: True for e in
                   ("file_access", "mcp_call", "delegation", "model_request", "tool_call")},
        "sources": {("otel", "file_path"): stages(collector_alive=1),
                    ("fs_watcher", "file_path"): stages(session_linked=1),
                    ("local_hooks", "file_path"): stages(normalised=1),
                    ("otel", "sensitivity_tier"): stages(collector_alive=1),
                    ("fs_watcher", "sensitivity_tier"): stages(session_linked=1),
                    ("local_hooks", "sensitivity_tier"): stages(normalised=1)},
    },
}

# Which COP asks about which metrics. Mirrors the specs.


def run(key):
    s = SCENARIOS[key]
    probe = lambda src, fieldname: s["sources"].get((src, fieldname), dict(OK))
    occurred = lambda event: s["events"].get(event, False)
    svc = EvidenceAvailabilityService(probe, occurred)

    print(f"\n{'=' * 78}\n{s['title'].upper()}  —  {s['note']}\n{'=' * 78}")
    print(f"{'COP':<24}{'status':<20}{'conf':<7}missing")
    print("-" * 78)

    gaps = []
    for cop, metrics in ASKS.items():
        env = svc.compile(f"demo-{key}", cop, metrics)
        v = env.for_cop()
        miss = ", ".join(v["missing"]) or "—"
        if v["blocking"]:
            miss += f"   (blocking: {', '.join(v['blocking'])})"
        print(f"{cop:<24}{v['status']:<20}{v['confidence_factor']:<7}{miss}")
        gaps += list(env.gaps)

    if gaps:
        seen = {}
        for g in gaps:
            seen.setdefault(g["recurrence_key"], g)
        print(f"\nevidence gaps recorded ({len(gaps)} across the COPS, "
              f"{len(seen)} distinct)")
        for k, g in seen.items():
            print(f"  {g['observable']:<18}{g['gap_type']:<26}{k}")
            print(f"    {g['reason_required']}")
            print(f"    event known to have occurred: {g['event_known_to_have_occurred']}")


if __name__ == "__main__":
    which = sys.argv[1:] or list(SCENARIOS)
    for k in which:
        run(k)
