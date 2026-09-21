#!/bin/bash
# Signal Collect — Cursor local hooks demo
# Replays a realistic Cursor session through the hook collector.

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
export SIGNAL_SESSION_DIR=./signal_sessions/cursor
rm -rf "$SIGNAL_SESSION_DIR"

fire () { printf '%s' "$1" | python3 signal_cursor_hooks.py > /dev/null 2>&1; }

echo
echo "${BOLD}Signal Collect — local hooks (Cursor)${RESET}"
echo "${DIM}21 events registered. One script, no tool-specific code per event.${RESET}"
echo

echo "${BOLD}1. A session, replayed through the hooks${RESET}"
fire '{"hook_event_name":"sessionStart","conversation_id":"conv-7f3a","workspace_roots":["/Users/p/payments-api"]}'
fire '{"hook_event_name":"beforeSubmitPrompt","text":"the auth check is failing, fix it and run the tests","conversation_id":"conv-7f3a","generation_id":"gen-01","model":"claude-sonnet-4.6","workspace_roots":["/Users/p/payments-api"]}'
fire '{"hook_event_name":"beforeReadFile","file_path":"/Users/p/payments-api/src/auth.py","conversation_id":"conv-7f3a","generation_id":"gen-01"}'
fire '{"hook_event_name":"afterFileEdit","file_path":"/Users/p/payments-api/src/auth.py","conversation_id":"conv-7f3a","generation_id":"gen-01","edits":[{"old_string":"return True","new_string":"return verify_signature(token, PUBLIC_KEY)"}]}'
fire '{"hook_event_name":"beforeShellExecution","command":"pytest tests/ -v","conversation_id":"conv-7f3a","generation_id":"gen-01"}'
fire '{"hook_event_name":"afterShellExecution","command":"pytest tests/ -v","exit_code":1,"output":"2 failed, 8 passed","conversation_id":"conv-7f3a","generation_id":"gen-01"}'
fire '{"hook_event_name":"beforeSubmitPrompt","text":"no that is wrong, revert it","conversation_id":"conv-7f3a","generation_id":"gen-02","model":"claude-sonnet-4.6"}'
fire '{"hook_event_name":"beforeMCPExecution","metadata":{"server":"github","tool_name":"create_pull_request"},"arguments":{"title":"fix auth","branch":"main"},"conversation_id":"conv-7f3a","generation_id":"gen-02"}'
fire '{"hook_event_name":"stop","conversation_id":"conv-7f3a","generation_id":"gen-02","loop_count":6}'

python3 - <<'PY'
import glob, json
f = sorted(glob.glob("signal_sessions/cursor/*.jsonl"))[-1]
for line in open(f):
    r = json.loads(line); d = r["detail"]
    note = (d.get("text") or d.get("file_path") or d.get("command")
            or d.get("tool") or "")
    if r["interaction_type"] == "file_edit":
        note = "%s  (+%d -%d chars)" % (d["file_path"], d["chars_added"], d["chars_removed"])
    if r["interaction_type"] == "shell_result":
        note = "%s  exit=%s" % (d["command"], d["exit_code"])
    flags = []
    if r["blockable"]: flags.append("blockable")
    if r["sensitivity_tier"] > 1: flags.append("tier%d" % r["sensitivity_tier"])
    if d.get("risk_markers"): flags.append("risk:" + ",".join(d["risk_markers"]))
    if not r["success"]: flags.append("failed")
    print("   %-16s %-52s %s" % (r["interaction_type"], str(note)[:52],
                                 " ".join(flags)))
PY

echo
echo "${BOLD}2. The same session with a policy in place${RESET}"
echo "${DIM}   SIGNAL_DENY=\".env,rm -rf,--force\"${RESET}"
export SIGNAL_DENY=".env,rm -rf,--force"
echo -n "   agent tried to read .env      -> "
printf '%s' '{"hook_event_name":"beforeReadFile","file_path":"/Users/p/payments-api/.env","conversation_id":"conv-7f3a","generation_id":"gen-03"}' | python3 signal_cursor_hooks.py
echo -n "   agent tried git push --force  -> "
printf '%s' '{"hook_event_name":"beforeShellExecution","command":"git push --force origin main","conversation_id":"conv-7f3a","generation_id":"gen-03"}' | python3 signal_cursor_hooks.py

echo
echo "${DIM}   Cursor honours the deny. The action never happens.${RESET}"
echo "${DIM}   Same row format the classifier and COPS already read.${RESET}"
echo
