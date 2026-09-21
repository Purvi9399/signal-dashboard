#!/usr/bin/env python3
"""
Project Signal — Coding Agent Telemetry Adapter (v0.1)

Reads OTLP JSON batches written by the OpenTelemetry Collector's file
exporter (data/events.json), converts each Claude Code event into a
Signal observable row, and writes it to:

  - Redis  (hot stream, same list the COPS scripts read)   [optional]
  - Supabase (historical store)                             [optional]

If neither REDIS_URL nor SUPABASE_URL is set, rows are printed to stdout
and appended to observables_out.jsonl — so you can validate the mapping
locally before touching live infrastructure.

Usage:
  python3 adapter.py --file data/events.json            # one-shot backfill
  python3 adapter.py --file data/events.json --follow   # tail -f mode (live)

Env (all optional):
  REDIS_URL          e.g. redis://localhost:6379  (local test)
                     In production: redis://10.6.107.99:6379 (only reachable
                     from inside the VPC — deploy this adapter to Cloud Run
                     with the project-signal-connector, NOT from a laptop)
  REDIS_KEY          list key COPS reads (default: signal:observables)
  SUPABASE_URL       https://<project>.supabase.co
  SUPABASE_KEY       service-role key
  SUPABASE_TABLE     default: observables

Dependencies (only needed for the backends you enable):
  pip install redis supabase
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

REDIS_KEY = os.environ.get("REDIS_KEY", "signal:observables")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "observables")

# ---------------------------------------------------------------------------
# Backends (lazy, optional)
# ---------------------------------------------------------------------------

def get_redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis  # pip install redis
    return redis.from_url(url)


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        return None
    from supabase import create_client  # pip install supabase
    return create_client(url, key)


# ---------------------------------------------------------------------------
# OTLP parsing helpers
# ---------------------------------------------------------------------------

def attr_value(v):
    """Unwrap an OTLP AnyValue into a plain Python value."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [attr_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: attr_value(kv.get("value")) for kv in v["kvlistValue"].get("values", [])}
    return v


def attrs_to_dict(attr_list):
    return {a["key"]: attr_value(a.get("value")) for a in (attr_list or [])}


def iter_log_records(batch):
    """Yield (resource_attrs, record_attrs, record) for every log record in an OTLP batch."""
    for rl in batch.get("resourceLogs", []):
        res_attrs = attrs_to_dict(rl.get("resource", {}).get("attributes"))
        for sl in rl.get("scopeLogs", []):
            for rec in sl.get("logRecords", []):
                yield res_attrs, attrs_to_dict(rec.get("attributes")), rec


# ---------------------------------------------------------------------------
# Mapping: Claude Code event -> Signal observable row
# ---------------------------------------------------------------------------
# NOTE: The observable schema below covers the identity / interaction /
# permission / latency / cost / cascade / violation core of the 64-observable
# schema and fills the rest with defaults. Adjust FIELD NAMES to match
# signal_classifier.py's expected columns exactly — the classifier and COPS
# must be able to consume these rows without changes.

# Map Claude Code tool names to Signal interaction/connector semantics.
TOOL_INTERACTION = {
    "Bash": ("command_execution", "shell"),
    "Read": ("file_read", "filesystem"),
    "Write": ("file_write", "filesystem"),
    "Edit": ("file_write", "filesystem"),
    "Glob": ("file_search", "filesystem"),
    "Grep": ("file_search", "filesystem"),
    "WebFetch": ("network_request", "web"),
    "WebSearch": ("network_request", "web"),
    "Task": ("delegation", "subagent"),
    "Agent": ("delegation", "subagent"),
}

# Very first sensitivity-tier ruleset for real file paths / commands.
# Extend with your enterprise path -> tier mapping.
SENSITIVE_MARKERS = {
    3: (".env", "secrets", "credential", "id_rsa", ".pem", "token", "password"),
    2: ("/etc/", "config", ".ssh", "prod", "payment", "billing"),
}


def sensitivity_tier(text):
    t = (text or "").lower()
    for tier, markers in SENSITIVE_MARKERS.items():
        if any(m in t for m in markers):
            return tier
    return 1


def base_row(res, attrs):
    """Fields shared by every observable row."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        # identity
        "observable_id": str(uuid.uuid4()),
        "timestamp": attrs.get("event.timestamp") or now,
        "agent_id": attrs.get("session.id") or res.get("session.id") or "unknown-session",
        "agent_type": "coding_agent",
        "agent_tool": res.get("service.name", "claude-code"),
        "operator": attrs.get("user.email") or attrs.get("user.id") or "",
        "organization_id": attrs.get("organization.id", ""),
        "company": "real_fleet",           # distinguishes from the 5 sim companies
        "team_id": res.get("team.id", ""),
        "surface": res.get("service.name", ""),
        "prompt_id": attrs.get("prompt.id", ""),
        "source_event": attrs.get("event.name", ""),
        # defaults for schema completeness — classifier-safe null-ish values
        "interaction_type": "other",
        "protocol": "otel",
        "connector": "",
        "permission_status": "not_applicable",
        "latency_ms": 0,
        "tokens_total": 0,
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_usd": 0.0,
        "model": "",
        "pii_flag": False,
        "sensitivity_tier": 1,
        "cascade_depth": 0,
        "parent_agent_id": "",
        "violation_count": 0,
        "escalation_flag": False,
        "retry_state": "none",
        "recovery_state": "none",
        "success": True,
        "error_type": "",
        "detail": {},
        "ingested_at": now,
    }


def map_event(res, attrs):
    """Return an observable row for events we care about, else None."""
    name = attrs.get("event.name", "")
    # collector may prefix event names; normalize
    short = name.split(".")[-1] if name.startswith("claude_code.") else name

    if short == "tool_result":
        row = base_row(res, attrs)
        tool = attrs.get("tool_name", "")
        interaction, connector = TOOL_INTERACTION.get(tool, ("tool_call", "unknown"))
        # MCP tools carry their own server/tool names when tool details enabled
        if attrs.get("mcp_server_name"):
            interaction, connector = "mcp_call", attrs["mcp_server_name"]
        tool_input = attrs.get("tool_input") or attrs.get("tool_parameters") or ""
        if isinstance(tool_input, (dict, list)):
            tool_input = json.dumps(tool_input)
        row.update({
            "interaction_type": interaction,
            "connector": connector,
            "latency_ms": attrs.get("duration_ms", 0) or 0,
            "success": attrs.get("success") in (True, "true", "True"),
            "error_type": attrs.get("error_type", "") or "",
            "permission_status": attrs.get("decision_source", "") or "config",
            "sensitivity_tier": sensitivity_tier(tool_input),
            "cascade_depth": 1 if attrs.get("subagent_type") or attrs.get("agent_id") else 0,
            "parent_agent_id": attrs.get("agent_id", "") or "",
            "detail": {
                "tool_name": tool,
                "tool_use_id": attrs.get("tool_use_id", ""),
                "tool_input": tool_input[:2000],
                "input_size": attrs.get("tool_input_size_bytes", 0),
                "result_size": attrs.get("tool_result_size_bytes", 0),
            },
        })
        if not row["success"]:
            row["retry_state"] = "candidate"
        return row

    if short == "api_request":
        row = base_row(res, attrs)
        tin = attrs.get("input_tokens", 0) or 0
        tout = attrs.get("output_tokens", 0) or 0
        row.update({
            "interaction_type": "llm_request",
            "connector": "anthropic_api",
            "model": attrs.get("model", ""),
            "latency_ms": attrs.get("duration_ms", 0) or 0,
            "tokens_input": tin,
            "tokens_output": tout,
            "tokens_total": tin + tout,
            "cost_usd": float(attrs.get("cost_usd", 0) or 0),
            "cascade_depth": 1 if attrs.get("query_source") == "subagent" else 0,
            "detail": {
                "cache_read_tokens": attrs.get("cache_read_tokens", 0),
                "cache_creation_tokens": attrs.get("cache_creation_tokens", 0),
                "query_source": attrs.get("query_source", ""),
                "request_id": attrs.get("request_id", ""),
            },
        })
        return row

    if short == "tool_decision":
        row = base_row(res, attrs)
        decision = (attrs.get("decision") or "").lower()
        row.update({
            "interaction_type": "permission_decision",
            "connector": attrs.get("tool_name", ""),
            "permission_status": decision or "unknown",
            "violation_count": 1 if decision == "reject" else 0,
            "detail": {
                "tool_name": attrs.get("tool_name", ""),
                "tool_use_id": attrs.get("tool_use_id", ""),
                "source": attrs.get("source", ""),
            },
        })
        return row

    if short == "permission_mode_changed":
        row = base_row(res, attrs)
        row.update({
            "interaction_type": "permission_mode_change",
            "escalation_flag": True,
            "detail": {
                "from_mode": attrs.get("from_mode", ""),
                "to_mode": attrs.get("to_mode", ""),
                "trigger": attrs.get("trigger", ""),
            },
        })
        return row

    if short == "user_prompt":
        row = base_row(res, attrs)
        row.update({
            "interaction_type": "user_prompt",
            "detail": {"prompt_length": attrs.get("prompt_length", 0)},
        })
        return row

    return None  # ignore other events for now (add as needed)


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------

class Sinks:
    def __init__(self):
        self.r = get_redis()
        self.sb = get_supabase()
        self.local = open("observables_out.jsonl", "a")
        self.count = 0

    def write(self, row):
        payload = json.dumps(row, default=str)
        if self.r is not None:
            # LPUSH so COPS' "last 120s" window reads work as with the sim.
            # Adjust to XADD / your exact structure if the sim uses streams.
            self.r.lpush(REDIS_KEY, payload)
        if self.sb is not None:
            try:
                self.sb.table(SUPABASE_TABLE).insert(row).execute()
            except Exception as e:  # don't let historical writes kill the hot path
                print(f"[supabase] insert failed: {e}", file=sys.stderr)
        self.local.write(payload + "\n")
        self.local.flush()
        self.count += 1
        print(f"[{self.count}] {row['source_event']:>28} -> {row['interaction_type']:<22} "
              f"agent={row['agent_id'][:8]} lat={row['latency_ms']}ms "
              f"tok={row['tokens_total']} perm={row['permission_status']}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def process_line(line, sinks):
    line = line.strip()
    if not line:
        return
    try:
        batch = json.loads(line)
    except json.JSONDecodeError:
        return
    for res, attrs, _rec in iter_log_records(batch):
        row = map_event(res, attrs)
        if row is not None:
            sinks.write(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/events.json")
    ap.add_argument("--follow", action="store_true", help="keep tailing for new events")
    args = ap.parse_args()

    sinks = Sinks()
    with open(args.file) as f:
        for line in f:
            process_line(line, sinks)
        if args.follow:
            print("--- following for new events (Ctrl+C to stop) ---")
            while True:
                line = f.readline()
                if line:
                    process_line(line, sinks)
                else:
                    time.sleep(1)

    print(f"\nDone. {sinks.count} observable rows written "
          f"(redis={'on' if sinks.r else 'off'}, supabase={'on' if sinks.sb else 'off'}, "
          f"local=observables_out.jsonl)")


if __name__ == "__main__":
    main()
