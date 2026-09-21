#!/usr/bin/env python3
"""
Signal Collect — test_runs bundle importer

Imports a structured multi-tool capture bundle into our raw session format,
so signal_curate.py reads it like any other capture.

The bundle is organised by tool, then by prompt, then by turn:

    test_runs/<tool>/<Sxx>/turn_<n>.meta.json          command, timing, exit code
                           turn_<n>.hooks_events.jsonl hook payloads
                           turn_<n>.otel_events.jsonl  captured OTLP posts
                           turn_<n>.mcp_events.jsonl   JSON-RPC on the wire
                           turn_<n>.transcript.jsonl   the tool's own record
                           turn_<n>.transcript.db      same, where SQLite
                           turn_<n>.json               the tool's stdout stream
                           turn_<n>.stderr.log

Every collector envelope differs from ours, and each tool differs again from
the others. This translates the envelope only. Nothing is invented, and where
the bundle carries no value the field stays absent.

    python3 import_test_runs.py <path-to-test_runs>
    python3 import_test_runs.py <path> --dry-run
    python3 import_test_runs.py <path> --operator malhar
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR",
                             os.path.join(HOME, ".signal", "sessions"))

TOOL_MAP = {"claude_code": "claude-code", "claude-code": "claude-code",
            "cursor": "cursor", "codex": "codex", "copilot": "copilot",
            "antigravity": "antigravity"}

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


def unwrap(v):
    """OTLP attribute values are tagged unions."""
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


class Writer:
    def __init__(self, operator, dry=False):
        self.operator = operator
        self.dry = dry
        self.stats = Counter()
        self.handles = {}
        if not dry:
            os.makedirs(SESSION_DIR, exist_ok=True)
        self.day = datetime.now().strftime("%Y%m%d")

    def _fh(self, name):
        if self.dry:
            return None
        if name not in self.handles:
            self.handles[name] = open(
                os.path.join(SESSION_DIR, f"{self.day}-{name}"), "a")
        return self.handles[name]

    def raw(self, tool, payload, when=None):
        """A vendor hook payload, written where our extractors expect it."""
        self.stats[f"{tool}:hook"] += 1
        if self.dry:
            return
        self._fh(f"{tool}-RAW.jsonl").write(json.dumps(
            {"received_at": when or now_iso(), "payload": payload},
            default=str) + "\n")

    def row(self, tool, interaction_type, when, collector, **extra):
        self.stats[f"{tool}:{collector}"] += 1
        if self.dry:
            return
        r = {
            "observable_id": uuid.uuid4().hex,
            "timestamp": when or now_iso(),
            "agent_id": extra.pop("session", ""),
            "agent_type": "coding_agent",
            "agent_tool": tool,
            "operator": self.operator,
            "company": "real_fleet",
            "surface": extra.pop("surface", "cli"),
            "collector": collector,
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
        self._fh(f"{tool}-import.jsonl").write(
            json.dumps(r, default=str) + "\n")

    def close(self):
        for h in self.handles.values():
            h.close()


# ---------------------------------------------------------------------------
# hooks. Every tool nests the event name differently.
# ---------------------------------------------------------------------------

def import_hooks(path, tool, w, meta):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = dict(rec.get("payload") or {})
        if not payload:
            continue

        # Claude Code and Codex name the event at the envelope level; Cursor
        # and Antigravity carry it inside the payload. Reading only one of
        # those is why an entire tool's hooks looked unrecognised.
        # Four tools, four conventions. Claude Code and Codex name the event
        # at the envelope level; Cursor and Antigravity nest it in the payload;
        # Copilot posts over HTTP with a camelCase hookName, and omits it
        # entirely on prompt submission, which has to be inferred from shape.
        ev = (rec.get("event_name") or payload.get("hook_event_name")
              or payload.get("event_name") or payload.get("hookName"))
        if not ev and payload.get("prompt") is not None:
            ev = "UserPromptSubmit"
        if not ev:
            w.stats[f"{tool}:hook_without_event"] += 1
            continue

        # normalise Copilot's camelCase onto the names our extractors read
        alias = {"permissionRequest": "PermissionRequest",
                 "preToolUse": "PreToolUse", "postToolUse": "PostToolUse",
                 "sessionStart": "SessionStart", "sessionEnd": "SessionEnd",
                 "userPromptSubmit": "UserPromptSubmit", "stop": "Stop"}
        payload["hook_event_name"] = alias.get(ev, ev)
        for camel, snake in (("sessionId", "session_id"),
                             ("toolName", "tool_name"),
                             ("toolInput", "tool_input"),
                             ("toolOutput", "tool_output"),
                             ("agentId", "agent_id"),
                             ("agentName", "agent_type"),
                             ("agentType", "agent_type"),
                             ("permissionSuggestions", "permission_suggestions"),
                             ("transcriptPath", "transcript_path")):
            if camel in payload and snake not in payload:
                payload[snake] = payload[camel]
        payload.setdefault("cwd", meta.get("cwd"))
        w.raw(tool, payload, rec.get("timestamp"))


# ---------------------------------------------------------------------------
# OTel. The bundle stores the raw OTLP post, not the decoded records.
# ---------------------------------------------------------------------------

OTEL_TYPE = {
    "user_prompt": "user_prompt", "assistant_response": "agent_response",
    "api_request": "model_request", "tool_result": "tool_result",
    "tool_decision": "permission_request",
}


def import_otel(path, tool, w):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = rec.get("body_json") or {}
        when = rec.get("timestamp")

        for key, holder, inner in (("resourceLogs", "scopeLogs", "logRecords"),
                                   ("resourceSpans", "scopeSpans", "spans"),
                                   ("resourceMetrics", "scopeMetrics", "metrics")):
            for rs in body.get(key, []):
                res = {a["key"]: unwrap(a.get("value"))
                       for a in rs.get("resource", {}).get("attributes", [])}
                for sc in rs.get(holder, []):
                    for item in sc.get(inner, []):
                        at = {a["key"]: unwrap(a.get("value"))
                              for a in item.get("attributes", [])}
                        merged = dict(res)
                        merged.update(at)
                        name = (at.get("event.name") or item.get("name") or "")
                        short = str(name).split(".")[-1]
                        w.row(tool, OTEL_TYPE.get(short, "other"), when, "otel",
                              session=(merged.get("session.id")
                                       or merged.get("conversation.id") or ""),
                              model=merged.get("model") or "",
                              tokens_total=(merged.get("input_tokens") or 0)
                                            + (merged.get("output_tokens") or 0),
                              detail={"event": name,
                                      "service": res.get("service.name"),
                                      "attributes": merged,
                                      "signal": key})


# ---------------------------------------------------------------------------
# MCP wire traffic
# ---------------------------------------------------------------------------

def import_mcp(path, tool, w):
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
            msg = json.loads(rec.get("raw") or "{}")
        except json.JSONDecodeError:
            continue
        when = rec.get("timestamp")
        method = msg.get("method")
        if method:
            it = ("mcp_tools_discovered" if "tools/list" in method
                  or "discover" in method else "mcp_call")
            params = msg.get("params") or {}
            w.row(tool, it, when, "mcp_proxy",
                  connector=(params.get("name") or "mcp"),
                  attribution="unknown",
                  sensitivity_tier=tier(json.dumps(params)),
                  detail={"method": method, "params": params,
                          "direction": rec.get("direction")})
        elif "result" in msg:
            res = msg.get("result") or {}
            tools = res.get("tools")
            w.row(tool, "mcp_result", when, "mcp_proxy",
                  attribution="unknown",
                  detail={"result_size": len(json.dumps(res)),
                          "tools": [t.get("name") for t in tools
                                    if isinstance(t, dict)] if tools else None})


# ---------------------------------------------------------------------------
# transcripts. Three layouts: Claude Code, Codex, and Cursor's SQLite.
# ---------------------------------------------------------------------------

def import_transcript(path, tool, w, session):
    tin = tout = cache = msgs = thinking = 0
    models, tools_used = set(), set()
    reasoning, texts = [], []
    lines = 0
    for line in open(path, errors="replace"):
        lines += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Codex: usage lives on an event_msg of type token_count
        if rec.get("type") == "event_msg":
            pay = rec.get("payload") or {}
            if pay.get("type") == "token_count":
                tu = (pay.get("info") or {}).get("last_token_usage") or {}
                if tu:
                    msgs += 1
                    tin += tu.get("input_tokens", 0) or 0
                    tout += tu.get("output_tokens", 0) or 0
                    cache += tu.get("cached_input_tokens", 0) or 0
                    if tu.get("reasoning_output_tokens"):
                        thinking += 1
            continue
        if rec.get("type") == "turn_context":
            m = (rec.get("payload") or {}).get("model")
            if isinstance(m, str):
                models.add(m)
            continue

        # Claude Code: usage on message
        msg = rec.get("message") or {}
        u = msg.get("usage") or rec.get("usage") or {}
        if u:
            msgs += 1
            tin += u.get("input_tokens", 0) or 0
            tout += u.get("output_tokens", 0) or 0
            cache += u.get("cache_read_input_tokens", 0) or 0
        if isinstance(msg.get("model"), str):
            models.add(msg["model"])
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "thinking":
                    thinking += 1
                    if b.get("thinking"):
                        reasoning.append(b["thinking"])
                if b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
                if b.get("type") == "tool_use" and b.get("name"):
                    tools_used.add(b["name"])

    w.row(tool, "transcript_appended", None, "fs_watcher",
          session=session, tokens_total=tin + tout,
          model=", ".join(sorted(models)), confidence="parsed",
          attribution="unknown",
          detail={"path": path, "lines": lines,
                  "messages_with_usage": msgs,
                  "input_tokens": tin, "output_tokens": tout,
                  "cache_read_tokens": cache,
                  "thinking_blocks": thinking,
                  "reasoning_text": "\n\n".join(reasoning)[:8000] or None,
                  "response_text": "\n\n".join(texts)[:8000] or None,
                  "models": sorted(models),
                  "tools_used": sorted(tools_used)})


def import_transcript_db(path, tool, w, session):
    """Cursor keeps its session record in SQLite rather than JSONL."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "select name from sqlite_master where type='table'")]
        summary = {}
        for t in tables:
            try:
                n = cur.execute(f"select count(*) from '{t}'").fetchone()[0]
                cols = [d[1] for d in cur.execute(f"pragma table_info('{t}')")]
                summary[t] = {"rows": n, "columns": cols}
            except sqlite3.Error:
                continue
        con.close()
    except sqlite3.Error as e:
        w.stats[f"{tool}:transcript_db_error"] += 1
        return

    w.row(tool, "transcript_appended", None, "fs_watcher",
          session=session, confidence="parsed", attribution="unknown",
          detail={"path": path, "format": "sqlite",
                  "tables": summary,
                  "table_count": len(summary),
                  "total_rows": sum(v["rows"] for v in summary.values())})


# ---------------------------------------------------------------------------
# the session envelope: command, timing, exit code
# ---------------------------------------------------------------------------

def import_meta(meta, tool, w, prompt_id, turn):
    cmd = " ".join(meta.get("command") or [])
    sess = meta.get("new_session_id") or meta.get("resume_id_used") or ""
    dur = meta.get("duration_s")
    w.row(tool, "session_start", meta.get("start_time"), "cli_wrapper",
          session=sess, workspace=meta.get("cwd", ""),
          detail={"prompt_id": prompt_id, "turn": turn,
                  "command": cmd, "restricted": meta.get("restricted"),
                  "risk_markers": markers(cmd) or None})
    w.row(tool, "session_end", meta.get("end_time"), "cli_wrapper",
          session=sess, workspace=meta.get("cwd", ""),
          latency_ms=int(dur * 1000) if dur else 0,
          success=(meta.get("exit_code") == 0),
          error_type="" if meta.get("exit_code") == 0
                     else f"exit_{meta.get('exit_code')}",
          detail={"prompt_id": prompt_id, "turn": turn,
                  "exit_code": meta.get("exit_code"),
                  "duration_s": dur,
                  "transcript_captured": meta.get("transcript_captured")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--operator", default="second_operator")
    a = ap.parse_args()

    base = os.path.abspath(os.path.expanduser(a.bundle))
    if not os.path.isdir(base):
        sys.exit(f"not a directory: {base}")
    if os.path.isdir(os.path.join(base, "test_runs")):
        base = os.path.join(base, "test_runs")

    w = Writer(a.operator, a.dry_run)
    print(f"\nreading {base}")
    if a.dry_run:
        print("DRY RUN, nothing will be written")
    print()

    sessions = 0
    for tool_dir in sorted(os.listdir(base)):
        tool = TOOL_MAP.get(tool_dir)
        if not tool:
            continue
        for prompt_dir in sorted(os.listdir(os.path.join(base, tool_dir))):
            pdir = os.path.join(base, tool_dir, prompt_dir)
            if not os.path.isdir(pdir):
                continue
            for meta_path in sorted(glob.glob(os.path.join(pdir, "turn_*.meta.json"))):
                stem = meta_path[:-len(".meta.json")]
                turn = os.path.basename(stem).replace("turn_", "")
                try:
                    meta = json.load(open(meta_path))
                except Exception:
                    continue
                sessions += 1
                session_id = meta.get("new_session_id") or f"{tool}:{prompt_dir}:{turn}"

                import_meta(meta, tool, w, prompt_dir, turn)

                p = f"{stem}.hooks_events.jsonl"
                if os.path.exists(p):
                    import_hooks(p, tool, w, meta)
                p = f"{stem}.otel_events.jsonl"
                if os.path.exists(p):
                    import_otel(p, tool, w)
                p = f"{stem}.mcp_events.jsonl"
                if os.path.exists(p):
                    import_mcp(p, tool, w)
                p = f"{stem}.transcript.jsonl"
                if os.path.exists(p):
                    import_transcript(p, tool, w, session_id)
                p = f"{stem}.transcript.db"
                if os.path.exists(p):
                    import_transcript_db(p, tool, w, session_id)

    w.close()
    print(f"{'source':<40} {'events':>9}")
    print("-" * 50)
    for k, v in sorted(w.stats.items()):
        print(f"{k:<40} {v:>9}")
    print(f"\n{sessions} turns across "
          f"{len({k.split(':')[0] for k in w.stats})} tools")
    if not a.dry_run:
        print(f"written to {SESSION_DIR}")
        print("\nnow run:  python3 signal_curate.py --stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
