#!/usr/bin/env python3
"""
Signal Collect — curation layer

Turns collector output into the curated observables table.

This is not a merge. Source rows stay as they are. This reads them, groups
the ones describing the same occurrence, decides which collector wins per
field using precedence_map.py, and writes one observable record with
provenance attached.

    python3 signal_curate.py --dry-run        # show what would be written
    python3 signal_curate.py --out rows.jsonl # write to a file
    python3 signal_curate.py --supabase       # write to Supabase
    python3 signal_curate.py --stats          # coverage per field

Env for --supabase:
    SUPABASE_URL, SUPABASE_KEY   (service role key)
    SUPABASE_TABLE               default: observables
    SUPABASE_SOURCE_TABLE        default: source_events
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import uuid
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [
    os.path.join(HOME, ".signal", "sessions"),
    os.path.join(HOME, "agent-marketplace", "signal-coding-agent-collector", "data"),
    "signal_sessions", "signal_sessions/cursor", "data", ".",
] + [os.path.join(HOME, d, "signal_sessions") for d in (
    "bench-claude", "bench-cursor", "bench-codex", "bench-copilot",
    "bench-antigravity", "sec-test", "idp-test", "project-signal-dashboard")]

# how close in time two rows must be to describe the same occurrence
MERGE_WINDOW_S = 3.0

H, O, W, P, F, K, D = ("local_hooks", "otel", "cli_wrapper",
                       "mcp_proxy", "fs_watcher", "webhook", "derived")

CONFIDENCE = {H: "reported", O: "reported", P: "reported",
              W: "observed", F: "parsed", K: "reported", D: "inferred"}


def load_precedence():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "precedence_map.py"), "precedence_map.py"):
        if os.path.exists(path):
            import importlib.util
            spec = importlib.util.spec_from_file_location("pm", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m.PRECEDENCE
    sys.exit("precedence_map.py not found, it must sit beside this script")


PRECEDENCE = load_precedence()


def ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def iso(dt):
    return dt.isoformat() if dt else None


# ===========================================================================
# extractors: one collector payload -> a flat dict of observable fields
# ===========================================================================

CC_TYPE = {
    "SessionStart": "session_start", "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt", "MessageDisplay": "agent_response",
    "PreToolUse": "tool_request", "PostToolUse": "tool_result",
    "PostToolUseFailure": "tool_failure", "PostToolBatch": "tool_batch",
    "PermissionRequest": "permission_request", "PermissionDenied": "permission_denied",
    "Notification": "notification", "Stop": "agent_response",
    "SubagentStart": "subagent_start", "SubagentStop": "subagent_stop",
    "TaskCreated": "plan_created", "TaskCompleted": "plan_completed",
    "PreCompact": "compaction", "FileChanged": "file_modify",
}
CUR_TYPE = {
    "sessionStart": "session_start", "sessionEnd": "session_end",
    "beforeSubmitPrompt": "user_prompt", "afterAgentResponse": "agent_response",
    "afterAgentThought": "agent_reasoning", "preToolUse": "tool_request",
    "postToolUse": "tool_result", "postToolUseFailure": "tool_failure",
    "beforeShellExecution": "shell_request", "afterShellExecution": "shell_result",
    "beforeMCPExecution": "mcp_request", "afterMCPExecution": "mcp_result",
    "beforeReadFile": "file_read", "afterFileEdit": "file_modify",
    "subagentStart": "subagent_start", "subagentStop": "subagent_stop",
    "preCompact": "compaction", "stop": "agent_response",
    "workspaceOpen": "session_start",
}
TOOL_CATEGORY = {"Bash": "shell", "Shell": "shell", "Read": "filesystem",
                 "Write": "filesystem", "Edit": "filesystem", "Glob": "filesystem",
                 "Grep": "filesystem", "WebFetch": "web", "WebSearch": "web",
                 "Task": "subagent", "Agent": "subagent", "TodoWrite": "planning"}
BUILTIN = set(TOOL_CATEGORY)

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key")
SECRET_SHAPES = ("sk-", "ghp_", "aws_secret", "-----begin", "api_key=",
                 "password=", "xoxb-")
DANGEROUS = ("rm -rf", "curl ", "wget ", "chmod 777", "sudo ",
             "git push --force", "drop table", "mkfs", "dd if=")


def tier(text):
    low = (text or "").lower()
    if any(m in low for m in SENSITIVE):
        return 3
    if any(m in low for m in ("/etc/", "config", "prod", "billing")):
        return 2
    return 1


def markers(text):
    low = (text or "").lower()
    return [m.strip() for m in DANGEROUS if m in low]


def has_secret(text):
    return any(m in (text or "").lower() for m in SECRET_SHAPES)


def extract_claude_hook(p, when, decision=None):
    ev = p.get("hook_event_name", "")
    tin = p.get("tool_input") or {}
    # tool_input is an object on most tools and a bare string on some.
    # Assuming the object shape crashes the whole load rather than skipping
    # one row.
    if not isinstance(tin, dict):
        tin = {"raw": tin} if tin else {}
    resp = p.get("tool_response") or p.get("tool_output") or {}
    # tool_response is not one shape. Some tools return an object, some a list
    # of content blocks, some a bare string. Assuming a dict silently loses
    # stderr on the other two.
    stdout = stderr = ""
    if isinstance(resp, dict):
        stdout = resp.get("stdout", "") or ""
        stderr = resp.get("stderr", "") or ""
    elif isinstance(resp, list):
        for b in resp:
            if isinstance(b, dict):
                stdout += b.get("stdout") or ""
                stderr += b.get("stderr") or ""
                txt = b.get("text") or b.get("content") or ""
                if isinstance(txt, str) and not stdout and not stderr:
                    stdout += txt
            elif isinstance(b, str):
                stdout += b
    elif isinstance(resp, str):
        low = resp.lower()
        if "traceback" in low or "error:" in low or "assertionerror" in low:
            stderr = resp
        else:
            stdout = resp
    prompt = p.get("prompt", "") or ""
    cmd = tin.get("command", "")
    path = tin.get("file_path") or tin.get("path") or ""
    eff = p.get("effort")
    f = {
        "tool": "claude-code", "occurred_at": when,
        "observable_type": CC_TYPE.get(ev, "other"),
        "session_id": p.get("session_id"), "conversation_id": p.get("session_id"),
        "turn_id": p.get("prompt_id"), "message_id": p.get("message_id"),
        "tool_use_id": p.get("tool_use_id"),
        "agent_id": p.get("agent_id") or None, "agent_type": p.get("agent_type") or None,
        "cwd": p.get("cwd"), "workspace": p.get("cwd"),
        "permission_mode": p.get("permission_mode"),
        "effort_level": (eff or {}).get("level") if isinstance(eff, dict) else eff,
        "raw_ref": p.get("transcript_path"),
        "model": p.get("model"),
        "prompt_text": prompt or None,
        "prompt_length": len(prompt) or None,
        "prompt_word_count": len(prompt.split()) or None,
        "slash_command": prompt.strip().split()[0] if prompt.strip().startswith("/") else None,
        "mentions_files": ("@" in prompt) or None,
        "response_text": p.get("delta") or p.get("last_assistant_message") or None,
        "response_index": p.get("index"),
        "response_is_final": p.get("final"),
        "tool_name": p.get("tool_name") or None,
        "tool_arguments": tin or None,
        "tool_result": resp or None,
        "tool_duration_ms": p.get("duration_ms"),
        "exit_code": (resp.get("exitCode", resp.get("exit_code"))
                      if isinstance(resp, dict) else None),
        "tool_interrupted": resp.get("interrupted") if isinstance(resp, dict) else None,
        "command": cmd or None,
        "stdout": stdout or None, "stderr": stderr or None,
        "file_path": path or None,
        "file_extension": os.path.splitext(path)[1] if path else None,
        "permission_options": p.get("permission_suggestions") or None,
        "background_tasks": len(p.get("background_tasks") or []) or None,
        "scheduled_jobs": len(p.get("session_crons") or []) or None,
        "batch_size": len(p.get("tool_calls") or []) or None,
        "compaction_reason": (p.get("reason") or p.get("trigger")
                              if ev in ("PreCompact", "PostCompact") else None),
        "plan_item": (p.get("task_subject") or p.get("task_description")
                      if ev.startswith("Task") else None),
        "plan_status": (("created" if ev == "TaskCreated" else "completed")
                        if ev.startswith("Task") else None),
        "error_type": p.get("reason") if ev == "SessionEnd" else None,
    }
    if f["tool_name"]:
        f["tool_category"] = TOOL_CATEGORY.get(f["tool_name"], "other")
        f["tool_is_builtin"] = f["tool_name"] in BUILTIN
    if cmd:
        f["risk_markers"] = markers(cmd) or None
    f["sensitivity_tier"] = max(tier(json.dumps(tin)), tier(path))
    if prompt and has_secret(prompt):
        f["secret_detected"] = True
        f["sensitivity_tier"] = 3
    if stderr or (isinstance(resp, dict) and resp.get("interrupted")):
        f["success"] = False
        f["error_type"] = (stderr or "interrupted")[:200]
    elif ev == "PostToolUse":
        f["success"] = True
    if decision:
        f["policy_rule_matched"] = decision.get("matched_rule") or None
        f["violation_count"] = decision.get("violation_count") or None
        f["escalation_flag"] = decision.get("escalation_flag") or None
    return f


def extract_cursor_hook(p, when, decision=None):
    ev = p.get("hook_event_name", "")
    tin = p.get("tool_input") or {}
    out_raw = p.get("tool_output") or ""
    exit_code, out_text = None, out_raw
    if isinstance(out_raw, str) and out_raw.strip().startswith("{"):
        try:
            parsed = json.loads(out_raw)
            exit_code = parsed.get("exitCode")
            out_text = parsed.get("output", out_raw)
        except json.JSONDecodeError:
            pass
    prompt = p.get("prompt", "") or ""
    text = p.get("text", "") or ""
    cmd = p.get("command") or tin.get("command") or ""
    edits = p.get("edits") or []
    roots = p.get("workspace_roots") or []
    tin_tok = p.get("input_tokens") or 0
    tout_tok = p.get("output_tokens") or 0
    f = {
        "tool": "cursor", "occurred_at": when,
        "observable_type": CUR_TYPE.get(ev, "other"),
        "session_id": p.get("conversation_id") or p.get("session_id"),
        "conversation_id": p.get("conversation_id"),
        "turn_id": p.get("generation_id"),
        "tool_use_id": p.get("tool_use_id"),
        "tool_version": p.get("cursor_version"),
        "operator_email": p.get("user_email"),
        "workspace": roots[0] if roots else None,
        "cwd": p.get("cwd") or None,
        "raw_ref": p.get("transcript_path"),
        "model": p.get("model"), "model_id": p.get("model_id"),
        "composer_mode": p.get("composer_mode"),
        "sampling_params": p.get("model_params") or None,
        "prompt_text": prompt or None,
        "prompt_length": len(prompt) or None,
        "prompt_word_count": len(prompt.split()) or None,
        "slash_command": prompt.strip().split()[0] if prompt.strip().startswith("/") else None,
        "attachment_count": len(p.get("attachments") or []) or None,
        "response_text": text if ev == "afterAgentResponse" else None,
        "reasoning_text": text if ev == "afterAgentThought" else None,
        "input_tokens": tin_tok or None, "output_tokens": tout_tok or None,
        "cache_read_tokens": p.get("cache_read_tokens") or None,
        "cache_write_tokens": p.get("cache_write_tokens") or None,
        "total_tokens": (tin_tok + tout_tok) or None,
        "stop_reason": p.get("status"),
        "loop_count": p.get("loop_count"),
        "tool_name": p.get("tool_name") or None,
        "tool_arguments": tin or None,
        "tool_result": {"output": str(out_text)[:4000]} if out_text else None,
        "tool_duration_ms": int(p["duration"]) if p.get("duration") else None,
        "tool_timeout_ms": tin.get("timeout"),
        "command": cmd or None,
        "stdout": p.get("output") or None,
        "exit_code": exit_code,
        "sandboxed": p.get("sandbox"),
        "mcp_server": (p.get("metadata") or {}).get("server") or p.get("server"),
        "mcp_tool": (p.get("metadata") or {}).get("tool_name"),
        "file_path": p.get("file_path") or None,
        "file_extension": os.path.splitext(p.get("file_path", ""))[1] or None,
    }
    if edits:
        f["old_content"] = (edits[0].get("old_string") or "")[:8000] or None
        f["new_content"] = (edits[0].get("new_string") or "")[:8000] or None
        f["chars_added"] = sum(len(e.get("new_string", "")) for e in edits)
        f["chars_removed"] = sum(len(e.get("old_string", "")) for e in edits)
    if f["tool_name"]:
        f["tool_category"] = TOOL_CATEGORY.get(f["tool_name"], "other")
        f["tool_is_builtin"] = f["tool_name"] in BUILTIN
    if cmd:
        f["risk_markers"] = markers(cmd) or None
    f["sensitivity_tier"] = max(tier(json.dumps(tin)), tier(cmd),
                                tier(p.get("file_path", "")))
    if prompt and has_secret(prompt):
        f["secret_detected"] = True
        f["sensitivity_tier"] = 3
    if exit_code not in (0, None):
        f["success"] = False
        f["error_type"] = f"exit_{exit_code}"
    if decision:
        f["policy_rule_matched"] = decision.get("matched_rule") or None
        f["violation_count"] = decision.get("violation_count") or None
        f["escalation_flag"] = decision.get("escalation_flag") or None
    return f


def unwrap(v):
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue"):
        if k in v:
            return v[k]
    for k in ("intValue", "doubleValue"):
        if k in v:
            try:
                return int(v[k]) if k == "intValue" else float(v[k])
            except (TypeError, ValueError):
                return v[k]
    return None


OTEL_SERVICE_TO_TOOL = {
    "claude-code": "claude-code", "claude-code-desktop": "claude-code",
    "codex_tui": "codex", "codex_exec": "codex", "codex_cli_rs": "codex",
    "codex-cli": "codex", "gemini-cli": "antigravity",
}


def _num(v):
    """OTel attributes arrive as strings on some tools. Codex sends
    duration_ms as "779" rather than 779, which then reads as empty."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        f = float(str(v).strip())
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def extract_otel(res, attrs, when):
    name = attrs.get("event.name", "")
    short = name.split(".")[-1]
    otype = {"user_prompt": "user_prompt", "assistant_response": "agent_response",
             "api_request": "model_request", "tool_result": "tool_result",
             "tool_decision": "permission_request",
             "mcp_server_connection": "mcp_discovery",
             "permission_mode_changed": "permission_mode_change",
             # codex prefixes everything with codex.
             "tool_call": "tool_request", "tool_call_result": "tool_result",
             "conversation_starts": "session_start",
             "sse_event": "agent_response",
             "user_prompt_submitted": "user_prompt"}.get(short, "other")
    svc = (res.get("service.name") or "claude-code").lower()
    f = {
        "tool": OTEL_SERVICE_TO_TOOL.get(svc, "custom"),
        "occurred_at": when, "observable_type": otype,
        "session_id": (attrs.get("session.id") or attrs.get("conversation.id")
                       or attrs.get("conversation_id")),
        "turn_id": (attrs.get("prompt.id") or attrs.get("turn.id")
                    or attrs.get("turn_id")),
        "tool_use_id": attrs.get("tool_use_id"),
        "sequence_num": _num(attrs.get("event.sequence")),
        "operator_email": attrs.get("user.email"),
        "operator_id": attrs.get("user.account_id"),
        "org_id": attrs.get("organization.id"),
        "team_id": attrs.get("team.id"),
        "tool_version": res.get("service.version"),
        "surface": "cli" if attrs.get("terminal.type") else None,
        "model": attrs.get("model") or attrs.get("model.slug"),
        "input_tokens": _num(attrs.get("input_tokens") or attrs.get("tokens.input")),
        "output_tokens": _num(attrs.get("output_tokens") or attrs.get("tokens.output")),
        "cache_read_tokens": _num(attrs.get("cache_read_tokens") or attrs.get("tokens.cached")),
        "cache_write_tokens": _num(attrs.get("cache_creation_tokens")),
        "cost_usd": _num(attrs.get("cost_usd")),
        "request_latency_ms": _num(attrs.get("duration_ms")),
        "stop_reason": attrs.get("stop_reason") or attrs.get("finish_reason"),
        "prompt_length": _num(attrs.get("prompt_length")),
        "slash_command": attrs.get("command_name"),
        "tool_name": attrs.get("tool_name") or attrs.get("tool.name"),
        "tool_result_size": _num(attrs.get("tool_result_size_bytes")),
        "permission_decision": attrs.get("decision"),
        "permission_source": attrs.get("decision_source") or attrs.get("source"),
        "mcp_server": attrs.get("mcp_server_name") or attrs.get("mcp.server"),
        "status": ("ok" if attrs.get("success") in (True, "true")
                   else "error" if attrs.get("success") in (False, "false") else None),
        "success": (True if attrs.get("success") in (True, "true")
                    else False if attrs.get("success") in (False, "false") else None),
        "error_type": attrs.get("error") or attrs.get("error.message"),
        "tool_duration_ms": _num(attrs.get("duration_ms")) if short in
                            ("tool_result", "tool_call_result") else None,
        "exit_code": _num(attrs.get("exit_code") or attrs.get("http.response.status_code")
                          if short in ("tool_result", "tool_call_result") else None),
        "command": attrs.get("command") or attrs.get("endpoint"),
    }
    # OTel redacts prompt text; keep the length, not the placeholder
    pt = attrs.get("prompt")
    if pt and pt != "<REDACTED>":
        f["prompt_text"] = pt
    if attrs.get("input_tokens") or attrs.get("output_tokens"):
        f["total_tokens"] = (attrs.get("input_tokens") or 0) + (attrs.get("output_tokens") or 0)
    return f


def extract_row(r):
    """A row already in Signal shape: wrapper, proxy or watcher."""
    d = r.get("detail") or {}
    coll = r.get("collector", "")
    tool = (r.get("agent_tool") or "unknown").lower()
    # the wrapper labels a session by the binary it wrapped, so map the
    # binary name onto the tool. Anything unrecognised stays "custom" rather
    # than being silently discarded.
    if tool.startswith("claude"):
        tool = "claude-code"
    elif tool.startswith("cursor"):
        tool = "cursor"
    elif tool.startswith("codex"):
        tool = "codex"
    elif tool in ("gh", "copilot", "gh-copilot"):
        tool = "copilot"
    elif tool.startswith("antigravity") or tool == "agy":
        tool = "antigravity"
    f = {
        "tool": tool if tool in ("claude-code", "cursor", "codex", "copilot",
                                 "antigravity") else "custom",
        "occurred_at": ts(r.get("timestamp")),
        "session_id": r.get("agent_id") or None,
        "turn_id": r.get("prompt_id") or r.get("generation_id") or None,
        "operator_username": r.get("operator") or None,
        "operator_email": r.get("operator_email") or None,
        "tool_version": r.get("tool_version") or None,
        "workspace": r.get("workspace") or None,
        "cwd": r.get("cwd") or d.get("cwd") or None,
        "sensitivity_tier": r.get("sensitivity_tier"),
        "latency_ms": r.get("latency_ms") or None,
        "success": r.get("success"),
        "error_type": r.get("error_type") or None,
        "escalation_flag": r.get("escalation_flag") or None,
        "violation_count": r.get("violation_count") or None,
        "attribution": r.get("attribution"),
    }
    it = r.get("interaction_type", "")

    if coll == "cli_wrapper":
        f["observable_type"] = {
            "session_start": "session_start", "session_end": "session_end",
            "user_input": "user_prompt", "stall": "shell_stall",
            "interrupt": "shell_interrupt"}.get(it, "other")
        f["command"] = d.get("text") if it == "user_input" else d.get("command")
        f["is_developer_command"] = True if it == "user_input" else None
        f["hung_seconds"] = d.get("silent_seconds")
        f["exit_code"] = d.get("exit_code")
        f["latency_ms"] = r.get("latency_ms") or None

    elif coll == "mcp_proxy":
        f["observable_type"] = {
            "mcp_call": "mcp_request", "mcp_result": "mcp_result",
            "mcp_call_blocked": "mcp_blocked",
            "mcp_tools_discovered": "mcp_discovery"}.get(it, "other")
        f["mcp_server"] = r.get("connector") or None
        f["mcp_tool"] = d.get("tool") or None
        f["tool_arguments"] = d.get("arguments") or None
        f["tool_result"] = d.get("result") or None
        f["tool_result_size"] = d.get("result_bytes") or d.get("result_size")
        f["mcp_tools_available"] = d.get("tools") or None
        f["permission_decision"] = "deny" if it == "mcp_call_blocked" else None
        f["policy_rule_matched"] = d.get("matched_rule") or None

    elif coll == "fs_watcher":
        f["observable_type"] = {
            "file_create": "file_create", "file_modify": "file_modify",
            "file_delete": "file_delete", "file_reverted": "file_revert",
            "transcript_appended": "transcript_appended",
            "artifact_written": "artifact_written",
            "collector_config_changed": "collector_config_changed"}.get(it, "other")
        f["file_path"] = d.get("abs_path") or d.get("path") or None
        f["file_extension"] = d.get("extension") or None
        f["file_change_kind"] = d.get("change") or None
        f["file_size"] = d.get("size") or None
        f["content_hash"] = d.get("content_hash") or None
        f["reverted_to_earlier"] = d.get("reverted_to_earlier_state")
        f["is_config_file"] = d.get("is_config")
        f["is_dependency_manifest"] = d.get("is_dependency_manifest")
        f["is_lockfile"] = d.get("is_lockfile")
        f["is_binary"] = d.get("binary")
        f["input_tokens"] = d.get("input_tokens") or None
        f["output_tokens"] = d.get("output_tokens") or None
        f["cache_read_tokens"] = d.get("cache_read_tokens") or None
        f["total_tokens"] = r.get("tokens_total") or None
        f["thinking_blocks"] = d.get("thinking_blocks") or None
        f["reasoning_text"] = d.get("reasoning_text") or None
        f["response_text"] = f.get("response_text") or d.get("response_text") or None
        if d.get("models"):
            f["model"] = ", ".join(d["models"])
    return f


# ===========================================================================
# loading
# ===========================================================================

def load_all(dirs):
    """Return [(collector, fields, raw)] plus the raw rows for source_events."""
    out, raw_events = [], []
    seen = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.json*"), recursive=True):
            rp = os.path.realpath(path)
            if rp in seen or rp.endswith(".wire.jsonl"):
                continue
            seen.add(rp)
            base = os.path.basename(rp).lower()

            for line_no, line in enumerate(open(rp, errors="replace"), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                source_ref = f"{rp}:{line_no}"

                # raw hook payload
                if isinstance(rec, dict) and isinstance(rec.get("payload"), dict):
                    p = rec["payload"]
                    when = ts(rec.get("received_at")) or datetime.now(timezone.utc)
                    is_cursor = "cursor" in base or "cursor_version" in p
                    decision = rec.get("signal_decision")
                    f = extract_cursor_hook(p, when, decision) if is_cursor \
                        else extract_claude_hook(p, when, decision)
                    out.append((H, f, p))
                    raw_events.append({"tool": f["tool"], "collector": H,
                                       "session_id": f.get("session_id"),
                                       "event_name": p.get("hook_event_name"),
                                       "payload": p, "file_origin": rp,
                                       "source_ref": source_ref})
                    continue

                # OTLP batch
                if isinstance(rec, dict) and "resourceLogs" in rec:
                    otel_i = 0
                    for rl in rec["resourceLogs"]:
                        res = {a["key"]: unwrap(a.get("value"))
                               for a in rl.get("resource", {}).get("attributes", [])}
                        for sl in rl.get("scopeLogs", []):
                            for lr in sl.get("logRecords", []):
                                at = {a["key"]: unwrap(a.get("value"))
                                      for a in lr.get("attributes", [])}
                                when = ts(at.get("event.timestamp")) or datetime.now(timezone.utc)
                                f = extract_otel(res, at, when)
                                out.append((O, f, at))
                                raw_events.append({"tool": f["tool"], "collector": O,
                                                   "session_id": f.get("session_id"),
                                                   "event_name": at.get("event.name"),
                                                   "payload": at, "file_origin": rp,
                                                   "source_ref": f"{source_ref}#{otel_i}"})
                                otel_i += 1
                    continue

                # already a Signal row
                if isinstance(rec, dict) and "observable_id" in rec:
                    coll = rec.get("collector")
                    if coll in (W, P, F):
                        f = extract_row(rec)
                        out.append((coll, f, rec))
                        raw_events.append({"tool": f["tool"], "collector": coll,
                                           "session_id": f.get("session_id"),
                                           "event_name": rec.get("interaction_type"),
                                           "payload": rec, "file_origin": rp,
                                           "source_ref": source_ref})
    return out, raw_events


# ===========================================================================
# grouping and precedence
# ===========================================================================

def merge_key(coll, f):
    """What counts as the same occurrence."""
    t, s, ty = f.get("tool"), f.get("session_id"), f.get("observable_type")
    if f.get("tool_use_id"):
        return (t, s, "tooluse", f["tool_use_id"])
    if ty in ("user_prompt", "agent_response", "model_request") and f.get("turn_id"):
        return (t, s, ty, f["turn_id"], f.get("response_index"))
    if ty in ("session_start", "session_end"):
        return (t, s, ty)
    # plan events are their own occurrence. Grouping them into a time bucket
    # merges them with a tool call from the same second and the plan is lost.
    if ty in ("plan_created", "plan_completed"):
        return (t, s, ty, f.get("plan_item"), f.get("turn_id"))
    if ty and ty.startswith("file_") and f.get("file_path"):
        bucket = int(f["occurred_at"].timestamp() // MERGE_WINDOW_S) if f.get("occurred_at") else 0
        return (t, s, "file", f["file_path"], bucket)
    bucket = int(f["occurred_at"].timestamp() // MERGE_WINDOW_S) if f.get("occurred_at") else 0
    return (t, s, ty, bucket, coll if not s else "")


def canonical(v):
    """Normalise a value for comparison.

    Collectors disagree on type as well as content. OTel sends several
    numeric attributes as strings, so 145 and "145" must not count as a
    conflict. Only real differences should surface for validation.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip()
        try:
            return float(t)
        except ValueError:
            pass
        low = t.lower()
        if low in ("true", "false"):
            return low == "true"
        return t
    if isinstance(v, (list, tuple)):
        return json.dumps([canonical(x) for x in v], default=str, sort_keys=True)
    if isinstance(v, dict):
        return json.dumps({k: canonical(x) for k, x in sorted(v.items())},
                          default=str, sort_keys=True)
    return json.dumps(v, default=str, sort_keys=True)


def resolve(field, tool, contributions):
    """Pick the winner using precedence. Returns (value, winner, agree, others)."""
    have = {c: v for c, v in contributions if v not in (None, "", [], {})}
    if not have:
        return None, None, None, None
    entry = PRECEDENCE.get(field)
    order = entry[0].get(tool, []) if entry else []
    winner = next((c for c in order if c in have), None)
    if winner is None:
        winner = sorted(have, key=lambda c: (c != H, c != O, c))[0]
    value = have[winner]
    vals = {c: canonical(v) for c, v in have.items()}
    agree = len(set(map(str, vals.values()))) == 1 if len(vals) > 1 else None
    others = {c: have[c] for c in have if c != winner} if agree is False else None
    return value, winner, agree, others


SKIP = {"tool", "occurred_at", "observable_type"}


def curate(loaded):
    # Mirrors the field_capability table in Supabase. NOT_APPLICABLE entries
    # are intentionally excluded: they are never diagnosed per-row, since
    # storing a status row for every field that structurally can't apply to
    # a given observable_type would balloon this table for no benefit.
    FIELD_CAPABILITY = {
        ("claude-code", "reasoning_text", "fs_watcher"): "NOT_OBSERVABLE",
        ("claude-code", "policy_rule_matched", "local_hooks"): "UNKNOWN",
        ("claude-code", "violation_count", "local_hooks"): "UNKNOWN",
        ("claude-code", "escalation_flag", "local_hooks"): "UNKNOWN",
        ("codex", "output_tokens", "otel"): "OBSERVED",
        ("codex", "tool_duration_ms", "otel"): "OBSERVED",
        ("cursor", "reasoning_text", "local_hooks"): "OBSERVED",
        ("cursor", "agent_id", "local_hooks"): "NOT_EMITTED",
        ("cursor", "old_content", "local_hooks"): "OBSERVED",
        ("claude-code", "prompt_text", "local_hooks"): "OBSERVED",
        ("claude-code", "prompt_text", "otel"): "WITHHELD_BY_SOURCE",
    }

    groups = defaultdict(list)
    for coll, f, _ in loaded:
        if not f.get("observable_type") or f["observable_type"] == "other":
            continue
        groups[merge_key(coll, f)].append((coll, f))

    records, all_conflicts, field_status = [], [], []
    for key, members in groups.items():
        tool = members[0][1].get("tool", "unknown")
        fields = set()
        for _, f in members:
            fields |= {k for k, v in f.items() if v not in (None, "", [], {})}

        rec = {
            "observable_id": str(uuid.uuid4()),
            "dedup_key": hashlib.sha256(repr(key).encode()).hexdigest(),
            "schema_version": "signal-obs-v1",
            "tool": tool,
            "observable_type": members[0][1]["observable_type"],
            "captured_at": iso(datetime.now(timezone.utc)),
        }
        times = [f["occurred_at"] for _, f in members if f.get("occurred_at")]
        rec["occurred_at"] = iso(min(times)) if times else iso(datetime.now(timezone.utc))

        contributing, conflicts, winners = set(), [], Counter()
        for field in fields - SKIP:
            val, winner, agree, others = resolve(
                field, tool, [(c, f.get(field)) for c, f in members])
            if val is None:
                continue
            if isinstance(val, datetime):
                val = iso(val)
            rec[field] = val
            if winner:
                contributing.add(winner)
                winners[winner] += 1
            if agree is False:
                conflicts.append({
                    "observable_id": rec["observable_id"],
                    "tool": tool,
                    "session_id": members[0][1].get("session_id"),
                    "turn_id": members[0][1].get("turn_id"),
                    "observable_type": rec["observable_type"],
                    "field": field,
                    "winner": winner,
                    "winner_value": val if not isinstance(val, datetime) else iso(val),
                    "others": {c: (iso(v) if isinstance(v, datetime) else v)
                               for c, v in (others or {}).items()},
                    "occurred_at": rec["occurred_at"],
                })
            for c, f in members:
                if f.get(field) not in (None, "", [], {}):
                    contributing.add(c)

        rec["collector"] = winners.most_common(1)[0][0] if winners else members[0][0]
        rec["contributing"] = sorted(contributing)
        rec["collectors_agree"] = (not conflicts) if len(contributing) > 1 else None
        all_conflicts.extend(conflicts)
        rec["confidence"] = CONFIDENCE.get(rec["collector"], "reported")
        if len(contributing) > 1:
            rec["confidence"] = "correlated"
        rec.setdefault("attribution", "agent")
        rec.setdefault("sensitivity_tier", 1)

        for (fc_tool, fc_field, fc_coll), fc_status in FIELD_CAPABILITY.items():
            if fc_tool != tool or fc_coll not in contributing:
                continue
            if rec.get(fc_field) not in (None, "", [], {}):
                continue  # field is actually populated, nothing to diagnose
            field_status.append({
                "observable_id": rec["observable_id"],
                "field_name": fc_field,
                "status": fc_status,
                "collector": fc_coll,
                "note": f"from field_capability mirror as of curation run",
            })

        records.append(rec)

    records.sort(key=lambda r: r["occurred_at"])
    for i, r in enumerate(records):
        r["sequence_num"] = r.get("sequence_num") if r.get("sequence_num") is not None else i
    derive(records)
    return records, all_conflicts, field_status


# published rates, USD per million tokens. Used only where no cost is emitted.
MODEL_RATES = {
    "claude-sonnet": (3.0, 15.0), "claude-opus": (15.0, 75.0),
    "claude-haiku": (0.8, 4.0), "gpt-5": (1.25, 10.0), "default": (3.0, 15.0),
}


def rate_for(model):
    m = (model or "").lower()
    for k, v in MODEL_RATES.items():
        if k in m:
            return v
    return MODEL_RATES["default"]


def derive(records):
    """Fields nothing emits, computed from fields we do have."""
    by_session = defaultdict(list)
    agent_parent = {}
    for r in records:
        if r.get("session_id"):
            by_session[r["session_id"]].append(r)

    # turn_count and delegation depth, per session
    for sid, rows in by_session.items():
        turns = {r.get("turn_id") for r in rows if r.get("turn_id")}
        agents = {}
        for r in rows:
            aid, pid = r.get("agent_id"), r.get("parent_agent_id")
            if aid:
                agents[aid] = pid
        def depth(aid, seen=None):
            seen = seen or set()
            if not aid or aid in seen:
                return 0
            seen.add(aid)
            return 1 + depth(agents.get(aid), seen)
        for r in rows:
            if r.get("turn_count") is None:
                r["turn_count"] = len(turns)
            if r.get("agent_id") and r.get("delegation_depth") in (None, 0):
                r["delegation_depth"] = depth(r["agent_id"])

    for r in records:
        # a path outside the workspace it was supposed to stay in
        if r.get("outside_workspace") is None and r.get("file_path") and r.get("workspace"):
            try:
                r["outside_workspace"] = not os.path.abspath(
                    r["file_path"]).startswith(os.path.abspath(r["workspace"]))
            except Exception:
                pass

        # how much came back
        if r.get("output_size") is None:
            n = len(r.get("stdout") or "") + len(r.get("stderr") or "")
            if n:
                r["output_size"] = n

        # cost, where the tool does not emit it
        if r.get("cost_usd") is None and (r.get("input_tokens") or r.get("output_tokens")):
            rin, rout = rate_for(r.get("model"))
            cost = ((r.get("input_tokens") or 0) * rin +
                    (r.get("output_tokens") or 0) * rout) / 1_000_000
            if cost:
                r["cost_usd"] = round(cost, 6)
                r["cost_is_estimated"] = True

        # change size, when we have both sides of an edit
        if r.get("chars_added") is None and r.get("new_content") is not None:
            r["chars_added"] = len(r["new_content"])
        if r.get("chars_removed") is None and r.get("old_content") is not None:
            r["chars_removed"] = len(r["old_content"])

        # a plain status, since the enum is what the classifier reads
        if not r.get("status"):
            if r.get("success") is False:
                r["status"] = "error"
            elif r.get("permission_decision") in ("deny", "denied"):
                r["status"] = "denied"
            elif r.get("success") is True:
                r["status"] = "ok"


# ===========================================================================
# output
# ===========================================================================

def to_supabase(records, raw_events, conflicts=None, field_status=None):
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY")
    from supabase import create_client
    sb = create_client(url, key)
    tbl = os.environ.get("SUPABASE_TABLE", "observables")
    src = os.environ.get("SUPABASE_SOURCE_TABLE", "source_events")

    def chunked(rows, n=200):
        for i in range(0, len(rows), n):
            yield rows[i:i + n]

    # dedup_key is a hash of merge_key() — the same tuple already used to
    # group raw events into one observable — not a positional index, so it
    # doesn't shift when a run scans a different set of directories than a
    # previous run did. source_ref anchors a raw event to (file, line
    # number); since collector files are append-only, that pair is stable
    # forever. Both are enforced by a UNIQUE index in the schema, so even if
    # this client-side pre-filter misses something, the insert itself can't
    # duplicate a row — it can only silently skip it (ignore_duplicates).
    def existing_keys(table, column):
        seen = set()
        try:
            page, size = 0, 1000
            while True:
                r = (sb.table(table).select(column)
                       .order(column)
                       .range(page * size, page * size + size - 1).execute())
                batch = r.data or []
                seen.update(row.get(column) for row in batch if row.get(column))
                if len(batch) < size:
                    break
                page += 1
            print(f"  {len(seen)} {table} rows already stored, they will be skipped")
        except Exception as e:
            sys.exit(f"  could not read existing {table} rows ({e}); aborting "
                     f"rather than risking duplicate inserts. Re-run once "
                     f"Supabase is reachable.")
        return seen

    def existing_dedup_to_obs_id(table):
        """dedup_key -> the real, already-stored observable_id.

        A curated record's own observable_id is a fresh uuid4() every run,
        regardless of whether it turns out to be new or a duplicate of
        something already stored. field_status diagnostics generated for a
        duplicate record are worthless with that fresh id -- it was never
        inserted anywhere. This lets those diagnostics get remapped onto
        the real, already-stored id instead of being silently dropped.
        """
        mapping = {}
        try:
            page, size = 0, 1000
            while True:
                r = (sb.table(table).select("dedup_key,observable_id")
                       .order("dedup_key")
                       .range(page * size, page * size + size - 1).execute())
                batch = r.data or []
                for row in batch:
                    if row.get("dedup_key"):
                        mapping[row["dedup_key"]] = row["observable_id"]
                if len(batch) < size:
                    break
                page += 1
        except Exception as e:
            sys.exit(f"  could not read existing {table} id map ({e}); aborting.")
        return mapping

    dedup_to_real_id = existing_dedup_to_obs_id(tbl)
    existing_obs = set(dedup_to_real_id)
    print(f"  {len(existing_obs)} {tbl} rows already stored, they will be skipped")

    # Remap before filtering: every curated record's fresh id needs a path
    # to the real stored id if it turns out to be a duplicate.
    id_remap = {r["observable_id"]: dedup_to_real_id[r["dedup_key"]]
                for r in records if r.get("dedup_key") in dedup_to_real_id}

    records = [r for r in records if r.get("dedup_key") not in existing_obs]
    if not records:
        print("  nothing new to load in observables")

    # field_status rows carry an observable_id FK into observables. Remap
    # duplicate-record diagnostics onto the real stored id (recovering what
    # would otherwise be silently lost); a record genuinely new this run
    # keeps its own freshly-generated id, since that's what gets inserted.
    live_ids = {r["observable_id"] for r in records}
    all_known_ids = live_ids | set(dedup_to_real_id.values())
    remapped = []
    for fs in (field_status or []):
        oid = id_remap.get(fs["observable_id"], fs["observable_id"])
        if oid in all_known_ids:
            fs = dict(fs)
            fs["observable_id"] = oid
            remapped.append(fs)
    field_status = remapped

    existing_src = existing_keys(src, "source_ref")
    raw_events = [r for r in raw_events if r.get("source_ref") not in existing_src]
    if not raw_events:
        print("  nothing new to load in source_events")

    n = 0
    for batch in chunked(raw_events):
        try:
            (sb.table(src).upsert(batch, on_conflict="source_ref",
                                   ignore_duplicates=True).execute())
            n += len(batch)
        except Exception as e:
            print(f"  source_events batch failed: {e}", file=sys.stderr)
    print(f"  {n} raw payloads -> {src}")

    n = 0
    for batch in chunked(records):
        try:
            (sb.table(tbl).upsert(batch, on_conflict="dedup_key",
                                   ignore_duplicates=True).execute())
            n += len(batch)
        except Exception as e:
            print(f"  observables batch failed: {e}", file=sys.stderr)
    print(f"  {n} observable records -> {tbl}")

    if conflicts:
        ctbl = os.environ.get("SUPABASE_CONFLICT_TABLE", "collector_conflicts")
        n = 0
        for batch in chunked(conflicts):
            try:
                sb.table(ctbl).insert(batch).execute()
                n += len(batch)
            except Exception as e:
                print(f"  conflicts batch failed: {e}", file=sys.stderr)
        print(f"  {n} conflicts -> {ctbl}")

    if field_status:
        fstbl = os.environ.get("SUPABASE_FIELD_STATUS_TABLE", "observable_field_status")
        n = 0
        for batch in chunked(field_status):
            try:
                # unique(observable_id, field_name) means a rerun on the
                # same data just overwrites the same diagnosis rather than
                # duplicating it.
                (sb.table(fstbl).upsert(
                    batch, on_conflict="observable_id,field_name").execute())
                n += len(batch)
            except Exception as e:
                print(f"  field_status batch failed: {e}", file=sys.stderr)
        print(f"  {n} field-status diagnostics -> {fstbl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--supabase", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--conflict-count", action="store_true",
                    help="just the count of fields that mismatch")
    ap.add_argument("--conflicts", action="store_true",
                    help="show every field where two collectors disagreed")
    ap.add_argument("--conflicts-out", help="write conflicts to a jsonl file")
    ap.add_argument("--keep-conflicts", action="store_true",
                    help="also write conflicts to the collector_conflicts table")
    a = ap.parse_args()
    dirs = a.dirs or DEFAULT_DIRS

    loaded, raw_events = load_all(dirs)
    if not loaded:
        print("No collector output found. Looked in:")
        for d in dirs:
            print("   " + d)
        return 1

    by_coll = Counter(c for c, _, _ in loaded)
    print(f"\nread {len(loaded)} source rows")
    for c, n in by_coll.most_common():
        print(f"   {c:<14} {n}")

    records, conflicts, field_status = curate(loaded)
    print(f"\ncurated into {len(records)} observable records")

    types = Counter(r["observable_type"] for r in records)
    print("\nby type:")
    for t, n in types.most_common(12):
        print(f"   {t:<26} {n}")

    multi = [r for r in records if len(r.get("contributing", [])) > 1]
    print(f"\n{len(multi)} records had more than one collector contributing")
    print(f"{len(conflicts)} field-level conflicts between collectors")
    print(f"{len(field_status)} field-status diagnostics (why-null flags) generated")

    if a.conflicts or a.conflict_count:
        # how many fields ever had two collectors both report a value,
        # and of those, how many ever disagreed
        compared, conflicted = Counter(), Counter()
        for c in conflicts:
            conflicted[c["field"]] += 1
        for r in records:
            if len(r.get("contributing", [])) > 1:
                for k, v in r.items():
                    if v not in (None, "", [], {}) and k not in SKIP:
                        compared[k] += 1
        both = set(compared)
        print("\n" + "=" * 60)
        print("FIELD MISMATCH COUNT")
        print("=" * 60)
        print(f"  fields compared by more than one collector : {len(both)}")
        print(f"  fields that disagreed at least once        : {len(conflicted)}")
        print(f"  fields that always agreed                  : {len(both - set(conflicted))}")
        print(f"  total disagreeing values                   : {len(conflicts)}")
        if conflicted:
            print(f"\n  {'field':<26} {'conflicts':>9} {'compared':>9}")
            print("  " + "-" * 46)
            for f, n in conflicted.most_common():
                print(f"  {f:<26} {n:>9} {compared.get(f, 0):>9}")
        if a.conflict_count:
            return 0

    if a.conflicts:
        if not conflicts:
            print("\nNo conflicts. Every field where two collectors both had a "
                  "value, they agreed.")
        else:
            print("\n" + "=" * 78)
            print("COLLECTOR CONFLICTS — for validation")
            print("The table stores the winner only. These are the cases where")
            print("another collector said something different.")
            print("=" * 78)
            by_field = defaultdict(list)
            for c in conflicts:
                by_field[c["field"]].append(c)
            for field, cs in sorted(by_field.items(), key=lambda x: -len(x[1])):
                print(f"\n{field}   ({len(cs)} conflicts)")
                for c in cs[:5]:
                    print(f"  {c['observable_type']}  session {str(c['session_id'])[:12]}"
                          f"  turn {str(c['turn_id'])[:10]}  {c['occurred_at'][:19]}")
                    print(f"     KEPT     {c['winner']:<12} {str(c['winner_value'])[:56]}")
                    for coll, v in c["others"].items():
                        print(f"     dropped  {coll:<12} {str(v)[:56]}")
                if len(cs) > 5:
                    print(f"  ... and {len(cs)-5} more")

    if a.stats:
        filled = Counter()
        for r in records:
            for k, v in r.items():
                if v not in (None, "", [], {}):
                    filled[k] += 1
        print(f"\nfield coverage across {len(records)} records:")
        for k, n in filled.most_common():
            print(f"   {k:<26} {n:>5}  {100*n//len(records):>3}%")

    if a.conflicts_out:
        with open(a.conflicts_out, "w") as f:
            for c in conflicts:
                f.write(json.dumps(c, default=str) + "\n")
        print(f"\n{len(conflicts)} conflicts -> {a.conflicts_out}")

    if a.out:
        with open(a.out, "w") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")
        print(f"\nwritten to {a.out}")
    elif a.supabase:
        print()
        to_supabase(records, raw_events, conflicts if a.keep_conflicts else None,
                    field_status)
    elif a.dry_run or True:
        print("\nfirst 3 records:\n")
        for r in records[:3]:
            keys = [k for k in r if r[k] not in (None, "", [], {})]
            print(f"  {r['observable_type']}  ({len(keys)} fields, "
                  f"from {', '.join(r['contributing'])})")
            for k in sorted(keys):
                if k in ("observable_id", "schema_version", "captured_at"):
                    continue
                print(f"      {k:<22} {str(r[k])[:60]}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
