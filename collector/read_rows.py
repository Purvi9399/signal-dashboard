#!/usr/bin/env python3
"""Small helper: prints the interesting rows from the newest MCP proxy log."""
import glob, json, sys

mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
files = [p for p in glob.glob("signal_sessions/*mcp*.jsonl") if not p.endswith(".wire.jsonl")]
if not files:
    sys.exit(0)
rows = [json.loads(l) for l in open(sorted(files)[-1])]
first = lambda t: next((r for r in rows if r["interaction_type"] == t), None)

if mode == "watch":
    r = first("mcp_tools_discovered")
    if r:
        print("   tools on offer      " + ", ".join(r["detail"]["tools"]))
    r = first("mcp_call")
    if r:
        print("   agent asked for     %s(%s)" % (r["detail"]["tool"], json.dumps(r["detail"]["arguments"])))
        print("   sensitivity         tier %s of 3" % r["sensitivity_tier"])
    r = first("mcp_result")
    if r:
        text = r["detail"]["result"]["content"][0]["text"].strip()
        print("   server returned     " + text[:60])
        print("   round trip          %s ms" % r["latency_ms"])
else:
    r = first("mcp_call_blocked")
    if r:
        print("   we recorded         blocked by rule '%s', tier %s" % (r["detail"]["matched_rule"], r["sensitivity_tier"]))
        print("   escalation flag     %s" % r["escalation_flag"])
