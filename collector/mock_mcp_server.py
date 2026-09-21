#!/usr/bin/env python3
"""
A minimal MCP server, only for demonstrating the Signal MCP proxy.

Speaks the same newline-delimited JSON-RPC that real MCP servers speak,
and offers two tools: read_file and list_files. It stands in for something
like the GitHub or filesystem server so the demo needs no setup.
"""

import json
import os
import sys

TOOLS = [
    {"name": "read_file", "description": "Read a file from disk",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "list_files", "description": "List files in a directory",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
]


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def error(msg_id, message):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": message}}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue

    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        reply(mid, {"protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "demo-files", "version": "0.1"},
                    "capabilities": {"tools": {}}})
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        path = args.get("path", "")
        try:
            if name == "read_file":
                with open(path) as f:
                    text = f.read()[:2000]
                reply(mid, {"content": [{"type": "text", "text": text}]})
            elif name == "list_files":
                names = sorted(os.listdir(path or "."))[:40]
                reply(mid, {"content": [{"type": "text", "text": "\n".join(names)}]})
            else:
                error(mid, f"unknown tool: {name}")
        except Exception as e:
            error(mid, str(e))
    elif method == "shutdown":
        reply(mid, {})
        break
