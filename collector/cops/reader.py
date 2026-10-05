"""
DashboardMetricReader

The only way a COP touches data.

read_metrics   selects precomputed rows from metric_events. It calculates
               nothing; if a metric is missing, it says so.

read_context   runs one of a fixed set of narrow queries, each bounded to one
               session or one trace and a handful of columns. There is no
               way to ask for a whole table through it. Every call is
               returned with its row count and time, so the decision log
               shows exactly what each COP looked at.

Two backends: Supabase for real use, Memory for tests and for running
straight after signal_metrics.compute() without a round trip.
"""

import json, os, time, urllib.parse, urllib.request, urllib.error
from collections import Counter

from .models import MetricEvent

# columns a context query may return from observables: identifiers and
# facts, never prompt, response, stdout or tool_result content
SAFE_OBS = ("observable_id,session_id,turn_id,agent_id,tool,collector,observable_type,"
            "occurred_at,tool_name,mcp_server,file_path,exit_code,success,sensitivity_tier,"
            "outside_workspace,secret_detected,policy_rule_matched,permission_decision,"
            "attribution,collectors_agree,raw_ref")
MAX_ROWS = 300


def _lane(trace_id):
    """trace ids are 'session:lane' or 'session:lane:seq'."""
    parts = (trace_id or "").split(":")
    return parts[1] if len(parts) >= 2 else "main"


class SupabaseBackend:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_KEY", "")

    def _get(self, path, params, limit=MAX_ROWS):
        q = urllib.parse.urlencode(params, safe=",.()*:")
        req = urllib.request.Request(
            f"{self.url}/rest/v1/{path}?{q}",
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                     "Range": f"0-{limit - 1}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def metrics(self, session_id, keys):
        ids = ",".join(f'"{k}"' for k in keys)
        return self._get("metric_events", {"session_id": f"eq.{session_id}",
                                           "metric_id": f"in.({ids})", "select": "*"})

    def sessions(self, limit):
        rows = self._get("metric_events", {"select": "session_id,session_started_at",
                                           "metric_id": "eq.telemetry.unknown_share",
                                           "order": "session_started_at.desc"}, limit)
        return [r["session_id"] for r in rows]

    def lane_path(self, session_id, trace_id):
        lane = _lane(trace_id)
        return self._get("trace_steps", {"session_id": f"eq.{session_id}", "lane": f"eq.{lane}",
                                         "select": "step_id,seq,step,failed,tool_name,decided_by",
                                         "order": "seq.asc"})

    def evidence_rows(self, refs):
        ids = [r.split(":", 1)[1] for r in refs if r.startswith("observable:")][:10]
        if not ids:
            return []
        return self._get("observables", {"observable_id": f"in.({','.join(ids)})",
                                         "select": SAFE_OBS}, 10)

    def matched_policy(self, session_id):
        return self._get("trace_steps", {"session_id": f"eq.{session_id}",
                                         "policy_rule_matched": "not.is.null",
                                         "select": "step_id,lane,step,tool_name,file_path,policy_rule_matched"}, 50)

    def collector_breakdown(self, session_id):
        rows = self._get("observables", {"session_id": f"eq.{session_id}", "select": "collector"}, 5000)
        return [{"collector": c, "rows": n} for c, n in Counter(r["collector"] for r in rows).items()]


class MemoryBackend:
    """Over signal_metrics.compute() output: (events, sessions)."""

    def __init__(self, events, sessions=()):
        self.events = events
        self.sess = {s.id: s for s in sessions}

    def metrics(self, session_id, keys):
        return [e for e in self.events if e["session_id"] == session_id and e["metric_id"] in keys]

    def sessions(self, limit):
        seen = []
        for e in self.events:
            if e["session_id"] not in seen:
                seen.append(e["session_id"])
        return seen[:limit]

    def lane_path(self, session_id, trace_id):
        s = self.sess.get(session_id)
        steps = (s.trace["lanes"].get(_lane(trace_id), []) if s else [])[:MAX_ROWS]
        return [{k: x.get(k) for k in ("step_id", "seq", "step", "failed", "tool_name", "decided_by")}
                for x in steps]

    def evidence_rows(self, refs):
        ids = {r.split(":", 1)[1] for r in refs if r.startswith("observable:")}
        out = []
        for s in self.sess.values():
            out += [{k: r.get(k) for k in SAFE_OBS.split(",")} for r in s.rows if r.get("observable_id") in ids]
        return out[:10]

    def matched_policy(self, session_id):
        s = self.sess.get(session_id)
        return [{k: x.get(k) for k in ("step_id", "lane", "step", "tool_name", "file_path", "policy_rule_matched")}
                for x in (s.steps if s else []) if x.get("policy_rule_matched")][:50]

    def collector_breakdown(self, session_id):
        s = self.sess.get(session_id)
        return [{"collector": c, "rows": n}
                for c, n in Counter(r.get("collector") for r in (s.rows if s else [])).items()]


# The complete list of things a COP may ask for. Anything else is refused.
CONTEXT = {
    "lane_path":           lambda b, sid, tid, refs: b.lane_path(sid, tid),
    "evidence_rows":       lambda b, sid, tid, refs: b.evidence_rows(refs),
    "matched_policy":      lambda b, sid, tid, refs: b.matched_policy(sid),
    "collector_breakdown": lambda b, sid, tid, refs: b.collector_breakdown(sid),
}


class DashboardMetricReader:
    def __init__(self, backend):
        self.b = backend

    def read_metrics(self, session_id, metric_keys):
        rows = self.b.metrics(session_id, tuple(metric_keys))
        events = [MetricEvent.from_row(r) for r in rows]
        found = {e.metric_id for e in events}
        return events, [k for k in metric_keys if k not in found]

    def read_context(self, session_id, trace_id, context_keys, evidence_refs=(), allowed=()):
        out, log = {}, []
        for key in context_keys:
            t0 = time.perf_counter()
            if key not in CONTEXT or (allowed and key not in allowed):
                log.append({"key": key, "rows": 0, "ms": 0, "error": "not permitted for this COP"})
                continue
            try:
                rows = CONTEXT[key](self.b, session_id, trace_id, list(evidence_refs))
                out[key] = rows
                log.append({"key": key, "rows": len(rows), "trace_id": trace_id,
                            "ms": int((time.perf_counter() - t0) * 1000)})
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                log.append({"key": key, "rows": 0, "ms": int((time.perf_counter() - t0) * 1000),
                            "error": f"{type(e).__name__}: {e}"[:200]})
        return out, log

    def sessions(self, limit=20):
        return self.b.sessions(limit)
