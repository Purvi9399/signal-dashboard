#!/usr/bin/env python3
"""
Signal Collect — MCP proxy (collector 2 of 6)

Sits between a coding agent and one of its MCP servers. The agent thinks it
is talking to the real server. The real server thinks it is talking to the
agent. Every message passes through us, gets recorded, and can be refused.

MCP over stdio is newline-delimited JSON-RPC, so the proxy is: read a line,
write it down, pass it on, and do the same in the other direction.

Use it by changing one line in the agent's MCP config. Instead of:

    "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]

write:

    "command": "python3",
    "args": ["signal_mcp_proxy.py", "--", "npx", "-y", "@modelcontextprotocol/server-github"]

Env:
    SIGNAL_SESSION_DIR   where to write            (default ./signal_sessions)
    SIGNAL_MCP_NAME      label for this server     (default: the binary name)
    SIGNAL_DENY          comma-separated substrings that cause a refusal
                         e.g. SIGNAL_DENY=".env,id_rsa,credentials"
    SIGNAL_OPERATOR      who is running it         (default $USER)
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR", "signal_sessions")
MCP_NAME = os.environ.get("SIGNAL_MCP_NAME", "")
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))
DENY = [s.strip().lower() for s in os.environ.get("SIGNAL_DENY", "").split(",") if s.strip()]

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password", "token")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class ProxyRecorder:
    def __init__(self, server_name, argv):
        os.makedirs(SESSION_DIR, exist_ok=True)
        self.session_id = uuid.uuid4().hex[:16]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.base = os.path.join(
            SESSION_DIR, f"{stamp}-mcp-{server_name}-{self.session_id[:8]}")
        self.rows = open(self.base + ".jsonl", "a")
        self.wire = open(self.base + ".wire.jsonl", "a")
        self.lock = threading.Lock()

        self.server_name = server_name
        self.argv = argv
        self.pending = {}          # request id -> (method, tool, sent_at)
        self.row_count = 0
        self.calls = 0
        self.blocked = 0

    def row(self, interaction_type, **extra):
        r = {
            "observable_id": uuid.uuid4().hex,
            "timestamp": now_iso(),
            "agent_id": self.session_id,
            "agent_type": "coding_agent",
            "agent_tool": os.environ.get("SIGNAL_TOOL", "unknown"),
            "operator": OPERATOR,
            "company": "real_fleet",
            "surface": "mcp",
            "collector": "mcp_proxy",
            "interaction_type": interaction_type,
            "protocol": "jsonrpc/stdio",
            "connector": self.server_name,
            "permission_status": "allowed",
            "latency_ms": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "sensitivity_tier": 1,
            "cascade_depth": 0,
            "violation_count": 0,
            "escalation_flag": False,
            "success": True,
            "error_type": "",
            "detail": {},
        }
        r.update(extra)
        with self.lock:
            self.rows.write(json.dumps(r, default=str) + "\n")
            self.rows.flush()
            self.row_count += 1
        return r

    def log_wire(self, direction, msg):
        with self.lock:
            self.wire.write(json.dumps(
                {"t": now_iso(), "dir": direction, "msg": msg}, default=str) + "\n")
            self.wire.flush()

    def close(self):
        self.rows.close()
        self.wire.close()


def sensitivity_of(text):
    low = (text or "").lower()
    if any(m in low for m in SENSITIVE):
        return 3
    if any(m in low for m in ("/etc/", "config", "prod", "billing")):
        return 2
    return 1


def should_deny(tool, args_text):
    if not DENY:
        return None
    low = (tool + " " + args_text).lower()
    for pattern in DENY:
        if pattern in low:
            return pattern
    return None


def main():
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: signal_mcp_proxy.py -- <server command> [args...]", file=sys.stderr)
        return 2

    server_name = MCP_NAME or os.path.basename(args[0])
    rec = ProxyRecorder(server_name, args)

    server = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=sys.stderr, bufsize=0)

    rec.row("mcp_session_start",
            detail={"server_command": " ".join(args), "cwd": os.getcwd(),
                    "deny_rules": DENY})

    def agent_to_server():
        """Requests travelling from the coding agent towards the real server."""
        for raw in sys.stdin.buffer:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                server.stdin.write(raw)
                continue

            rec.log_wire("agent->server", msg)
            method = msg.get("method", "")
            mid = msg.get("id")

            if method == "tools/call":
                params = msg.get("params", {}) or {}
                tool = params.get("name", "")
                call_args = params.get("arguments", {}) or {}
                args_text = json.dumps(call_args)
                rec.calls += 1

                denied = should_deny(tool, args_text)
                if denied:
                    rec.blocked += 1
                    rec.row("mcp_call_blocked",
                            permission_status="denied",
                            violation_count=1,
                            escalation_flag=True,
                            success=False,
                            error_type="blocked_by_policy",
                            sensitivity_tier=sensitivity_of(args_text),
                            detail={"tool": tool, "arguments": call_args,
                                    "matched_rule": denied})
                    # answer the agent ourselves; the real server never sees it
                    refusal = {"jsonrpc": "2.0", "id": mid,
                               "error": {"code": -32000,
                                         "message": f"Blocked by Signal policy: '{denied}'"}}
                    sys.stdout.write(json.dumps(refusal) + "\n")
                    sys.stdout.flush()
                    rec.log_wire("proxy->agent(blocked)", refusal)
                    continue

                rec.pending[mid] = (method, tool, time.time())
                rec.row("mcp_call",
                        sensitivity_tier=sensitivity_of(args_text),
                        detail={"tool": tool, "arguments": call_args,
                                "request_id": mid})
            elif method:
                rec.pending[mid] = (method, "", time.time())
                if method in ("tools/list", "initialize", "resources/list"):
                    rec.row("mcp_" + method.replace("/", "_"),
                            detail={"request_id": mid})

            server.stdin.write(raw if raw.endswith(b"\n") else raw + b"\n")
            server.stdin.flush()

        try:
            server.stdin.close()
        except Exception:
            pass

    def server_to_agent():
        """Responses travelling from the real server back to the agent."""
        for raw in server.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
                continue

            rec.log_wire("server->agent", msg)
            mid = msg.get("id")
            if mid in rec.pending:
                method, tool, sent = rec.pending.pop(mid)
                latency = int((time.time() - sent) * 1000)
                if method == "tools/call":
                    result = msg.get("result", {})
                    err = msg.get("error")
                    body = json.dumps(result)[:4000]
                    rec.row("mcp_result",
                            latency_ms=latency,
                            success=err is None,
                            error_type=(err or {}).get("message", "") if err else "",
                            sensitivity_tier=sensitivity_of(body),
                            detail={"tool": tool, "request_id": mid,
                                    "result_bytes": len(json.dumps(result)),
                                    "result": result})
                elif method == "tools/list":
                    tools = (msg.get("result") or {}).get("tools", [])
                    rec.row("mcp_tools_discovered",
                            latency_ms=latency,
                            detail={"count": len(tools),
                                    "tools": [t.get("name") for t in tools]})

            sys.stdout.buffer.write(raw if raw.endswith(b"\n") else raw + b"\n")
            sys.stdout.buffer.flush()

    t1 = threading.Thread(target=agent_to_server, daemon=True)
    t2 = threading.Thread(target=server_to_agent, daemon=True)
    t1.start(); t2.start()
    t1.join()
    server.wait()
    time.sleep(0.2)

    rec.row("mcp_session_end",
            detail={"tool_calls": rec.calls, "blocked": rec.blocked})
    rec.close()

    sys.stderr.write(
        f"\n[signal-mcp] server={server_name} calls={rec.calls} "
        f"blocked={rec.blocked} rows={rec.row_count} -> {rec.base}.jsonl\n")
    return server.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
