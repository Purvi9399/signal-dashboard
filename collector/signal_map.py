#!/usr/bin/env python3
"""
Signal Collect — observable map generator

Builds the surface-by-observable map from data we actually captured, so every
cell carries evidence instead of an opinion.

    python3 signal_map.py                      # scan default locations
    python3 signal_map.py --tool claude-code   # one tool only
    python3 signal_map.py --md > map.md        # markdown table for the doc

Reads two things:
  *-RAW.jsonl   raw hook payloads, exactly as the tool sent them
  *.jsonl       mapped observable rows from any collector

For each observable it reports one of:
  CONFIRMED   the field was present in a real payload, with the event and key
  DERIVE      not emitted, but computable from fields we do have
  NOT SEEN    registered or expected, never appeared in this data
  N/A         does not exist on this surface
"""

import glob
import json
import os
import sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".signal", "sessions"), "signal_sessions",
                "signal_sessions/cursor", "."]

# observable, [(event, field) pairs that would prove it], derive_note
# field "*" means the event firing at all is the evidence
SPEC = [
    ("Identity & Correlation", [
        ("Session id",            [("*", "session_id"), ("*", "conversation_id")], None),
        ("Conversation id",       [("*", "conversation_id"), ("*", "session_id")], None),
        ("Generation / turn id",  [("*", "turn_id"), ("*", "generation_id"), ("*", "prompt_id")], None),
        ("Model request id",      [("*", "message_id"), ("*", "request_id"), ("*", "generation_id")], None),
        ("Tool call id",          [("*", "tool_use_id")], None),
        ("Workspace roots",       [("*", "workspace_roots")], None),
        ("Transcript pointer",    [("*", "transcript_path")], None),
        ("Parent session id",     [("*", "parent_session_id")], None),
        ("Subagent id",           [("*", "agent_id")], None),
        ("Parent agent id",       [("*", "parent_agent_id")], "nested agent_id chain"),
        ("User account uuid",     [("*", "user_id"), ("*", "account_uuid")], None),
        ("Organization id",       [("*", "organization_id")], None),
        ("User email",            [("*", "user.email"), ("*", "user_email")], None),
        ("User account uuid",     [("*", "user.account_uuid")], None),
        ("User account id",       [("*", "user.account_id")], None),
        ("Hashed user id",        [("*", "user.id")], None),
        ("Surface / terminal type", [("*", "terminal.type")], None),
        ("Tool version",          [("*", "service.version"), ("*", "cursor_version")], None),
        ("OS type and version",   [("*", "os.type"), ("*", "os.version")], None),
        ("CPU architecture",      [("*", "host.arch")], None),
        ("Event sequence number", [("*", "event.sequence")], None),
        ("OS username",           [], "CLI Wrapper / collector environment"),
        ("Hostname",              [], "collector environment"),
    ]),
    ("Session Lifecycle", [
        ("Session start time",    [("SessionStart", "*"), ("sessionStart", "*")], None),
        ("Session end time",      [("SessionEnd", "*"), ("sessionEnd", "*")], None),
        ("Session duration",      [], "start and end timestamps"),
        ("Session resumed vs fresh", [("SessionStart", "source")], None),
        ("Model at session start",[("SessionStart", "model")], None),
        ("Exit reason",           [("SessionEnd", "reason")], None),
    ]),
    ("Prompt & Input", [
        ("Prompt text",           [("UserPromptSubmit", "prompt"), ("beforeSubmitPrompt", "prompt")], None),
        ("Prompt length",         [], "len of prompt text"),
        ("Prompt word count",     [], "split of prompt text"),
        ("Permission mode at prompt", [("UserPromptSubmit", "permission_mode")], None),
        ("Working directory",     [("*", "cwd")], None),
        ("Attachment count",      [("*", "attachments")], None),
        ("Composer mode (agent/ask/edit)", [("*", "composer_mode")], None),
        ("Slash command used",    [("*", "command_name")], "prompt text starts with /"),
        ("Slash command source",  [("*", "command_source")], None),
        ("Prompt redacted by default", [("user_prompt", "prompt")], None),
        ("Secret-shaped string in prompt", [], "pattern match on prompt text"),
        ("Time of day of prompt", [], "row timestamp"),
        ("Gap since previous prompt", [], "difference of prompt timestamps"),
        ("Similarity to previous prompt", [], "text comparison across prompts"),
        ("Frustration markers",   [], "language classification on prompt text"),
    ]),
    ("Model / LLM Call", [
        ("Model name",            [("SessionStart", "model"), ("api_request", "model"), ("*", "model_id")], None),
        ("Input tokens",          [("claude_code.token.usage", "*"), ("api_request", "input_tokens"), ("*", "input_tokens")], "transcript usage"),
        ("Output tokens",         [("claude_code.token.usage", "*"), ("api_request", "output_tokens"), ("*", "output_tokens")], "transcript usage"),
        ("Cache read tokens",     [("claude_code.token.usage", "*"), ("*", "cache_read_tokens")], "transcript usage"),
        ("Cache creation tokens", [("claude_code.token.usage", "*"), ("*", "cache_write_tokens")], "transcript usage"),
        ("Total tokens",          [("claude_code.token.usage", "*")], "sum of input and output"),
        ("Cost in USD",           [("claude_code.cost.usage", "*"), ("api_request", "cost_usd")], "tokens times model rate"),
        ("Thinking / reasoning blocks", [], "transcript content type thinking"),
        ("Effort level",          [("*", "effort")], None),
        ("Sampling parameters",   [("*", "model_params")], None),
        ("Assistant response text", [("MessageDisplay", "delta"), ("Stop", "last_assistant_message"),
                                     ("assistant_response", "*"), ("afterAgentResponse", "text")], None),
        ("Agent reasoning text",  [("afterAgentThought", "text")], None),
        ("Reasoning step duration", [("afterAgentThought", "duration_ms")], None),
        ("Response streamed in parts", [("MessageDisplay", "index")], None),
        ("Response final flag",   [("MessageDisplay", "final")], None),
        ("Request latency",       [("api_request", "duration_ms")], None),
        ("Time to first token",   [], "OTel only"),
        ("Stop reason",           [("api_request", "stop_reason")], None),
    ]),
    ("Agent Behavior & Planning", [
        ("Active working time",   [("claude_code.active_time.total", "*")], None),
        ("Turn count in session", [], "count of distinct prompt_id"),
        ("Loop count within a turn", [("stop", "loop_count")], "count events per prompt_id"),
        ("Turn outcome status",   [("stop", "status")], None),
        ("Plan / todo list created", [("TaskCreated", "*")], None),
        ("Plan items completed", [("TaskCompleted", "*")], None),
        ("Subagent spawned",      [("SubagentStart", "*"), ("subagentStart", "*")], None),
        ("Subagent completed",    [("subagent_completed", "*")], None),
        ("Subagent type",         [("SubagentStart", "agent_type"), ("SubagentStop", "agent_type")], None),
        ("Subagent transcript",   [("SubagentStop", "agent_transcript_path")], None),
        ("Subagent duration",     [], "start and stop timestamps per agent_id"),
        ("Delegation depth",      [], "nesting of agent_id"),
        ("Context compaction fired", [("PreCompact", "*"), ("preCompact", "*")], None),
        ("Background tasks running", [("Stop", "background_tasks")], None),
        ("Scheduled jobs on session", [("Stop", "session_crons")], None),
        ("Instructions / rules loaded", [("InstructionsLoaded", "*")], None),
        ("MCP server connection", [("mcp_server_connection", "*")], None),
        ("Session count", [("claude_code.session.count", "*")], None),
        ("Our own collectors executing", [("hook_execution_complete", "*")], None),
        ("Collector registration", [("hook_registered", "*")], None),
    ]),
    ("Tool Calls", [
        ("Tool name",             [("PreToolUse", "tool_name"), ("preToolUse", "tool_name")], None),
        ("Full tool arguments",   [("PreToolUse", "tool_input"), ("preToolUse", "tool_input")], None),
        ("Tool description",      [("PreToolUse", "tool_input")], "tool_input.description"),
        ("Full tool result",      [("PostToolUse", "tool_response"), ("postToolUse", "tool_output")], None),
        ("Tool duration",         [("PostToolUse", "duration_ms"), ("postToolUse", "duration")], None),
        ("Tool success or failure", [("PostToolUse", "tool_response"), ("postToolUse", "tool_output")], "exit code inside tool_output"),
        ("Tool interrupted",      [("PostToolUse", "tool_response")], "tool_response.interrupted"),
        ("Tool error text",       [("PostToolUse", "tool_response")], "tool_response.stderr"),
        ("Tools called in a parallel batch", [("PostToolBatch", "tool_calls")], None),
        ("Batch size",            [("PostToolBatch", "tool_calls")], "len of tool_calls"),
        ("Built-in vs MCP tool",  [], "tool_name against the built-in set"),
        ("Tool sequence within a turn", [], "ordering within one prompt_id"),
        ("Repeated identical tool calls", [], "duplicate tool_input within a session"),
    ]),
    ("File Operations", [
        ("File path read",        [("PreToolUse", "tool_input"), ("beforeReadFile", "file_path")], "tool_input.file_path"),
        ("File path written",     [("PostToolUse", "tool_input"), ("afterFileEdit", "file_path")], "tool_input.file_path"),
        ("Full old content",      [("afterFileEdit", "edits")], "edits[].old_string"),
        ("Full new content",      [("afterFileEdit", "edits")], "edits[].new_string"),
        ("Characters added and removed", [("afterFileEdit", "edits")], "lengths of old and new"),
        ("File changed on disk",  [("FileChanged", "*")], "FS Watcher"),
        ("Lines of code added or removed", [("claude_code.lines_of_code.count", "*")], None),
        ("File is a config or secret file", [], "path pattern match"),
        ("File outside the workspace root", [], "path against cwd"),
    ]),
    ("Shell / Terminal", [
        ("Shell command string",  [("PreToolUse", "tool_input"), ("beforeShellExecution", "command")], None),
        ("stdout content",        [("PostToolUse", "tool_response"), ("afterShellExecution", "output")], None),
        ("stderr content",        [("PostToolUse", "tool_response")], "tool_response.stderr"),
        ("Exit code",             [("postToolUse", "tool_output")], "inside tool_output json"),
        ("Command ran sandboxed", [("beforeShellExecution", "sandbox")], None),
        ("Command timeout set",   [("preToolUse", "tool_input")], "tool_input.timeout"),
        ("Command duration",      [("PostToolUse", "duration_ms"), ("afterShellExecution", "duration")], None),
        ("Command hung while alive", [], "CLI Wrapper stall detection"),
        ("Risky command markers", [], "pattern match on command"),
    ]),
    ("Permission & Approval", [
        ("Permission requested",  [("PermissionRequest", "*")], None),
        ("Permission options offered", [("PermissionRequest", "permission_suggestions")], None),
        ("Permission prompt shown to user", [("Notification", "notification_type")], None),
        ("Permission granted",    [("tool_decision", "*")], "PermissionRequest then PreToolUse in same prompt_id"),
        ("Permission denied",     [("PermissionDenied", "*"), ("tool_decision", "decision")], "PermissionRequest with no matching PreToolUse"),
        ("Denial reason typed by the human", [("PermissionDenied", "reason")], None),
        ("Approval scope once vs always", [("*", "permission_mode")], None),
        ("Permission mode changed", [], "change of permission_mode across events"),
    ]),
    ("Human Intervention", [
        ("Session abandoned mid-task", [("SessionEnd", "reason")], "reason plus incomplete turn"),
        ("Edit accepted or rejected", [("claude_code.code_edit_tool.decision", "*")], None),
        ("Undo after an agent edit", [], "FS Watcher content reversion"),
        ("Manual edit after an agent edit", [], "FS Watcher change with no tool event"),
        ("Rapid interrupts / rage quit", [], "CLI Wrapper interrupt events"),
    ]),
]

BUILTIN_EVENTS_BY_TOOL = {
    "claude-code": {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
                    "PostToolUse", "PostToolBatch", "PermissionRequest", "Notification",
                    "MessageDisplay", "Stop", "SubagentStart", "SubagentStop"},
}


def load_raw(dirs, tool_filter=None):
    """Return {tool: {event: {field: example_value}}} from raw payloads."""
    out = defaultdict(lambda: defaultdict(dict))
    counts = defaultdict(lambda: defaultdict(int))
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*RAW.jsonl"), recursive=True):
            base = os.path.basename(path).lower()
            tool = "cursor" if "cursor" in base else "claude-code"
            if tool_filter and tool != tool_filter:
                continue
            for line in open(path):
                try:
                    p = json.loads(line).get("payload", {})
                except Exception:
                    continue
                ev = p.get("hook_event_name", "?")
                counts[tool][ev] += 1
                for k, v in p.items():
                    if k not in out[tool][ev]:
                        out[tool][ev][k] = v
    return out, counts


def load_otel(dirs, tool_filter=None):
    """Return {tool: {event_or_metric: {attr: example}}} from OTLP json."""
    out = defaultdict(lambda: defaultdict(dict))
    counts = defaultdict(lambda: defaultdict(int))

    def unwrap(v):
        if not isinstance(v, dict):
            return v
        for k in ("stringValue", "boolValue", "intValue", "doubleValue"):
            if k in v:
                return v[k]
        return v

    def attrs(lst):
        return {a["key"]: unwrap(a.get("value")) for a in (lst or [])}

    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
            try:
                head = open(path).read(400)
            except Exception:
                continue
            if "resourceLogs" not in head and "resourceMetrics" not in head:
                continue
            for line in open(path):
                try:
                    batch = json.loads(line)
                except Exception:
                    continue
                for rl in batch.get("resourceLogs", []):
                    res = attrs(rl.get("resource", {}).get("attributes"))
                    tool = res.get("service.name", "claude-code")
                    if tool_filter and tool_filter not in tool:
                        continue
                    for sl in rl.get("scopeLogs", []):
                        for rec in sl.get("logRecords", []):
                            a = attrs(rec.get("attributes"))
                            name = a.get("event.name", "log")
                            counts[tool][name] += 1
                            merged = dict(res); merged.update(a)
                            for k, v in merged.items():
                                out[tool][name].setdefault(k, v)
                for rm in batch.get("resourceMetrics", []):
                    res = attrs(rm.get("resource", {}).get("attributes"))
                    tool = res.get("service.name", "claude-code")
                    if tool_filter and tool_filter not in tool:
                        continue
                    for sm in rm.get("scopeMetrics", []):
                        for m in sm.get("metrics", []):
                            name = m.get("name", "metric")
                            pts = (m.get("sum") or m.get("gauge")
                                   or m.get("histogram") or {}).get("dataPoints", [])
                            counts[tool][name] += len(pts) or 1
                            for p in pts:
                                a = attrs(p.get("attributes"))
                                merged = dict(res); merged.update(a)
                                for k, v in merged.items():
                                    out[tool][name].setdefault(k, v)
    return out, counts


def evidence_for(observable, proofs, derive, events):
    """Decide the status of one observable from what was really captured."""
    for ev, field in proofs:
        if ev != "*" and ev not in events and ("[OTel] " + ev) in events:
            ev = "[OTel] " + ev
        if ev == "*":
            for real_ev, fields in events.items():
                if field in fields:
                    return "CONFIRMED", f"{real_ev}.{field}"
        elif ev in events:
            if field == "*":
                return "CONFIRMED", f"{ev} fired"
            if field in events[ev]:
                return "CONFIRMED", f"{ev}.{field}"
    if derive:
        return "DERIVE", derive
    if proofs:
        wanted = {e for e, _ in proofs if e != "*"}
        if wanted:
            return "NOT SEEN", "expected on " + ", ".join(sorted(wanted))
    return "NOT SEEN", ""


def main():
    argv = sys.argv[1:]
    md = "--md" in argv
    tool_filter = None
    skip = set()
    if "--tool" in argv:
        i = argv.index("--tool")
        if i + 1 < len(argv):
            tool_filter = argv[i + 1]
            skip.add(i + 1)
    args = [a for j, a in enumerate(argv)
            if not a.startswith("--") and j not in skip]
    dirs = args or DEFAULT_DIRS

    raw, counts = load_raw(dirs, tool_filter)
    otel, ocounts = load_otel(dirs, tool_filter)

    # fold OTel under the same tool key so cells can say Hooks, OTel or both
    for tool, evs in otel.items():
        key = "claude-code" if "claude" in tool else tool
        for ev, fields in evs.items():
            raw[key]["[OTel] " + ev].update(fields)
            counts[key]["[OTel] " + ev] += ocounts[tool][ev]

    if not raw:
        print("No raw payload files found. Looked for *RAW.jsonl in:")
        for d in dirs:
            print("   " + d)
        print("\nInstall the hooks, use the tool, then run this again.")
        return 1

    for tool, events in sorted(raw.items()):
        total = sum(counts[tool].values())
        print(f"\n{'#' if md else ''} {tool}")
        print(f"\n{total} payloads, {len(events)} event types fired: "
              f"{', '.join(sorted(events))}\n")

        confirmed = derived = missing = 0
        for section, items in SPEC:
            print(f"\n{'##' if md else ''} {section}\n")
            if md:
                print("| Observable | Status | Evidence |")
                print("|---|---|---|")
            for name, proofs, derive in items:
                status, ev = evidence_for(name, proofs, derive, events)
                if status == "CONFIRMED":
                    ev = ("OTel: " + ev.replace("[OTel] ", "")) if ev.startswith("[OTel]") \
                         else ("Hooks: " + ev)
                if status == "CONFIRMED":
                    confirmed += 1
                elif status == "DERIVE":
                    derived += 1
                else:
                    missing += 1
                if md:
                    print(f"| {name} | {status} | {ev} |")
                else:
                    print(f"  {name:<38} {status:<10} {ev}")

        print(f"\n{'---' if md else ''}")
        print(f"confirmed from real payloads: {confirmed}")
        print(f"derivable from what we have:  {derived}")
        print(f"not seen in this data:        {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
