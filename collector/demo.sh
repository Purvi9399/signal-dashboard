#!/bin/bash
# Signal Collect — CLI wrapper demo
# Shows: transparency, stall detection, exit codes, tool-agnostic operation.

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'

echo
echo "${BOLD}Signal Collect — CLI wrapper${RESET}"
echo "${DIM}One collector. No tool-specific code. Works on any CLI.${RESET}"
echo

echo "${BOLD}1. Transparency${RESET} — the developer sees exactly what they would have seen"
echo "${DIM}\$ python3 signal_wrap.py bash -c 'echo hello; ls'${RESET}"
python3 signal_wrap.py bash -c 'echo hello; ls' 2>/dev/null
echo

echo "${BOLD}2. A process that hangs${RESET} — telemetry reports nothing until a command finishes."
echo "${DIM}   The wrapper notices the silence while it is still happening.${RESET}"
echo "${DIM}\$ SIGNAL_STALL_SECONDS=2 python3 signal_wrap.py bash -c 'echo working...; sleep 5; exit 3'${RESET}"
SIGNAL_STALL_SECONDS=2 python3 signal_wrap.py bash -c 'echo working...; sleep 5; exit 3' 2>/dev/null
echo "   ${DIM}exit code captured: $?${RESET}"
echo

echo "${BOLD}3. What we recorded${RESET}"
LATEST=$(ls -t signal_sessions/*.jsonl | head -1)
python3 - "$LATEST" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    r = json.loads(line)
    d = r["detail"]
    note = d.get("command") or d.get("text") or d.get("note") or ""
    print(f"   {r['interaction_type']:<14} {r['latency_ms']:>6} ms   {str(note)[:64]}")
PY
echo
echo "${DIM}   Same row format the classifier and COPS already read.${RESET}"
echo "${DIM}   Full byte stream in .raw, readable transcript in .txt${RESET}"
echo
