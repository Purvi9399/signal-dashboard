#!/usr/bin/env python3
"""
Signal Collect — local hooks, Cursor (collector 3 of 6)

Cursor runs this script at defined moments in the agent loop. It receives a
JSON payload on stdin, records it as Signal observable rows, and prints a
JSON decision on stdout.

One script handles every event. Cursor tells us which one via hook_event_name.

Install:
    python3 signal_cursor_hooks.py --install            # this project
    python3 signal_cursor_hooks.py --install --global   # all projects

Test without Cursor:
    echo '{"hook_event_name":"beforeShellExecution","command":"rm -rf /",
           "conversation_id":"c1","generation_id":"g1"}' \
      | python3 signal_cursor_hooks.py

Env:
    SIGNAL_SESSION_DIR   where rows are written   (default ~/.signal/sessions)
    SIGNAL_DENY          comma-separated refusal patterns, e.g. ".env,rm -rf"
    SIGNAL_OPERATOR      who is running it        (default $USER)
    SIGNAL_ENDPOINT      optional http endpoint to POST rows to
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR", os.path.join(HOME, ".signal", "sessions"))
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))
DENY = [s.strip().lower() for s in os.environ.get("SIGNAL_DENY", "").split(",") if s.strip()]
ENDPOINT = os.environ.get("SIGNAL_ENDPOINT", "")

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key")
DANGEROUS = ("rm -rf", "curl ", "wget ", "chmod 777", "sudo ", "git push --force",
             "drop table", "truncate", "> /dev/", "mkfs", "dd if=")

# Every hook event Cursor can fire, mapped to what we call it.
EVENT_MAP = {
    "sessionStart":         "session_start",
    "sessionEnd":           "session_end",
    "workspaceOpen":        "workspace_open",
    "beforeSubmitPrompt":   "user_prompt",
    "afterAgentResponse":   "agent_response",
    "afterAgentThought":    "agent_thought",
    "beforeShellExecution": "shell_request",
    "afterShellExecution":  "shell_result",
    "beforeMCPExecution":   "mcp_request",
    "afterMCPExecution":    "mcp_result",
    "beforeReadFile":       "file_read",
    "afterFileEdit":        "file_edit",
    "preToolUse":           "tool_request",
    "postToolUse":          "tool_result",
    "postToolUseFailure":   "tool_failure",
    "subagentStart":        "subagent_start",
    "subagentStop":         "subagent_stop",
    "preCompact":           "context_compaction",
    "stop":                 "turn_end",
    "beforeTabFileRead":    "tab_file_read",
    "afterTabFileEdit":     "tab_file_edit",
}

# Events where a "deny" decision is honoured by Cursor.
BLOCKABLE = {"beforeShellExecution", "beforeMCPExecution", "beforeReadFile",
             "beforeSubmitPrompt", "preToolUse"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sensitivity(text):
    low = (text or "").lower()
    if any(m in low for m in SENSITIVE):
        return 3
    if any(m in low for m in ("/etc/", "config", "prod", "billing", ".git/")):
        return 2
    return 1


SECRET_SHAPES = ("sk-", "ghp_", "aws_secret", "-----begin", "api_key=",
                 "password=", "authorization: bearer", "xoxb-")


def secret_in_text(text):
    """For free text, only flag things shaped like an actual secret value.

    The word "token" in a sentence is not a secret. A string starting sk- is.
    """
    low = (text or "").lower()
    return 3 if any(m in low for m in SECRET_SHAPES) else 1


def risk_markers(text):
    low = (text or "").lower()
    return [m.strip() for m in DANGEROUS if m in low]


def deny_match(text):
    low = (text or "").lower()
    for pattern in DENY:
        if pattern in low:
            return pattern
    return None


def read_transcript_usage(path):
    """Cursor writes an agent transcript too. Pull any usage we can find."""
    if not path or not os.path.exists(path):
        return None
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
              "messages": 0, "models": set(), "lines": 0}
    try:
        with open(path) as f:
            for line in f:
                totals["lines"] += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                def dig(o):
                    if isinstance(o, dict):
                        u = o.get("usage") or o.get("tokenUsage") or o.get("token_usage")
                        if isinstance(u, dict):
                            totals["messages"] += 1
                            for a, b in (("input_tokens", "inputTokens"),
                                         ("output_tokens", "outputTokens"),
                                         ("cache_read_tokens", "cacheReadTokens")):
                                totals[a] += u.get(a, u.get(b, 0)) or 0
                        for k in ("model", "modelName", "model_id"):
                            if isinstance(o.get(k), str):
                                totals["models"].add(o[k])
                        for v in o.values():
                            dig(v)
                    elif isinstance(o, list):
                        for v in o:
                            dig(v)
                dig(rec)
    except Exception:
        return None
    totals["models"] = sorted(totals["models"])
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def write_raw(payload, signal_decision=None):
    """Keep every payload exactly as Cursor sent it, for schema discovery."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = os.path.join(SESSION_DIR, f"{day}-cursor-RAW.jsonl")
    rec = {"received_at": now_iso(), "payload": payload}
    # Namespaced separately from payload: this is Signal's own judgment,
    # never something Cursor itself sent. Curation reads this from the RAW
    # file it already consumes, so the decision survives a reload instead
    # of only existing in the per-session row file that load_all() ignores.
    if signal_decision:
        rec["signal_decision"] = signal_decision
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def write_row(row, conversation_id):
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = os.path.join(SESSION_DIR, f"{day}-cursor-{conversation_id[:12] or 'nosession'}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
    if ENDPOINT:
        try:
            import urllib.request
            req = urllib.request.Request(
                ENDPOINT, data=json.dumps(row).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=1)
        except Exception:
            pass  # never let telemetry break the developer's session
    return path


def build_row(payload, interaction_type, **extra):
    row = {
        "observable_id": uuid.uuid4().hex,
        "timestamp": now_iso(),
        "agent_id": payload.get("conversation_id", ""),
        "generation_id": payload.get("generation_id", ""),
        "agent_type": "coding_agent",
        "agent_tool": "cursor",
        "tool_version": payload.get("cursor_version", ""),
        "operator_email": payload.get("user_email", ""),
        "session_id": payload.get("session_id", ""),
        "composer_mode": payload.get("composer_mode", ""),
        "model_id": payload.get("model_id", ""),
        "transcript_path": payload.get("transcript_path", ""),
        "operator": OPERATOR,
        "company": "real_fleet",
        "surface": "ide",
        "collector": "local_hooks",
        "interaction_type": interaction_type,
        "protocol": "hook/stdio",
        "connector": "",
        "permission_status": "allowed",
        "latency_ms": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
        "model": payload.get("model", ""),
        "sensitivity_tier": 1,
        "cascade_depth": 0,
        "violation_count": 0,
        "escalation_flag": False,
        "success": True,
        "error_type": "",
        "workspace": (payload.get("workspace_roots") or [""])[0],
        "cwd": payload.get("cwd", ""),
        "hook_event": payload.get("hook_event_name", ""),
        "blockable": payload.get("hook_event_name", "") in BLOCKABLE,
        "detail": {},
    }
    row.update(extra)
    return row


def handle(payload):
    """Turn one hook payload into observable rows and a decision."""
    event = payload.get("hook_event_name", "")
    kind = EVENT_MAP.get(event, "hook_" + (event or "unknown"))
    rows = []
    decision = {"continue": True, "permission": "allow"}

    # --- shell commands ---------------------------------------------------
    if event in ("beforeShellExecution", "afterShellExecution"):
        cmd = payload.get("command", "")
        markers = risk_markers(cmd)
        row = build_row(payload, kind,
                        connector="shell",
                        sensitivity_tier=max(sensitivity(cmd),
                                             2 if markers else 1),
                        latency_ms=int(payload.get("duration", 0) or 0),
                        detail={"command": cmd,
                                "risk_markers": markers,
                                "cwd": payload.get("cwd", ""),
                                "sandboxed": payload.get("sandbox"),
                                "exit_code": payload.get("exit_code"),
                                "output": (payload.get("output") or "")[:2000],
                                "output_size": len(payload.get("output", "") or "")})
        if event == "afterShellExecution":
            code = payload.get("exit_code")
            row["success"] = (code in (0, None))
            row["error_type"] = "" if row["success"] else f"exit_{code}"
        rows.append(row)

        hit = deny_match(cmd)
        if event == "beforeShellExecution" and hit:
            row["permission_status"] = "denied"
            row["violation_count"] = 1
            row["escalation_flag"] = True
            row["success"] = False
            row["error_type"] = "blocked_by_policy"
            row["detail"]["matched_rule"] = hit
            decision = {"continue": False, "permission": "deny",
                        "userMessage": f"Blocked by Signal policy: '{hit}'",
                        "agentMessage": f"This command was blocked by policy (rule: {hit})."}

    # --- MCP tool calls ---------------------------------------------------
    elif event in ("beforeMCPExecution", "afterMCPExecution"):
        meta = payload.get("metadata", {}) or {}
        server = meta.get("server", payload.get("server", ""))
        tool = meta.get("tool_name", payload.get("tool_name", ""))
        args = payload.get("arguments", payload.get("params", {}))
        text = json.dumps(args) if args else payload.get("text", "")
        row = build_row(payload, kind,
                        connector=server,
                        sensitivity_tier=sensitivity(f"{tool} {text}"),
                        detail={"server": server, "tool": tool,
                                "arguments": args,
                                "result_size": len(payload.get("text", "") or "")})
        rows.append(row)

        hit = deny_match(f"{tool} {text}")
        if event == "beforeMCPExecution" and hit:
            row["permission_status"] = "denied"
            row["violation_count"] = 1
            row["escalation_flag"] = True
            row["detail"]["matched_rule"] = hit
            decision = {"continue": False, "permission": "deny",
                        "userMessage": f"Blocked by Signal policy: '{hit}'"}

    # --- file reads and edits --------------------------------------------
    elif event in ("beforeReadFile", "afterFileEdit", "beforeTabFileRead", "afterTabFileEdit"):
        path = payload.get("file_path", "")
        edits = payload.get("edits", []) or []
        added = sum(len(e.get("new_string", "")) for e in edits)
        removed = sum(len(e.get("old_string", "")) for e in edits)
        row = build_row(payload, kind,
                        connector="filesystem",
                        sensitivity_tier=sensitivity(path),
                        detail={"file_path": path,
                                "extension": os.path.splitext(path)[1],
                                "edit_count": len(edits),
                                "chars_added": added,
                                "chars_removed": removed,
                                "net_change": added - removed,
                                "content_size": len(payload.get("content", "") or ""),
                                "edits": [{"old_string": (e.get("old_string") or "")[:2000],
                                           "new_string": (e.get("new_string") or "")[:2000]}
                                          for e in edits[:20]],
                                "by_tab_completion": event.startswith("beforeTab") or event.startswith("afterTab")})
        rows.append(row)

        hit = deny_match(path)
        if event == "beforeReadFile" and hit:
            row["permission_status"] = "denied"
            row["violation_count"] = 1
            row["escalation_flag"] = True
            row["detail"]["matched_rule"] = hit
            decision = {"continue": False, "permission": "deny",
                        "userMessage": f"Blocked by Signal policy: '{hit}'"}

    # --- prompts and responses -------------------------------------------
    elif event in ("beforeSubmitPrompt", "afterAgentResponse", "afterAgentThought"):
        text = payload.get("text", payload.get("prompt", "")) or ""
        tin = payload.get("input_tokens", 0) or 0
        tout = payload.get("output_tokens", 0) or 0
        row = build_row(payload, kind,
                        connector="chat",
                        latency_ms=payload.get("duration_ms", 0) or 0,
                        tokens_total=tin + tout,
                        sensitivity_tier=secret_in_text(text),
                        detail={"length": len(text),
                                "word_count": len(text.split()),
                                "attachments": len(payload.get("attachments", []) or []),
                                "mentions_file": "@" in text,
                                "input_tokens": tin,
                                "output_tokens": tout,
                                "cache_read_tokens": payload.get("cache_read_tokens", 0) or 0,
                                "cache_write_tokens": payload.get("cache_write_tokens", 0) or 0,
                                "model_params": payload.get("model_params", {}),
                                "text": text[:4000]})
        rows.append(row)

    # --- sub-agents -------------------------------------------------------
    elif event in ("subagentStart", "subagentStop"):
        row = build_row(payload, kind,
                        connector="subagent",
                        cascade_depth=1,
                        detail={"subagent_type": payload.get("subagent_type",
                                                             payload.get("type", "")),
                                "parent_generation": payload.get("generation_id", "")})
        rows.append(row)

    # --- generic tool use, where Cursor routes file work through Shell/Read --
    elif event in ("preToolUse", "postToolUse", "postToolUseFailure"):
        tool = payload.get("tool_name", "")
        tin_args = payload.get("tool_input", {}) or {}
        out_raw = payload.get("tool_output", "") or ""
        exit_code, out_text = None, out_raw
        if isinstance(out_raw, str) and out_raw.strip().startswith("{"):
            try:
                parsed = json.loads(out_raw)
                exit_code = parsed.get("exitCode")
                out_text = parsed.get("output", out_raw)
            except json.JSONDecodeError:
                pass
        cmd = tin_args.get("command", "")
        path = tin_args.get("path", tin_args.get("file_path", ""))
        markers = risk_markers(cmd)
        row = build_row(payload, kind,
                        connector={"Shell": "shell", "Read": "filesystem",
                                   "Write": "filesystem", "Edit": "filesystem"}.get(tool, "tool"),
                        latency_ms=int(payload.get("duration", 0) or 0),
                        sensitivity_tier=max(sensitivity(json.dumps(tin_args)),
                                             2 if markers else 1),
                        detail={"tool": tool,
                                "tool_use_id": payload.get("tool_use_id", ""),
                                "arguments": tin_args,
                                "command": cmd,
                                "file_path": path,
                                "risk_markers": markers,
                                "exit_code": exit_code,
                                "output": str(out_text)[:2000],
                                "timeout": tin_args.get("timeout")})
        if exit_code not in (0, None) or event == "postToolUseFailure":
            row["success"] = False
            row["error_type"] = f"exit_{exit_code}" if exit_code is not None else "tool_failure"
        rows.append(row)

    # --- end of a turn: tokens are in the payload itself --------------------
    elif event in ("stop", "sessionEnd"):
        tin = payload.get("input_tokens", 0) or 0
        tout = payload.get("output_tokens", 0) or 0
        detail = {"loop_count": payload.get("loop_count", ""),
                  "status": payload.get("status", ""),
                  "input_tokens": tin,
                  "output_tokens": tout,
                  "cache_read_tokens": payload.get("cache_read_tokens", 0) or 0,
                  "cache_write_tokens": payload.get("cache_write_tokens", 0) or 0}
        row = build_row(payload, kind, tokens_total=tin + tout,
                        success=payload.get("status", "completed") == "completed",
                        detail=detail)
        usage = None if tin else read_transcript_usage(payload.get("transcript_path"))
        if usage:
            row["tokens_total"] = usage["total_tokens"]
            row["model"] = ", ".join(usage["models"]) or payload.get("model", "")
            detail.update(usage)
            detail["source"] = "transcript"
        rows.append(row)

    # --- generic tool events and everything else --------------------------
    else:
        row = build_row(payload, kind,
                        detail={k: v for k, v in payload.items()
                                if k not in ("hook_event_name",) and
                                not isinstance(v, (dict, list)) or k in ("edits", "metadata")})
        if event == "postToolUseFailure":
            row["success"] = False
            row["error_type"] = str(payload.get("error", "tool_failure"))[:200]
        if event == "preCompact":
            row["detail"]["reason"] = payload.get("reason", "")
        rows.append(row)

    return rows, decision


HOOK_EVENTS_TO_REGISTER = list(EVENT_MAP.keys())


def install(global_scope=False):
    """Write .cursor/hooks.json so Cursor calls this script for every event."""
    target_dir = os.path.join(HOME, ".cursor") if global_scope \
        else os.path.join(os.getcwd(), ".cursor")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "hooks.json")
    me = os.path.abspath(__file__)

    config = {"version": 1, "hooks": {}}
    if os.path.exists(path):
        try:
            config = json.load(open(path))
            config.setdefault("version", 1)
            config.setdefault("hooks", {})
        except Exception:
            pass

    entry = {"command": f"{sys.executable} {me}"}
    for event in HOOK_EVENTS_TO_REGISTER:
        existing = config["hooks"].setdefault(event, [])
        if not any(e.get("command") == entry["command"] for e in existing):
            existing.append(dict(entry))

    with open(path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Registered {len(HOOK_EVENTS_TO_REGISTER)} hook events in {path}")
    print(f"Rows will be written to {SESSION_DIR}")
    print("Restart Cursor for it to pick this up.")


def report():
    """Show what Cursor actually sent: which events fired and which fields."""
    import glob
    from collections import defaultdict
    files = sorted(glob.glob(os.path.join(SESSION_DIR, "*cursor-RAW.jsonl")))
    if not files:
        print(f"No raw payloads yet in {SESSION_DIR}")
        print("Install the hooks, restart Cursor, use it, then run this again.")
        return

    events = defaultdict(int)
    fields = defaultdict(set)
    samples = {}
    total = 0
    for path in files:
        for line in open(path):
            try:
                p = json.loads(line)["payload"]
            except Exception:
                continue
            total += 1
            name = p.get("hook_event_name", "?")
            events[name] += 1
            for k, v in p.items():
                fields[name].add(k)
            samples.setdefault(name, p)

    print(f"\n{total} payloads across {len(events)} event types\n")
    print(f"{'event':<24} {'count':>6}   fields Cursor actually sent")
    print("-" * 100)
    for name in sorted(events, key=lambda n: -events[n]):
        keys = sorted(fields[name] - {"hook_event_name"})
        print(f"{name:<24} {events[name]:>6}   {', '.join(keys) or '(none)'}")

    print("\nNot seen yet (registered but never fired):")
    missing = [e for e in EVENT_MAP if e not in events]
    print("   " + (", ".join(missing) if missing else "none, all 21 fired"))

    print("\nOne sample payload per event:")
    for name in sorted(samples):
        body = json.dumps(samples[name], indent=2)
        if len(body) > 700:
            body = body[:700] + "\n  ... truncated"
        print(f"\n--- {name} ---\n{body}")


def check_text():
    """Is the captured text complete? Measures what actually arrived.

    Truncation, chunking and missing content all show up here.
    """
    import glob
    from collections import defaultdict
    files = sorted(glob.glob(os.path.join(SESSION_DIR, "*cursor-RAW.jsonl")))
    if not files:
        print(f"No raw payloads yet in {SESSION_DIR}")
        return

    text_events = ("afterAgentResponse", "afterAgentThought", "beforeSubmitPrompt",
                   "afterMCPExecution", "afterShellExecution")
    seen = defaultdict(list)
    for path in files:
        for line in open(path):
            try:
                p = json.loads(line)["payload"]
            except Exception:
                continue
            name = p.get("hook_event_name", "")
            if name not in text_events:
                continue
            body = p.get("text") or p.get("output") or p.get("content") or ""
            seen[name].append({
                "gen": p.get("generation_id", ""),
                "conv": p.get("conversation_id", ""),
                "len": len(body),
                "head": body[:70].replace("\n", " "),
                "tail": body[-70:].replace("\n", " "),
                "fields": sorted(p.keys()),
            })

    if not seen:
        print("No text-bearing events captured yet.")
        print("Use Cursor so the agent replies, then run this again.")
        return

    for name, items in seen.items():
        print(f"\n=== {name} ===  fired {len(items)} times")
        print(f"    fields present: {', '.join(items[0]['fields'])}")
        by_gen = defaultdict(int)
        for it in items:
            by_gen[it["gen"]] += 1
        multi = {g: c for g, c in by_gen.items() if c > 1}
        if multi:
            print(f"    FIRES MORE THAN ONCE PER TURN: {multi}")
            print("    -> the response arrives in pieces, they must be joined")
        else:
            print("    fires once per turn")
        for it in items[-3:]:
            print(f"    len={it['len']:>7}  start: {it['head']!r}")
            print(f"    {'':>11}  end:   {it['tail']!r}")
            if it["len"] in (1000, 2000, 4000, 4096, 8192, 10000):
                print("    SUSPICIOUS: length is a round number, likely truncated")


def main():
    if "--check-text" in sys.argv:
        check_text()
        return 0
    if "--report" in sys.argv:
        report()
        return 0
    if "--install" in sys.argv:
        install(global_scope="--global" in sys.argv)
        return 0

    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"continue": True, "permission": "allow"}))
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"continue": True, "permission": "allow"}))
        return 0

    try:
        rows, decision = handle(payload)
        signal_decision = None
        for row in rows:
            if row.get("violation_count") or row.get("escalation_flag"):
                signal_decision = {
                    "matched_rule": (row.get("detail") or {}).get("matched_rule"),
                    "violation_count": row.get("violation_count"),
                    "escalation_flag": row.get("escalation_flag"),
                }
                break
        write_raw(payload, signal_decision)
        for row in rows:
            write_row(row, payload.get("conversation_id", ""))
    except Exception as e:
        # A collector must never break the developer's session.
        sys.stderr.write(f"signal-hooks: {e}\n")
        decision = {"continue": True, "permission": "allow"}

    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
