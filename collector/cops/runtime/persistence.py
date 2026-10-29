"""
Where runtime results go.

One column list per table, used three ways so they cannot drift apart:
    the SQLite store creates its tables from it (local, durable, tested);
    the Supabase writer builds payloads from it (column for column);
    tests/test_runtime_persistence.py parses cops_schema.sql and
    cops_runtime_schema.sql and checks both agree with it.

decision_row() is the single conversion from a COP result to a cop_decisions
row. Both stores call it; nothing else builds that row.

Nothing in this module writes to a remote database unless a caller passes a
poster explicitly and the environment names a database. Tests use a fake
poster and assert on the payloads.
"""

import json, sqlite3, threading, uuid

from ..models import NS as DECISION_NS
from .contracts import digest, CONTRACT_VERSION

# (column, postgres type). The first group mirrors cops_schema.sql exactly;
# the second is added by cops_runtime_schema.sql.
COP_DECISIONS_BASE = [
    ("decision_id", "uuid"), ("run_id", "text"), ("cop", "text"), ("cop_version", "text"),
    ("session_id", "text"), ("trace_id", "text"), ("metric_id", "text"),
    ("behavioural_question", "text"), ("evidence_refs", "text[]"), ("metrics_read", "jsonb"),
    ("context_used", "jsonb"), ("upstream_decisions", "text[]"), ("interpretation", "text"),
    ("rationale", "text"), ("confidence", "numeric(4,3)"), ("evidence_confidence", "numeric(4,3)"),
    ("severity", "text"), ("action", "text"), ("action_reason", "text"),
    ("downstream_cops", "text[]"), ("interpreter", "text"), ("skipped_interpreter", "boolean"),
    ("mode", "text"), ("evaluation_ms", "int"), ("failed", "boolean"), ("error", "text"),
    ("decided_at", "timestamptz"), ("inputs_hash", "text"),
]
COP_DECISIONS_RUNTIME = [
    ("case_id", "text"), ("tenant_id", "text"), ("contract_version", "text"),
    ("cop_mode", "text"), ("decision_status", "text"), ("verified", "boolean"),
    ("finding", "jsonb"), ("verifier_results", "jsonb"), ("proposed_action", "jsonb"),
    ("execution_status", "text"), ("llm_calls", "int"), ("tool_calls", "int"),
    ("node_timings", "jsonb"),
]

TABLES = {
    "cop_decisions": {"columns": COP_DECISIONS_BASE + COP_DECISIONS_RUNTIME,
                      "key": "decision_id"},
    "cop_llm_invocations": {"key": "invocation_id", "columns": [
        ("invocation_id", "text"), ("decision_id", "uuid"), ("cop", "text"), ("case_id", "text"),
        ("purpose", "text"), ("provider", "text"), ("model", "text"), ("model_version", "text"),
        ("prompt_version", "text"), ("simulated", "boolean"), ("evidence_refs", "text[]"),
        ("tools_exposed", "text[]"), ("input_tokens", "int"), ("output_tokens", "int"),
        ("latency_ms", "numeric"), ("cost_usd", "numeric"), ("cost_basis", "text"),
        ("structured_response", "jsonb"), ("validation", "jsonb"), ("retry_count", "int"),
        ("error", "text"), ("timed_out", "boolean")]},
    "cop_tool_calls": {"key": "tool_call_id", "columns": [
        ("tool_call_id", "text"), ("decision_id", "uuid"), ("cop", "text"), ("case_id", "text"),
        ("tool", "text"), ("args", "jsonb"), ("requested_by", "text"), ("status", "text"),
        ("rejection_reason", "text"), ("result_summary", "jsonb"), ("injection_flags", "text[]"),
        ("latency_ms", "numeric")]},
    "cop_memory_records": {"key": "record_id", "columns": [
        ("record_id", "text"), ("tenant_id", "text"), ("agent_scope", "text"),
        ("owner_cop", "text"), ("record_type", "text"), ("payload", "jsonb"),
        ("provenance", "jsonb"), ("validation_status", "text"), ("created_at", "timestamptz"),
        ("expires_at", "timestamptz")]},
    "cop_memory_access_log": {"key": "access_id", "columns": [
        ("access_id", "text"), ("at", "timestamptz"), ("cop", "text"), ("case_id", "text"),
        ("tenant_id", "text"), ("operation", "text"), ("record_type", "text"),
        ("record_id", "text"), ("outcome", "text"), ("reason", "text")]},
}

_JSON = ("jsonb",)
_ARRAY = ("text[]",)


def decision_id(run_id, session_id, cop):
    """Same formula as models.COPDecision.decision_id, so rows from the old
    runner and the runtime share one key space."""
    return str(uuid.uuid5(DECISION_NS, f"{run_id}|{session_id}|{cop}"))


def decision_row(r):
    """COP result (runtime/graph.py) -> one cop_decisions row, every column."""
    f = r.get("finding") or {}
    act = r.get("proposed_action") or {}
    ev = (r.get("evidence") or {}).get("rows", [])
    metrics_read = [{"metric": x["metric"], "value": x.get("value"),
                     "state": x.get("state") or "NOT_COMPUTED", "evidence": x["evidence"]}
                    for x in ev]
    refs = sorted({ref for c in f.get("claims", []) for ref in c.get("evidence_refs", [])})
    driver = next((c.get("metric_id") for c in f.get("claims", []) if c.get("metric_id")), None)
    upstream_ids = [decision_id(r["run_id"], r["session_id"], u["cop"])
                    for u in r.get("upstream", []) if u.get("present")]
    tool_calls = r.get("tool_calls", [])
    row = {
        "decision_id": decision_id(r["run_id"], r["session_id"], r["cop"]),
        "run_id": r["run_id"], "cop": r["cop"], "cop_version": r.get("cop_version", "1"),
        "session_id": r["session_id"], "trace_id": r.get("trace_id"), "metric_id": driver,
        "behavioural_question": r.get("behavioural_question"),
        "evidence_refs": refs, "metrics_read": metrics_read,
        "context_used": [{"key": t["tool"], "status": t["status"], "ms": t.get("latency_ms")}
                         for t in tool_calls],
        "upstream_decisions": upstream_ids,
        "interpretation": f.get("label"), "rationale": f.get("rationale", ""),
        "confidence": round(float(f.get("confidence") or 0.0), 3),
        "evidence_confidence": r.get("evidence_confidence"),
        "severity": f.get("severity", "NONE"),
        "action": act.get("action", "NONE"), "action_reason": act.get("reason", ""),
        "downstream_cops": list(r.get("downstream_cops", [])),
        "interpreter": r.get("interpreter", "rules"), "skipped_interpreter": False,
        "mode": r.get("mode", "dry_run"), "evaluation_ms": int(r.get("total_ms") or 0),
        "failed": f.get("status") == "FAILED" or bool(r.get("error")),
        "error": r.get("error"), "decided_at": r.get("decided_at"),
        "inputs_hash": digest({"m": metrics_read, "u": upstream_ids,
                               "t": [(t["tool"], t.get("args")) for t in tool_calls]}),
        "case_id": r.get("case_id"), "tenant_id": r.get("tenant_id"),
        "contract_version": CONTRACT_VERSION, "cop_mode": r.get("classification"),
        "decision_status": f.get("status"), "verified": bool(f.get("verified")),
        "finding": f, "verifier_results": r.get("verifier_results", []),
        "proposed_action": act, "execution_status": act.get("execution_status"),
        "llm_calls": len(r.get("llm_invocations", [])), "tool_calls": len(tool_calls),
        "node_timings": r.get("node_timings", {}),
    }
    cols = [c for c, _ in TABLES["cop_decisions"]["columns"]]
    assert set(row) == set(cols), set(row) ^ set(cols)
    return row


def side_rows(r):
    """The llm invocation and tool call rows that belong with one decision."""
    did = decision_id(r["run_id"], r["session_id"], r["cop"])
    inv = [{**{c: i.get(c) for c, _ in TABLES["cop_llm_invocations"]["columns"]},
            "decision_id": did} for i in r.get("llm_invocations", [])]
    tc = [{**{c: t.get(c) for c, _ in TABLES["cop_tool_calls"]["columns"]},
           "decision_id": did, "cop": r["cop"], "case_id": r.get("case_id")}
          for t in r.get("tool_calls", [])]
    return {"cop_llm_invocations": inv, "cop_tool_calls": tc}


# ---------------------------------------------------------------- SQLite

def _sqlite_type(pg):
    if pg in ("int",) or pg.startswith("int"):
        return "INTEGER"
    if pg.startswith("numeric"):
        return "REAL"
    if pg == "boolean":
        return "INTEGER"
    return "TEXT"


class SQLiteStore:
    """Durable local store for decisions, invocations, tool calls and memory.

    Same tables and columns as the Supabase schema. JSON and array columns
    are stored as JSON text.
    """

    def __init__(self, path=":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            for t, spec in TABLES.items():
                cols = ", ".join(f"{c} {_sqlite_type(p)}" + (" PRIMARY KEY" if c == spec["key"] else "")
                                 for c, p in spec["columns"])
                self.conn.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols})")
            self.conn.commit()

    def _enc(self, table, row):
        types = dict(TABLES[table]["columns"])
        out = []
        for c, _ in TABLES[table]["columns"]:
            v = row.get(c)
            if types[c] in _JSON + _ARRAY and v is not None:
                v = json.dumps(v, default=str, sort_keys=True)
            elif types[c] == "boolean" and v is not None:
                v = int(bool(v))
            out.append(v)
        return out

    def _dec(self, table, values):
        out = {}
        for (c, p), v in zip(TABLES[table]["columns"], values):
            if p in _JSON + _ARRAY and v is not None:
                v = json.loads(v)
            elif p == "boolean" and v is not None:
                v = bool(v)
            out[c] = v
        return out

    def upsert(self, table, rows):
        if not rows:
            return 0
        cols = [c for c, _ in TABLES[table]["columns"]]
        sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        with self.lock:
            self.conn.executemany(sql, [self._enc(table, r) for r in rows])
            self.conn.commit()
        return len(rows)

    def select(self, table, where="", params=()):
        cols = [c for c, _ in TABLES[table]["columns"]]
        with self.lock:
            cur = self.conn.execute(f"SELECT {', '.join(cols)} FROM {table} "
                                    + (f"WHERE {where}" if where else ""), params)
            return [self._dec(table, v) for v in cur.fetchall()]

    def save_result(self, r):
        self.upsert("cop_decisions", [decision_row(r)])
        for t, rows in side_rows(r).items():
            self.upsert(t, rows)

    def decisions(self, **eq):
        where = " AND ".join(f"{k} = ?" for k in eq)
        return self.select("cop_decisions", where, tuple(eq.values()))


# ---------------------------------------------------------------- Supabase

class SupabaseRuntimeWriter:
    """Builds PostgREST upserts for the runtime tables.

    poster(table, rows, conflict) is injected. In production that is
    signal_traces.post; in tests it is a recorder. The class never imports
    or calls the network on its own.
    """

    def __init__(self, poster):
        if poster is None:
            raise ValueError("a poster must be passed explicitly; nothing posts by default")
        self.poster = poster

    @staticmethod
    def payload(table, rows):
        cols = [c for c, _ in TABLES[table]["columns"]]
        out = []
        for r in rows:
            extra = set(r) - set(cols)
            if extra:
                raise ValueError(f"{table}: columns not in schema: {sorted(extra)}")
            out.append({c: r.get(c) for c in cols})
        return out, TABLES[table]["key"]

    def save_result(self, r):
        sent = []
        rows, key = self.payload("cop_decisions", [decision_row(r)])
        self.poster("cop_decisions", rows, key)
        sent.append(("cop_decisions", len(rows), key))
        for t, srows in side_rows(r).items():
            if srows:
                p, k = self.payload(t, srows)
                self.poster(t, p, k)
                sent.append((t, len(p), k))
        return sent
