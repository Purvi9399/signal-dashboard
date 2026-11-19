"""
Real-data replay: stored observations through the same pipeline the synthetic harness uses.

    raw observables -> signal_metrics.compute -> evidence_assessment / cop_package
    -> rules.py (through the LangGraph case graph) -> verifiers.py -> dry-run finding

Nothing here computes a metric, assesses evidence or interprets a COP: it builds the
case the runtime already accepts (evaluation.make_runtime) and runs it (graph.run_case)
in dry_run mode with no model provider. Production writes are not possible from this
module: Supabase is only read, through signal_traces.get, and only with an explicit
approval flag; results go to a local store.

A replay is pinned by its snapshot (rows, raw events and collector capability, with the
query boundaries that produced them), the metric definition version, the COP package
versions and the evaluation time. The same pins give the same results.

    cd collector
    python3 -m cops.runtime.replay --snapshot FILE --at 2026-11-19T00:00:00Z --out DIR
    python3 -m cops.runtime.replay --supabase --since T0 --until T1 --at T1 --save FILE   # read-only
"""

import argparse, hashlib, json, os, sys
from collections import Counter, defaultdict
from urllib.parse import quote

from ..specs import BY_KEY
from . import cop_package as P, evidence_assessment as EA
from .contracts import CONTRACT_VERSION, digest, stable_id
from .evaluation import make_runtime
from .graph import run_case
from .memory import VERIFIED
from .persistence import SQLiteStore
from .verifiers import REGISTRY_VERSION

signal_metrics, signal_traces = EA.signal_metrics, EA.signal_traces

SUPPORTED = ("runtime_monitoring", "interaction_discovery")
SELECT = signal_metrics.COLS + "," + signal_metrics.CATALOGUE_COLS + ",dedup_key"
# source_events without payload: content is never read for provenance.
RAW_COLS = "source_id,session_id,source_ref,file_origin,collector,tool,received_at,payload"
KEYED = ("tooluse", "user_prompt", "agent_response", "model_request", "session_start", "session_end",
         "plan_created", "plan_completed")
CAP_COLS = "tool,collector,declared,configured,first_seen,last_seen,rows_seen,observed_state"
READ_APPROVAL = "COPS_REPLAY_READ_APPROVED"
TENANT = "replay"


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _content(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- snapshot

def _stores(snap):
    return [snap["rows"], snap.get("raw_events"), snap.get("capability")] + \
        ([snap["native"]] if snap.get("native") is not None else [])


def snapshot(rows, source, raw_events=None, capability=None, native=None):
    """Rows and the stores provenance is resolved against, with how they were obtained. native: an index
    of the native transcripts raw_ref names, built where they live."""
    rows = sorted(rows, key=lambda r: (str(r.get("occurred_at") or ""), str(r.get("observable_id") or "")))
    snap = {"source": source, "rows": rows, "raw_events": raw_events, "capability": capability,
            "row_count": len(rows), **({"native": native} if native is not None else {})}
    return dict(snap, digest=_content(_stores(snap)))


def save_snapshot(snap, path):
    with open(path, "w") as f:
        json.dump(snap, f, default=str)


def load_snapshot(path):
    with open(path) as f:
        snap = json.load(f)
    if _content(_stores(snap)) != snap["digest"]:
        raise ValueError(f"{path}: content does not match its recorded digest")
    return snap


def reconstruct_keys(raw_events):
    """Rebuild each stored source event's observable dedup_key from its payload with signal_curate's own
    extractors and merge_key, then drop the payload. A key that depends on the time the collector wrote
    the event (not stored in source_events) is not rebuilt: the event says why instead."""
    import signal_curate as C
    out = []
    for e in raw_events:
        e = dict(e)
        p, coll = e.pop("payload", None), e.get("collector")
        f, why = None, None
        try:
            if not isinstance(p, dict):
                why = "no payload"
            elif coll == C.H:
                cursor = "cursor" in os.path.basename(str(e.get("file_origin") or "")).lower() or "cursor_version" in p
                f = (C.extract_cursor_hook if cursor else C.extract_claude_hook)(p, None)
                if p.get("transcript_path"):
                    e["transcript_path"] = p["transcript_path"]
            elif coll in (C.W, C.P, C.F) and "observable_id" in p:
                f = C.extract_row(p)
            else:
                why = f"{coll} payloads do not keep what the curator merged on"
        except Exception as x:                                    # noqa: BLE001
            why = f"payload not readable by the curator: {type(x).__name__}"
        if f is not None:
            if not f.get("observable_type") or f["observable_type"] == "other":
                why = "the curator does not build an observable from this event"
            else:
                key = C.merge_key(coll, f)
                if coll == C.H and key[2] not in KEYED:
                    why = "the merge key depends on the collector's write time, which source_events does not keep"
                else:
                    e["dedup_key"] = hashlib.sha256(repr(key).encode()).hexdigest()
        if why:
            e["dedup_key_unavailable"] = why
        out.append(e)
    return out


def native_index(paths, host):
    """Which native transcripts exist on this host and which session ids they contain. Reads only the
    session id fields of each JSON line; a file whose lines carry none is recorded with session_ids None."""
    out = {}
    for path in sorted(set(p for p in paths if p)):
        if not os.path.isfile(path):
            out[path] = {"exists": False, "session_ids": []}
            continue
        ids = set()
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    ids |= {str(rec[k]) for k in ("sessionId", "session_id", "conversation_id") if rec.get(k)}
        out[path] = {"exists": True, "session_ids": sorted(ids) if ids else None}
    return {"host": host, "paths": out}


def fetch_supabase(since, until, approved=False, getter=None, batch=50):
    """Read-only snapshot of observables in [since, until), their raw events and collector capability.

    Refuses unless approved=True and COPS_REPLAY_READ_APPROVED=1. Only GET requests are made."""
    if not approved or os.environ.get(READ_APPROVAL) != "1":
        raise PermissionError(f"a Supabase read needs approved=True and {READ_APPROVAL}=1")
    get = getter or signal_traces.get
    if getter is None and not (signal_traces.URL and signal_traces.KEY):
        raise PermissionError("SUPABASE_URL and SUPABASE_KEY are not set")
    window = f"occurred_at=gte.{quote(since)}&occurred_at=lt.{quote(until)}"
    obs_q = f"select={SELECT}&{window}&order=occurred_at.asc,observable_id.asc"
    rows = get("observables", obs_q)
    sids = sorted({r["session_id"] for r in rows if r.get("session_id")})
    raw, raw_q = [], []
    for i in range(0, len(sids), batch):
        q = f"select={RAW_COLS}&session_id=in.({','.join(quote(s) for s in sids[i:i + batch])})"
        raw_q.append(q)
        raw += reconstruct_keys(get("source_events", q))
    cap_q = f"select={CAP_COLS}"
    cap = get("collector_capability", cap_q)
    source = {"kind": "supabase", "since": since, "until": until,
              "queries": {"observables": obs_q, "source_events": raw_q, "collector_capability": cap_q}}
    return snapshot(rows, source, raw, cap)


# ---------------------------------------------------------------- versions and leakage

def versions(cfg, cops):
    here = EA.HERE
    pkgs = {}
    for cop in cops:
        path = os.path.join(EA.PACKAGES, cop, "METRICS.yaml")
        pkgs[cop] = ({"version": EA.load_package(cop)["version"], "sha256": _sha(path)}
                     if os.path.exists(path) else {"version": None, "note": "no COP package"})
    return {"metric_version": cfg["metric_version"], "metrics_config_sha256": _content(cfg),
            "signal_metrics_sha256": _sha(os.path.join(here, "signal_metrics.py")),
            "signal_traces_sha256": _sha(os.path.join(here, "signal_traces.py")),
            "packages": pkgs, "contract_version": CONTRACT_VERSION, "verifier_registry": REGISTRY_VERSION}


def available_at(rows, at):
    """Rows that existed at the evaluation time: occurred and, when recorded, ingested by then."""
    t = signal_traces.ts(at)
    keep, late, undated = [], 0, 0
    for r in rows:
        when = [signal_traces.ts(r.get(k)) for k in ("occurred_at", "ingested_at")]
        when = [w for w in when if w is not None and (w.tzinfo is None) == (t.tzinfo is None)]
        if not when:
            undated += 1
        if any(w > t for w in when):
            late += 1
            continue
        keep.append(r)
    return keep, {"after_evaluation": late, "undated_kept": undated}


# ---------------------------------------------------------------- replay

def _chain(c):
    return dict(c, nodes=[{k: n[k] for k in ("ref", "kind", "status", "method")} for n in c["nodes"]])


def _summary(cop, out, events, rows, raw_events, native):
    f, r = out["findings"][cop], out["results"][cop]
    refs = sorted({x for c in f.get("claims", []) for x in c.get("evidence_refs", [])})
    ea = (f.get("cop_specific") or {}).get("evidence_assessment") or {}
    answers = {q: t["answer"] for q, t in ((f.get("cop_specific") or {}).get("questions") or {}).items()}
    classes = ea.get("classes") or answers or None
    logical = {"label": f["label"], "status": f["status"], "verified": f["verified"],
               "claims": sorted(c.get("statement") or "" for c in f.get("claims", [])),
               "classes": classes}
    return {"finding_id": f["finding_id"], "label": f["label"], "status": f["status"], "verified": f["verified"],
            "not_passed": sorted({v["check_id"] for v in r["verifier_results"] if v["result"] in ("FAIL", "WARN")}),
            "action": (r.get("proposed_action") or {}).get("action"),
            "execution_status": (r.get("proposed_action") or {}).get("execution_status"),
            "evidence_refs": refs[:20],
            "provenance_chain": _chain(EA.resolve_chain(refs, events, rows, raw_events, native)),
            "question_classes": classes, "qualifications": ea.get("qualifications"),
            "memory_write": {k: (r.get("memory_write") or {}).get(k) for k in ("written", "record_id", "status")},
            "logical_digest": digest(logical)}


def replay(snap, at, cops=SUPPORTED, sessions=None, cfg=None, tenant=TENANT, store=None):
    """Every session of the snapshot (or the named ones) through the COP graph, as of `at`."""
    if not at:
        raise ValueError("a replay needs an explicit evaluation time")
    bad = [c for c in cops if c not in SUPPORTED]
    if bad:
        raise ValueError(f"replay supports {SUPPORTED}; not {bad}")
    cops = [c for c in BY_KEY if c in cops]
    cfg = cfg or signal_metrics.load_config()
    rows, excluded = available_at(snap["rows"], at)
    ver = versions(cfg, cops)
    replay_id = stable_id("replay", snap["digest"], _content(ver), at, ",".join(cops), tenant)
    events, built = signal_metrics.compute(rows, cfg, computed_at=at)
    sids = sorted(s.id for s in built if sessions is None or s.id in sessions)
    artifacts = {k: v for k, v in (("source_events", snap.get("raw_events")),
                                   ("collector_capability", snap.get("capability")),
                                   ("native_transcripts", snap.get("native"))) if v is not None}
    store = store if store is not None else SQLiteStore()
    t = signal_traces.ts(at)
    results = {}
    for sid in sids:
        rt = make_runtime(stable_id(replay_id, sid), sid, rows, artifacts, store=store, tenant=tenant, cfg=cfg,
                          computed_at=at, synthetic=snap["source"].get("kind") == "synthetic",
                          run_id=f"replay-{replay_id}", clock=lambda: t, events=events, sessions=built)
        out = run_case(rt, cops)
        sev = {e["metric_id"]: e for e in events if e["session_id"] == sid}
        srows = signal_metrics.unique_rows(next(x for x in built if x.id == sid))
        results[sid] = {cop: _summary(cop, out, sev, srows, snap.get("raw_events"), snap.get("native"))
                        for cop in cops}
    manifest = {"replay_id": replay_id, "evaluated_at": at, "cops": cops, "tenant": tenant,
                "source": snap["source"], "snapshot_digest": snap["digest"], "rows_in_snapshot": snap["row_count"],
                "rows_used": len(rows), "excluded": excluded, "sessions": sids,
                "raw_store_supplied": snap.get("raw_events") is not None,
                "capability_supplied": snap.get("capability") is not None,
                "native_store_supplied": snap.get("native") is not None, "versions": ver,
                "results_digest": digest({s: {c: v["logical_digest"] for c, v in r.items()}
                                          for s, r in results.items()})}
    return {"manifest": manifest, "results": results, "rows": rows, "cfg": cfg, "store": store,
            "raw_events": snap.get("raw_events"), "native": snap.get("native")}


# ---------------------------------------------------------------- evidence availability profile

NUMERIC = {"total_tokens", "cost_usd", "tool_duration_ms", "exit_code", "sequence_num", "hung_seconds",
           "chars_added", "chars_removed", "prompt_length", "delegation_depth", "violation_count"}
ATTRIBUTION = ("session_id", "agent_id", "turn_id")
ACCOUNTING = ("total_tokens", "cost_usd", "cost_is_estimated", "model")


def _valid(field, v):
    if field.endswith("_at"):
        return signal_traces.ts(v) is not None
    if field in NUMERIC:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False
    if isinstance(v, bool):
        return True
    return signal_metrics.has(v)


def _fields(rows, fields):
    out = {}
    for f in fields:
        supplied = sum(1 for r in rows if f in r)
        valued = [r[f] for r in rows if signal_metrics.has(r.get(f))]
        out[f] = {"rows": len(rows), "supplied": supplied, "populated": len(valued),
                  "valid": sum(1 for v in valued if _valid(f, v))}
    return out


def select_gaps(metric_ids=None):
    """Fields a metric requires that the replay's observables query does not select."""
    cols = set(SELECT.split(","))
    req = {m[0]: m[4] for m in signal_metrics.METRICS + signal_metrics.CATALOGUE}
    return {m: sorted(set(f) - cols) for m, f in req.items()
            if (metric_ids is None or m in metric_ids) and set(f) - cols}


def relevant_metrics(cops=SUPPORTED):
    out = set()
    for cop in cops:
        out |= set(BY_KEY[cop].metric_keys)
        path = os.path.join(EA.PACKAGES, cop, "METRICS.yaml")
        if os.path.exists(path):
            out |= {r["metric"] for q in EA.load_package(cop)["questions"].values() for r in q["requires"]}
    return sorted(out)


def evidence_profile(rep):
    """Per metric and tool: presence, population and validity, provenance, attribution, sources, history,
    calculability and question usability. Calculated is never the same as concluded: findings are
    counted separately, with whether they verified."""
    rows, cfg, at = rep["rows"], rep["cfg"], rep["manifest"]["evaluated_at"]
    events, built = EA._computed(rows, cfg, at)
    by_sid = {s.id: s for s in built}
    tool_of = {s.id: s.tool or "unattributed" for s in built}
    tools = sorted(set(tool_of.values()))
    trows = defaultdict(list)
    for s in built:
        trows[tool_of[s.id]] += signal_metrics.unique_rows(s)
    req = {m[0]: m[4] for m in signal_metrics.METRICS + signal_metrics.CATALOGUE}
    wanted = set(relevant_metrics(rep["manifest"]["cops"]))
    metrics = defaultdict(lambda: {"sessions": 0, "calculated": 0, "states": Counter(), "reasons": Counter(),
                                   "errors": 0})
    for e in events:
        if e["metric_id"] not in wanted:
            continue
        m = metrics[(e["metric_id"], tool_of.get(e["session_id"], "unattributed"))]
        m["sessions"] += 1
        m["calculated"] += e["value"] is not None
        m["states"][e["state"]] += 1
        m["errors"] += bool(e.get("error"))
        f = e.get("features") or {}
        if e["value"] is None or e["state"] in ("UNKNOWN", "NOT_APPLICABLE"):
            m["reasons"][f"{e['state']}|{f.get('availability') or ''}|{f.get('reason') or e.get('error') or ''}"] += 1
    snapshot_raw = rep.get("raw_events")
    per_tool = {}
    min_hist = cfg["min_sessions_for_baseline"]
    for tool in tools:
        sids = [s for s in rep["manifest"]["sessions"] if tool_of.get(s) == tool]
        rs = trows[tool]
        need = sorted({f for mid in wanted for f in req.get(mid, [])} | set(ATTRIBUTION) | set(ACCOUNTING)
                      | {"raw_ref", "collector"})
        tok = [r for r in rs if signal_metrics.has(r.get("total_tokens"))]
        cov = Counter()
        exp = cfg["expected_collectors"].get(tool, [])
        for sid in sids:
            for c in exp:
                if c not in by_sid[sid].collectors:
                    cov[c] += 1
        crossed = [r for r in rs if r.get("collectors_agree") is not None]
        hist = Counter()
        for sid in sids:
            s = by_sid[sid]
            earlier = [x for x in built if x.tool == s.tool and x.operator == s.operator
                       and (x.start or "") < (s.start or "")]
            hist["sufficient" if len(earlier) >= min_hist else "insufficient"] += 1
        prov = {"source": Counter(), "native": Counter(), "derived": Counter(), "methods": Counter()}
        for sid in sids:
            srows = signal_metrics.unique_rows(by_sid[sid])
            if snapshot_raw is None:
                prov["source"]["NOT_EXECUTED (raw store not supplied)"] += len(srows)
                continue
            for link in EA.resolve_provenance(srows, snapshot_raw, rep.get("native")):
                prov["source"][link["source"]["status"]] += 1
                prov["methods"][link["source"]["method"]] += 1
                for part in ("native", "derived"):
                    if link[part]:
                        prov[part][link[part]["status"]] += 1
        q = defaultdict(Counter)
        concluded = defaultdict(Counter)
        for sid in sids:
            for cop, v in rep["results"][sid].items():
                concluded[cop][f"{v['label']}|{'verified' if v['verified'] else 'not verified'}"] += 1
                for qid, cls in (v.get("question_classes") or {}).items():
                    q[f"{cop}:{qid}"][cls] += 1
        per_tool[tool] = {
            "sessions": len(sids), "rows": len(rs), "fields": _fields(rs, need),
            "attribution": {f: _fields(rs, [f])[f]["populated"] for f in ATTRIBUTION},
            "token_attribution": {"rows_with_tokens": len(tok),
                                  "with_turn": sum(1 for r in tok if signal_metrics.has(r.get("turn_id"))),
                                  "with_agent": sum(1 for r in tok if signal_metrics.has(r.get("agent_id"))),
                                  "with_valid_cost": sum(1 for r in tok if _valid("cost_usd", r.get("cost_usd"))),
                                  "cost_estimated": sum(1 for r in tok if r.get("cost_is_estimated") is True)},
            "expected_collectors": exp, "sessions_missing_collector": dict(cov),
            "source_reliability": {"cross_checked_rows": len(crossed),
                                   "disagreeing_rows": sum(1 for r in crossed if r["collectors_agree"] is False),
                                   "multi_collector_sessions": sum(1 for s in sids if len(by_sid[s].collectors) > 1)},
            "history": {"min_sessions_for_baseline": min_hist, **hist},
            "provenance": {k: dict(v) for k, v in prov.items()},
            "question_usability": {k: dict(v) for k, v in q.items()},
            "findings": {k: dict(v) for k, v in concluded.items()},
        }
    for cop in rep["manifest"]["cops"]:
        if not os.path.exists(os.path.join(EA.PACKAGES, cop, "METRICS.yaml")):
            for t in per_tool.values():
                t["question_usability"][f"{cop}:*"] = {"NO_QUESTION_CONTRACT": t["sessions"]}
    return {"replay_id": rep["manifest"]["replay_id"], "tools": per_tool,
            "metrics": {f"{m}|{t}": {**v, "states": dict(v["states"]), "reasons": dict(v["reasons"]),
                                     "required_fields": req.get(m, [])}
                        for (m, t), v in sorted(metrics.items())},
            "select_gaps": select_gaps(wanted),
            "not_calculated": sorted(m for m in wanted if m not in set(req) | {"telemetry.unknown_share"})}


def memory_layers(store, registry=None, tenant=TENANT):
    """The five kinds of state, counted where each lives."""
    recs = [r for r in store.select("cop_memory_records") if r["tenant_id"] == tenant]
    st = Counter(r["validation_status"] for r in recs)
    reg = registry.to_dict()["proposals"] if registry is not None else []
    return {"current_evaluation": "LangGraph state of each run; not persisted by the replay",
            "historical_observations": {"UNVALIDATED": st.get("UNVALIDATED", 0), VERIFIED: st.get(VERIFIED, 0),
                                        "decisions": len(store.select("cop_decisions"))},
            "verified_outcomes": {"VALIDATED": st.get("VALIDATED", 0), "REJECTED": st.get("REJECTED", 0)},
            "proposed_improvements": sum(1 for p in reg if p["status"] == "PROPOSED"),
            "approved_changes": sum(1 for p in reg if p["status"] in ("APPROVED", "IMPLEMENTED"))}


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot", help="replay a saved snapshot file")
    ap.add_argument("--supabase", action="store_true", help="read a snapshot from Supabase (read-only)")
    ap.add_argument("--approved-read-only", action="store_true", help=f"with --supabase; also needs {READ_APPROVAL}=1")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--at", required=True, help="evaluation time")
    ap.add_argument("--save", help="write the snapshot here")
    ap.add_argument("--out", help="write manifest, results, profile and proposals to this directory")
    ap.add_argument("--native-index", action="store_true",
                    help="index the native transcripts raw_ref names on this host (reads session ids only)")
    a = ap.parse_args()
    if a.supabase:
        snap = fetch_supabase(a.since, a.until, approved=a.approved_read_only)
    elif a.snapshot:
        snap = load_snapshot(a.snapshot)
    else:
        sys.exit("give --snapshot FILE or --supabase")
    if a.native_index:
        import socket
        snap = snapshot(snap["rows"], snap["source"], snap.get("raw_events"), snap.get("capability"),
                        native_index([r.get("raw_ref") for r in snap["rows"]], socket.gethostname()))
    if a.save:
        save_snapshot(snap, a.save)
    from . import evolution
    rep = replay(snap, a.at)
    prof = evidence_profile(rep)
    props = evolution.proposals_from(prof, rep)
    print(f"replay {rep['manifest']['replay_id']}: {len(rep['manifest']['sessions'])} sessions, "
          f"{rep['manifest']['rows_used']:,} rows, results {rep['manifest']['results_digest']}, "
          f"{len(props)} proposals (none applied)")
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        for name, obj in (("manifest", rep["manifest"]), ("results", rep["results"]), ("profile", prof),
                          ("proposals", [p.to_dict() for p in props])):
            with open(os.path.join(a.out, f"{name}.json"), "w") as f:
                json.dump(obj, f, indent=1, default=str)


if __name__ == "__main__":
    main()
