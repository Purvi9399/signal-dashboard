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
        self.actions = [r for r in rows if has(r.get("tool_name"))]
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

def build_sessions(rows):
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
    return [Session(sid, rows_by[sid], traces.get(sid), steps_by[sid], fx_by[sid])
            for sid in sorted(rows_by)]


def compute(rows, cfg=None, computed_at=None):
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session")
    a = ap.parse_args()
    if not (URL and KEY):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY (source ~/.signal-env)")
    rows = signal_traces.get("observables", f"select={COLS}&order=occurred_at.asc")
    events, sessions = compute(rows)

    by_state = Counter(e["state"] for e in events)
    print(f"\n{len(sessions)} sessions, {len(events):,} metric events  {dict(by_state)}")
    per = defaultdict(Counter)
    for e in events:
        per[e["metric_id"]][e["state"]] += 1
    print(f"\n{'metric':<30}" + "".join(f"{s:>10}" for s in STATES))
    for mid in METRIC_IDS + ["telemetry.unknown_share"]:
        print(f"{mid:<30}" + "".join(f"{per[mid][s]:>10}" for s in STATES))

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


if __name__ == "__main__":
    main()
