#!/usr/bin/env python3
"""
Signal metrics

The one place a metric is calculated. Everything downstream, the dashboard
and every COP, reads what this writes and never recomputes it.

    observables  ->  signal_traces.build  ->  metrics per session  ->  metric_events

Each row in metric_events is one metric for one session: its value, the
threshold it was judged against, the state that produced, and references
back to the observables and trace steps behind it.

Two rules hold throughout.

  A metric whose detector could not have fired on this session is UNKNOWN,
  not NORMAL. Each metric names its basis: the evidence the detector needs.
  If a session has none of it and the count is zero, nothing was seen
  because nothing could be seen. A non-zero count is still evidence.

  Unusual is not prohibited. The anomaly metrics measure distance from
  what this estate normally does; they say nothing about permission.

The fourteen Insights checks are ported here so the dashboard can read
them instead of recomputing them in the browser. One is corrected:
repetition now compares arguments, which the browser version never
fetched, so it was really counting a tool name used twice in a turn.

    python3 signal_metrics.py --dry-run            compute and summarise
    python3 signal_metrics.py --session ID         one session, every metric
    python3 signal_metrics.py                      compute and write metric_events
"""

import argparse, json, os, sys, uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import signal_traces                                   # traces, not rebuilt here
from signal_paths import AGENTS

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")
NS = uuid.UUID("5e1a7c0e-0000-4000-8000-000000000001")

# signal_traces' columns plus what the metrics need. No content columns
# (prompt, response, stdout) are read; tool_arguments is needed to tell a
# true repeat from two different reads.
COLS = signal_traces.COLS + "," + ",".join([
    "tool_version", "model", "machine_id", "hostname", "permission_source",
    "is_lockfile", "is_dependency_manifest", "collectors_agree", "contributing",
    "ingested_at", "prompt_length",
])

# NOT_APPLICABLE: the condition the metric asks about never arose (no
# subagents, no commands). UNKNOWN: it may have arisen and we could not see.
STATES = ("NORMAL", "WARNING", "EXCEPTION", "CRITICAL", "UNKNOWN", "NOT_APPLICABLE")


def load_config(path=None):
    with open(path or os.path.join(HERE, "metrics_config.json")) as f:
        return json.load(f)


# ------------------------------------------------------------------ helpers

def has(v):
    return v not in (None, "", [], {})

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def nonzero_exit(r):
    c = r.get("exit_code")
    return has(c) and str(c) not in ("0", "0.0")

CALL_TYPES = {"tool_request", "tool_call", "mcp_call", "command", "shell_command"}
NOT_CALLS = {"tool_result", "permission_request", "mcp_result", "subagent_start", "subagent_stop"}

def calls(rows):
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r.get("occurred_at") or "", r.get("sequence_num") or 0)):
        if not has(r.get("tool_name")) or (r.get("observable_type") or "") in NOT_CALLS:
            continue
        uid = r.get("tool_use_id")
        if uid:
            if uid in seen:
                continue
            seen.add(uid)
        out.append(r)
    return out

def ref(kind, x):
    return f"{kind}:{x}" if has(x) else None

def refs(kind, items, key, cap=20):
    out = [ref(kind, i.get(key)) for i in items]
    return [x for x in out if x][:cap]


def judge(value, t):
    """State for a value against a threshold block. Above: higher is worse."""
    if value is None:
        return "UNKNOWN"
    up = t.get("direction", "above") == "above"
    worse = (lambda a, b: a >= b) if up else (lambda a, b: a <= b)
    for state in ("critical", "exception", "warning"):
        if state in t and worse(value, t[state]):
            return state.upper()
    return "NORMAL"


# ------------------------------------------------------------------ session context

class Session:
    """Everything one session's metrics are computed from, gathered once."""

    def __init__(self, sid, rows, trace, steps, effects):
        self.id = sid
        self.rows = rows
        self.trace = trace or {"lanes": {}}
        self.steps = steps
        self.effects = effects
        self.tool = next((r["tool"] for r in rows if r.get("tool") in AGENTS), None)
        self.collectors = {r.get("collector") for r in rows if r.get("collector")}
        for r in rows:
            for c in (r.get("contributing") or []):
                self.collectors.add(c)
        if effects:
            self.collectors.add("fs_watcher")
        self.workspace = next((r.get("workspace") for r in rows if r.get("workspace")), None)
        self.operator = next((r.get("operator_username") or r.get("operator_email")
                              for r in rows if r.get("operator_username") or r.get("operator_email")), None)
        times = sorted(r["occurred_at"] for r in rows if r.get("occurred_at"))
        self.start, self.end = (times[0], times[-1]) if times else (None, None)
        # one entry per call the agent made: the request row, or the first row
        # seen for a tool_use_id. Results and permission rows for the same call
        # are not separate actions (counting them was the old repetition bug).
        self.actions = calls(rows)
        self.approvals = [s for s in steps if s["step"] == "ASK"]
        self.tokens = sum(num(r.get("total_tokens")) for r in rows)

    def minutes(self):
        a, b = signal_traces.ts(self.start), signal_traces.ts(self.end)
        return (b - a).total_seconds() / 60 if a and b else 0.0

    def transitions(self):
        out = []
        for lane in self.trace["lanes"].values():
            seq = [s["step"] for s in lane]
            out += list(zip(seq, seq[1:]))
        return out


# ------------------------------------------------------------------ metric definitions
#
# Each metric: (id, name, compute, basis, required_fields, applies)
#   compute(s, estate, cfg) -> (value, features, evidence_refs, trace_id)
#   basis(s) -> True if the detector could have fired on this session
#
# required_fields documents the observables the metric reads, which is
# what Runtime Monitoring needs to explain an UNKNOWN.

def m_repetition(s, e, cfg):
    seen, hits = set(), []
    for r in s.actions:
        if not r.get("turn_id"):
            continue
        k = (r["turn_id"], r["tool_name"], json.dumps(r.get("tool_arguments"), sort_keys=True, default=str))
        if k in seen:
            hits.append(r)
        else:
            seen.add(k)
    tools = Counter(r["tool_name"] for r in hits)
    return len(hits), {"by_tool": dict(tools.most_common(5))}, refs("observable", hits, "observable_id"), None

def m_retry(s, e, cfg):
    failed, hits = set(), []
    for r in sorted((r for r in s.rows if has(r.get("command"))), key=lambda r: r.get("occurred_at") or ""):
        k = str(r["command"]).strip()
        if k in failed:
            hits.append(r)
        if nonzero_exit(r):
            failed.add(k)
    return len(hits), {"distinct_commands": len({str(r['command']).strip() for r in hits})}, \
        refs("observable", hits, "observable_id"), None

def m_stalled(s, e, cfg):
    hits = [r for r in s.rows if has(r.get("hung_seconds"))]
    return len(hits), {"max_hung_s": max((num(r["hung_seconds"]) for r in hits), default=0)}, \
        refs("observable", hits, "observable_id"), None

def m_outside(s, e, cfg):
    hits = [r for r in s.rows if r.get("outside_workspace")] + \
           [x for x in s.effects if x.get("outside_workspace")]
    return len(hits), {}, refs("observable", hits, "observable_id"), None

def m_sensitive_unasked(s, e, cfg):
    hits = [r for r in s.rows if num(r.get("sensitivity_tier")) >= 3]
    if s.approvals:
        return 0, {"approvals": len(s.approvals), "sensitive": len(hits)}, [], None
    feats = {"approvals": 0, "sensitive": len(hits)}
    if hits and not approvals_visible(s, cfg):
        feats["_state"] = "UNKNOWN"
        feats["reason"] = "sensitive access seen; this tool cannot show whether approval was asked"
    return len(hits), feats, refs("observable", hits, "observable_id"), None

def m_secrets(s, e, cfg):
    hits = [r for r in s.rows if r.get("secret_detected")] + \
           [x for x in s.effects if x.get("secret_detected")]
    return len(hits), {}, refs("observable", hits, "observable_id"), None

def m_blocked(s, e, cfg):
    hits = [r for r in s.rows if has(r.get("policy_rule_matched"))]
    return len(hits), {"rules": dict(Counter(r["policy_rule_matched"] for r in hits))}, \
        refs("observable", hits, "observable_id"), None

def m_no_oversight(s, e, cfg):
    n = len(s.actions)
    if not approvals_visible(s, cfg):
        return None, {"actions": n, "reason": "this tool cannot show approvals"}, [], None
    val = n if not s.approvals else 0
    return val, {"actions": n, "approvals": len(s.approvals)}, [], None

def m_reverted(s, e, cfg):
    hits = [r for r in s.rows if r.get("reverted_to_earlier")] + \
           [x for x in s.effects if x.get("reverted")]
    return len(hits), {}, refs("observable", hits, "observable_id"), None

def m_lockfile(s, e, cfg):
    files = s.rows + s.effects
    if any(r.get("is_lockfile") for r in files):
        return 0, {"lockfile_changed": True}, [], None
    hits = [r for r in files if r.get("is_dependency_manifest")]
    return len(hits), {"lockfile_changed": False}, refs("observable", hits, "observable_id"), None

def m_failed(s, e, cfg):
    hits = [r for r in s.rows if r.get("success") is False]
    rate = len(hits) / len(s.actions) if s.actions else 0
    return len(hits), {"failure_rate": round(rate, 3)}, refs("observable", hits, "observable_id"), None

def m_unattributed(s, e, cfg):
    hits = [x for x in s.effects if x.get("attributed_to") == "unaccounted"]
    return len(hits), {"effects": len(s.effects)}, refs("observable", hits, "observable_id"), None

def m_token_outlier(s, e, cfg):
    m = e["median_tokens"]
    if not m or e["sessions_with_tokens"] < cfg["min_sessions_for_baseline"]:
        return None, {"reason": "no estate baseline yet"}, [], None
    return round(s.tokens / m, 2), {"tokens": s.tokens, "estate_median": m}, [], None

def m_duration_outlier(s, e, cfg):
    m = e["median_duration_ms"]
    if not m:
        return None, {"reason": "no recorded durations in estate"}, [], None
    lim = m * cfg["duration_outlier_multiple"]
    hits = [x for x in s.steps if num(x.get("duration_ms")) > lim]
    return len(hits), {"limit_ms": lim, "estate_median_ms": m}, \
        refs("step", hits, "step_id"), (hits[0]["step_id"] if hits else None)

# ---- telemetry: can the evidence be relied on

def m_collector_coverage(s, e, cfg):
    want = set(cfg["expected_collectors"].get(s.tool or "", []))
    if not want:
        return None, {"reason": f"no expectation configured for {s.tool}"}, [], None
    got = want & s.collectors
    return round(len(got) / len(want), 3), \
        {"expected": sorted(want), "present": sorted(s.collectors), "missing": sorted(want - got)}, [], None

def m_source_agreement(s, e, cfg):
    multi = [r for r in s.rows if r.get("collectors_agree") is not None]
    if not multi:
        return None, {"reason": "no event seen by more than one collector"}, [], None
    bad = [r for r in multi if r["collectors_agree"] is False]
    return round(1 - len(bad) / len(multi), 3), {"cross_checked": len(multi), "disagreed": len(bad)}, \
        refs("observable", bad, "observable_id"), None

def m_turn_linkage(s, e, cfg):
    acts = [x for x in s.steps if x["step"] not in ("START", "END")]
    if not acts:
        return None, {"reason": "no steps"}, [], None
    linked = [x for x in acts if x.get("turn_id")]
    return round(len(linked) / len(acts), 3), {"steps": len(acts), "without_turn": len(acts) - len(linked)}, [], None

def m_lane_linkage(s, e, cfg):
    subs = {k: v for k, v in s.trace["lanes"].items() if k != "main"}
    if not subs:
        return None, {"reason": "no subagent lanes"}, [], None
    linked = [k for k, v in subs.items() if v and v[0].get("parent_step_id")]
    return round(len(linked) / len(subs), 3), {"lanes": len(subs), "unlinked": sorted(set(subs) - set(linked))[:10]}, [], None

# ---- anomaly: distance from what this estate normally does

def m_transition_novelty(s, e, cfg):
    mine = s.transitions()
    others = e["transitions_by_tool"].get(s.tool, Counter()) - Counter(mine)
    if len(mine) < cfg["min_transitions_for_novelty"] or \
       e["sessions_by_tool"].get(s.tool, 0) - 1 < cfg["min_sessions_for_baseline"]:
        return None, {"reason": "too few transitions or sessions for a baseline"}, [], None
    novel = [t for t in mine if others[t] == 0]
    lane = max(s.trace["lanes"], key=lambda k: len(s.trace["lanes"][k]), default=None)
    return round(len(novel) / len(mine), 3), \
        {"transitions": len(mine), "novel": [f"{a}>{b} x{n}" for (a, b), n in Counter(novel).most_common(8)]}, [], \
        (f"{s.id}:{lane}" if lane else None)

def m_action_rate(s, e, cfg):
    mins = s.minutes()
    base = e["median_rate_by_tool"].get(s.tool)
    if not base or mins <= 0 or e["sessions_by_tool"].get(s.tool, 0) < cfg["min_sessions_for_baseline"]:
        return None, {"reason": "no baseline rate"}, [], None
    rate = len(s.actions) / mins
    return round(rate / base, 2), {"actions_per_min": round(rate, 2), "tool_median": round(base, 2)}, [], None

def m_first_seen_mcp(s, e, cfg):
    earlier = e["mcp_before"](s)
    mine = {r["mcp_server"] for r in s.rows if has(r.get("mcp_server"))}
    new = sorted(mine - earlier)
    hits = [r for r in s.rows if r.get("mcp_server") in new]
    return len(new), {"new_servers": new, "scope": "operator"}, refs("observable", hits, "observable_id"), None

# ---- oversight

def m_fast_approvals(s, e, cfg):
    hits = [x for x in s.approvals if x.get("approval_wait_ms") is not None
            and x["approval_wait_ms"] < cfg["fast_approval_ms"]]
    return len(hits), {"approvals": len(s.approvals)}, refs("step", hits, "step_id"), None

def m_declined_inferred(s, e, cfg):
    hits = [x for x in s.steps if x.get("decided_by") == "declined_inferred"]
    return len(hits), {}, refs("step", hits, "step_id"), None

# ---- interaction

def m_subagent_lanes(s, e, cfg):
    subs = [k for k in s.trace["lanes"] if k != "main"]
    return len(subs), {"lanes": subs[:10]}, [], None

def m_mcp_servers(s, e, cfg):
    servers = Counter(r["mcp_server"] for r in s.rows if has(r.get("mcp_server")))
    return len(servers), {"servers": dict(servers.most_common(10))}, [], None


def approvals_visible(s, cfg):
    cap = cfg.get("tool_capabilities", {}).get("approval_events", {})
    seen = any(r.get("observable_type") == "permission_request" or has(r.get("permission_decision"))
               for r in s.rows)
    return seen or cap.get(s.tool) is True


# applicability: did the condition the metric asks about arise at all
A = {
    "always":    lambda s: True,
    "actions":   lambda s: bool(s.actions),
    "commands":  lambda s: any(has(r.get("command")) for r in s.rows),
    "asked":     lambda s: bool(s.approvals),
    "subagents": lambda s: any(k != "main" for k in s.trace["lanes"]),
    "multi":     lambda s: len(s.collectors) > 1,
    "effects":   lambda s: bool(s.effects),
    "steps":     lambda s: bool(s.steps),
}

# basis checks: could this detector have fired here at all
B = {
    "args":      lambda s: any(has(r.get("tool_arguments")) for r in s.actions),
    "commands":  lambda s: any(has(r.get("exit_code")) for r in s.rows if has(r.get("command"))),
    "wrapper":   lambda s: "cli_wrapper" in s.collectors,
    "paths":     lambda s: any(has(r.get("file_path")) for r in s.rows) or bool(s.effects),
    "approvals": None,      # tool capability, needs cfg: see approvals_visible
    "content":   lambda s: any(has(r.get("tool_arguments")) or has(r.get("command")) for r in s.rows),
    "our_rules": lambda s: bool({"local_hooks", "mcp_proxy"} & s.collectors),
    "hashes":    lambda s: any(has(x.get("content_hash")) for x in s.effects),
    "fs":        lambda s: bool(s.effects),
    "outcomes":  lambda s: any(r.get("success") is not None or has(r.get("exit_code")) for r in s.rows),
    "tokens":    lambda s: s.tokens > 0,
    "durations": lambda s: any(num(x.get("duration_ms")) > 0 for x in s.steps),
    "mcp":       lambda s: bool({"mcp_proxy", "local_hooks", "otel"} & s.collectors),
    "always":    lambda s: True,
}

METRICS = [
    ("insights.repetition",        "Identical call repeated within a turn", m_repetition,        "args",      ["tool_name", "tool_arguments", "turn_id"], "actions"),
    ("insights.retry",             "Failed command reissued unchanged",     m_retry,             "commands",  ["command", "exit_code"], "commands"),
    ("insights.stalled",           "Execution left hanging",                m_stalled,           "wrapper",   ["hung_seconds"], "always"),
    ("insights.outside",           "Work outside the workspace",            m_outside,           "paths",     ["file_path", "outside_workspace"], "always"),
    ("insights.sensitive_unasked", "Sensitive path, no approval in session", m_sensitive_unasked, "approvals", ["sensitivity_tier", "permission_decision"], "always"),
    ("insights.secrets",           "Secret-shaped content handled",         m_secrets,           "content",   ["secret_detected"], "always"),
    ("insights.blocked",           "Action stopped by our deny rules",      m_blocked,           "our_rules", ["policy_rule_matched"], "always"),
    ("insights.no_oversight",      "Long session, nobody asked",            m_no_oversight,      "approvals", ["tool_name", "permission_decision"], "always"),
    ("insights.reverted",          "Agent work put back",                   m_reverted,          "hashes",    ["content_hash", "reverted_to_earlier"], "effects"),
    ("insights.lockfile",          "Manifest changed without lockfile",     m_lockfile,          "fs",        ["is_dependency_manifest", "is_lockfile"], "always"),
    ("insights.failed",            "Actions that failed",                   m_failed,            "outcomes",  ["success", "exit_code"], "actions"),
    ("insights.unattributed",      "Disk change no agent accounts for",     m_unattributed,      "fs",        ["file_path", "attribution"], "always"),
    ("insights.token_outlier",     "Session tokens / estate median",        m_token_outlier,     "tokens",    ["total_tokens"], "always"),
    ("insights.duration_outlier",  "Steps beyond 10x median duration",      m_duration_outlier,  "durations", ["tool_duration_ms"], "steps"),
    ("telemetry.collector_coverage", "Expected collectors present",         m_collector_coverage, "always",   ["collector", "contributing"], "always"),
    ("telemetry.source_agreement", "Cross-collector agreement",             m_source_agreement,  "always",    ["collectors_agree"], "multi"),
    ("telemetry.turn_linkage",     "Steps carrying a turn id",              m_turn_linkage,      "always",    ["turn_id"], "steps"),
    ("telemetry.lane_linkage",     "Subagent lanes linked to a handoff",    m_lane_linkage,      "always",    ["agent_id", "observable_type"], "subagents"),
    ("path.transition_novelty",    "Step transitions unseen for this tool", m_transition_novelty, "always",   ["tool_name", "command", "observable_type"], "always"),
    ("activity.action_rate_ratio", "Actions/min vs tool median",            m_action_rate,       "always",    ["tool_name", "occurred_at"], "always"),
    ("access.first_seen_mcp",      "MCP server new for this operator",      m_first_seen_mcp,    "mcp",       ["mcp_server"], "always"),
    ("oversight.fast_approvals",   "Approvals returned in under a second",  m_fast_approvals,    "approvals", ["observable_type", "occurred_at"], "asked"),
    ("oversight.declined_inferred", "Approval requests apparently declined", m_declined_inferred, "approvals", ["observable_type", "turn_id"], "asked"),
    ("interaction.subagent_lanes", "Subagent lanes in session",             m_subagent_lanes,    "always",    ["agent_id"], "always"),
    ("interaction.mcp_servers",    "Distinct MCP servers used",             m_mcp_servers,       "mcp",       ["mcp_server"], "always"),
]
METRIC_IDS = [m[0] for m in METRICS]


# ------------------------------------------------------------------ estate baselines (computed once per run)

def estate(sessions):
    by_tool = Counter(s.tool for s in sessions)
    trans = defaultdict(Counter)
    rates = defaultdict(list)
    for s in sessions:
        trans[s.tool].update(s.transitions())
        if s.minutes() > 0 and s.actions:
            rates[s.tool].append(len(s.actions) / s.minutes())
    toks = [s.tokens for s in sessions if s.tokens > 0]
    durs = [num(x.get("duration_ms")) for s in sessions for x in s.steps if num(x.get("duration_ms")) > 0]

    # MCP servers each operator had used before a session began
    seen = defaultdict(list)                               # operator -> [(start, servers)]
    for s in sessions:
        seen[s.operator].append((s.start or "", {r["mcp_server"] for r in s.rows if has(r.get("mcp_server"))}))
    def mcp_before(s):
        return set().union(*[sv for st, sv in seen[s.operator] if st < (s.start or "")] or [set()])

    return {
        "median_tokens": median(toks) if toks else 0,
        "sessions_with_tokens": len(toks),
        "median_duration_ms": median(durs) if durs else 0,
        "sessions_by_tool": dict(by_tool),
        "transitions_by_tool": trans,
        "median_rate_by_tool": {t: median(v) for t, v in rates.items() if v},
        "mcp_before": mcp_before,
    }


# ------------------------------------------------------------------ build

def build_sessions(rows, with_unlinked=False):
    """Agent sessions, each with its rows, trace, steps and effects.

    Two kinds of capture record are not agent sessions and are folded or
    held out here rather than measured as if they were:

      terminal twins   the wrapper names its own session; where signal_traces
                       joined it to an agent session, its rows and steps are
                       merged into that session as a "terminal" lane;
      transcript-only  a session seen only through its transcript (usually run
                       before hooks were installed). Transcript ids are first
                       re-keyed to the agent session (signal_traces.canonical);
                       what remains is a real session and is measured, with
                       coverage saying how little was seen.
    """
    rows = signal_traces.canonicalize(rows)
    traces, steps, fx = signal_traces.build(rows)
    rows_by = defaultdict(list)
    for r in rows:
        if r.get("session_id"):
            rows_by[r["session_id"]].append(r)
    steps_by, fx_by = defaultdict(list), defaultdict(list)
    for st in steps:
        steps_by[st["session_id"]].append(st)
    for x in fx:
        if x.get("session_id"):
            fx_by[x["session_id"]].append(x)

    merged_into = {}
    for sid, t in traces.items():
        partner = t.get("wrapper_session_id")
        wrapper_only = {r.get("collector") for r in t["rows"]} == {"cli_wrapper"}
        if partner and wrapper_only and partner in traces:
            merged_into[sid] = partner
    for w, p in merged_into.items():
        rows_by[p] += rows_by.pop(w, [])
        steps_by[p] += steps_by.pop(w, [])
        fx_by[p] += fx_by.pop(w, [])
        for lane, lane_steps in traces[w]["lanes"].items():
            traces[p]["lanes"]["terminal" if lane == "main" else f"terminal:{lane}"] = lane_steps

    sessions, unlinked = [], []
    for sid in sorted(rows_by):
        if sid in merged_into:
            continue
        if sid not in traces:
            unlinked.append(sid)
        sessions.append(Session(sid, rows_by[sid], traces.get(sid), steps_by[sid], fx_by[sid]))
    if with_unlinked:
        return sessions, {"merged": merged_into, "unlinked": {u: rows_by[u] for u in unlinked},
                          "agent_rows": rows_by}
    return sessions


def orphans(rows):
    """Sessions seen only through a transcript, and where each might belong."""
    sessions, extra = build_sessions(rows, with_unlinked=True)
    sessions = [s for s in sessions if s.id not in extra["unlinked"]]
    by_agent_id = defaultdict(set)
    for s in sessions:
        for r in s.rows:
            if r.get("agent_id"):
                by_agent_id[r["agent_id"]].add(s.id)
    print(f"\n{len(sessions)} sessions, {len(extra['merged'])} terminal twins merged, "
          f"{len(extra['unlinked'])} seen only through a transcript\n")
    print(f"{'tool':<13}{'record id':<40}{'rows':>6}{'tokens':>9}  likely owner")
    for oid, orows in sorted(extra["unlinked"].items(), key=lambda x: (str(x[1][0].get("tool")), x[0])):
        tool = next((r.get("tool") for r in orows if r.get("tool")), None)
        toks = sum(num(r.get("total_tokens")) for r in orows)
        t0 = min((r.get("occurred_at") or "" for r in orows), default="")
        hit, how = None, ""
        if by_agent_id.get(oid):
            hit, how = sorted(by_agent_id[oid])[0], "subagent transcript (agent_id)"
        else:
            for s in sessions:
                if s.id in oid or oid in s.id:
                    hit, how = s.id, "id contained in file name"
                    break
        if not hit:
            near = [s for s in sessions if s.tool == tool and s.start and s.end and s.start <= t0 <= s.end]
            if len(near) == 1:
                hit, how = near[0].id, "same tool, inside its time window"
            elif near:
                how = f"{len(near)} overlapping sessions, ambiguous"
        print(f"{str(tool):<13}{oid[:38]:<40}{len(orows):>6}{int(toks):>9}  "
              f"{(hit or '-')[:24]}  {how}")


def compute(rows, cfg=None, computed_at=None, catalogue=False):
    """Every metric for every session, once. Returns metric_event dicts."""
    cfg = cfg or load_config()
    computed_at = computed_at or datetime.now(timezone.utc).isoformat()
    sessions = build_sessions(rows)
    est = estate(sessions)
    out = []
    for s in sessions:
        events = []
        for mid, name, fn, basis, fields, applies in METRICS:
            t = cfg["thresholds"].get(mid, {})
            could_fire = approvals_visible(s, cfg) if basis == "approvals" else B[basis](s)
            if not A[applies](s):
                events.append(event(s, mid, name, None, t, "NOT_APPLICABLE",
                                    {"reason": f"did not arise: {applies}"}, [], None,
                                    fields, basis, could_fire, cfg, computed_at, None))
                continue
            try:
                value, feats, ev, trace_id = fn(s, est, cfg)
                err = None
            except Exception as ex:                       # a broken metric is a fact, not a crash
                value, feats, ev, trace_id, err = None, {}, [], None, f"{type(ex).__name__}: {ex}"
            state = judge(value, t)
            if err:
                state = "UNKNOWN"
            elif "_state" in feats:
                state = feats.pop("_state")
            elif state == "NORMAL" and not could_fire and not value:
                state = "UNKNOWN"
                feats = {**feats, "reason": f"basis absent: {basis}"}
            events.append(event(s, mid, name, value, t, state, feats, ev, trace_id,
                                fields, basis, could_fire, cfg, computed_at, err))
        # share of this session's metrics that could not be judged
        judged = [x for x in events if x["state"] != "NOT_APPLICABLE"]
        unknown = sum(1 for x in judged if x["state"] == "UNKNOWN") / max(len(judged), 1)
        t = cfg["thresholds"]["telemetry.unknown_share"]
        events.append(event(s, "telemetry.unknown_share", "Share of metrics that could not be judged",
                            round(unknown, 3), t, judge(unknown, t),
                            {"unknown": [x["metric_id"] for x in events if x["state"] == "UNKNOWN"]},
                            [], None, [], "always", True, cfg, computed_at, None))
        out += events
    if catalogue:
        out += catalogue_events(sessions, est, out, cfg, computed_at)
    return out, sessions


def event(s, mid, name, value, t, state, feats, ev, trace_id, fields, basis, could_fire, cfg, at, err):
    version = cfg["metric_version"]
    return {
        "metric_event_id": str(uuid.uuid5(NS, f"{s.id}|{mid}|{version}")),
        "metric_id": mid, "metric_name": name,
        "session_id": s.id, "trace_id": trace_id,
        "tool": s.tool, "operator": s.operator, "workspace": s.workspace,
        "value": value,
        "threshold": t.get("exception"), "direction": t.get("direction"),
        "thresholds": t,
        "state": state,
        "features": feats,
        "evidence_refs": ev,
        "required_fields": fields, "basis": basis, "basis_present": could_fire,
        "source": f"signal_metrics.py:{mid}@{version}",
        "metric_version": version,
        "session_started_at": s.start, "session_ended_at": s.end,
        "computed_at": at,
        "error": err,
    }


# ------------------------------------------------------------------ cli

def why(events, mid):
    """What sits behind a metric's states: tool, state and the deciding features."""
    rows = [e for e in events if e["metric_id"] == mid]
    groups = Counter()
    for e in rows:
        f = e["features"]
        key = f.get("reason") or json.dumps({k: v for k, v in f.items()
                                             if k in ("missing", "present")}, default=str)
        groups[(e["tool"] or "unattributed", e["state"], key[:110])] += 1
    print(f"\n{mid}: {len(rows)} sessions\n")
    print(f"{'tool':<14}{'state':<16}{'n':>4}   detail")
    for (tool, state, key), n in sorted(groups.items(), key=lambda x: (x[0][0], -x[1])):
        print(f"{tool:<14}{state:<16}{n:>4}   {key}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--why", help="metric id: group its non-normal sessions by tool and reason")
    ap.add_argument("--orphans", action="store_true", help="capture records with no agent session")
    ap.add_argument("--catalogue", action="store_true", help="also compute the catalogue metrics (with --dry-run)")
    a = ap.parse_args()
    if a.catalogue and not a.dry_run:
        sys.exit("catalogue metrics are not written to metric_events; use --catalogue with --dry-run")
    if not (URL and KEY):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY (source ~/.signal-env)")
    cols = COLS + ("," + CATALOGUE_COLS if a.catalogue else "")
    rows = signal_traces.get("observables", f"select={cols}&order=occurred_at.asc")
    if a.orphans:
        orphans(rows)
        return
    events, sessions = compute(rows, catalogue=a.catalogue)

    by_state = Counter(e["state"] for e in events)
    print(f"\n{len(sessions)} sessions, {len(events):,} metric events  {dict(by_state)}")
    per = defaultdict(Counter)
    for e in events:
        per[e["metric_id"]][e["state"]] += 1
    print(f"\n{'metric':<30}" + "".join(f"{s:>10}" for s in STATES))
    for mid in METRIC_IDS + ["telemetry.unknown_share"]:
        print(f"{mid:<30}" + "".join(f"{per[mid][s]:>10}" for s in STATES))
    if a.catalogue:
        catalogue_summary(events)

    if a.why:
        why(events, a.why)
        return
    if a.session:
        print(f"\nsession {a.session}")
        for e in events:
            if e["session_id"].startswith(a.session):
                print(f"  {e['state']:<10}{e['metric_id']:<30}{str(e['value']):>10}  "
                      f"{json.dumps(e['features'], default=str)[:90]}")
        return
    if a.dry_run:
        print("\ndry run, nothing written")
        return
    signal_traces.post("metric_events", events, "metric_event_id")


# ------------------------------------------------------------------ catalogue metrics
#
# Metrics named in docs/cops/architecture/COPS_METRIC_MAPPING.md that the
# Signal tables can answer. Each entry in metric_registry.json says what it
# reads, its scope and when it cannot be calculated. They are computed only
# with compute(..., catalogue=True) and are not yet written to metric_events.
#
# A catalogue metric never reports zero for absent evidence: if the basis is
# absent or the inputs are missing, the value is None and the state UNKNOWN,
# with features.availability saying why. A value with no approved threshold
# in metrics_config.json has state NO_THRESHOLD.

CATALOGUE_COLS = ",".join([
    "captured_at", "conversation_id", "parent_agent_id", "delegation_depth", "operator_id",
    "org_id", "team_id", "model", "tool_interrupted", "mcp_server_version", "mcp_tools_available",
    "permission_mode", "plan_item", "plan_status", "compaction_reason", "violation_count",
    "confidence", "derived_from", "raw_ref",
])
CALL_REQUESTS = {"tool_request", "mcp_request", "shell_request"}
CALL_RESULTS = {"tool_result", "mcp_result", "shell_result", "tool_failure"}


def unique_rows(s):
    if getattr(s, "_unique", None) is None:
        seen, out = set(), []
        for r in sorted(s.rows, key=lambda r: (r.get("occurred_at") or "", r.get("sequence_num") or 0)):
            k = r.get("observable_id")
            if k in seen:
                continue
            if k:
                seen.add(k)
            out.append(r)
        s._unique = out
    return s._unique

def of_type(s, *types):
    return [r for r in unique_rows(s) if r.get("observable_type") in types]

def missing(reason, *fields, availability="MISSING_DATA"):
    return None, {"reason": reason, "missing": list(fields), "availability": availability}, [], None

def seconds(a, b):
    a, b = signal_traces.ts(a), signal_traces.ts(b)
    return (b - a).total_seconds() if a and b else None

def fetched(s, field):
    return any(field in r for r in s.rows)

def capable(s, cfg, name, seen):
    return bool(seen) or cfg.get("tool_capabilities", {}).get(name, {}).get(s.tool) is True

def sub_lanes(s):
    return {k: v for k, v in s.trace["lanes"].items() if k != "main" and not k.startswith("terminal")}

def lane_of(r):
    return "main" if "subagent" in (r.get("observable_type") or "") else (r.get("agent_id") or "main")

def linked(lane_steps):
    return bool(lane_steps) and bool(lane_steps[0].get("parent_step_id"))

def by_turn(rows, key):
    out, loose = defaultdict(float), 0.0
    for r in rows:
        v = num(r.get(key))
        if r.get("turn_id"):
            out[r["turn_id"]] += v
        else:
            loose += v
    return out, loose

def turn_order(s):
    first = {}
    for r in unique_rows(s):
        if r.get("turn_id") and r["turn_id"] not in first:
            first[r["turn_id"]] = r.get("occurred_at") or ""
    return sorted(first, key=first.get)

def is_work(x):
    return x["step"] not in ("START", "END")

def work_steps(s):
    return [x for x in s.steps if is_work(x)]

def spans(steps):
    out = []
    for x in steps:
        a, b = signal_traces.ts(x.get("started_at")), signal_traces.ts(x.get("ended_at"))
        if a and b and b >= a:
            out.append((a, b))
    return out

def max_overlap(intervals):
    points = sorted([(a, 1) for a, b in intervals if b > a] + [(b, -1) for a, b in intervals if b > a],
                    key=lambda p: (p[0], p[1]))
    best = cur = 0
    for _, d in points:
        cur += d
        best = max(best, cur)
    return best

def ratio(n, d, digits=3):
    return round(n / d, digits) if d else None

# ---- workflow

def plan_items(s):
    made = {r["plan_item"] for r in of_type(s, "plan_created") if has(r.get("plan_item"))}
    done = {r["plan_item"] for r in of_type(s, "plan_completed") if has(r.get("plan_item"))}
    return made, done

def m_task_progress(s, e, cfg):
    made, done = plan_items(s)
    if not made:
        return missing("plan events carry no plan_item", "plan_item")
    return ratio(len(made & done), len(made)), \
        {"created": len(made), "completed": len(made & done), "completed_not_created": len(done - made)}, \
        refs("observable", of_type(s, "plan_created", "plan_completed"), "observable_id"), None

def m_unresolved_items(s, e, cfg):
    made, done = plan_items(s)
    if not made:
        return missing("plan events carry no plan_item", "plan_item")
    open_ = sorted(made - done)
    hits = [r for r in of_type(s, "plan_created") if r.get("plan_item") in open_]
    return len(open_), {"created": len(made), "open": open_[:10]}, refs("observable", hits, "observable_id"), None

def m_workflow_rework_rate(s, e, cfg):
    changes = [x for x in work_steps(s) if x["step"] in ("CHANGE", "CREATE", "DELETE") and has(x.get("file_path"))]
    timed = [x for x in changes if x.get("turn_id")]
    if not timed:
        return missing("no file change carries a turn_id", "turn_id")
    turns = defaultdict(set)
    for x in timed:
        turns[x["file_path"]].add(x["turn_id"])
    again = sorted(f for f, t in turns.items() if len(t) > 1)
    hits = [x for x in timed if x["file_path"] in again]
    return ratio(len(again), len(turns)), \
        {"files_changed": len(turns), "changed_in_later_turn": again[:10], "changes_without_turn": len(changes) - len(timed)}, \
        refs("step", hits, "step_id"), None

def m_stalled_duration_per_turn(s, e, cfg):
    hits = [r for r in unique_rows(s) if has(r.get("hung_seconds"))]
    per, loose = by_turn(hits, "hung_seconds")
    worst = max(per.values(), default=0.0)
    return round(max(worst, loose), 1), \
        {"per_turn": dict(sorted(per.items(), key=lambda x: -x[1])[:10]), "without_turn_s": round(loose, 1)}, \
        refs("observable", hits, "observable_id"), None

def m_excessive_delegation_ratio(s, e, cfg):
    handoffs = [x for x in s.trace["lanes"].get("main", []) if x["step"] == "DELEGATE"]
    return ratio(len(handoffs), len(s.actions)), {"delegations": len(handoffs), "actions": len(s.actions)}, \
        refs("step", handoffs, "step_id"), None

# ---- telemetry

def m_event_freshness_live(s, e, cfg):
    lags = [(r, seconds(r.get("occurred_at"), r.get("ingested_at"))) for r in unique_rows(s)]
    lags = [(r, v) for r, v in lags if v is not None]
    if not lags:
        return missing("no row carries both occurred_at and ingested_at", "ingested_at")
    vals = [v for _, v in lags]
    neg = [r for r, v in lags if v < 0]
    return round(median(vals), 1), {"rows": len(vals), "max_lag_s": round(max(vals), 1),
                                    "ingested_before_occurred": len(neg)}, refs("observable", neg, "observable_id"), None

def m_missing_event_rate(s, e, cfg):
    req = {r["tool_use_id"]: r for r in of_type(s, *CALL_REQUESTS) if r.get("tool_use_id")}
    if not req:
        return missing("no call request carries a tool_use_id", "tool_use_id")
    done = {r["tool_use_id"] for r in of_type(s, *CALL_RESULTS) if r.get("tool_use_id")}
    lost = [r for u, r in req.items() if u not in done]
    return ratio(len(lost), len(req)), {"requests": len(req), "without_result": len(lost)}, \
        refs("observable", lost, "observable_id"), None

# ---- anomaly

def m_token_expansion_per_turn(s, e, cfg):
    per, _ = by_turn([r for r in unique_rows(s) if num(r.get("total_tokens")) > 0], "total_tokens")
    order = [t for t in turn_order(s) if t in per]
    if len(order) < 2:
        return missing("fewer than two turns carry token counts", "total_tokens", "turn_id")
    steps = [(b, per[b] / per[a]) for a, b in zip(order, order[1:])]
    turn, worst = max(steps, key=lambda x: x[1])
    return round(worst, 2), {"turns": len(order), "largest_expansion_turn": turn}, [], None

# ---- context

def m_compaction_count(s, e, cfg):
    hits = of_type(s, "compaction")
    return len(hits), {"reasons": dict(Counter(r.get("compaction_reason") or "unrecorded" for r in hits))}, \
        refs("observable", hits, "observable_id"), None

# ---- interaction

def interaction_edges(s):
    edges = set()
    for r in unique_rows(s):
        lane = lane_of(r)
        if lane != "main" and has(r.get("parent_agent_id")):
            edges.add((r["parent_agent_id"], lane, "delegation"))
        if has(r.get("mcp_server")):
            edges.add((lane, f"mcp:{r['mcp_server']}", "mcp"))
    parented = {b for a, b, k in edges if k == "delegation"}
    for lane, steps in sub_lanes(s).items():
        if lane not in parented and linked(steps):
            edges.add(("main", lane, "delegation"))
    return edges

def m_interaction_edge_count(s, e, cfg):
    edges = interaction_edges(s)
    return len(edges), {"by_kind": dict(Counter(k for _, _, k in edges)),
                        "edges": sorted(f"{a}>{b}" for a, b, _ in edges)[:20]}, [], None

def m_interaction_unique_entities(s, e, cfg):
    rows = unique_rows(s)
    agents = {lane_of(r) for r in rows if has(r.get("tool_name")) or has(r.get("mcp_server"))} | set(sub_lanes(s))
    servers = {r["mcp_server"] for r in rows if has(r.get("mcp_server"))}
    tools = {r["tool_name"] for r in s.actions}
    return len(agents) + len(servers) + len(tools), \
        {"agents": len(agents), "mcp_servers": len(servers), "tools": len(tools)}, [], None

def m_interaction_graph_completeness(s, e, cfg):
    lanes = sub_lanes(s)
    known = set(lanes)
    rows = [r for r in unique_rows(s) if lane_of(r) in lanes]
    if not rows:
        return missing("subagent lanes have no rows carrying agent_id", "agent_id")
    ok = [r for r in rows if linked(lanes[lane_of(r)]) or r.get("parent_agent_id") in known]
    bad = [r for r in rows if r not in ok]
    return ratio(len(ok), len(rows)), {"rows": len(rows), "unresolved": len(bad)}, \
        refs("observable", bad, "observable_id"), None

def lane_depths(s):
    lanes = sub_lanes(s)
    parent = {}
    for r in unique_rows(s):
        lane = lane_of(r)
        if lane in lanes and has(r.get("parent_agent_id")) and r["parent_agent_id"] in lanes:
            parent.setdefault(lane, r["parent_agent_id"])
    def depth(lane, seen=()):
        p = parent.get(lane)
        return 1 if p is None or p in seen else 1 + depth(p, seen + (lane,))
    return {lane: depth(lane) for lane in lanes if linked(lanes[lane]) or lane in parent}

def m_interaction_propagation_depth(s, e, cfg):
    structural = max(lane_depths(s).values(), default=0)
    reported = [int(num(r.get("delegation_depth"))) for r in unique_rows(s) if r.get("delegation_depth") is not None]
    rep = max(reported, default=None)
    if sub_lanes(s) and not lane_depths(s) and not rep:
        return missing("subagent lanes are not linked and carry no delegation_depth",
                       "parent_agent_id", "delegation_depth")
    feats = {"structural": structural, "reported": rep}
    if rep and rep != structural:
        feats["conflict"] = "reported delegation_depth differs from the linked lane structure"
    return max(structural, rep or 0), feats, [], None

def m_interaction_propagation_breadth(s, e, cfg):
    lanes = sub_lanes(s)
    parent = {}
    for r in unique_rows(s):
        lane = lane_of(r)
        if lane in lanes and has(r.get("parent_agent_id")):
            parent.setdefault(lane, r["parent_agent_id"])
    for lane, steps in lanes.items():
        if lane not in parent and linked(steps):
            parent[lane] = "main"
    if not parent:
        return missing("no subagent lane has a known parent", "parent_agent_id")
    kids = Counter(parent.values())
    return max(kids.values()), {"children": dict(kids), "lanes_without_parent": len(set(lanes) - set(parent))}, [], None

def m_interaction_orphaned_events(s, e, cfg):
    lanes = sub_lanes(s)
    hits = [r for r in unique_rows(s) if lane_of(r) in lanes
            and not linked(lanes[lane_of(r)]) and not has(r.get("parent_agent_id"))]
    return len(hits), {"lanes": sorted({lane_of(r) for r in hits})[:10]}, refs("observable", hits, "observable_id"), None

# ---- policy

def m_unauthorized_action_attempts(s, e, cfg):
    seen, hits = set(), []
    for r in unique_rows(s):
        if r.get("observable_type") in ("permission_denied", "mcp_blocked") or r.get("status") in ("denied", "blocked"):
            k = r.get("tool_use_id") or r.get("observable_id")
            if k not in seen:
                seen.add(k)
                hits.append(r)
    return len(hits), {"by_type": dict(Counter(r.get("observable_type") for r in hits))}, \
        refs("observable", hits, "observable_id"), None

def m_boundary_violation_count(s, e, cfg):
    seen, hits = set(), []
    for r in unique_rows(s) + s.effects:
        if r.get("outside_workspace") or num(r.get("violation_count")) > 0:
            k = r.get("observable_id")
            if k not in seen:
                seen.add(k)
                hits.append(r)
    return len(hits), {"outside_workspace": sum(1 for r in hits if r.get("outside_workspace")),
                       "policy_violations": sum(1 for r in hits if num(r.get("violation_count")) > 0),
                       "violation_count_read": fetched(s, "violation_count")}, \
        refs("observable", hits, "observable_id"), None

def m_restricted_resource_access_rate(s, e, cfg):
    acts = {r.get("observable_id"): r for r in s.actions + [r for r in unique_rows(s) if has(r.get("file_path"))]}
    rated = [r for r in acts.values() if r.get("sensitivity_tier") is not None]
    if not rated:
        return missing("no file or tool row carries sensitivity_tier", "sensitivity_tier")
    hits = [r for r in rated if num(r["sensitivity_tier"]) >= 3]
    return ratio(len(hits), len(rated)), {"rated": len(rated), "restricted": len(hits), "unrated": len(acts) - len(rated)}, \
        refs("observable", hits, "observable_id"), None

def m_approval_action_linkage(s, e, cfg):
    grants = of_type(s, "permission_granted")
    keyed = [r for r in grants if r.get("tool_use_id")]
    if not keyed:
        return missing("no permission_granted row carries a tool_use_id", "tool_use_id")
    acted = {}
    for r in s.actions:
        if r.get("tool_use_id"):
            acted.setdefault(r["tool_use_id"], r.get("occurred_at") or "")
    ok = [r for r in keyed if r["tool_use_id"] in acted and acted[r["tool_use_id"]] >= (r.get("occurred_at") or "")]
    bad = [r for r in keyed if r not in ok]
    return ratio(len(ok), len(keyed)), {"grants": len(grants), "keyed": len(keyed), "unlinked": len(bad)}, \
        refs("observable", bad, "observable_id"), None

def m_policy_violation_rate(s, e, cfg):
    if not fetched(s, "violation_count"):
        return missing("violation_count was not read for this session", "violation_count")
    hits = [r for r in s.actions if num(r.get("violation_count")) > 0]
    return ratio(len(hits), len(s.actions)), {"actions": len(s.actions), "with_violation": len(hits)}, \
        refs("observable", hits, "observable_id"), None

# ---- resources

def session_tokens(s):
    return sum(num(r.get("total_tokens")) for r in unique_rows(s))

def session_seconds(s):
    times = [r["occurred_at"] for r in unique_rows(s) if r.get("occurred_at")]
    return seconds(times[0], times[-1]) if len(times) > 1 else None

def m_token_consumption_per_session(s, e, cfg):
    return session_tokens(s), {"rows_with_tokens": sum(1 for r in unique_rows(s) if num(r.get("total_tokens")) > 0)}, [], None

def m_token_consumption_per_turn(s, e, cfg):
    per, loose = by_turn([r for r in unique_rows(s) if num(r.get("total_tokens")) > 0], "total_tokens")
    if not per:
        return missing("no token count carries a turn_id", "turn_id")
    return max(per.values()), {"turns": len(per), "median_per_turn": median(per.values()),
                               "tokens_without_turn": loose}, [], None

def m_cost_per_session(s, e, cfg):
    rows = [r for r in unique_rows(s) if r.get("cost_usd") is not None]
    if not rows:
        return missing("no row carries cost_usd", "cost_usd")
    return round(sum(num(r["cost_usd"]) for r in rows), 4), \
        {"rows": len(rows), "estimated": any(r.get("cost_is_estimated") for r in rows)}, [], None

def m_execution_duration_per_session(s, e, cfg):
    sec = session_seconds(s)
    if sec is None:
        return missing("fewer than two timestamped rows", "occurred_at")
    return round(sec, 1), {}, [], None

def m_execution_duration_threshold_breach(s, e, cfg):
    if "execution_duration_threshold_breach" not in cfg["thresholds"]:
        return missing("no approved session duration threshold in metrics_config.json",
                       "thresholds.execution_duration_threshold_breach", availability="MISSING_REFERENCE")
    return m_execution_duration_per_session(s, e, cfg)

def m_tool_timeout_rate(s, e, cfg):
    hits = [r for r in s.actions if r.get("status") == "timeout"]
    return ratio(len(hits), len(s.actions)), {"actions": len(s.actions), "timeouts": len(hits)}, \
        refs("observable", hits, "observable_id"), None

def m_resource_consumption_rate(s, e, cfg):
    tok, sec = session_tokens(s), session_seconds(s)
    if not sec:
        return missing("session has no measurable duration", "occurred_at")
    return round(tok / (sec / 60), 1), {"tokens": tok, "minutes": round(sec / 60, 2)}, [], None

def m_resource_concurrency_level(s, e, cfg):
    sp = spans(work_steps(s))
    if not sp:
        return missing("no step has a start and an end time", "occurred_at", "tool_duration_ms")
    return max(max_overlap(sp), 1), {"timed_steps": len(sp), "steps": len(work_steps(s))}, [], None

def m_resource_stall_rate(s, e, cfg):
    steps = work_steps(s)
    hits = [x for x in steps if x["step"] == "STALL"]
    return ratio(len(hits), len(steps)), {"steps": len(steps), "stalled": len(hits)}, refs("step", hits, "step_id"), None

def repeated_calls(s, e, cfg):
    cited = set(m_repetition(s, e, cfg)[2])
    return [r for r in s.actions if f"observable:{r.get('observable_id')}" in cited]

def m_resource_redundant_execution_cost(s, e, cfg):
    hits = repeated_calls(s, e, cfg)
    timed = [r for r in hits if r.get("tool_duration_ms") is not None]
    if hits and not timed:
        return missing("repeated calls carry no tool_duration_ms", "tool_duration_ms")
    return sum(num(r["tool_duration_ms"]) for r in timed), \
        {"repeated_calls": len(hits), "untimed": len(hits) - len(timed), "unit": "ms",
         "tokens": "not attributable: tokens are recorded per turn, not per call"}, \
        refs("observable", hits, "observable_id"), None

def lane_tokens(s):
    lanes = sub_lanes(s)
    out = defaultdict(float)
    for r in unique_rows(s):
        if num(r.get("total_tokens")) > 0:
            out[lane_of(r) if lane_of(r) in lanes else "main"] += num(r["total_tokens"])
    return out

def m_subagent_resource_consumption(s, e, cfg):
    per = {k: v for k, v in lane_tokens(s).items() if k != "main"}
    if not per:
        return missing("no token count is attributed to a subagent", "total_tokens", "agent_id")
    return sum(per.values()), {"by_lane": per, "lanes_without_tokens": sorted(set(sub_lanes(s)) - set(per))}, [], None

# ---- behaviour

def m_behavior_action_sequence_frequency(s, e, cfg):
    grams = Counter()
    for steps in s.trace["lanes"].values():
        seq = [x["step"] for x in steps if is_work(x)]
        grams.update(">".join(seq[i:i + 3]) for i in range(len(seq) - 2))
    if not grams:
        return missing("no lane has three or more steps", "observable_type")
    return len(grams), {"top": dict(grams.most_common(10))}, [], None

def m_behavior_tool_usage_distribution(s, e, cfg):
    tools = Counter(r["tool_name"] for r in s.actions)
    share = {t: round(n / len(s.actions), 3) for t, n in tools.most_common(10)}
    return max(share.values()), {"shares": share, "tools": len(tools)}, [], None

def m_behavior_tool_transition_frequency(s, e, cfg):
    pairs = Counter()
    for steps in s.trace["lanes"].values():
        names = [x["tool_name"] for x in steps if is_work(x) and x.get("tool_name")]
        pairs.update(f"{a}>{b}" for a, b in zip(names, names[1:]))
    if not pairs:
        return missing("no lane has two consecutive tool calls", "tool_name")
    return len(pairs), {"top": dict(pairs.most_common(10))}, [], None

def m_behavior_model_variation(s, e, cfg):
    models = Counter(r["model"] for r in unique_rows(s) if has(r.get("model")))
    if not models:
        return missing("no row carries model", "model")
    return len(models), {"models": dict(models)}, [], None

# ---- recovery

def attempts(s):
    out = []
    for r in unique_rows(s):
        if has(r.get("command")) and (has(r.get("exit_code")) or r.get("success") is not None):
            out.append((str(r["command"]).strip(), signal_traces.failed(r), r))
    return out

def recoveries(s):
    a = attempts(s)
    out = []
    for i, (cmd, bad, r) in enumerate(a):
        if bad:
            nxt = next((x for x in a[i + 1:] if x[0] == cmd), None)
            out.append((r, nxt))
    return out

def m_recovery_disruption_count(s, e, cfg):
    steps = [x for x in work_steps(s) if x["failed"] or x["step"] == "STALL"]
    ids = {x.get("observable_id") for x in steps}
    extra = [r for r in unique_rows(s) if r.get("tool_interrupted") and r.get("observable_id") not in ids]
    return len(steps) + len(extra), {"failed": sum(1 for x in steps if x["failed"]),
                                     "stalled": sum(1 for x in steps if x["step"] == "STALL"),
                                     "interrupted": len(extra)}, refs("step", steps, "step_id"), None

def m_recovery_failure_rate(s, e, cfg):
    rec = [(r, n) for r, n in recoveries(s) if n]
    if not rec:
        return missing("no failed command was run again", "command", availability="NOT_OBSERVED")
    again = [r for r, n in rec if n[1]]
    return ratio(len(again), len(rec)), {"retried_failures": len(rec), "failed_again": len(again),
                                         "failures_not_retried": sum(1 for _, n in recoveries(s) if not n)}, \
        refs("observable", again, "observable_id"), None

def recovered(s):
    a = attempts(s)
    out = []
    for i, (cmd, bad, r) in enumerate(a):
        if bad:
            ok = next((x for x in a[i + 1:] if x[0] == cmd and not x[1]), None)
            if ok:
                out.append((r, ok[2]))
    return out

def m_recovery_time(s, e, cfg):
    pairs = [(r, ok, seconds(r.get("occurred_at"), ok.get("occurred_at"))) for r, ok in recovered(s)]
    pairs = [p for p in pairs if p[2] is not None]
    if not pairs:
        return missing("no failed command was later run successfully", "command", availability="NOT_OBSERVED")
    return round(median(p[2] for p in pairs), 1), {"recoveries": len(pairs), "max_s": round(max(p[2] for p in pairs), 1)}, \
        refs("observable", [p[1] for p in pairs], "observable_id"), None

def m_recovery_workflow_resumption_rate(s, e, cfg):
    steps = sorted(work_steps(s), key=lambda x: x.get("started_at") or "")
    bad = [x for x in steps if x["failed"]]
    later_ok = [x for x in bad if any(not y["failed"] and (y.get("started_at") or "") > (x.get("started_at") or "")
                                      for y in steps)]
    return ratio(len(later_ok), len(bad)), {"failures": len(bad), "resumed": len(later_ok)}, refs("step", bad, "step_id"), None

def m_recovery_retry_recurrence(s, e, cfg):
    ok, hits = set(), []
    for cmd, bad, r in attempts(s):
        if cmd in ok:
            hits.append(r)
        if not bad:
            ok.add(cmd)
    return len(hits), {"commands": len({c for c, _, _ in attempts(s)})}, refs("observable", hits, "observable_id"), None

def m_recovery_stability_duration(s, e, cfg):
    a = attempts(s)
    rec = recovered(s)
    if not rec:
        return missing("no failed command was later run successfully", "command", availability="NOT_OBSERVED")
    out = []
    for _, ok in rec:
        at = ok.get("occurred_at") or ""
        nxt = next((r for _, bad, r in a if bad and (r.get("occurred_at") or "") > at), None)
        out.append(seconds(at, nxt["occurred_at"] if nxt else s.end))
    out = [v for v in out if v is not None]
    return round(median(out), 1), {"recoveries": len(out)}, [], None

# ---- workload

def lane_work(s):
    return {k: [x for x in v if is_work(x)] for k, v in s.trace["lanes"].items()}

def m_workload_distribution_balance(s, e, cfg):
    work = {k: len(v) for k, v in lane_work(s).items() if v}
    total = sum(work.values())
    return ratio(max(work.values()), total), {"steps_by_lane": work}, [], None

def m_workload_per_agent(s, e, cfg):
    work = {k: len(v) for k, v in lane_work(s).items() if v}
    return max(work.values()), {"steps_by_lane": work, "tokens_by_lane": dict(lane_tokens(s))}, [], None

def m_workload_per_model(s, e, cfg):
    per = defaultdict(float)
    for r in unique_rows(s):
        if has(r.get("model")) and num(r.get("total_tokens")) > 0:
            per[r["model"]] += num(r["total_tokens"])
    if not per:
        return missing("no token count carries model", "model", "total_tokens")
    return len(per), {"tokens_by_model": dict(per)}, [], None

def lane_span(steps):
    sp = spans(steps)
    return (min(a for a, _ in sp), max(b for _, b in sp)) if sp else None

def m_agent_utilization_rate(s, e, cfg):
    util = {}
    for lane, steps in lane_work(s).items():
        span = lane_span(steps)
        busy = sum(num(x.get("duration_ms")) for x in steps) / 1000
        wall = (span[1] - span[0]).total_seconds() if span else 0
        if wall > 0 and busy > 0:
            util[lane] = round(min(busy / wall, 1.0), 3)
    if not util:
        return missing("no lane has step durations and a measurable span", "tool_duration_ms")
    return round(sum(util.values()) / len(util), 3), {"by_lane": util}, [], None

def m_agent_idle_time(s, e, cfg):
    gaps = {}
    for lane, steps in lane_work(s).items():
        steps = sorted(steps, key=lambda x: x.get("started_at") or "")
        g = [seconds(a.get("ended_at"), b.get("started_at")) for a, b in zip(steps, steps[1:])]
        g = [v for v in g if v is not None]
        if g:
            gaps[lane] = round(max(max(g), 0), 1)
    if not gaps:
        return missing("no lane has two timed steps", "occurred_at")
    return max(gaps.values()), {"max_gap_by_lane_s": gaps}, [], None

def m_parallel_execution_efficiency(s, e, cfg):
    steps = work_steps(s)
    busy = sum(num(x.get("duration_ms")) for x in steps) / 1000
    sec = session_seconds(s)
    if not busy or not sec:
        return missing("steps carry no durations or the session has no span", "tool_duration_ms")
    return round(busy / sec, 4), {"busy_s": round(busy, 1), "wall_s": round(sec, 1)}, [], None

def m_delegation_overhead_ratio(s, e, cfg):
    main = {x["step_id"]: x for x in s.trace["lanes"].get("main", [])}
    out = {}
    for lane, steps in sub_lanes(s).items():
        work = [x for x in steps if is_work(x) and x["step"] != "DELEGATE"]
        handoff = main.get(steps[0].get("parent_step_id")) if steps else None
        span = lane_span(work)
        if not (handoff and work and span):
            continue
        lead = seconds(handoff["started_at"], work[0]["started_at"])
        total = seconds(handoff["started_at"], span[1].isoformat())
        if lead is not None and total:
            out[lane] = round(max(lead, 0) / total, 3)
    if not out:
        return missing("no linked subagent lane has timed work", "parent_step_id", "occurred_at")
    return round(sum(out.values()) / len(out), 3), {"by_lane": out, "basis": "time; tokens are not recorded per lane"}, [], None

def m_subagent_resource_overhead(s, e, cfg):
    per = lane_tokens(s)
    sub = sum(v for k, v in per.items() if k != "main")
    if not sub:
        return missing("no token count is attributed to a subagent", "total_tokens", "agent_id")
    return ratio(sub, sum(per.values())), {"subagent_tokens": sub, "session_tokens": sum(per.values())}, [], None

def m_workload_concurrency_level(s, e, cfg):
    sp = [lane_span(v) for v in lane_work(s).values()]
    sp = [x for x in sp if x]
    if not sp:
        return missing("no lane has timed steps", "occurred_at")
    return max(max_overlap(sp), 1), {"timed_lanes": len(sp)}, [], None

# ---- audit

def m_audit_actor_attribution_rate(s, e, cfg):
    rows = unique_rows(s)
    ok = [r for r in rows if r.get("operator_id") or r.get("operator_email") or r.get("operator_username")]
    return ratio(len(ok), len(rows)), {"rows": len(rows), "with_actor": len(ok)}, [], None

def m_audit_action_attribution_rate(s, e, cfg):
    rows = unique_rows(s)
    rated = [r for r in rows if r.get("attribution") is not None]
    if not rated:
        return missing("no row carries attribution", "attribution")
    ok = [r for r in rated if r["attribution"] != "unknown"]
    return ratio(len(ok), len(rows)), {"rows": len(rows), "attributed": len(ok), "unrecorded": len(rows) - len(rated)}, [], None

def m_audit_trace_completeness(s, e, cfg):
    steps = work_steps(s)
    ok = [x for x in steps if x.get("observable_id")]
    return ratio(len(ok), len(steps)), {"steps": len(steps)}, [], None

def m_audit_execution_reconstruction_rate(s, e, cfg):
    lanes = sub_lanes(s)
    steps = work_steps(s)
    ok = [x for x in steps if x.get("turn_id") and x.get("observable_id")
          and (x["lane"] not in lanes or linked(lanes[x["lane"]]))]
    return ratio(len(ok), len(steps)), {"steps": len(steps), "reconstructable": len(ok)}, [], None

def m_audit_event_ordering_consistency(s, e, cfg):
    turns = defaultdict(list)
    for r in unique_rows(s):
        if r.get("turn_id") and r.get("sequence_num") is not None and r.get("occurred_at"):
            turns[r["turn_id"]].append(r)
    pairs, bad = 0, []
    for rows in turns.values():
        rows.sort(key=lambda r: num(r["sequence_num"]))
        for a, b in zip(rows, rows[1:]):
            gap = seconds(a["occurred_at"], b["occurred_at"])
            if gap is None:
                continue
            pairs += 1
            if gap < 0:
                bad.append(b)
    if not pairs:
        return missing("no turn has two rows with sequence_num", "sequence_num")
    return round(1 - len(bad) / pairs, 3), {"pairs": pairs, "out_of_order": len(bad)}, refs("observable", bad, "observable_id"), None

def m_audit_timestamp_integrity(s, e, cfg):
    rows = [r for r in unique_rows(s) if r.get("occurred_at") and r.get("captured_at") and r.get("ingested_at")]
    if not rows:
        return missing("no row carries occurred_at, captured_at and ingested_at", "captured_at", "ingested_at")
    read, bad = [], []
    for r in rows:
        a, b, c = (signal_traces.ts(r[k]) for k in ("occurred_at", "captured_at", "ingested_at"))
        if not (a and b and c) or len({x.tzinfo is None for x in (a, b, c)}) > 1:
            continue
        read.append(r)
        if not a <= b <= c:
            bad.append(r)
    if not read:
        return missing("timestamps could not be read or mix zoned and unzoned values", "captured_at", "ingested_at")
    return round(1 - len(bad) / len(read), 3), {"rows": len(read), "violations": len(bad), "unreadable": len(rows) - len(read)}, \
        refs("observable", bad, "observable_id"), None

def m_audit_source_provenance_completeness(s, e, cfg):
    if not fetched(s, "raw_ref"):
        return missing("raw_ref was not read for this session", "raw_ref")
    rows = unique_rows(s)
    ok = [r for r in rows if r.get("collector") and r.get("raw_ref")]
    return ratio(len(ok), len(rows)), {"rows": len(rows), "with_collector": sum(1 for r in rows if r.get("collector")),
                                       "with_raw_ref": sum(1 for r in rows if r.get("raw_ref"))}, [], None

def m_audit_conflicting_record_rate(s, e, cfg):
    multi = [r for r in unique_rows(s) if r.get("collectors_agree") is not None]
    if not multi:
        return missing("no row was seen by more than one collector", "collectors_agree")
    bad = [r for r in multi if r["collectors_agree"] is False]
    return ratio(len(bad), len(multi)), {"cross_checked": len(multi)}, refs("observable", bad, "observable_id"), None

def m_audit_unverifiable_record_rate(s, e, cfg):
    rows = [r for r in unique_rows(s) if r.get("confidence")]
    if not rows:
        return missing("no row carries confidence", "confidence")
    bad = [r for r in rows if r["confidence"] == "inferred" and not r.get("derived_from")]
    return ratio(len(bad), len(rows)), {"rows": len(rows)}, refs("observable", bad, "observable_id"), None

def m_audit_metric_evidence_linkage(s, e, cfg):
    cited = [x for ev in e["events_by_session"].get(s.id, []) for x in ev["evidence_refs"]]
    if not cited:
        return missing("no metric event of this session cites evidence", "evidence_refs", availability="NOT_OBSERVED")
    have = {f"observable:{r.get('observable_id')}" for r in s.rows} | \
           {f"observable:{x.get('observable_id')}" for x in s.effects} | {f"step:{x['step_id']}" for x in s.steps}
    bad = [x for x in cited if x not in have]
    return ratio(len(cited) - len(bad), len(cited)), {"refs": len(cited), "unresolved": bad[:10]}, [], None

def m_agent_ownership_attribution(s, e, cfg):
    return (1 if s.operator else 0), {"operator": s.operator}, [], None

def m_agent_configuration_change_rate(s, e, cfg):
    hits = of_type(s, "collector_config_changed", "permission_mode_change")
    return len(hits), {"by_type": dict(Counter(r["observable_type"] for r in hits))}, refs("observable", hits, "observable_id"), None

# ---- history: earlier sessions of the same operator in the fetched estate

def earlier(s, e, same):
    if not s.operator:
        return None
    return [x for x in e["sessions"] if x.operator == s.operator and x is not s
            and (x.start or "") < (s.start or "") and same(x)]

def no_history(prior, cfg, need=None):
    need = cfg["min_sessions_for_baseline"] if need is None else need
    if prior is None:
        return missing("session has no operator, so it has no history", "operator_email")
    if len(prior) < need:
        return missing(f"{len(prior)} earlier sessions; {need} needed", availability="INSUFFICIENT_HISTORY")

def files_of(s):
    return {r["file_path"] for r in unique_rows(s) + s.effects if has(r.get("file_path"))}

def m_resource_access_novelty_files(s, e, cfg):
    prior = earlier(s, e, lambda x: x.workspace == s.workspace)
    gap = no_history(prior, cfg)
    if gap:
        return gap
    mine = files_of(s)
    if not mine:
        return missing("session touched no file path", "file_path")
    seen = set().union(*(files_of(x) for x in prior))
    new = sorted(mine - seen)
    return ratio(len(new), len(mine)), {"files": len(mine), "new": new[:10], "earlier_sessions": len(prior)}, [], None

def m_interaction_route_novelty(s, e, cfg):
    prior = earlier(s, e, lambda x: x.tool == s.tool)
    gap = no_history(prior, cfg)
    if gap:
        return gap
    mine = {(a, b) for a, b, _ in interaction_edges(s)}
    if not mine:
        return missing("session has no interaction edges", "agent_id", "mcp_server", availability="NOT_OBSERVED")
    seen = {(a, b) for x in prior for a, b, _ in interaction_edges(x)}
    new = sorted(f"{a}>{b}" for a, b in mine - seen)
    return ratio(len(new), len(mine)), {"edges": len(mine), "new": new[:10], "earlier_sessions": len(prior)}, [], None

def trend(base):
    def fn(s, e, cfg):
        prior = earlier(s, e, lambda x: x.tool == s.tool)
        gap = no_history(prior, cfg)
        if gap:
            return gap
        vals = [e["values"].get(x.id, {}).get(base) for x in prior + [s]]
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) < cfg["min_sessions_for_baseline"] + 1:
            return missing(f"{base} has a value in {len(pts)} of these sessions", base,
                           availability="INSUFFICIENT_HISTORY")
        mx = sum(i for i, _ in pts) / len(pts)
        my = sum(v for _, v in pts) / len(pts)
        den = sum((i - mx) ** 2 for i, _ in pts)
        slope = sum((i - mx) * (v - my) for i, v in pts) / den
        return round(slope, 3), {"sessions": len(pts), "metric": base, "unit": f"{base} per session"}, [], None
    return fn

def previous(s, e):
    prior = earlier(s, e, lambda x: x.tool == s.tool)
    return prior, (max(prior, key=lambda x: x.start or "") if prior else None)

def latest(s, key):
    return next((r[key] for r in reversed(unique_rows(s)) if has(r.get(key))), None)

def changed_from_previous(fields):
    def fn(s, e, cfg):
        prior, last = previous(s, e)
        gap = no_history(prior, cfg, need=1)
        if gap:
            return gap
        now = {f: latest(s, f) for f in fields}
        if all(v is None for v in now.values()):
            return missing("session carries none of the compared fields", *fields)
        before = {f: latest(last, f) for f in fields}
        diff = sorted(f for f in fields if now[f] is not None and before[f] is not None and now[f] != before[f])
        unknown = sorted(f for f in fields if now[f] is None or before[f] is None)
        return len(diff), {"changed": diff, "not_comparable": unknown, "previous_session": last.id}, [], None
    return fn

def tool_sets(s):
    out = defaultdict(set)
    for r in unique_rows(s):
        if has(r.get("mcp_tools_available")):
            out[r.get("mcp_server") or ""].update(r["mcp_tools_available"])
    return out

def m_agent_capability_change_count(s, e, cfg):
    prior, last = previous(s, e)
    gap = no_history(prior, cfg, need=1)
    if gap:
        return gap
    now, before = tool_sets(s), tool_sets(last)
    if not now or not before:
        return missing("this or the previous session carries no mcp_tools_available", "mcp_tools_available")
    a, b = set().union(*now.values()), set().union(*before.values())
    return len(a ^ b), {"added": sorted(a - b)[:10], "removed": sorted(b - a)[:10], "previous_session": last.id}, [], None

def m_agent_mcp_configuration_drift(s, e, cfg):
    prior, last = previous(s, e)
    gap = no_history(prior, cfg, need=1)
    if gap:
        return gap
    def versions(x):
        return {r["mcp_server"]: r["mcp_server_version"] for r in unique_rows(x)
                if has(r.get("mcp_server")) and has(r.get("mcp_server_version"))}
    now, before = versions(s), versions(last)
    both = set(now) & set(before)
    if not both:
        return missing("no MCP server reports a version in both sessions", "mcp_server_version")
    tools_now, tools_before = tool_sets(s), tool_sets(last)
    drift = sorted(k for k in both if now[k] != before[k] or tools_now.get(k) != tools_before.get(k))
    return len(drift), {"servers_compared": len(both), "drifted": drift, "previous_session": last.id}, [], None

def m_agent_connectivity_change_rate(s, e, cfg):
    prior, last = previous(s, e)
    gap = no_history(prior, cfg, need=1)
    if gap:
        return gap
    a = {r["mcp_server"] for r in unique_rows(s) if has(r.get("mcp_server"))}
    b = {r["mcp_server"] for r in unique_rows(last) if has(r.get("mcp_server"))}
    return len(a ^ b), {"added": sorted(a - b), "removed": sorted(b - a), "previous_session": last.id}, [], None

def m_agent_machine_change_frequency(s, e, cfg):
    prior = earlier(s, e, lambda x: x.tool == s.tool)
    gap = no_history(prior, cfg, need=1)
    if gap:
        return gap
    seq = [latest(x, "machine_id") or latest(x, "hostname") for x in sorted(prior, key=lambda x: x.start or "") + [s]]
    seq = [v for v in seq if v]
    if len(seq) < 2:
        return missing("fewer than two sessions carry machine_id or hostname", "machine_id", "hostname")
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b), {"sessions": len(seq), "machines": len(set(seq))}, [], None

def m_agent_organizational_mapping_consistency(s, e, cfg):
    prior = earlier(s, e, lambda x: True)
    gap = no_history(prior, cfg, need=1)
    if gap:
        return gap
    pairs = {(latest(x, "org_id"), latest(x, "team_id")) for x in prior + [s]}
    pairs.discard((None, None))
    if not pairs:
        return missing("no session of this operator carries org_id or team_id", "org_id", "team_id")
    return len(pairs), {"mappings": sorted(f"{a}/{b}" for a, b in pairs)}, [], None


CB = {
    "plans":        lambda s, cfg: capable(s, cfg, "plan_events", of_type(s, "plan_created", "plan_completed")),
    "compactions":  lambda s, cfg: capable(s, cfg, "compaction_events", of_type(s, "compaction")),
    "results":      lambda s, cfg: bool(of_type(s, *CALL_RESULTS)),
    "ingest":       lambda s, cfg: any(r.get("ingested_at") for r in s.rows),
    "status":       lambda s, cfg: any(has(r.get("status")) for r in s.actions),
    "denials":      lambda s, cfg: approvals_visible(s, cfg) or B["our_rules"](s),
    "config_events": lambda s, cfg: capable(s, cfg, "config_events",
                                           of_type(s, "collector_config_changed", "permission_mode_change")),
}
CA = {
    "plans":     lambda s: bool(of_type(s, "plan_created", "plan_completed")),
    "changes":   lambda s: any(x["step"] in ("CHANGE", "CREATE", "DELETE") for x in s.steps),
    "grants":    lambda s: bool(of_type(s, "permission_granted")),
    "failures":  lambda s: any(x["failed"] for x in s.steps),
    "multilane": lambda s: sum(1 for v in lane_work(s).values() if v) > 1,
    "lanes":     lambda s: any(v for v in lane_work(s).values()),
    "file_rows": lambda s: bool(s.actions) or any(has(r.get("file_path")) for r in s.rows),
}

def basis_of(name, s, cfg):
    if name in CB:
        return CB[name](s, cfg)
    return approvals_visible(s, cfg) if name == "approvals" else B[name](s)

def applies_of(name, s):
    return (CA.get(name) or A[name])(s)

CATALOGUE = [
    ("task_progress",                      "Plan items completed / created",           m_task_progress,                 "plans",       ["plan_item", "plan_status", "observable_type"], "plans"),
    ("unresolved_items",                   "Plan items created and not completed",     m_unresolved_items,              "plans",       ["plan_item", "plan_status", "observable_type"], "plans"),
    ("workflow_rework_rate",               "Files changed again in a later turn",      m_workflow_rework_rate,          "always",      ["file_path", "turn_id", "tool_name"], "changes"),
    ("stalled_duration_per_turn",          "Longest hung time in one turn (s)",        m_stalled_duration_per_turn,     "wrapper",     ["hung_seconds", "turn_id"], "always"),
    ("excessive_delegation_ratio",         "Delegations per action",                   m_excessive_delegation_ratio,    "always",      ["observable_type", "tool_name"], "actions"),
    ("event_freshness_live",               "Median ingest lag (s)",                    m_event_freshness_live,          "ingest",      ["occurred_at", "ingested_at"], "always"),
    ("missing_event_rate",                 "Call requests with no result",             m_missing_event_rate,            "results",     ["tool_use_id", "observable_type"], "actions"),
    ("audit_missing_event_rate",           "Call requests with no result",             m_missing_event_rate,            "results",     ["tool_use_id", "observable_type"], "actions"),
    ("token_expansion_per_turn",           "Largest turn-on-turn token ratio",         m_token_expansion_per_turn,      "tokens",      ["total_tokens", "turn_id"], "always"),
    ("compaction_count",                   "Context compactions",                      m_compaction_count,              "compactions", ["observable_type", "compaction_reason"], "always"),
    ("interaction_edge_count",             "Distinct delegation and MCP edges",        m_interaction_edge_count,        "always",      ["agent_id", "parent_agent_id", "mcp_server"], "always"),
    ("interaction_unique_entities",        "Distinct agents, MCP servers and tools",   m_interaction_unique_entities,   "always",      ["agent_id", "mcp_server", "tool_name"], "actions"),
    ("interaction_graph_completeness",     "Subagent rows with a resolvable parent",   m_interaction_graph_completeness, "always",     ["agent_id", "parent_agent_id", "observable_type"], "subagents"),
    ("interaction_propagation_depth",      "Deepest delegation level",                 m_interaction_propagation_depth, "always",      ["agent_id", "parent_agent_id", "delegation_depth"], "always"),
    ("interaction_propagation_breadth",    "Most subagents under one parent",          m_interaction_propagation_breadth, "always",    ["agent_id", "parent_agent_id"], "subagents"),
    ("interaction_orphaned_events",        "Subagent rows with no known parent",       m_interaction_orphaned_events,   "always",      ["agent_id", "parent_agent_id", "observable_type"], "subagents"),
    ("unauthorized_action_attempts",       "Denied or blocked actions",                m_unauthorized_action_attempts,  "denials",     ["observable_type", "status", "tool_use_id"], "always"),
    ("boundary_violation_count",           "Outside-workspace or policy-violating rows", m_boundary_violation_count,   "paths",       ["outside_workspace", "violation_count"], "always"),
    ("restricted_resource_access_rate",    "Tool and file rows at sensitivity tier 3+", m_restricted_resource_access_rate, "paths",    ["sensitivity_tier", "file_path", "tool_name"], "file_rows"),
    ("approval_action_linkage",            "Grants followed by their action",          m_approval_action_linkage,       "always",      ["observable_type", "tool_use_id"], "grants"),
    ("policy_violation_rate",              "Actions with violation_count > 0",         m_policy_violation_rate,         "our_rules",   ["violation_count"], "actions"),
    ("token_consumption_per_session",      "Session tokens",                           m_token_consumption_per_session, "tokens",      ["total_tokens"], "always"),
    ("token_consumption_per_turn",         "Most tokens in one turn",                  m_token_consumption_per_turn,    "tokens",      ["total_tokens", "turn_id"], "always"),
    ("cost_per_session",                   "Session cost (USD)",                       m_cost_per_session,              "always",      ["cost_usd", "cost_is_estimated"], "always"),
    ("execution_duration_per_session",     "Session duration (s)",                     m_execution_duration_per_session, "always",     ["occurred_at"], "always"),
    ("execution_duration_threshold_breach", "Session duration against its threshold",  m_execution_duration_threshold_breach, "always", ["occurred_at"], "always"),
    ("tool_timeout_rate",                  "Actions that timed out",                   m_tool_timeout_rate,             "status",      ["status"], "actions"),
    ("resource_consumption_rate",          "Tokens per minute",                        m_resource_consumption_rate,     "tokens",      ["total_tokens", "occurred_at"], "always"),
    ("resource_concurrency_level",         "Most steps running at once",               m_resource_concurrency_level,    "durations",   ["occurred_at", "tool_duration_ms", "tool_use_id"], "steps"),
    ("resource_stall_rate",                "Steps that stalled",                       m_resource_stall_rate,           "wrapper",     ["hung_seconds"], "steps"),
    ("resource_redundant_execution_cost",  "Time spent on repeated calls (ms)",        m_resource_redundant_execution_cost, "args",    ["tool_name", "tool_arguments", "turn_id", "tool_duration_ms"], "actions"),
    ("subagent_resource_consumption",      "Tokens attributed to subagents",           m_subagent_resource_consumption, "tokens",      ["total_tokens", "agent_id"], "subagents"),
    ("behavior_action_sequence_frequency", "Distinct three-step sequences",            m_behavior_action_sequence_frequency, "always", ["observable_type", "tool_name", "command"], "steps"),
    ("behavior_tool_usage_distribution",   "Share of the most used tool",              m_behavior_tool_usage_distribution, "always",   ["tool_name"], "actions"),
    ("behavior_tool_transition_frequency", "Distinct tool-to-tool transitions",        m_behavior_tool_transition_frequency, "always", ["tool_name"], "actions"),
    ("behavior_model_variation",           "Distinct models in session",               m_behavior_model_variation,      "always",      ["model"], "always"),
    ("recovery_disruption_count",          "Failed, stalled or interrupted steps",     m_recovery_disruption_count,     "outcomes",    ["success", "exit_code", "hung_seconds", "tool_interrupted"], "steps"),
    ("recovery_failure_rate",              "Re-run failures that failed again",        m_recovery_failure_rate,         "commands",    ["command", "exit_code", "success"], "failures"),
    ("recovery_time",                      "Median time to a successful re-run (s)",   m_recovery_time,                 "commands",    ["command", "exit_code", "occurred_at"], "failures"),
    ("recovery_workflow_resumption_rate",  "Failures followed by successful work",     m_recovery_workflow_resumption_rate, "outcomes", ["success", "exit_code", "occurred_at"], "failures"),
    ("recovery_retry_recurrence",          "Commands re-run after they succeeded",     m_recovery_retry_recurrence,     "commands",    ["command", "exit_code"], "commands"),
    ("recovery_stability_duration",        "Median time from recovery to next failure (s)", m_recovery_stability_duration, "commands", ["command", "exit_code", "occurred_at"], "failures"),
    ("workload_distribution_balance",      "Share of steps in the busiest lane",       m_workload_distribution_balance, "always",      ["agent_id"], "multilane"),
    ("workload_per_agent",                 "Steps in the busiest lane",                m_workload_per_agent,            "always",      ["agent_id", "total_tokens"], "lanes"),
    ("workload_per_model",                 "Models with token use",                    m_workload_per_model,            "tokens",      ["model", "total_tokens"], "always"),
    ("agent_utilization_rate",             "Mean busy share of each lane",             m_agent_utilization_rate,        "durations",   ["agent_id", "tool_duration_ms", "occurred_at"], "lanes"),
    ("agent_idle_time",                    "Longest gap between steps in a lane (s)",  m_agent_idle_time,               "always",      ["agent_id", "occurred_at"], "lanes"),
    ("parallel_execution_efficiency",      "Step time / wall time",                    m_parallel_execution_efficiency, "durations",   ["tool_duration_ms", "occurred_at"], "steps"),
    ("delegation_overhead_ratio",          "Lane lead time / lane time",               m_delegation_overhead_ratio,     "always",      ["agent_id", "observable_type", "occurred_at"], "subagents"),
    ("subagent_resource_overhead",         "Subagent tokens / session tokens",         m_subagent_resource_overhead,    "tokens",      ["total_tokens", "agent_id"], "subagents"),
    ("workload_concurrency_level",         "Most lanes active at once",                m_workload_concurrency_level,    "always",      ["agent_id", "occurred_at"], "lanes"),
    ("audit_actor_attribution_rate",       "Rows naming an operator",                  m_audit_actor_attribution_rate,  "always",      ["operator_id", "operator_email", "operator_username"], "always"),
    ("audit_action_attribution_rate",      "Rows with a known attribution",            m_audit_action_attribution_rate, "always",      ["attribution"], "always"),
    ("audit_trace_completeness",           "Steps citing their observable",            m_audit_trace_completeness,      "always",      ["observable_id"], "steps"),
    ("audit_execution_reconstruction_rate", "Steps placed in a turn and a lane",       m_audit_execution_reconstruction_rate, "always", ["turn_id", "observable_id", "agent_id"], "steps"),
    ("audit_event_ordering_consistency",   "Sequence order agrees with time order",    m_audit_event_ordering_consistency, "always",   ["sequence_num", "occurred_at", "turn_id"], "always"),
    ("audit_timestamp_integrity",          "occurred <= captured <= ingested",         m_audit_timestamp_integrity,     "always",      ["occurred_at", "captured_at", "ingested_at"], "always"),
    ("audit_source_provenance_completeness", "Rows naming collector and raw_ref",      m_audit_source_provenance_completeness, "always", ["collector", "raw_ref"], "always"),
    ("audit_conflicting_record_rate",      "Cross-checked rows that disagree",         m_audit_conflicting_record_rate, "always",      ["collectors_agree"], "multi"),
    ("audit_unverifiable_record_rate",     "Inferred rows with no source",             m_audit_unverifiable_record_rate, "always",     ["confidence", "derived_from"], "always"),
    ("audit_metric_evidence_linkage",      "Metric evidence refs that resolve",        m_audit_metric_evidence_linkage, "always",      ["observable_id"], "always"),
    ("agent_ownership_attribution",        "Session has an operator",                  m_agent_ownership_attribution,   "always",      ["operator_email", "operator_username"], "always"),
    ("agent_configuration_change_rate",    "Config and permission-mode changes",       m_agent_configuration_change_rate, "config_events", ["observable_type"], "always"),
    ("resource_access_novelty_files",      "Files new for this operator and workspace", m_resource_access_novelty_files, "paths",      ["file_path", "operator_email", "workspace"], "always"),
    ("interaction_route_novelty",          "Interaction edges new for this operator",  m_interaction_route_novelty,     "always",      ["agent_id", "parent_agent_id", "mcp_server", "operator_email"], "always"),
    ("behavior_repetition_trend",          "Slope of insights.repetition",             trend("insights.repetition"),    "always",      ["operator_email"], "always"),
    ("behavior_retry_trend",               "Slope of insights.retry",                  trend("insights.retry"),         "always",      ["operator_email"], "always"),
    ("behavior_failure_trend",             "Slope of insights.failed",                 trend("insights.failed"),        "always",      ["operator_email"], "always"),
    ("agent_permission_drift",             "Permission mode changed since last session", changed_from_previous(["permission_mode"]), "always", ["permission_mode"], "always"),
    ("agent_tool_inventory_change",        "Tool version changed since last session",  changed_from_previous(["tool_version"]), "always", ["tool_version"], "always"),
    ("agent_runtime_environment_drift",    "Tool version or host changed",             changed_from_previous(["tool_version", "hostname"]), "always", ["tool_version", "hostname"], "always"),
    ("agent_capability_change_count",      "MCP tools added or removed",               m_agent_capability_change_count, "mcp",         ["mcp_tools_available"], "always"),
    ("agent_mcp_configuration_drift",      "MCP servers whose version or tools changed", m_agent_mcp_configuration_drift, "mcp",       ["mcp_server", "mcp_server_version", "mcp_tools_available"], "always"),
    ("agent_connectivity_change_rate",     "MCP servers added or removed",             m_agent_connectivity_change_rate, "mcp",        ["mcp_server"], "always"),
    ("agent_machine_change_frequency",     "Machine changes across sessions",          m_agent_machine_change_frequency, "always",     ["machine_id", "hostname"], "always"),
    ("agent_organizational_mapping_consistency", "Distinct org/team mappings for operator", m_agent_organizational_mapping_consistency, "always", ["org_id", "team_id"], "always"),
]
CATALOGUE_IDS = [m[0] for m in CATALOGUE]


def catalogue_events(sessions, est, events, cfg, computed_at):
    by_session = defaultdict(list)
    for ev in events:
        by_session[ev["session_id"]].append(ev)
    est = {**est, "sessions": sessions, "events_by_session": by_session,
           "values": {sid: {ev["metric_id"]: ev["value"] for ev in evs if ev["state"] not in ("UNKNOWN", "NOT_APPLICABLE")}
                      for sid, evs in by_session.items()}}
    out = []
    for s in sessions:
        for mid, name, fn, basis, fields, applies in CATALOGUE:
            t = cfg["thresholds"].get(mid, {})
            could_fire = basis_of(basis, s, cfg)
            value, feats, ev, trace_id, err = None, {}, [], None, None
            if not applies_of(applies, s):
                state, feats = "NOT_APPLICABLE", {"reason": f"did not arise: {applies}", "availability": "NOT_APPLICABLE"}
            elif not could_fire:
                state, feats = "UNKNOWN", {"reason": f"basis absent: {basis}", "availability": "MISSING_DATA"}
            else:
                try:
                    value, feats, ev, trace_id = fn(s, est, cfg)
                except Exception as ex:
                    value, feats, err = None, {"availability": "ERROR"}, f"{type(ex).__name__}: {ex}"
                if value is None:
                    state = "UNKNOWN"
                else:
                    state = judge(value, t) if t else "NO_THRESHOLD"
                    feats = {**feats, "availability": "CALCULATED"}
            out.append(event(s, mid, name, value, t, state, feats, ev, trace_id,
                             fields, basis, could_fire, cfg, computed_at, err))
    return out


def catalogue_summary(events):
    kinds = ("CALCULATED", "MISSING_DATA", "MISSING_REFERENCE", "INSUFFICIENT_HISTORY", "NOT_OBSERVED",
             "NOT_APPLICABLE", "ERROR")
    avail = defaultdict(Counter)
    for e in events:
        if e["metric_id"] in CATALOGUE_IDS:
            avail[e["metric_id"]][e["features"].get("availability")] += 1
    print(f"\n{'catalogue metric':<42}" + "".join(f"{k[:12]:>13}" for k in kinds))
    for mid in CATALOGUE_IDS:
        print(f"{mid:<42}" + "".join(f"{avail[mid][k]:>13}" for k in kinds))



def metric_event(s, entry, est, cfg, computed_at):
    """One METRICS entry for one session against the estate est, exactly as compute() reads it (a test holds the
    two equal). For a caller that needs another comparison group, such as a leave-one-out estate."""
    mid, name, fn, basis, fields, applies = entry
    t = cfg["thresholds"].get(mid, {})
    could_fire = approvals_visible(s, cfg) if basis == "approvals" else B[basis](s)
    if not A[applies](s):
        return event(s, mid, name, None, t, "NOT_APPLICABLE", {"reason": f"did not arise: {applies}"}, [], None,
                     fields, basis, could_fire, cfg, computed_at, None)
    try:
        value, feats, ev, trace_id = fn(s, est, cfg)
        err = None
    except Exception as ex:
        value, feats, ev, trace_id, err = None, {}, [], None, f"{type(ex).__name__}: {ex}"
    state = judge(value, t)
    if err:
        state = "UNKNOWN"
    elif "_state" in feats:
        state = feats.pop("_state")
    elif state == "NORMAL" and not could_fire and not value:
        state = "UNKNOWN"
        feats = {**feats, "reason": f"basis absent: {basis}"}
    return event(s, mid, name, value, t, state, feats, ev, trace_id, fields, basis, could_fire, cfg, computed_at,
                 err)


if __name__ == "__main__":
    main()

