#!/usr/bin/env python3
"""
Signal Collect — field status diagnosis

For every field, on every tool, says whether it is populated and if not, why.
A null is not one thing. It has six possible causes and they carry entirely
different implications, three of them ours and two of them the vendor's.

    POPULATED             the field carries a value
    NOT_APPLICABLE        no event where this field could apply occurred
    COLLECTOR_ABSENT      the collector that supplies it never ran on this tool
    EXTRACTION_GAP        the raw payload contains it and our curation dropped it
    DERIVABLE             the inputs are present and the derivation is not built
    VENDOR_ABSENT         the condition arose, the collector ran, and the raw
                          payload contains nothing resembling the field

Only VENDOR_ABSENT is a claim about the tool. COLLECTOR_ABSENT, EXTRACTION_GAP
and DERIVABLE are claims about us. NOT_APPLICABLE is a claim about the sessions
we ran. Reporting all five as absence, which a coverage count does, makes a
study's own limitations look like findings about the vendors.

The decisive test is EXTRACTION_GAP. We search the retained raw payloads for
the field under every name we have seen a vendor use for it. If it is in the
raw and not in the curated record, the fault is ours and it is fixable.

    python3 build_field_status.py
    python3 build_field_status.py --tool cursor
    python3 build_field_status.py --ours          # only what we can fix
    python3 build_field_status.py --supabase      # write to the database
    python3 build_field_status.py --csv out.csv
"""

import argparse
import glob
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
TOOLS = ["claude-code", "cursor", "codex", "copilot", "antigravity"]
LABEL = {"claude-code": "Claude Code", "cursor": "Cursor", "codex": "Codex",
         "copilot": "Copilot", "antigravity": "Antigravity"}

# Which observable types make a field applicable. If none occurred for a tool,
# the question was never put to it and the null says nothing about the vendor.
APPLICABLE = {
 "prompt_text":       {"user_prompt"},
 "prompt_length":     {"user_prompt"},
 "response_text":     {"agent_response"},
 "reasoning_text":    {"agent_reasoning", "agent_response", "transcript_appended"},
 "thinking_blocks":   {"agent_reasoning", "transcript_appended"},
 "plan_item":         {"plan_created", "plan_completed"},
 "plan_status":       {"plan_created", "plan_completed"},
 "compaction_reason": {"compaction"},
 "agent_id":          {"subagent_start", "subagent_stop", "tool_request",
                       "tool_result", "delegation"},
 "agent_type":        {"subagent_start", "subagent_stop"},
 "parent_agent_id":   {"subagent_start", "subagent_stop", "delegation"},
 "delegation_depth":  {"subagent_start", "subagent_stop", "delegation"},
 "tool_name":         {"tool_request", "tool_result", "tool_failure"},
 "tool_arguments":    {"tool_request", "tool_result", "mcp_request"},
 "tool_result":       {"tool_result", "mcp_result"},
 "tool_duration_ms":  {"tool_result"},
 "exit_code":         {"tool_result", "shell_result"},
 "command":           {"tool_request", "tool_result", "shell_request",
                       "shell_result"},
 "stdout":            {"tool_result", "shell_result"},
 "stderr":            {"tool_result", "shell_result", "tool_failure"},
 "output_size":       {"tool_result", "shell_result"},
 "total_tokens":      {"model_request", "agent_response", "transcript_appended"},
 "input_tokens":      {"model_request", "agent_response", "transcript_appended"},
 "output_tokens":     {"model_request", "agent_response", "transcript_appended"},
 "cache_read_tokens": {"model_request", "agent_response", "transcript_appended"},
 "cost_usd":          {"model_request", "agent_response", "transcript_appended"},
 "request_latency_ms": {"model_request"},
 "file_path":         {"file_create", "file_modify", "file_delete", "file_read",
                       "tool_request", "tool_result"},
 "file_change_kind":  {"file_create", "file_modify", "file_delete", "file_rename"},
 "content_hash":      {"file_create", "file_modify"},
 "old_content":       {"file_modify"},
 "new_content":       {"file_modify"},
 "chars_added":       {"file_modify", "file_create"},
 "chars_removed":     {"file_modify", "file_delete"},
 "reverted_to_earlier": {"file_modify", "file_revert"},
 "permission_decision": {"permission_request", "permission_granted",
                         "permission_denied"},
 "permission_options": {"permission_request"},
 "denial_reason":     {"permission_denied"},
 "mcp_server":        {"mcp_request", "mcp_result", "mcp_discovery"},
 "mcp_tools_available": {"mcp_discovery"},
 "loop_count":        {"agent_response", "turn_end"},
 "stop_reason":       {"agent_response", "model_request", "session_end"},
 "hung_seconds":      {"shell_stall"},
}

# Which collector supplies each field. If none of these produced any row for a
# tool, the field could not have arrived however willing the vendor was.
SUPPLIED_BY = {
 "prompt_text": {"local_hooks", "otel", "fs_watcher"},
 "response_text": {"local_hooks", "otel", "fs_watcher"},
 "reasoning_text": {"local_hooks", "fs_watcher"},
 "thinking_blocks": {"fs_watcher"},
 "total_tokens": {"local_hooks", "otel", "fs_watcher"},
 "input_tokens": {"local_hooks", "otel", "fs_watcher"},
 "output_tokens": {"local_hooks", "otel", "fs_watcher"},
 "cache_read_tokens": {"local_hooks", "otel", "fs_watcher"},
 "cost_usd": {"otel", "derived"},
 "agent_id": {"local_hooks", "otel"},
 "agent_type": {"local_hooks"},
 "parent_agent_id": {"local_hooks", "otel"},
 "tool_name": {"local_hooks", "otel", "mcp_proxy"},
 "tool_arguments": {"local_hooks", "mcp_proxy"},
 "tool_result": {"local_hooks", "mcp_proxy"},
 "mcp_server": {"mcp_proxy", "local_hooks"},
 "mcp_tools_available": {"mcp_proxy"},
 "command": {"local_hooks", "cli_wrapper"},
 "stdout": {"local_hooks", "cli_wrapper"},
 "stderr": {"local_hooks", "cli_wrapper"},
 "exit_code": {"local_hooks", "cli_wrapper"},
 "hung_seconds": {"cli_wrapper"},
 "file_path": {"local_hooks", "fs_watcher"},
 "file_change_kind": {"fs_watcher"},
 "content_hash": {"fs_watcher"},
 "reverted_to_earlier": {"fs_watcher"},
 "old_content": {"local_hooks"},
 "new_content": {"local_hooks"},
 "permission_decision": {"local_hooks", "otel"},
 "permission_options": {"local_hooks"},
 "denial_reason": {"local_hooks"},
 "compaction_reason": {"local_hooks"},
 "plan_item": {"local_hooks"},
 "plan_status": {"local_hooks"},
}

# Every name we have seen a vendor use for a field. Searching the raw payload
# under all of them is what distinguishes their omission from our oversight,
# and the list is long because no two tools agree on any of it.
ALIASES = {
 "prompt_text": ["prompt", "text", "user_prompt", "message", "userPrompt"],
 "response_text": ["delta", "last_assistant_message", "assistant_response",
                   "response", "content", "text"],
 "reasoning_text": ["thinking", "reasoning", "thought", "reasoning_content"],
 "thinking_blocks": ["thinking", "reasoning_output_tokens"],
 "total_tokens": ["total_tokens", "totalTokens", "tokens", "usage"],
 "input_tokens": ["input_tokens", "inputTokens", "prompt_tokens",
                  "tokens.input", "cached_input_tokens"],
 "output_tokens": ["output_tokens", "outputTokens", "completion_tokens",
                   "tokens.output"],
 "cache_read_tokens": ["cache_read_input_tokens", "cache_read_tokens",
                       "cached_input_tokens", "cacheReadTokens"],
 "cost_usd": ["cost_usd", "cost", "costUsd", "total_cost"],
 "agent_id": ["agent_id", "agentId", "subagent_id", "agent.id"],
 "agent_type": ["agent_type", "agentType", "agentName", "subagent_type"],
 "parent_agent_id": ["parent_agent_id", "parentAgentId", "parent.agent.id"],
 "tool_name": ["tool_name", "toolName", "tool", "name"],
 "tool_arguments": ["tool_input", "toolInput", "arguments", "args", "params",
                    "input"],
 "tool_result": ["tool_response", "tool_output", "toolOutput", "result",
                 "output"],
 "tool_duration_ms": ["duration_ms", "durationMs", "duration", "elapsed_ms"],
 "exit_code": ["exit_code", "exitCode", "returncode", "status_code"],
 "command": ["command", "cmd", "CommandLine", "commandLine"],
 "stdout": ["stdout", "output", "stdOut"],
 "stderr": ["stderr", "error", "stdErr", "error_message"],
 "file_path": ["file_path", "filePath", "path", "TargetFile", "abs_path"],
 "file_change_kind": ["change", "event_type", "change_type", "kind"],
 "old_content": ["old_string", "oldString", "old_content", "before"],
 "new_content": ["new_string", "newString", "new_content", "after"],
 "permission_decision": ["decision", "permission_decision", "permissionDecision",
                         "permission"],
 "permission_options": ["permission_suggestions", "permissionSuggestions"],
 "denial_reason": ["reason", "denial_reason", "denialReason", "userMessage"],
 "compaction_reason": ["reason", "trigger", "compaction_reason"],
 "plan_item": ["task_subject", "task_description", "task", "todo", "plan"],
 "plan_status": ["status", "state", "task_status"],
 "mcp_server": ["server", "mcp_server", "mcpServer", "serverName"],
 "mcp_tools_available": ["tools", "available_tools"],
 "model": ["model", "modelName", "model_id", "model.slug"],
 "loop_count": ["loop_count", "loopCount", "stepIdx", "iteration"],
 "stop_reason": ["stop_reason", "finish_reason", "status", "terminationReason"],
 "sandboxed": ["sandbox", "sandboxed", "is_sandboxed"],
}

# What each derived field needs. Present inputs plus an absent output means the
# derivation exists to be written and has not been.
DERIVES_FROM = {
 "turn_count": ["turn_id", "session_id"],
 "delegation_depth": ["agent_id"],
 "outside_workspace": ["file_path", "workspace"],
 "output_size": ["stdout"],
 "cost_usd": ["total_tokens", "model"],
 "chars_added": ["new_content"],
 "chars_removed": ["old_content"],
 "sequence_num": ["occurred_at"],
 "status": ["success"],
}

CAUSE_OWNER = {
 "POPULATED": "-",
 "NOT_APPLICABLE": "sessions",
 "COLLECTOR_ABSENT": "ours",
 "EXTRACTION_GAP": "ours",
 "DERIVABLE": "ours",
 "VENDOR_ABSENT": "vendor",
}


def load(dirs):
    p = os.path.join(HERE, "signal_curate.py")
    if not os.path.exists(p):
        sys.exit("signal_curate.py must sit beside this script")
    spec = importlib.util.spec_from_file_location("sc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    dirs = dirs or getattr(m, "DEFAULT_DIRS", ["."])
    loaded, raw = m.load_all(dirs)
    # curate() has grown a third return value in this copy; take the first
    # regardless of how many it returns.
    result = m.curate(loaded)
    records = result[0] if isinstance(result, tuple) else result
    return records, raw, m


def index_raw(raw_events):
    """Every key name appearing in each tool's raw payloads, once."""
    seen = defaultdict(set)
    blobs = defaultdict(list)

    def walk(obj, out, depth=0):
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(k)
                walk(v, out, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:40]:
                walk(v, out, depth + 1)

    for ev in raw_events:
        tool = ev.get("tool")
        if tool not in TOOLS:
            continue
        keys = set()
        walk(ev.get("payload"), keys)
        seen[tool] |= keys
        if len(blobs[tool]) < 400:
            blobs[tool].append(json.dumps(ev.get("payload"), default=str)[:4000])
    return seen, blobs


def diagnose(records, raw_keys, raw_blobs, fields):
    by_tool = defaultdict(list)
    for r in records:
        if r.get("tool") in TOOLS:
            by_tool[r["tool"]].append(r)

    out = {}
    for tool in TOOLS:
        rows = by_tool.get(tool, [])
        types_seen = {r.get("observable_type") for r in rows}
        collectors_seen = set()
        for r in rows:
            collectors_seen.update(r.get("contributing") or [])
            if r.get("collector"):
                collectors_seen.add(r["collector"])

        cells = {}
        for field in fields:
            filled = [r for r in rows if r.get(field) not in (None, "", [], {})]
            n = len(filled)

            applicable_types = APPLICABLE.get(field)
            if applicable_types is None:
                applicable = rows
            else:
                applicable = [r for r in rows
                              if r.get("observable_type") in applicable_types]
            sessions_applicable = {r.get("session_id") for r in applicable
                                   if r.get("session_id")}

            if n:
                src = Counter(r.get("collector") or "derived" for r in filled)
                ex = None
                for r in filled:
                    v = r[field]
                    ex = (json.dumps(v, default=str)
                          if isinstance(v, (dict, list)) else str(v))[:60]
                    break
                cells[field] = {
                    "status": "POPULATED", "count": n,
                    "applicable": len(applicable),
                    "sessions": len(sessions_applicable),
                    "sources": dict(src.most_common(3)),
                    "example": ex, "note": "",
                }
                continue

            # --- it is null. Work out why, in order of decisiveness. ---

            if not rows:
                status, note = "COLLECTOR_ABSENT", "no records captured for this tool"

            elif not applicable:
                status = "NOT_APPLICABLE"
                want = ", ".join(sorted(applicable_types or [])[:3])
                note = f"no {want} events occurred"

            else:
                # is the field present in the raw payload under any known name?
                aliases = set(ALIASES.get(field, [])) | {field}
                hit = aliases & raw_keys.get(tool, set())
                if hit:
                    status = "EXTRACTION_GAP"
                    note = f"raw payload carries {', '.join(sorted(hit)[:3])}"
                else:
                    # could it be computed from what we do have?
                    inputs = DERIVES_FROM.get(field)
                    if inputs and all(
                            any(r.get(i) not in (None, "", [], {}) for r in rows)
                            for i in inputs):
                        status = "DERIVABLE"
                        note = f"inputs present: {', '.join(inputs)}"
                    else:
                        suppliers = SUPPLIED_BY.get(field)
                        if suppliers and not (suppliers & collectors_seen):
                            status = "COLLECTOR_ABSENT"
                            note = ("needs " + " or ".join(sorted(suppliers))
                                    + ", none ran on this tool")
                        else:
                            status = "VENDOR_ABSENT"
                            note = (f"{len(applicable)} applicable records "
                                    f"across {len(sessions_applicable)} sessions, "
                                    f"nothing resembling it in the raw payload")

            cells[field] = {
                "status": status, "count": 0,
                "applicable": len(applicable),
                "sessions": len(sessions_applicable),
                "sources": {}, "example": None, "note": note,
            }
        out[tool] = cells
    return out, by_tool


def print_report(diag, by_tool, fields, ours_only=False):
    w = 20
    print("\nField status by tool: why every null is null\n")
    hdr = f"{'field':<24}" + "".join(f"{LABEL[t][:18]:>{w}}" for t in TOOLS)
    print(hdr)
    print("-" * len(hdr))

    short = {"POPULATED": "ok", "NOT_APPLICABLE": "n/a",
             "COLLECTOR_ABSENT": "COLLECTOR", "EXTRACTION_GAP": "EXTRACTION",
             "DERIVABLE": "derivable", "VENDOR_ABSENT": "vendor"}

    for field in fields:
        cells = [diag[t][field] for t in TOOLS]
        if ours_only and not any(
                CAUSE_OWNER[c["status"]] == "ours" for c in cells):
            continue
        line = f"{field:<24}"
        for c in cells:
            s = short[c["status"]]
            v = f"{s} {c['count']}" if c["status"] == "POPULATED" else s
            line += f"{v:>{w}}"
        print(line)

    print("\n" + "-" * len(hdr))
    tally = {t: Counter(diag[t][f]["status"] for f in fields) for t in TOOLS}
    for st in ("POPULATED", "NOT_APPLICABLE", "COLLECTOR_ABSENT",
               "EXTRACTION_GAP", "DERIVABLE", "VENDOR_ABSENT"):
        line = f"{st + ' (' + CAUSE_OWNER[st] + ')':<24}"
        for t in TOOLS:
            line += f"{tally[t][st]:>{w}}"
        print(line)

    print("\n\nWhat is ours to fix\n")
    any_ours = False
    for t in TOOLS:
        items = [(f, diag[t][f]) for f in fields
                 if CAUSE_OWNER[diag[t][f]["status"]] == "ours"]
        if not items:
            continue
        any_ours = True
        print(f"  {LABEL[t]}")
        for f, c in items:
            print(f"    {c['status']:<18} {f:<22} {c['note']}")
        print()
    if not any_ours:
        print("  nothing: every null is applicability or vendor absence")

    print("\nVendor absences, which are the only claims about the tools\n")
    for t in TOOLS:
        items = [(f, diag[t][f]) for f in fields
                 if diag[t][f]["status"] == "VENDOR_ABSENT"]
        if not items:
            continue
        print(f"  {LABEL[t]}")
        for f, c in items:
            weak = "  (single session, not replicated)" if c["sessions"] < 2 else ""
            print(f"    {f:<24} {c['applicable']:>5} applicable records, "
                  f"{c['sessions']} sessions{weak}")
        print()


def write_csv(diag, fields, path):
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "tool", "status", "owner", "count",
                    "applicable_records", "sessions", "sources", "note",
                    "example"])
        for field in fields:
            for t in TOOLS:
                c = diag[t][field]
                w.writerow([field, LABEL[t], c["status"],
                            CAUSE_OWNER[c["status"]], c["count"],
                            c["applicable"], c["sessions"],
                            ",".join(c["sources"]), c["note"],
                            c["example"] or ""])
    print(f"written: {path}")


def to_supabase(diag, fields):
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY")
    from supabase import create_client
    sb = create_client(url, key)
    rows = []
    for field in fields:
        for t in TOOLS:
            c = diag[t][field]
            rows.append({
                "field": field, "tool": t, "status": c["status"],
                "owner": CAUSE_OWNER[c["status"]],
                "populated_count": c["count"],
                "applicable_records": c["applicable"],
                "sessions_applicable": c["sessions"],
                "sources": list(c["sources"]),
                "note": c["note"], "example": c["example"],
            })
    tbl = os.environ.get("SUPABASE_STATUS_TABLE", "observable_field_status")
    n = 0
    for i in range(0, len(rows), 200):
        try:
            sb.table(tbl).insert(rows[i:i + 200]).execute()
            n += len(rows[i:i + 200])
        except Exception as e:
            print(f"  batch failed: {e}", file=sys.stderr)
    print(f"{n} rows -> {tbl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--tool")
    ap.add_argument("--ours", action="store_true",
                    help="only fields where the fault is ours")
    ap.add_argument("--csv")
    ap.add_argument("--supabase", action="store_true")
    a = ap.parse_args()

    records, raw, mod = load(a.dirs)
    if not records:
        sys.exit("no captured data found")

    fields = sorted(set(APPLICABLE) | set(SUPPLIED_BY) | set(DERIVES_FROM)
                    | set(ALIASES))
    raw_keys, raw_blobs = index_raw(raw)
    diag, by_tool = diagnose(records, raw_keys, raw_blobs, fields)

    print(f"\n{len(records)} curated records, {len(raw)} raw payloads, "
          f"{len(fields)} fields diagnosed")
    for t in TOOLS:
        rows = by_tool.get(t, [])
        print(f"  {LABEL[t]:<14} {len(rows):>6} records, "
              f"{len({r.get('session_id') for r in rows if r.get('session_id')})} sessions, "
              f"{len(raw_keys.get(t, set()))} distinct raw keys")

    print_report(diag, by_tool, fields, a.ours)
    if a.csv:
        write_csv(diag, fields, a.csv)
    if a.supabase:
        to_supabase(diag, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
