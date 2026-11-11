"""
A local, isolated action-execution gateway (sandbox).

The graph decides an action and checks its authority (authority_check,
await_approval). This gateway carries an authorised action out against local
test targets only and records each stage separately:

    PROPOSED      the COP's candidate proposed it
    AUTHORISED    authority_check let it stand (AU-01 and PE-01 passed); the
                  gateway re-checks with authority.check_typed and refuses if not
    AWAITING_APPROVAL / APPROVAL_DENIED   approval-required actions only
    DELIVERED     the adapter handed it to its target and got a receipt
    APPLIED       the target reports the effect is in place
    VERIFIED      an independent read of the target confirms the effect
    RELEASED / CANCELLED   a hold's two endings
    FAILED        delivery or verification failed (never treated as done)
    REFUSED       the gateway's own authority re-check failed

Adapters (no production system, no external enforcement):
    ALERT              HTTP POST to a loopback test endpoint (TestAlertEndpoint);
                       verified by reading the alert back from the endpoint
    REQUEST_APPROVAL   a persistent PENDING approval row in the gateway's SQLite
                       file; verified by reading it back on a fresh connection
    HOLD_TOOL_CALL     the coding agent's mock tool runner (MockToolRunner) asks
                       the gateway before running each call; a held call does not
                       run until it is released, and never runs if denied;
                       verified from the runner's own execution log

Only loopback endpoints are accepted. Approval decisions need a named human
reviewer; a COP, the gateway or "system" cannot decide, and no answer is a
denial (fail closed).
"""

import json, sqlite3, threading, time, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ..authority import check_typed, typed, needs_approval, TAXONOMY
from ..specs import BY_KEY
from .contracts import stable_id

GATEWAY_VERSION = "sandbox-gateway-1"
PROPOSED, AUTHORISED, REFUSED = "PROPOSED", "AUTHORISED", "REFUSED"
AWAITING_APPROVAL, APPROVAL_DENIED = "AWAITING_APPROVAL", "APPROVAL_DENIED"
DELIVERED, APPLIED, VERIFIED, FAILED = "DELIVERED", "APPLIED", "VERIFIED", "FAILED"
RELEASED, CANCELLED = "RELEASED", "CANCELLED"
STATES = (PROPOSED, AUTHORISED, REFUSED, AWAITING_APPROVAL, APPROVAL_DENIED, DELIVERED, APPLIED,
          VERIFIED, RELEASED, CANCELLED, FAILED)
ADAPTED = ("ALERT", "REQUEST_APPROVAL", "HOLD_TOOL_CALL")
HUMAN_REVIEW = "HUMAN_REVIEW"             # runtime escalation to a person, not a COP action
NOT_A_REVIEWER_PREFIXES = ("cop:", "gateway", "system", "auto")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
DEFAULT_APPROVAL_TTL_S = 3600


def _now_iso(clock):
    return clock().isoformat(timespec="milliseconds")


def _utc():
    return datetime.now(timezone.utc)


# ====================================================================== store

SCHEMA = {
    "gateway_actions": "action_id TEXT PRIMARY KEY, case_id TEXT, cop TEXT, finding_id TEXT, typed_action TEXT, "
                       "target TEXT, state TEXT, history TEXT, detail TEXT, created_at TEXT, updated_at TEXT",
    "gateway_approvals": "approval_id TEXT PRIMARY KEY, action_id TEXT, case_id TEXT, cop TEXT, typed_action TEXT, "
                         "question TEXT, status TEXT, requested_at TEXT, expires_at TEXT, decided_at TEXT, "
                         "reviewer TEXT, note TEXT",
    "gateway_holds": "hold_id TEXT PRIMARY KEY, action_id TEXT, tool_call_id TEXT, status TEXT, created_at TEXT, "
                     "updated_at TEXT, decided_by TEXT",
    "runner_calls": "tool_call_id TEXT PRIMARY KEY, session_id TEXT, tool TEXT, command TEXT, status TEXT, "
                    "submitted_at TEXT, executed_at TEXT, result TEXT",
}


class GatewayStore:
    """SQLite file shared by the gateway and the mock runner. Survives a restart:
    open a new GatewayStore on the same path and every record is there."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with self._conn() as c:
            for t, cols in SCHEMA.items():
                c.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols})")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10)

    def put(self, table, row):
        cols = list(row)
        with self.lock, self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in row.values()])

    def get(self, table, key_col, key):
        rows = self.where(table, f"{key_col} = ?", (key,))
        return rows[0] if rows else None

    def where(self, table, cond="", params=()):
        with self.lock, self._conn() as c:
            c.row_factory = sqlite3.Row
            cur = c.execute(f"SELECT * FROM {table}" + (f" WHERE {cond}" if cond else ""), params)
            out = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("history", "detail", "result"):
                    if d.get(k):
                        try:
                            d[k] = json.loads(d[k])
                        except (TypeError, ValueError):
                            pass
                out.append(d)
            return out


# ====================================================================== test endpoint

class TestAlertEndpoint:
    """A loopback HTTP endpoint that stands for the reviewing team's alert inbox.
    POST /alerts stores the alert and returns an ack id; GET /alerts/<ack> reads it
    back. It keeps its own log (a JSON-lines file), separate from the gateway."""

    def __init__(self, log_path, fail_with=None):
        self.log_path, self.fail_with = log_path, fail_with
        ep = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path != "/alerts":
                    return self._send(404, {"error": "not found"})
                if ep.fail_with:
                    return self._send(ep.fail_with, {"error": "endpoint configured to fail"})
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                ack = str(uuid.uuid4())
                rec = {"ack_id": ack, "received_at": _utc().isoformat(timespec="milliseconds"), "alert": body}
                with open(ep.log_path, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                return self._send(201, {"ack_id": ack, "received_at": rec["received_at"]})

            def do_GET(self):
                if not self.path.startswith("/alerts/"):
                    return self._send(404, {"error": "not found"})
                ack = self.path.split("/", 2)[2]
                for r in ep.read_all():
                    if r["ack_id"] == ack:
                        return self._send(200, r)
                return self._send(404, {"error": "no such alert"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def read_all(self):
        try:
            with open(self.log_path) as fh:
                return [json.loads(x) for x in fh if x.strip()]
        except FileNotFoundError:
            return []

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _http(method, url, body=None, timeout=5):
    host = urlparse(url).hostname
    if host not in LOOPBACK:
        raise PermissionError(f"sandbox gateway delivers to loopback test endpoints only, not {host}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never via a proxy
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# ====================================================================== mock tool runner

class MockToolRunner:
    """The coding agent's tool runner in the sandbox. Before running a call it asks
    the gateway; a call runs only when the gateway says so. Its log is the
    independent record the gateway's hold verification reads."""

    def __init__(self, store, gateway=None, clock=_utc):
        self.store, self.gateway, self.clock = store, gateway, clock

    def submit(self, tool_call_id, session_id, tool, command, defer=False):
        """defer=True: the runner's pre-execution hook keeps the call queued while a
        governance case on this session is open; step() runs it once the gateway allows."""
        self.store.put("runner_calls", {"tool_call_id": tool_call_id, "session_id": session_id, "tool": tool,
                                        "command": command, "status": "SUBMITTED",
                                        "submitted_at": _now_iso(self.clock), "executed_at": None,
                                        "result": None})
        return "SUBMITTED" if defer else self.step(tool_call_id)

    def step(self, tool_call_id):
        """Run the call if the gateway allows it now. Returns the call's status."""
        call = self.store.get("runner_calls", "tool_call_id", tool_call_id)
        if call["status"] in ("EXECUTED", "CANCELLED"):
            return call["status"]
        verdict = self.gateway.gate(tool_call_id) if self.gateway else "run"
        if verdict == "run":
            call.update(status="EXECUTED", executed_at=_now_iso(self.clock),
                        result={"exit_code": 0, "note": "mock execution: nothing ran outside the sandbox"})
        elif verdict == "cancelled":
            call.update(status="CANCELLED")
        else:
            call.update(status="HELD" if verdict == "held" else "WAITING_FOR_DECISION")
        self.store.put("runner_calls", call)
        return call["status"]

    def pending(self, session_id):
        return [c for c in self.store.where("runner_calls", "session_id = ?", (session_id,))
                if c["status"] in ("SUBMITTED", "HELD", "WAITING_FOR_DECISION")]

    def executed(self, tool_call_id):
        c = self.store.get("runner_calls", "tool_call_id", tool_call_id)
        return bool(c and c["status"] == "EXECUTED")


# ====================================================================== the gateway

class SandboxGateway:
    """Carries authorised actions out against local test targets. Also the graph's
    enforcement gateway (execute()) and its approval hook (on_approval_requested)."""

    simulated = True            # no production target; effects are local to the sandbox
    sandbox = True

    def __init__(self, store, alert_url=None, runner=None, clock=_utc, approval_ttl_s=DEFAULT_APPROVAL_TTL_S):
        self.store, self.alert_url, self.clock = store, alert_url, clock
        self.runner = runner
        if runner is not None:
            runner.gateway = self
        self.approval_ttl_s = approval_ttl_s
        self.dry_run_adapter = None

    # ---------------------------------------------------------- records
    def _action(self, action_id):
        return self.store.get("gateway_actions", "action_id", action_id)

    def _move(self, a, state, **detail):
        assert state in STATES, state
        at = _now_iso(self.clock)
        a["history"] = list(a.get("history") or []) + [{"state": state, "at": at, **detail}]
        a["state"], a["updated_at"] = state, at
        a["detail"] = {**(a.get("detail") or {}), **detail}
        self.store.put("gateway_actions", a)
        return a

    def action_id(self, case_id, cop, finding_id, typed_action, target):
        return stable_id(case_id, cop, finding_id, typed_action, target, "gateway-action")

    def history(self, action_id):
        a = self._action(action_id)
        return [h["state"] for h in (a or {}).get("history") or []]

    # ---------------------------------------------------------- entry
    def submit(self, cop, case_id, finding, pa, mode, live_session=False):
        """PROPOSED -> AUTHORISED (or REFUSED). pa is the graph's proposed_action after
        authority_check. Nothing is delivered here."""
        t = pa.get("typed_action") or typed(pa.get("action", "NONE"))
        target = pa.get("target") or ""
        aid = self.action_id(case_id, cop, finding.get("finding_id"), t, target)
        a = self._action(aid)
        if a and a["state"] not in (PROPOSED,):
            return a                                       # idempotent: an action is carried out once
        a = {"action_id": aid, "case_id": case_id, "cop": cop, "finding_id": finding.get("finding_id"),
             "typed_action": t, "target": target, "state": PROPOSED, "history": [], "detail": {},
             "created_at": _now_iso(self.clock), "updated_at": None}
        a = self._move(a, PROPOSED, candidate=(pa.get("original_proposal") or {}).get("action"),
                       finding_label=finding.get("label"), verified=bool(finding.get("verified")))
        fa = pa.get("final_authority") or {}
        ok, sub, why = check_typed(BY_KEY[cop], t, mode, live_session)
        if not fa.get("authorised") or not ok or pa.get("execution_status") == "NOT_EXECUTED_BLOCKED":
            return self._move(a, REFUSED, reason=why or pa.get("authority_reason") or "authority_check refused",
                              checks=fa.get("checks"))
        if not finding.get("verified") and t not in ("HOLD_TOOL_CALL",):
            return self._move(a, REFUSED, reason="the finding did not verify: nothing is carried out on it")
        if t not in ADAPTED and t not in ("NONE", "WATCH"):
            return self._move(a, REFUSED, reason=f"no sandbox adapter for {t}")
        return self._move(a, AUTHORISED, checks=fa.get("checks"), approval_required=bool(pa.get("requires_approval")))

    def carry_out(self, action_id, finding, pa, tool_call_id=None):
        """AUTHORISED -> the adapter. Approval-required actions stop at AWAITING_APPROVAL."""
        a = self._action(action_id)
        if a["state"] != AUTHORISED:
            return a
        t = a["typed_action"]
        if t in ("NONE", "WATCH"):
            return a
        if pa.get("requires_approval") or t == "REQUEST_APPROVAL":
            return self.request_approval(action_id, f"{a['cop']}: {t} on {a['target'] or 'session'} "
                                                    f"({finding.get('label')}): {pa.get('reason', '')[:200]}",
                                         tool_call_id=tool_call_id)
        if t == "ALERT":
            return self.deliver_alert(action_id, finding, pa)
        if t == "HOLD_TOOL_CALL":
            return self.apply_hold(action_id, tool_call_id)
        return a

    # ---------------------------------------------------------- ALERT
    def deliver_alert(self, action_id, finding, pa):
        a = self._action(action_id)
        if not self.alert_url:
            return self._move(a, FAILED, reason="no alert endpoint configured")
        body = {"action_id": action_id, "case_id": a["case_id"], "cop": a["cop"],
                "finding_id": a["finding_id"], "label": finding.get("label"),
                "verification_support": {k: (finding.get("verification_support") or {}).get(k)
                                         for k in ("decision", "score", "against", "unknown")},
                "reason": pa.get("reason", "")[:300], "sandbox": True}
        try:
            code, resp = _http("POST", f"{self.alert_url}/alerts", body)
        except Exception as e:
            return self._move(a, FAILED, reason=f"delivery failed: {type(e).__name__}: {e}"[:300])
        if code != 201 or not resp.get("ack_id"):
            return self._move(a, FAILED, reason=f"endpoint answered {code}: {resp}"[:300])
        a = self._move(a, DELIVERED, endpoint=self.alert_url, ack_id=resp["ack_id"], http_status=code)
        a = self._move(a, APPLIED, received_at=resp.get("received_at"))
        return self.verify(action_id)

    # ---------------------------------------------------------- REQUEST_APPROVAL
    def approval_id(self, action_id):
        return stable_id(action_id, "approval")

    def request_approval(self, action_id, question, tool_call_id=None):
        a = self._action(action_id)
        apid = self.approval_id(action_id)
        if not self.store.get("gateway_approvals", "approval_id", apid):
            now = self.clock()
            self.store.put("gateway_approvals", {
                "approval_id": apid, "action_id": action_id, "case_id": a["case_id"], "cop": a["cop"],
                "typed_action": a["typed_action"], "question": question[:500], "status": "PENDING",
                "requested_at": now.isoformat(timespec="milliseconds"),
                "expires_at": (now + timedelta(seconds=self.approval_ttl_s)).isoformat(timespec="milliseconds"),
                "decided_at": None, "reviewer": None, "note": None})
        a = self._move(a, AWAITING_APPROVAL, approval_id=apid, tool_call_id=tool_call_id)
        if a["typed_action"] == "REQUEST_APPROVAL":
            # the action IS the request: delivered when the row exists, applied when it reads PENDING
            a = self._move(a, DELIVERED, approval_id=apid)
            a = self._move(a, APPLIED, approval_status="PENDING")
            return self.verify(action_id)
        return a

    def on_approval_requested(self, payload):
        """Hook for graph.await_approval: creates (once) the persistent approval row for
        the action the graph is waiting on. Returns the approval id."""
        aid = self.action_id(payload["case_id"], payload["cop"], payload.get("finding_id"),
                             payload.get("typed_action") or typed(payload["action"]), payload.get("target") or "")
        if not self._action(aid):
            self.store.put("gateway_actions", {
                "action_id": aid, "case_id": payload["case_id"], "cop": payload["cop"],
                "finding_id": payload.get("finding_id"), "typed_action": payload.get("typed_action"),
                "target": payload.get("target") or "", "state": PROPOSED, "history": [], "detail": {},
                "created_at": _now_iso(self.clock), "updated_at": None})
            a = self._action(aid)
            a = self._move(a, PROPOSED, source="graph await_approval")
            self._move(a, AUTHORISED, approval_required=True)
        a = self._action(aid)
        if a["state"] == AUTHORISED:
            tc = next((op.get("tool_call_id") for op in payload.get("pending_operations") or []
                       if op.get("tool_call_id")), None) if payload.get("typed_action") == "HOLD_TOOL_CALL" else None
            self.request_approval(aid, f"{payload['cop']}: {payload.get('typed_action')} on "
                                       f"{payload.get('target') or 'session'}: {payload.get('reason', '')[:200]}",
                                  tool_call_id=tc)
            if tc and self.runner:
                self.runner.step(tc)              # the runner learns the call waits for this decision
        return self.approval_id(aid)

    def pending_approvals(self):
        return [r for r in self.store.where("gateway_approvals", "status = ?", ("PENDING",))]

    def decide(self, approval_id, decision, reviewer, note=""):
        """A named human approves or denies. Anything else is refused; expiry is a denial."""
        ap = self.store.get("gateway_approvals", "approval_id", approval_id)
        if ap is None:
            raise KeyError(approval_id)
        if ap["status"] != "PENDING":
            return ap
        if not reviewer or str(reviewer).lower().startswith(NOT_A_REVIEWER_PREFIXES):
            raise PermissionError("an approval needs a named human reviewer, not a COP or the system")
        now = self.clock()
        if ap["expires_at"] and now.isoformat(timespec="milliseconds") > ap["expires_at"]:
            decision, note = "deny", f"expired at {ap['expires_at']}: no answer is a denial"
        ap.update(status="APPROVED" if decision == "approve" else "DENIED",
                  decided_at=now.isoformat(timespec="milliseconds"), reviewer=reviewer, note=note[:300])
        self.store.put("gateway_approvals", ap)
        a = self._action(ap["action_id"])
        if ap["status"] == "DENIED" and a and a["state"] == AWAITING_APPROVAL:
            self._move(a, APPROVAL_DENIED, reviewer=reviewer, note=note[:200])
            tc = (a.get("detail") or {}).get("tool_call_id")
            if tc and self.runner:
                self.runner.step(tc)          # a denied hold leaves the call free to run
        return ap

    def expire(self):
        """Deny every PENDING approval past its expiry (fail closed)."""
        out = []
        now = self.clock().isoformat(timespec="milliseconds")
        for ap in self.pending_approvals():
            if ap["expires_at"] and now > ap["expires_at"]:
                ap.update(status="DENIED", decided_at=now, reviewer="expiry", note="no answer before expiry")
                self.store.put("gateway_approvals", ap)
                a = self._action(ap["action_id"])
                if a and a["state"] == AWAITING_APPROVAL:
                    self._move(a, APPROVAL_DENIED, reviewer="expiry", note="no answer is a denial")
                out.append(ap["approval_id"])
        return out

    def proceed_after_approval(self, action_id, finding, pa, tool_call_id=None):
        """After an APPROVED decision: carry the approved action out."""
        a = self._action(action_id)
        ap = self.store.get("gateway_approvals", "approval_id", self.approval_id(action_id))
        if a["state"] != AWAITING_APPROVAL or not ap or ap["status"] != "APPROVED":
            return a
        a = self._move(a, AUTHORISED, approved_by=ap["reviewer"])
        t = a["typed_action"]
        if t == "ALERT":
            return self.deliver_alert(action_id, finding, pa)
        if t == "HOLD_TOOL_CALL":
            return self.apply_hold(action_id, tool_call_id or (a.get("detail") or {}).get("tool_call_id"))
        return self._move(a, FAILED, reason=f"no sandbox adapter for {t}")

    # ---------------------------------------------------------- HUMAN_REVIEW (escalation)
    def open_review_task(self, case_id, cop, finding, human_task):
        """The runtime's escalation when a finding stays uncertain or a limit was hit: a
        persistent task for a named reviewer. It is not a COP action and changes nothing in
        the session, so no COP authority is needed or used; it is verified like an approval
        request (the row is read back on a fresh connection)."""
        aid = self.action_id(case_id, cop, finding.get("finding_id"), HUMAN_REVIEW, human_task.get("kind", ""))
        a = self._action(aid)
        if a and a["state"] != PROPOSED:
            return a
        a = {"action_id": aid, "case_id": case_id, "cop": cop, "finding_id": finding.get("finding_id"),
             "typed_action": HUMAN_REVIEW, "target": human_task.get("kind", ""), "state": PROPOSED,
             "history": [], "detail": {}, "created_at": _now_iso(self.clock), "updated_at": None}
        a = self._move(a, PROPOSED, source="runtime escalation", task_kind=human_task.get("kind"),
                       finding_label=finding.get("label"), verified=bool(finding.get("verified")))
        a = self._move(a, AUTHORISED, reason="a request to a person changes nothing in the session; "
                                             "no COP authority is used")
        apid = self.approval_id(aid)
        if not self.store.get("gateway_approvals", "approval_id", apid):
            now = self.clock()
            self.store.put("gateway_approvals", {
                "approval_id": apid, "action_id": aid, "case_id": case_id, "cop": cop,
                "typed_action": HUMAN_REVIEW, "question": str(human_task.get("question", ""))[:500],
                "status": "PENDING", "requested_at": now.isoformat(timespec="milliseconds"),
                "expires_at": (now + timedelta(seconds=self.approval_ttl_s)).isoformat(timespec="milliseconds"),
                "decided_at": None, "reviewer": None, "note": None})
        a = self._move(a, DELIVERED, approval_id=apid)
        a = self._move(a, APPLIED, approval_status="PENDING")
        return self.verify(aid)

    # ---------------------------------------------------------- HOLD_TOOL_CALL
    def hold_id(self, action_id):
        return stable_id(action_id, "hold")

    def apply_hold(self, action_id, tool_call_id):
        a = self._action(action_id)
        if not tool_call_id or not self.runner or not self.store.get("runner_calls", "tool_call_id", tool_call_id):
            return self._move(a, FAILED, reason="no pending tool call to hold")
        call = self.store.get("runner_calls", "tool_call_id", tool_call_id)
        if call["status"] == "EXECUTED":
            return self._move(a, FAILED, reason="the tool call already ran: a hold cannot apply")
        now = _now_iso(self.clock)
        self.store.put("gateway_holds", {"hold_id": self.hold_id(action_id), "action_id": action_id,
                                         "tool_call_id": tool_call_id, "status": "HELD", "created_at": now,
                                         "updated_at": now, "decided_by": None})
        a = self._move(a, DELIVERED, hold_id=self.hold_id(action_id), tool_call_id=tool_call_id)
        st = self.runner.step(tool_call_id)            # the runner asks and is told to wait
        a = self._move(a, APPLIED, runner_status=st)
        return self.verify(action_id)

    def gate(self, tool_call_id):
        """What the runner may do with a call now: run | held | waiting | cancelled."""
        for h in self.store.where("gateway_holds", "tool_call_id = ?", (tool_call_id,)):
            if h["status"] == "HELD":
                return "held"
            if h["status"] == "DENIED":
                return "cancelled"
        for a in self.store.where("gateway_actions", "typed_action = ? AND state = ?",
                                  ("HOLD_TOOL_CALL", AWAITING_APPROVAL)):
            if (a.get("detail") or {}).get("tool_call_id") == tool_call_id:
                return "waiting"        # a hold for this call is awaiting a decision: fail closed
        return "run"

    def end_hold(self, action_id, outcome, reviewer, note=""):
        """release -> the call runs; deny -> the call is cancelled and never runs."""
        if not reviewer or str(reviewer).lower().startswith(NOT_A_REVIEWER_PREFIXES):
            raise PermissionError("ending a hold needs a named human reviewer")
        a = self._action(action_id)
        h = self.store.get("gateway_holds", "hold_id", self.hold_id(action_id))
        if not h or h["status"] != "HELD":
            return a
        h.update(status="RELEASED" if outcome == "release" else "DENIED", updated_at=_now_iso(self.clock),
                 decided_by=reviewer)
        self.store.put("gateway_holds", h)
        st = self.runner.step(h["tool_call_id"])
        a = self._move(a, RELEASED if outcome == "release" else CANCELLED, reviewer=reviewer, note=note[:200],
                       runner_status=st)
        return a

    # ---------------------------------------------------------- verification
    def verify(self, action_id):
        """An independent read of the target. VERIFIED only if it confirms the effect."""
        a = self._action(action_id)
        t, d = a["typed_action"], a.get("detail") or {}
        ok, why = False, ""
        if t == "ALERT":
            try:
                code, rec = _http("GET", f"{d['endpoint']}/alerts/{d['ack_id']}")
                ok = code == 200 and (rec.get("alert") or {}).get("finding_id") == a["finding_id"] \
                    and (rec.get("alert") or {}).get("action_id") == action_id
                why = f"endpoint returned {code} for ack {d['ack_id']}"
            except Exception as e:
                why = f"read-back failed: {e}"
        elif t in ("REQUEST_APPROVAL", HUMAN_REVIEW) or a["state"] == AWAITING_APPROVAL:
            con = sqlite3.connect(self.store.path)                      # a fresh connection, not the cache
            row = con.execute("SELECT status FROM gateway_approvals WHERE approval_id = ?",
                              (d.get("approval_id"),)).fetchone()
            con.close()
            ok = bool(row) and row[0] in ("PENDING", "APPROVED", "DENIED")
            why = f"approval row reads {row[0] if row else 'absent'} on a fresh connection"
        elif t == "HOLD_TOOL_CALL":
            call = self.store.get("runner_calls", "tool_call_id", d.get("tool_call_id"))
            ok = bool(call) and call["status"] == "HELD" and not call.get("executed_at")
            why = f"runner reports the call {call['status'] if call else 'absent'}"
        if not ok:
            return self._move(a, FAILED, reason=f"verification failed: {why}")
        return self._move(a, VERIFIED, observed=why)

    # ---------------------------------------------------------- the graph's enforcement interface
    def execute(self, cop, case_id, action, target):
        """graph.execute_action calls this after an approval. It carries out the approved
        action through the adapters and reports whether the outcome was observed."""
        cands = [a for a in self.store.where("gateway_actions", "case_id = ? AND cop = ?", (case_id, cop))
                 if a["state"] == AWAITING_APPROVAL]
        if not cands:
            return {"applied": False, "outcome_confirmed": False, "simulated": True,
                    "detail": "sandbox gateway: no approved action waiting for this COP"}
        a = cands[-1]
        out = self.proceed_after_approval(a["action_id"], {"label": None, "verification_support": {}},
                                          {"reason": "approved"})
        return {"applied": out["state"] in (APPLIED, VERIFIED), "outcome_confirmed": out["state"] == VERIFIED,
                "simulated": True, "sandbox": True, "gateway_action_id": a["action_id"],
                "detail": f"sandbox gateway: {a['typed_action']} -> {out['state']}"}
