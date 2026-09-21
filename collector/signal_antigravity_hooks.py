#!/usr/bin/env python3
"""
Signal Collect — local hooks, Antigravity (collector 3c of 6)

Antigravity exposes five lifecycle hooks through a hooks.json placed in the
customisation root, by default .agents/hooks.json in the workspace. Two of the
five are tool-scoped and take a matcher wrapper; three fire around the model
call and take a flat handler list. The format differs from both Claude Code
and Cursor, which is the third distinct hook vocabulary in this study.

    python3 signal_antigravity_hooks.py --install                # this workspace
    python3 signal_antigravity_hooks.py --install --dir ~/bench-antigravity
    python3 signal_antigravity_hooks.py --report

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
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR",
                             os.path.join(HOME, ".signal", "sessions"))
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))
DENY = [s.strip().lower() for s in os.environ.get("SIGNAL_DENY", "").split(",")
        if s.strip()]

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key")
SECRET_SHAPES = ("sk-", "ghp_", "aws_secret", "-----begin", "api_key=",
                 "password=", "xoxb-")
DANGEROUS = ("rm -rf", "curl ", "wget ", "chmod 777", "sudo ",
             "git push --force", "drop table", "mkfs", "dd if=")

# Two of the five take a matcher and wrap handlers in a group; three take a
# flat list. Registering the wrong shape means the hook is accepted and never
# runs, which is the failure mode we already met on another tool.
GROUPED_EVENTS = ["PreToolUse", "PostToolUse"]
FLAT_EVENTS = ["PreInvocation", "PostInvocation", "Stop"]
ALL_EVENTS = GROUPED_EVENTS + FLAT_EVENTS

EVENT_MAP = {
    "PreToolUse":     "tool_request",
    "PostToolUse":    "tool_result",
    "PreInvocation":  "model_request",
    "PostInvocation":  "agent_response",
    "Stop":           "session_end",
}
BLOCKABLE = {"PreToolUse", "PreInvocation"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def tier(text):
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


def write_raw(payload):
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    with open(os.path.join(SESSION_DIR, f"{day}-antigravity-RAW.jsonl"), "a") as f:
        f.write(json.dumps({"received_at": now_iso(), "payload": payload},
                           default=str) + "\n")


def write_row(row, session):
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    name = f"{day}-antigravity-{(session or 'nosession')[:12]}.jsonl"
    with open(os.path.join(SESSION_DIR, name), "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def build_row(p, interaction_type, **extra):
    ev = p.get("hook_event_name", "")
    roots = p.get("workspacePaths") or p.get("workspace_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    row = {
        "observable_id": uuid.uuid4().hex,
        "timestamp": now_iso(),
        "agent_id": p.get("conversationId") or p.get("session_id") or "",
        "generation_id": (f"step-{p['stepIdx']}"
                          if p.get("stepIdx") is not None else ""),
        "agent_type": "coding_agent",
        "agent_tool": "antigravity",
        "operator": OPERATOR,
        "company": "real_fleet",
        "surface": "cli",
        "collector": "local_hooks",
        "interaction_type": interaction_type,
        "protocol": "hook/stdio",
        "connector": "",
        "permission_status": "allowed",
        "latency_ms": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
        "model": p.get("modelName") or "",
        "sensitivity_tier": 1,
        "cascade_depth": 0,
        "violation_count": 0,
        "escalation_flag": False,
        "success": True,
        "error_type": "",
        "workspace": roots[0] if roots else "",
        "hook_event": ev,
        "blockable": ev in BLOCKABLE,
        "transcript_path": p.get("transcriptPath") or "",
        "artifact_dir": p.get("artifactDirectoryPath") or "",
        "detail": {},
    }
    row.update(extra)
    return row


def handle(p):
    ev = p.get("hook_event_name", "")
    kind = EVENT_MAP.get(ev, "other")
    rows, decision = [], {}

    tc = p.get("toolCall") or {}
    tool_name = tc.get("name") or tc.get("toolName") or tc.get("tool") or ""
    args = tc.get("args") or tc.get("arguments") or tc.get("input") or {}
    result = tc.get("result") or tc.get("output") or tc.get("toolResult")
    cmd = args.get("command") or args.get("CommandLine") or "" \
        if isinstance(args, dict) else ""
    path = (args.get("file_path") or args.get("path") or args.get("TargetFile")
            or "") if isinstance(args, dict) else ""

    if ev in ("PreToolUse", "PostToolUse"):
        markers = risk_markers(cmd)
        row = build_row(p, kind,
                        connector={"run_command": "shell",
                                   "view_file": "filesystem",
                                   "edit_file": "filesystem"}.get(tool_name, "tool"),
                        sensitivity_tier=max(tier(json.dumps(args)),
                                             tier(path),
                                             2 if markers else 1),
                        detail={"tool": tool_name,
                                "arguments": args,
                                "command": cmd,
                                "file_path": path,
                                "risk_markers": markers,
                                "result": (result if isinstance(result, (str, dict))
                                           else None),
                                "step_index": p.get("stepIdx"),
                                "invocation": p.get("invocationNum")})
        if ev == "PostToolUse":
            err = p.get("error") or (result or {}).get("error") \
                if isinstance(result, dict) else p.get("error")
            if err:
                row["success"] = False
                row["error_type"] = str(err)[:200]
        rows.append(row)

        hit = deny_match(f"{tool_name} {json.dumps(args)}")
        if ev == "PreToolUse" and hit:
            row["permission_status"] = "denied"
            row["violation_count"] = 1
            row["escalation_flag"] = True
            row["detail"]["matched_rule"] = hit
            decision = {"decision": "block",
                        "reason": f"Blocked by Signal policy: '{hit}'"}

    elif ev in ("PreInvocation", "PostInvocation"):
        text = p.get("text") or p.get("prompt") or ""
        rows.append(build_row(p, kind,
                              connector="chat",
                              sensitivity_tier=secret_in_text(text),
                              detail={"step_index": p.get("stepIdx"),
                                      "invocation": p.get("invocationNum"),
                                      "length": len(text),
                                      "text": text[:4000]}))

    elif ev == "Stop":
        rows.append(build_row(p, kind,
                              detail={"reason": p.get("terminationReason") or "",
                                      "step_index": p.get("stepIdx"),
                                      "invocation": p.get("invocationNum")}))
    else:
        rows.append(build_row(p, "other",
                              detail={k: v for k, v in p.items()
                                      if not isinstance(v, (dict, list))}))
    return rows, decision


def install(workspace):
    """Write .agents/hooks.json. Grouped events need a matcher, flat ones do not."""
    root = os.path.join(os.path.abspath(workspace), ".agents")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "hooks.json")
    me = os.path.abspath(__file__)
    cmd = f"{sys.executable} {me}"

    cfg = {}
    if os.path.exists(path):
        try:
            cfg = json.load(open(path))
        except Exception:
            cfg = {}

    spec = {"enabled": True}
    for ev in GROUPED_EVENTS:
        spec[ev] = [{"matcher": "*",
                     "hooks": [{"type": "command", "command": cmd,
                                "timeout": 10}]}]
    for ev in FLAT_EVENTS:
        spec[ev] = [{"type": "command", "command": cmd, "timeout": 10}]

    cfg["signal-collect"] = spec
    json.dump(cfg, open(path, "w"), indent=2)

    print(f"Registered {len(ALL_EVENTS)} hook events in {path}")
    print(f"  grouped, with matcher: {', '.join(GROUPED_EVENTS)}")
    print(f"  flat:                  {', '.join(FLAT_EVENTS)}")
    print(f"  interpreter:           {sys.executable}")
    print(f"Rows will be written to {SESSION_DIR}")
    return 0


def report():
    import glob
    from collections import defaultdict
    files = sorted(glob.glob(os.path.join(SESSION_DIR,
                                          "*antigravity-RAW.jsonl")))
    if not files:
        print(f"No raw payloads yet in {SESSION_DIR}")
        return 0
    events, fields, samples, total = defaultdict(int), defaultdict(set), {}, 0
    for path in files:
        for line in open(path, errors="replace"):
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
    for name in sorted(events, key=lambda n: -events[name]):
        print(f"{name:<20} {events[name]:>5}   "
              f"{', '.join(sorted(fields[name] - {'hook_event_name'}))}")
    missing = [e for e in ALL_EVENTS if e not in events]
    print("\nRegistered but never fired: " +
          (", ".join(missing) if missing else "none"))
    for name in sorted(samples):
        body = json.dumps(samples[name], indent=2)
        print(f"\n--- {name} ---\n{body[:700]}")
    return 0


def main():
    if "--report" in sys.argv:
        return report()
    if "--install" in sys.argv:
        ws = os.getcwd()
        if "--dir" in sys.argv:
            i = sys.argv.index("--dir")
            if i + 1 < len(sys.argv):
                ws = os.path.expanduser(sys.argv[i + 1])
        return install(ws)

    raw = sys.stdin.read().strip()
    if not raw:
        print("{}")
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("{}")
        return 0

    decision = {}
    try:
        write_raw(payload)
        rows, decision = handle(payload)
        for row in rows:
            write_row(row, payload.get("conversationId", ""))
    except Exception as e:
        sys.stderr.write(f"signal-hooks: {e}\n")
        decision = {}
    print(json.dumps(decision) if decision else "{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
