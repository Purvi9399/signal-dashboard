#!/usr/bin/env python3
"""
Signal Collect — local hooks, Claude Code (collector 3b of 6)

Claude Code runs this script at defined moments. It receives a JSON payload
on stdin, records observable rows, and prints a decision on stdout.

Unlike Cursor, every Claude Code payload carries transcript_path, a pointer
to the full session file on disk. That file contains token usage per message,
so this collector reads it and recovers tokens and cost without needing
OpenTelemetry at all.

Install:
    python3 signal_claude_hooks.py --install            # this project
    python3 signal_claude_hooks.py --install --global   # all projects

Inspect what Claude Code really sent:
    python3 signal_claude_hooks.py --report
    python3 signal_claude_hooks.py --check-text

Env:
    SIGNAL_SESSION_DIR   where rows go        (default ~/.signal/sessions)
    SIGNAL_DENY          refusal patterns     (default none, observe only)
    SIGNAL_OPERATOR      who is running it    (default $USER)
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

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key")
SECRET_SHAPES = ("sk-", "ghp_", "aws_secret", "-----begin", "api_key=",
                 "password=", "authorization: bearer", "xoxb-")
DANGEROUS = ("rm -rf", "curl ", "wget ", "chmod 777", "sudo ", "git push --force",
             "drop table", "truncate", "> /dev/", "mkfs", "dd if=")

EVENT_MAP = {
    "SessionStart":        "session_start",
    "SessionEnd":          "session_end",
    "Setup":               "setup",
    "UserPromptSubmit":    "user_prompt",
    "UserPromptExpansion": "prompt_expanded",
    "PreToolUse":          "tool_request",
    "PostToolUse":         "tool_result",
    "PostToolUseFailure":  "tool_failure",
    "PostToolBatch":       "tool_batch_end",
    "PermissionRequest":   "permission_request",
    "PermissionDenied":    "permission_denied",
    "Stop":                "turn_end",
    "StopFailure":         "turn_failed",
    "SubagentStart":       "subagent_start",
    "SubagentStop":        "subagent_stop",
    "TaskCreated":         "plan_item_created",
    "TaskCompleted":       "plan_item_completed",
    "FileChanged":         "file_changed",
    "CwdChanged":          "cwd_changed",
    "ConfigChange":        "config_changed",
    "InstructionsLoaded":  "instructions_loaded",
    "WorktreeCreate":      "worktree_created",
    "WorktreeRemove":      "worktree_removed",
    "PreCompact":          "compaction_start",
    "PostCompact":         "compaction_end",
    "Notification":        "notification",
    "MessageDisplay":      "message_displayed",
    "Elicitation":         "elicitation",
    "ElicitationResult":   "elicitation_result",
    "TeammateIdle":        "teammate_idle",
}

BLOCKABLE = {"PreToolUse", "PermissionRequest", "UserPromptSubmit",
             "UserPromptExpansion", "Stop", "SubagentStop", "TaskCreated",
             "TaskCompleted", "ConfigChange", "PreCompact", "PostToolBatch",
             "Elicitation"}

TOOL_CONNECTOR = {
    "Bash": "shell", "Read": "filesystem", "Write": "filesystem",
    "Edit": "filesystem", "Glob": "filesystem", "Grep": "filesystem",
    "WebFetch": "web", "WebSearch": "web", "Task": "subagent",
    "TodoWrite": "planning", "NotebookEdit": "filesystem",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sensitivity(text):
    low = (text or "").lower()
    if any(m in low for m in SENSITIVE):
        return 3
    if any(m in low for m in ("/etc/", "config", "prod", "billing", ".git/")):
        return 2
    return 1


def secret_in_text(text):
    low = (text or "").lower()
    return 3 if any(m in low for m in SECRET_SHAPES) else 1


def risk_markers(text):
    low = (text or "").lower()
    return [m.strip() for m in DANGEROUS if m in low]


def deny_match(text):
    low = (text or "").lower()
    for p in DENY:
        if p in low:
            return p
    return None


def read_transcript_usage(path):
    """Claude Code's transcript carries token usage per message.

    This is the reason hooks alone can give us tokens and cost on Claude Code,
    with no OpenTelemetry involved. Cursor has no equivalent.
    """
    if not path or not os.path.exists(path):
        return None
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
              "cache_creation_tokens": 0, "messages": 0, "models": set(),
              "thinking_blocks": 0}
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                if usage:
                    totals["messages"] += 1
                    totals["input_tokens"] += usage.get("input_tokens", 0) or 0
                    totals["output_tokens"] += usage.get("output_tokens", 0) or 0
                    totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
                    totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
                if msg.get("model"):
                    totals["models"].add(msg["model"])
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "thinking":
                            totals["thinking_blocks"] += 1
    except Exception:
        return None
    totals["models"] = sorted(totals["models"])
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def write_raw(payload, signal_decision=None):
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = os.path.join(SESSION_DIR, f"{day}-claude-RAW.jsonl")
    rec = {"received_at": now_iso(), "payload": payload}
    # Namespaced separately from payload: this is Signal's own judgment
    # (e.g. a SIGNAL_DENY match), never something Claude Code itself sent.
    # Curation reads this from the RAW file it already consumes, so the
    # decision survives a reload instead of only existing in the
    # per-session row file that load_all() does not parse.
    if signal_decision:
        rec["signal_decision"] = signal_decision
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def write_row(row, session_id):
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = os.path.join(SESSION_DIR, f"{day}-claude-{(session_id or 'nosession')[:12]}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def build_row(payload, interaction_type, **extra):
    event = payload.get("hook_event_name", "")
    row = {
        "observable_id": uuid.uuid4().hex,
        "timestamp": now_iso(),
        "agent_id": payload.get("session_id", ""),
        "prompt_id": payload.get("prompt_id", ""),
        "turn_id": payload.get("turn_id", ""),
        "agent_type": "coding_agent",
        "agent_tool": "claude-code",
        "operator": OPERATOR,
        "company": "real_fleet",
        "surface": "cli",
        "collector": "local_hooks",
        "interaction_type": interaction_type,
        "protocol": "hook/stdio",
        "connector": "",
        "permission_status": payload.get("permission_mode", "allowed"),
        "latency_ms": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
        "model": "",
        "sensitivity_tier": 1,
        "cascade_depth": 1 if payload.get("agent_id") else 0,
        "parent_agent_id": payload.get("agent_id", ""),
        "violation_count": 0,
        "escalation_flag": False,
        "success": True,
        "error_type": "",
        "cwd": payload.get("cwd", ""),
        "effort": (payload.get("effort") or {}).get("level", "")
                  if isinstance(payload.get("effort"), dict)
                  else payload.get("effort", ""),
        "hook_event": event,
        "blockable": event in BLOCKABLE,
        "transcript_path": payload.get("transcript_path", ""),
        "detail": {},
    }
    row.update(extra)
    return row


def handle(payload):
    event = payload.get("hook_event_name", "")
    kind = EVENT_MAP.get(event, "hook_" + (event or "unknown"))
    rows = []
    decision = {}

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    args_text = json.dumps(tool_input) if tool_input else ""

    # tool events, where most of the substance lives
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        connector = TOOL_CONNECTOR.get(tool, "unknown")
        command = tool_input.get("command", "")
        path = tool_input.get("file_path", tool_input.get("path", ""))
        markers = risk_markers(command)

        # PostToolUse carries a structured tool_response, not a string
        resp = payload.get("tool_response") or payload.get("tool_output") or {}
        if isinstance(resp, dict):
            stdout = resp.get("stdout", "") or ""
            stderr = resp.get("stderr", "") or ""
            interrupted = bool(resp.get("interrupted"))
            resp_text = (stdout + stderr) or json.dumps(resp)[:2000]
        else:
            stdout = stderr = ""
            interrupted = False
            resp_text = str(resp)

        row = build_row(payload, kind,
                        connector=connector,
                        latency_ms=payload.get("duration_ms", 0) or 0,
                        sensitivity_tier=max(sensitivity(args_text),
                                             2 if markers else 1),
                        detail={"tool": tool,
                                "tool_use_id": payload.get("tool_use_id", ""),
                                "command": command,
                                "description": tool_input.get("description", ""),
                                "file_path": path,
                                "risk_markers": markers,
                                "arguments": tool_input,
                                "stdout": stdout[:2000],
                                "stderr": stderr[:2000],
                                "output_size": len(stdout) + len(stderr),
                                "output_preview": resp_text[:1500],
                                "interrupted": interrupted})
        if event == "PostToolUseFailure" or stderr or interrupted:
            row["success"] = False
            row["error_type"] = ("interrupted" if interrupted
                                 else (stderr[:200] or "tool_failure"))
        rows.append(row)

        hit = deny_match(f"{tool} {args_text}")
        if event == "PreToolUse" and hit:
            row["permission_status"] = "denied"
            row["violation_count"] = 1
            row["escalation_flag"] = True
            row["detail"]["matched_rule"] = hit
            decision = {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blocked by Signal policy: '{hit}'"}}

    # a parallel batch finished: how many tools ran together
    elif event == "PostToolBatch":
        calls = payload.get("tool_calls") or []
        rows.append(build_row(payload, kind,
                              connector="batch",
                              detail={"batch_size": len(calls),
                                      "tools": [c.get("tool_name") for c in calls],
                                      "tool_use_ids": [c.get("tool_use_id") for c in calls]}))

    # the assistant's reply, streamed out as deltas
    elif event == "MessageDisplay":
        text = payload.get("delta", "") or ""
        rows.append(build_row(payload, kind,
                              connector="chat",
                              sensitivity_tier=secret_in_text(text),
                              detail={"message_id": payload.get("message_id", ""),
                                      "index": payload.get("index", 0),
                                      "final": payload.get("final", False),
                                      "length": len(text),
                                      "text": text[:4000]}))

    # Claude Code telling the user something, including permission prompts
    elif event == "Notification":
        ntype = payload.get("notification_type", "")
        rows.append(build_row(payload, kind,
                              connector="ui",
                              escalation_flag=(ntype == "permission_prompt"),
                              detail={"notification_type": ntype,
                                      "message": payload.get("message", "")}))

    elif event == "SessionStart":
        rows.append(build_row(payload, kind,
                              model=payload.get("model", ""),
                              detail={"source": payload.get("source", ""),
                                      "model": payload.get("model", "")}))

    # prompts
    elif event in ("UserPromptSubmit", "UserPromptExpansion"):
        text = payload.get("prompt", "") or ""
        rows.append(build_row(payload, kind,
                              connector="chat",
                              sensitivity_tier=secret_in_text(text),
                              detail={"length": len(text),
                                      "word_count": len(text.split()),
                                      "mentions_file": "@" in text,
                                      "slash_command": text.strip().startswith("/"),
                                      "text": text[:4000]}))

    # permissions, which Claude Code exposes as first-class events
    elif event in ("PermissionRequest", "PermissionDenied"):
        rows.append(build_row(payload, kind,
                              connector=TOOL_CONNECTOR.get(tool, "unknown"),
                              permission_status="requested" if event == "PermissionRequest" else "denied",
                              violation_count=1 if event == "PermissionDenied" else 0,
                              sensitivity_tier=sensitivity(args_text),
                              detail={"tool": tool,
                                      "arguments": tool_input,
                                      "reason": payload.get("reason", ""),
                                      "mode": payload.get("permission_mode", ""),
                                      "suggestions": payload.get("permission_suggestions", []),
                                      "suggestion_types": [s.get("type") for s in
                                                           (payload.get("permission_suggestions") or [])]}))

    # plan state, which nothing else emits anywhere
    elif event in ("TaskCreated", "TaskCompleted"):
        rows.append(build_row(payload, kind,
                              connector="planning",
                              detail={"task": payload.get("task",
                                       payload.get("description", "")),
                                      "status": payload.get("status", "")}))

    # subagents
    elif event in ("SubagentStart", "SubagentStop"):
        rows.append(build_row(payload, kind,
                              connector="subagent",
                              cascade_depth=1,
                              detail={"agent_id": payload.get("agent_id", ""),
                                      "agent_type": payload.get("agent_type", "")}))

    # end of turn, where we read the transcript for tokens
    elif event in ("Stop", "SubagentStop", "SessionEnd"):
        usage = read_transcript_usage(
            payload.get("agent_transcript_path") or payload.get("transcript_path"))
        detail = {"reason": payload.get("reason", ""),
                  "last_assistant_message": (payload.get("last_assistant_message") or "")[:4000],
                  "background_tasks": len(payload.get("background_tasks") or []),
                  "session_crons": len(payload.get("session_crons") or []),
                  "stop_hook_active": payload.get("stop_hook_active", False),
                  "agent_transcript_path": payload.get("agent_transcript_path", "")}
        row = build_row(payload, kind, detail=detail)
        if usage:
            row["tokens_total"] = usage["total_tokens"]
            row["model"] = ", ".join(usage["models"])
            detail.update(usage)
            detail["source"] = "transcript"
        rows.append(row)

    elif event == "FileChanged":
        p = payload.get("file_path", "")
        rows.append(build_row(payload, kind,
                              connector="filesystem",
                              sensitivity_tier=sensitivity(p),
                              detail={"file_path": p,
                                      "change": payload.get("change_type", "")}))

    else:
        rows.append(build_row(payload, kind,
                              detail={k: v for k, v in payload.items()
                                      if k not in ("hook_event_name", "transcript_path")
                                      and not isinstance(v, (dict, list))}))

    return rows, decision


def install(global_scope=False):
    target = os.path.join(HOME, ".claude") if global_scope \
        else os.path.join(os.getcwd(), ".claude")
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "settings.json")
    me = os.path.abspath(__file__)

    settings = {}
    if os.path.exists(path):
        try:
            settings = json.load(open(path))
        except Exception:
            settings = {}
    hooks = settings.setdefault("hooks", {})
    entry = {"type": "command", "command": f"{sys.executable} {me}"}

    for event in EVENT_MAP:
        matchers = hooks.setdefault(event, [])
        block = None
        for m in matchers:
            if m.get("matcher") in ("*", None, ""):
                block = m
                break
        if block is None:
            block = {"matcher": "*", "hooks": []}
            matchers.append(block)
        inner = block.setdefault("hooks", [])
        if not any(h.get("command") == entry["command"] for h in inner):
            inner.append(dict(entry))

    with open(path, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"Registered {len(EVENT_MAP)} hook events in {path}")
    print(f"Rows will be written to {SESSION_DIR}")
    print("Restart Claude Code for it to pick this up.")


def report():
    import glob
    from collections import defaultdict
    files = sorted(glob.glob(os.path.join(SESSION_DIR, "*claude-RAW.jsonl")))
    if not files:
        print(f"No raw payloads yet in {SESSION_DIR}")
        print("Install the hooks, restart Claude Code, use it, then run this again.")
        return
    events, fields, samples, total = defaultdict(int), defaultdict(set), {}, 0
    for path in files:
        for line in open(path):
            try:
                p = json.loads(line)["payload"]
            except Exception:
                continue
            total += 1
            name = p.get("hook_event_name", "?")
            events[name] += 1
            fields[name].update(p.keys())
            samples.setdefault(name, p)

    print(f"\n{total} payloads across {len(events)} event types\n")
    print(f"{'event':<24} {'count':>6}   fields Claude Code actually sent")
    print("-" * 100)
    for name in sorted(events, key=lambda n: -events[n]):
        keys = sorted(fields[name] - {"hook_event_name"})
        print(f"{name:<24} {events[name]:>6}   {', '.join(keys) or '(none)'}")
    missing = [e for e in EVENT_MAP if e not in events]
    print("\nRegistered but never fired:")
    print("   " + (", ".join(missing) if missing else "none, all fired"))
    print("\nOne sample payload per event:")
    for name in sorted(samples):
        body = json.dumps(samples[name], indent=2)
        if len(body) > 700:
            body = body[:700] + "\n  ... truncated for display"
        print(f"\n--- {name} ---\n{body}")


def main():
    if "--report" in sys.argv:
        report(); return 0
    if "--install" in sys.argv:
        install(global_scope="--global" in sys.argv); return 0

    raw = sys.stdin.read().strip()
    if not raw:
        print("{}"); return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("{}"); return 0

    decision = {}
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
            write_row(row, payload.get("session_id", ""))
    except Exception as e:
        sys.stderr.write(f"signal-hooks: {e}\n")
        decision = {}

    print(json.dumps(decision) if decision else "{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
