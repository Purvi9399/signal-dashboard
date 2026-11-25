"""
Real, end-to-end Deployment-phase pipeline demo:

    DiscoveryEngine.scan()          -- what's actually installed/viable HERE
        -> PolicyEngine.evaluate()   -- what's actually approved to run, and why
        -> ActivationEngine.wire()   -- register + activate() the approved ones for real
        -> registry.merged_capture() -- real RawEvents flowing out the other end
        -> registry.deactivate_all()

This is the first script in this package that runs all four components
together against this real machine. Every earlier script exercised exactly
one stage (`discovery_engine.py`, `policy_engine.py`) or replayed data
directly into hand-picked collectors (`run_demo_full.py`) without going
through a real policy decision at all. This one does the real, complete
thing: whatever is approved after real discovery + real policy is what
actually gets registered and activated -- nothing here is scripted around
in advance.

For the Hooks techniques specifically, this reuses `run_demo_all_five.py`'s
own real-data loaders (unwrapping this project's real captured
`captured_events.jsonl` files) so the events flowing through the merged
capture stream are genuine, not synthesized. Every other approved
technique is wired with an explicit empty replay list (see
`activation_engine.py`'s module docstring for why that's the safe default,
not a limitation of this demo) -- it will correctly show 0 events, which
is honest: there is no live agent session running during this demo, and
Activation Engine's whole point is safe wiring, not data fabrication.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.discovery_engine import scan_this_machine
from policy.policy_engine import PolicyConfig, PolicyEngine
from activation.activation_engine import ActivationEngine, _print_wiring_result

import run_demo_all_five as _hooks_demo


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def main() -> None:
    banner("STAGE 1: DiscoveryEngine.scan() -- real scan of this machine")
    discovery_report = scan_this_machine()
    found = [a.agent for a in discovery_report.found_agents()]
    print(f"agents found: {found}")

    banner("STAGE 2: PolicyEngine.evaluate() -- real policy decision")
    policy_path = Path(__file__).resolve().parents[1] / "policy" / "sample_policy.json"
    policy = PolicyConfig.load(str(policy_path))
    decision_report = PolicyEngine(policy).evaluate(discovery_report)
    approved_pairs = decision_report.approved_pairs()
    print(f"policy_source: {decision_report.policy_source}")
    print(f"approved pairs ({len(approved_pairs)}):")
    for agent, technique in approved_pairs:
        print(f"  - {agent}.{technique}")

    banner("STAGE 3: ActivationEngine.wire() -- real register() + activate()")
    # Real replay data for the 5 Hooks collectors only (reusing
    # run_demo_all_five.py's own loaders against this project's real
    # captured_events.jsonl files) -- every other approved pair gets an
    # explicit empty replay list, per activation_engine.py's safety design.
    replay_data = {
        ("claude_code", "hooks"): _hooks_demo.load_claude_replay_events(),
        ("cursor", "hooks"): _hooks_demo.load_cursor_replay_events(),
        ("copilot", "hooks"): _hooks_demo.load_copilot_replay_events(),
        ("antigravity", "hooks"): _hooks_demo.load_antigravity_replay_events(),
        ("codex", "hooks"): _hooks_demo.load_codex_replay_events(),
    }

    engine = ActivationEngine(mode="replay", replay_data=replay_data)
    registry, wiring_result = engine.wire(decision_report)
    _print_wiring_result(wiring_result)

    banner("STAGE 4: registry.merged_capture() -- real RawEvents")
    events = list(registry.merged_capture())
    by_collector: dict[str, int] = {}
    for e in events:
        by_collector[e.collector_id] = by_collector.get(e.collector_id, 0) + 1
    for cid, count in sorted(by_collector.items()):
        print(f"  {cid:28s} -> {count} RawEvent(s)")
    print(f"\n  TOTAL: {len(events)} RawEvents across {len(wiring_result.activated)} activated collectors")

    if events:
        print("\n  --- first RawEvent overall ---")
        e = events[0]
        print(f"  collector_id : {e.collector_id}")
        print(f"  agent        : {e.agent}")
        print(f"  raw_payload  : {str(e.raw_payload)[:200]}...(truncated)")

    if registry.failures:
        print(f"\n  Collector failures (isolated, did not crash the run): {len(registry.failures)}")
        for f in registry.failures:
            print(f"    {f}")

    banner("STAGE 5: registry.deactivate_all()")
    registry.deactivate_all()
    print("All active collectors deactivated cleanly.")

    banner("DONE -- full pipeline ran against this real machine, start to finish")


if __name__ == "__main__":
    main()
