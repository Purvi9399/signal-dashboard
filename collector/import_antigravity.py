#!/usr/bin/env python3
"""
Signal Collect — Antigravity capture importer

Imports an Antigravity live-capture bundle produced by a different collector
implementation into our raw session format, so signal_curate.py can read it
like any other capture.

The bundle uses a different envelope from ours. Hooks carry an event_type plus
a raw_stdin string holding the real payload; the other collectors wrap theirs
in collector_id / agent / raw_payload. This translates the envelope only. It
does not invent fields, and anything the bundle did not carry stays absent.

    python3 import_antigravity.py <path-to-unzipped-bundle>
    python3 import_antigravity.py <path> --dry-run

Writes into SIGNAL_SESSION_DIR (default ~/.signal/sessions) as:
    <date>-antigravity-RAW.jsonl        hook payloads
    <date>-antigravity-import.jsonl     wrapper, watcher and proxy rows
"""

import argparse
import glob
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR",
                             os.path.join(HOME, ".signal", "sessions"))

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key")
DANGEROUS = ("rm -rf", "curl ", "wget ", "chmod 777", "sudo ",
             "git push --force", "drop table", "mkfs", "dd if=")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def row(interaction_type, when, **extra):
    r = {
        "observable_id": uuid.uuid4().hex,
        "timestamp": when or now_iso(),
        "agent_id": extra.pop("session", ""),
        "agent_type": "coding_agent",
        "agent_tool": "antigravity",
        "operator": extra.pop("operator", ""),
        "company": "real_fleet",
        "surface": extra.pop("surface", "cli"),
        "collector": extra.pop("collector", "unknown"),
        "interaction_type": interaction_type,
        "protocol": "import",
        "connector": extra.pop("connector", ""),
        "permission_status": "not_applicable",
        "latency_ms": extra.pop("latency_ms", 0),
        "tokens_total": extra.pop("tokens_total", 0),
        "cost_usd": 0.0,
        "model": extra.pop("model", ""),
        "sensitivity_tier": extra.pop("sensitivity_tier", 1),
        "cascade_depth": 0,
        "violation_count": 0,
        "escalation_flag": False,
        "success": extra.pop("success", True),
        "error_type": extra.pop("error_type", ""),
        "workspace": extra.pop("workspace", ""),
        "attribution": extra.pop("attribution", "agent"),
        "confidence": extra.pop("confidence", "reported"),
        "detail": extra.pop("detail", {}),
    }
    r.update(extra)
    return r


# ---------------------------------------------------------------------------
# hooks: event_type + raw_stdin holding the real payload
# ---------------------------------------------------------------------------

def import_hooks(path, out_raw, stats):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = rec.get("event_type")
        if not ev:
            stats["hook_lines_without_event"] += 1
            continue
        try:
            inner = json.loads(rec.get("raw_stdin") or "{}")
        except json.JSONDecodeError:
            inner = {}
        if not inner:
            continue

        # flatten into a payload shaped like the ones our extractors read
        payload = dict(inner)
        payload["hook_event_name"] = ev
        payload["session_id"] = inner.get("conversationId")
        payload["conversation_id"] = inner.get("conversationId")
        payload["model"] = inner.get("modelName")
        payload["transcript_path"] = inner.get("transcriptPath")
        payload["artifact_dir"] = inner.get("artifactDirectoryPath")
        roots = inner.get("workspacePaths") or []
        payload["workspace_roots"] = roots if isinstance(roots, list) else [roots]
        payload["cwd"] = rec.get("cwd")
        if inner.get("stepIdx") is not None:
            payload["generation_id"] = f"step-{inner['stepIdx']}"
            payload["loop_count"] = inner.get("stepIdx")
        if inner.get("invocationNum") is not None:
            payload["invocation_num"] = inner["invocationNum"]

        tc = inner.get("toolCall") or {}
        if isinstance(tc, dict) and tc:
            payload["tool_name"] = (tc.get("name") or tc.get("toolName")
                                    or tc.get("tool"))
            payload["tool_input"] = (tc.get("args") or tc.get("arguments")
                                     or tc.get("input") or {})
            for k in ("result", "output", "toolResult"):
                if tc.get(k) is not None:
                    payload["tool_output"] = tc[k]
                    break
            if tc.get("id"):
                payload["tool_use_id"] = tc["id"]
        if inner.get("error"):
            payload["error"] = inner["error"]
        if inner.get("terminationReason"):
            payload["status"] = inner["terminationReason"]

        out_raw.write(json.dumps(
            {"received_at": rec.get("timestamp") or now_iso(),
             "payload": payload}, default=str) + "\n")
        stats[f"hook:{ev}"] += 1


# ---------------------------------------------------------------------------
# file system watcher
# ---------------------------------------------------------------------------

CHANGE = {"created": "file_create", "modified": "file_modify",
          "deleted": "file_delete", "moved": "file_rename"}


def import_fs(path, out_rows, stats):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
            p = rec["raw_payload"]
        except (json.JSONDecodeError, KeyError):
            continue
        if p.get("is_directory"):
            stats["fs_directories_skipped"] += 1
            continue
        fp = p.get("path", "")
        kind = p.get("event_type", "modified")
        # the watcher cannot attribute a file change to a tool
        out_rows.write(json.dumps(row(
            CHANGE.get(kind, "file_modify"),
            rec.get("timestamp"),
            collector="fs_watcher",
            connector="filesystem",
            attribution="unknown",
            confidence="observed",
            sensitivity_tier=tier(fp),
            detail={"path": fp,
                    "abs_path": fp,
                    "extension": os.path.splitext(fp)[1],
                    "change": kind,
                    "size": p.get("size_bytes"),
                    "in_scratch": "/scratch/" in fp,
                    "in_brain": "/brain/" in fp}), default=str) + "\n")
        stats[f"fs:{kind}"] += 1


# ---------------------------------------------------------------------------
# CLI wrapper: pty chunks whose content carries agy stream-json
# ---------------------------------------------------------------------------

def import_wrapper(path, out_rows, stats):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
            p = rec["raw_payload"]
        except (json.JSONDecodeError, KeyError):
            continue
        when = rec.get("timestamp")
        content = p.get("content", "") or ""

        emitted = False
        for piece in content.split("\n"):
            piece = piece.strip()
            if not piece.startswith("{"):
                continue
            try:
                ev = json.loads(piece)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            sess = ev.get("conversation_id") or ""

            if kind == "init":
                init = ev.get("init") or {}
                out_rows.write(json.dumps(row(
                    "session_start", when, session=sess,
                    collector="cli_wrapper",
                    workspace=(init.get("cwd") or ""),
                    detail={"cwd": init.get("cwd"),
                            "tools_available": init.get("tools"),
                            "model": init.get("model")}), default=str) + "\n")
                stats["wrapper:init"] += 1
                emitted = True

            elif kind == "step_update":
                step = ev.get("step") or ev.get("step_update") or {}
                out_rows.write(json.dumps(row(
                    "agent_step", when, session=sess,
                    collector="cli_wrapper",
                    detail={"step_index": step.get("index") or ev.get("step_idx"),
                            "text": (json.dumps(step)[:1500]
                                     if step else piece[:1500])}),
                    default=str) + "\n")
                stats["wrapper:step_update"] += 1
                emitted = True

            elif kind == "result":
                # the payload nests everything under result, not at top level
                res = ev.get("result") if isinstance(ev.get("result"), dict) else ev
                usage = res.get("usage") or {}
                sess = res.get("conversation_id") or sess
                tin = usage.get("input_tokens") or 0
                tout = usage.get("output_tokens") or 0
                dur = res.get("duration_seconds")
                out_rows.write(json.dumps(row(
                    "turn_end", when, session=sess,
                    collector="cli_wrapper",
                    tokens_total=(tin + tout) or usage.get("total_tokens") or 0,
                    latency_ms=int(float(dur) * 1000) if dur else 0,
                    success=(str(res.get("status") or "ok").lower() in ("success", "ok")),
                    error_type=(res.get("error") or ""
                                if str(res.get("status") or "ok").lower() not in ("success", "ok")
                                else ""),
                    detail={"status": res.get("status"),
                            "error": res.get("error"),
                            "input_tokens": tin,
                            "output_tokens": tout,
                            "cache_read_tokens": usage.get("cache_read_tokens"),
                            "thinking_tokens": usage.get("thinking_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                            "duration_seconds": dur,
                            "num_turns": res.get("num_turns")}), default=str) + "\n")
                stats["wrapper:result"] += 1
                emitted = True

        if not emitted and content.strip():
            stats["wrapper:raw_chunk"] += 1


# ---------------------------------------------------------------------------
# MCP proxy: JSON-RPC on the wire
# ---------------------------------------------------------------------------

def import_mcp(path, out_rows, stats):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
            msg = json.loads(rec.get("raw") or "{}")
        except json.JSONDecodeError:
            continue
        when = rec.get("timestamp")
        method = msg.get("method")
        if method:
            it = ("mcp_tools_discovered"
                  if "discover" in method or "tools/list" in method
                  else "mcp_call")
            out_rows.write(json.dumps(row(
                it, when, collector="mcp_proxy", connector="antigravity-mcp",
                attribution="unknown",
                detail={"method": method,
                        "params": msg.get("params"),
                        "direction": rec.get("direction")}), default=str) + "\n")
            stats[f"mcp:{method}"] += 1
        elif "result" in msg:
            res = msg.get("result") or {}
            tools = res.get("tools") or res.get("servers")
            out_rows.write(json.dumps(row(
                "mcp_result", when, collector="mcp_proxy",
                connector="antigravity-mcp", attribution="unknown",
                detail={"result_size": len(json.dumps(res)),
                        "tools": [t.get("name") for t in tools
                                  if isinstance(t, dict)] if tools else None}),
                default=str) + "\n")
            stats["mcp:result"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", help="path to the unzipped capture bundle")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    base = a.bundle
    raw = os.path.join(base, "raw")
    if not os.path.isdir(raw):
        found = glob.glob(os.path.join(base, "*", "raw"))
        if not found:
            sys.exit(f"no raw/ directory under {base}")
        raw = found[0]
    print(f"\nreading {raw}\n")

    stats = Counter()
    os.makedirs(SESSION_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    raw_path = os.path.join(SESSION_DIR, f"{day}-antigravity-RAW.jsonl")
    row_path = os.path.join(SESSION_DIR, f"{day}-antigravity-import.jsonl")

    if a.dry_run:
        import io
        out_raw, out_rows = io.StringIO(), io.StringIO()
        print("DRY RUN, nothing will be written\n")
    else:
        out_raw, out_rows = open(raw_path, "a"), open(row_path, "a")

    for f in sorted(glob.glob(os.path.join(raw, "sliced", "hooks_*.jsonl"))):
        import_hooks(f, out_raw, stats)
    for f in sorted(glob.glob(os.path.join(raw, "sliced", "fs_watcher_*.jsonl"))
                    or glob.glob(os.path.join(raw, "fs_watcher_*.jsonl"))):
        import_fs(f, out_rows, stats)
    for f in sorted(glob.glob(os.path.join(raw, "cli_wrapper_prompt*.jsonl"))):
        import_wrapper(f, out_rows, stats)
    for f in sorted(glob.glob(os.path.join(raw, "sliced", "mcp_*.jsonl"))):
        import_mcp(f, out_rows, stats)

    if not a.dry_run:
        out_raw.close()
        out_rows.close()

    print(f"{'source':<34} {'events':>8}")
    print("-" * 44)
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"{k:<34} {v:>8}")

    total = sum(v for k, v in stats.items() if not k.endswith("skipped"))
    print(f"\n{total} events imported")
    if not a.dry_run:
        print(f"  hook payloads -> {raw_path}")
        print(f"  collector rows -> {row_path}")
        print("\nnow run:  python3 signal_curate.py --stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
