import { useMemo, useState } from "react";
import { C } from "../theme";

/* ==================================================================
 * COP investigations
 *
 * Event-driven: every figure on this page is read from the trace events
 * that the integration tests produce (collector/cops/runtime/scenario.py,
 * checked by tests/test_runtime_scenario.py). Nothing here is computed
 * from scratch or typed in by hand. Regenerate the files with
 *   cd collector && python3 -m cops.runtime.scenario --out ../docs/cops/evaluation/scenario
 *
 * The scenario is a sandbox: the coding agent is synthetic, model
 * replies are scripted, and actions go to local test targets only.
 * ================================================================== */

const FILES = import.meta.glob("../../docs/cops/evaluation/scenario/*.json", { eager: true });
const ORDER = ["successful", "contradictory", "missing_evidence", "timeout", "denied_approval"];
const RUNS = Object.fromEntries(
  Object.entries(FILES).map(([p, m]) => [p.split("/").pop().replace(".json", ""), m.default || m]));
const VARIANTS = ORDER.filter((v) => RUNS[v]);

const SHORT = {
  anomaly_detection: "Anomaly Detection", runtime_monitoring: "Runtime Monitoring",
  behavior_analytics: "Behaviour Analytics", workflow_orchestration: "Workflow Orchestration",
  context_memory: "Context Memory",
};
const nice = (s) => (s || "").replace(/_/g, " ").toLowerCase();
const tone = (s) => {
  if (!s) return C.mute;
  if (/VERIFIED$|ANSWERED_VERIFIED|REUSED_VERIFIED|EXECUTED_SANDBOX|CONFIRMED|approve/.test(s)) return C.good;
  if (/TIMED_OUT|FAILED|REFUSED|DENIED|REJECTED|REFUTED|deny/.test(s)) return C.danger;
  if (/NO_FINDING|UNVERIFIED|NOT_OPENED|context_only|UNRESOLVED|AWAITING/.test(s)) return C.warn;
  return C.body;
};

function Card({ title, sub, children }) {
  return (
    <section className="rounded p-4" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      <h3 className="text-sm" style={{ color: C.ink }}>{title}</h3>
      {sub && <p className="text-xs mt-0.5 mb-3" style={{ color: C.mute }}>{sub}</p>}
      {children}
    </section>
  );
}

function Pill({ children, color }) {
  return (
    <span className="inline-block text-xs px-1.5 py-0.5 rounded"
          style={{ color: color || C.body, border: `1px solid ${color || C.rule}` }}>{children}</span>
  );
}

/* ---------------------------------------------------- handoff graph */

function handoffs(trace) {
  const edges = [];
  const byRef = {};
  trace.forEach((e) => {
    const ie = e.investigation_event;
    if (!ie) return;
    if (ie.kind === "referral_requested") {
      const ed = { id: ie.referral_id, from: ie.cop, to: ie.receiver, depth: ie.depth, reason: ie.reason,
                   status: "REQUESTED", at: ie.at };
      byRef[ie.referral_id] = ed;
      edges.push(ed);
    } else if (ie.kind === "referral_not_opened") {
      edges.push({ id: `${ie.cop}-${ie.receiver}-n`, from: ie.cop, to: ie.receiver, depth: null,
                   status: "NOT_OPENED", reason: ie.reason, at: ie.at });
    } else if (byRef[ie.referral_id]) {
      const ed = byRef[ie.referral_id];
      if (ie.kind === "referral_answered") Object.assign(ed, { status: ie.status, delivered: ie.delivered_as,
                                                               elapsed: ie.elapsed_s, checks: ie.checks });
      if (ie.kind === "referral_refused") Object.assign(ed, { status: ie.status, detail: ie.detail });
      if (ie.kind === "referral_answer_reused") Object.assign(ed, { status: "REUSED_VERIFIED" });
      if (ie.kind === "referral_failure_reused") Object.assign(ed, { status: ie.status, detail: "not run again" });
      if (ie.kind === "referral_failed") Object.assign(ed, { status: "RECEIVER_FAILED", detail: ie.detail });
    }
  });
  return edges;
}

function HandoffGraph({ trace }) {
  const edges = handoffs(trace);
  // columns by depth: the root at 0, receivers at their referral depth
  const depthOf = { anomaly_detection: 0 };
  edges.forEach((e) => {
    const d = e.depth ?? (depthOf[e.from] ?? 0) + 1;
    if (depthOf[e.to] === undefined || d < depthOf[e.to]) depthOf[e.to] = d;
  });
  const cols = {};
  Object.entries(depthOf).forEach(([k, d]) => { (cols[d] = cols[d] || []).push(k); });
  const W = 210, H = 54, GX = 90, GY = 22;
  const pos = {};
  Object.entries(cols).forEach(([d, ks]) => ks.forEach((k, i) => {
    pos[k] = { x: 10 + Number(d) * (W + GX), y: 10 + i * (H + GY) };
  }));
  const width = 20 + (Object.keys(cols).length) * (W + GX) - GX;
  const height = 70 + Math.max(...Object.values(cols).map((c) => c.length)) * (H + GY) - GY; // room for back edges
  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} role="img" aria-label="COP handoff graph">
        {edges.map((e, i) => {
          const a = pos[e.from], b = pos[e.to];
          if (!a || !b) return null;
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
          const back = x2 <= x1;
          const path = back
            ? `M${a.x + W / 2},${a.y + H} C${a.x + W / 2},${a.y + H + 40} ${b.x + W / 2},${b.y + H + 40} ${b.x + W / 2},${b.y + H}`
            : `M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`;
          return (
            <g key={e.id + i}>
              <path d={path} fill="none" stroke={tone(e.status)} strokeWidth="1.5"
                    strokeDasharray={e.status === "NOT_OPENED" || /REFUSED|REUSED/.test(e.status) ? "4 3" : ""} />
              <circle cx={back ? b.x + W / 2 : x2} cy={back ? b.y + H : y2} r="3" fill={tone(e.status)} />
            </g>
          );
        })}
        {Object.entries(pos).map(([k, p]) => (
          <g key={k}>
            <rect x={p.x} y={p.y} width={W} height={H} rx="4" fill={C.panel}
                  stroke={k === "anomaly_detection" ? C.accent : C.rule} />
            <text x={p.x + 10} y={p.y + 22} fontSize="12" fill={C.ink}>{SHORT[k] || k}</text>
            <text x={p.x + 10} y={p.y + 40} fontSize="10" fill={C.mute}>
              {k === "anomaly_detection" ? "initiator (LLM, scripted replies)"
                : /runtime_monitoring|behavior_analytics/.test(k) ? "deterministic"
                : k === "workflow_orchestration" ? "LLM, scripted replies" : "not in referral scope"}
            </text>
          </g>
        ))}
      </svg>
      <table className="w-full text-xs mt-3">
        <thead><tr style={{ color: C.mute }}>
          <th className="text-left py-1">handoff</th><th className="text-left">why</th>
          <th className="text-left">result</th><th className="text-left">delivered as</th>
          <th className="text-left">RS checks</th></tr></thead>
        <tbody>
          {edges.map((e, i) => (
            <tr key={e.id + i} style={{ borderTop: `1px solid ${C.rule}` }}>
              <td className="py-1 pr-2">{SHORT[e.from]} → {SHORT[e.to] || e.to}
                {e.depth != null && <span style={{ color: C.mute }}> · depth {e.depth}</span>}</td>
              <td className="pr-2" style={{ color: C.body }}>{e.reason}</td>
              <td className="pr-2"><Pill color={tone(e.status)}>{nice(e.status)}</Pill>
                {e.elapsed != null && <span style={{ color: C.mute }}> {e.elapsed}s</span>}
                {e.detail && <div style={{ color: C.mute }}>{e.detail}</div>}</td>
              <td className="pr-2">{e.delivered ? nice(e.delivered) : "—"}</td>
              <td>{e.checks ? Object.entries(e.checks).map(([k, v]) => (
                <span key={k} className="mr-1" style={{ color: tone(v === "PASS" ? "VERIFIED" : "FAILED") }}>
                  {k.replace("RS-0", "")}{v === "PASS" ? "✓" : "✗"}</span>)) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------- VS-01 bars */

function SupportBar({ label, v }) {
  const seg = [["support", v.support, C.good], ["against", v.against, C.danger], ["unknown", v.unknown, C.rule]];
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs mb-1">
        <span style={{ color: C.ink }}>{label}</span>
        <span style={{ color: C.body }}>{nice(v.decision)} · {v.label ? nice(v.label) + " · " : ""}
          telemetry reliability {v.telemetry_reliability}</span>
      </div>
      <div className="flex h-4 rounded overflow-hidden" style={{ border: `1px solid ${C.rule}` }}>
        {seg.map(([k, x, col]) => x > 0 && (
          <div key={k} title={`${k} ${x}`} style={{ width: `${x * 100}%`, background: col }} />))}
      </div>
      <div className="flex gap-4 text-xs mt-1" style={{ color: C.mute }}>
        <span>support {v.support}</span><span>against {v.against}</span><span>unknown {v.unknown}</span>
        {v.assumptions?.length > 0 && <span style={{ color: C.warn }}>assumed: {v.assumptions.join(", ")}</span>}
      </div>
    </div>
  );
}

/* ---------------------------------------------------- page */

const at = (t) => (t || "").slice(11, 23);

export default function CopInvestigations() {
  const [variant, setVariant] = useState(VARIANTS[0]);
  const run = RUNS[variant];
  const ev = useMemo(() => {
    const by = {};
    (run?.trace || []).forEach((e) => { (by[e.stage] = by[e.stage] || []).push(e); });
    return by;
  }, [run]);
  if (!run) return <p className="text-sm" style={{ color: C.body }}>No scenario traces found.</p>;
  const one = (s) => (ev[s] || [])[0];
  const last = (s) => (ev[s] || []).slice(-1)[0];
  const ini = one("vs01_initial"), q0 = one("handling_quotient_initial"), f0 = one("initial_finding");
  const rev = last("revised_evidence_score"), fin = one("investigation_final");
  const act = one("final_action"), auth = one("authority_check"), exe = one("action_execution");
  const out = one("outcome_confirmation"), fb = one("feedback_persisted"), rs = one("restart_check");
  const apReq = one("approval_requested"), apDec = one("approval_decided"), task = one("review_task_opened");
  const tel = one("telemetry_ingested");
  const history = exe?.gateway_history?.length ? exe.gateway_history : task?.gateway_history || [];
  const t0 = new Date(run.trace[0].at).getTime();

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-lg">COP investigations</h2>
        <p className="text-xs mt-1" style={{ color: C.mute }}>
          A sandbox coding-agent scenario, read from the integration-test trace files
          (docs/cops/evaluation/scenario). The agent is synthetic, model replies are scripted, and every
          action goes to a local test target. Nothing reaches a production system.
        </p>
        <div className="flex flex-wrap gap-2 mt-3">
          {VARIANTS.map((v) => (
            <button key={v} onClick={() => setVariant(v)} className="text-xs px-3 py-1.5 rounded"
                    style={{ background: v === variant ? C.soft : C.panel, color: v === variant ? C.accent : C.body,
                             border: `1px solid ${v === variant ? C.accent : C.rule}` }}>{nice(v)}</button>))}
        </div>
      </header>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <Card title="What the agent did and what telemetry saw"
              sub={`${(ev.agent_event || []).length} agent events · collector ${tel?.collectors?.join(", ")}`}>
          <ul className="text-xs space-y-1" style={{ color: C.body }}>
            <li>{(ev.agent_event || []).filter((e) => e.success === false).length} failing tool calls
              ({(ev.agent_event || []).find((e) => e.success === false)?.command})</li>
            <li>tokens used {(ev.agent_event || []).find((e) => e.total_tokens)?.total_tokens?.toLocaleString()};
              reported by telemetry {tel?.tokens_reported?.toLocaleString()}</li>
            <li>comparable earlier sessions: {tel?.baseline_sessions}</li>
            {tel?.tool_calls_dropped_by_collector?.length > 0 && (
              <li style={{ color: C.warn }}>collector dropped {tel.tool_calls_dropped_by_collector.length} tool calls</li>)}
          </ul>
        </Card>
        <Card title="Handling quotient and route" sub="initial run, after referrals, and the investigation's final route">
          <table className="w-full text-xs"><tbody>
            {[["initial", q0?.quotient, q0?.tier, q0?.selected_action],
              ["revised", rev?.quotient, rev?.tier, rev?.selected_action],
              ["final", null, fin?.tier, fin?.selected_action]].map(([k, qq, t, a]) => (
              <tr key={k} style={{ borderTop: `1px solid ${C.rule}` }}>
                <td className="py-1" style={{ color: C.mute }}>{k}</td><td>{qq ?? ""}</td>
                <td><Pill color={t === "autonomous" ? C.good : t === "human" ? C.danger : C.warn}>{t}</Pill></td>
                <td>{nice(a)}</td></tr>))}
          </tbody></table>
          <p className="text-xs mt-2" style={{ color: C.body }}>{fin?.routing_reason}</p>
        </Card>
      </div>

      <Card title="Investigation graph and COP handoffs"
            sub={`each handoff is a typed referral; the receiver ran through its own LangGraph COP subgraph · resolution ${nice(fin?.resolution)}`}>
        <HandoffGraph trace={run.trace} />
      </Card>

      <Card title="Verification support (VS-01): initial and revised"
            sub={`initial run ${f0?.run_key?.split(":").slice(-1)} vs. reassessment after admitted answers`}>
        <SupportBar label="initial" v={{ ...ini, label: f0?.label }} />
        <SupportBar label={rev?.round ? `revised (round ${rev.round})` : "revised (no reassessment ran)"} v={rev || {}} />
        {rev?.component_changes?.length > 0 && (
          <table className="w-full text-xs mt-2"><tbody>
            {rev.component_changes.map((c) => (
              <tr key={c.component} style={{ borderTop: `1px solid ${C.rule}` }}>
                <td className="py-1 pr-2">{nice(c.component)}</td>
                <td className="pr-2">{c.from} → {c.to}</td>
                <td className="pr-2">{c.contribution.from} → {c.contribution.to}</td>
                <td style={{ color: C.mute }}>{c.basis}</td></tr>))}
          </tbody></table>)}
      </Card>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <Card title="Selected action, authority and execution"
              sub={`act-phase run in supervised mode · decision ${act?.exec_decision_id?.slice(0, 8)}`}>
          <div className="text-xs space-y-1" style={{ color: C.body }}>
            <div>action <Pill color={C.accent}>{nice(act?.typed_action)}</Pill>
              {act?.requires_approval && <span> · needs approval: {act.approval_because}</span>}</div>
            <div>authority {auth?.authorised ? <Pill color={C.good}>authorised</Pill> : <Pill color={C.danger}>refused</Pill>}
              {" "}{auth?.checks?.map((c) => `${c.check_id} ${nice(c.result)}`).join(" · ")}</div>
            {apReq && <div>approval {apReq.approval_id.slice(0, 8)} requested at {at(apReq.at)}</div>}
            {apDec && <div>decided <Pill color={tone(apDec.decision)}>{apDec.decision}</Pill> by {apDec.reviewer}: {apDec.note}</div>}
            {task && <div>review task opened ({nice(task.kind)}): {task.question}</div>}
            <div>execution <Pill color={tone(exe?.execution_status)}>{nice(exe?.execution_status)}</Pill></div>
          </div>
          {history.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 mt-3 text-xs">
              {history.map((h, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span style={{ color: C.mute }}>→</span>}
                  <Pill color={tone(h)}>{nice(h)}</Pill></span>))}
            </div>)}
        </Card>
        <Card title="Observed outcome and feedback" sub="independent read-back, the ledger, and a restart of the stores">
          <div className="text-xs space-y-1" style={{ color: C.body }}>
            <div>gateway state <Pill color={tone(out?.gateway_state)}>{nice(out?.gateway_state) || "none"}</Pill></div>
            {out?.observed && <div style={{ color: C.mute }}>{out.observed}</div>}
            <div>alerts received by the test endpoint: {out?.alerts_at_endpoint?.length ?? 0}</div>
            <div>review <Pill color={tone(fb?.review_entry?.outcome)}>{nice(fb?.review_entry?.outcome)}</Pill> by{" "}
              {fb?.review_entry?.source} · eligible for scoring{" "}
              <b>{String(fb?.review_entry?.eligible_for_scoring)}</b> · applied to memory{" "}
              <b>{String(fb?.review_entry?.applied_to_memory)}</b></div>
            {fb?.ineligible_because?.length > 0 && <div style={{ color: C.warn }}>{fb.ineligible_because.join("; ")}</div>}
            <div>after restart: ledger {rs?.ledger?.map((l) => l.join(" ")).join(", ")} · memory record{" "}
              {nice(rs?.memory_validation_status) || "none written"}</div>
          </div>
        </Card>
      </div>

      <Card title="Event timeline" sub={`${run.trace.length} events · ${run.summary?.cop_runs} COP runs · spend $${run.summary?.spent_usd}`}>
        <div className="text-xs max-h-80 overflow-y-auto">
          {run.trace.filter((e) => e.stage !== "agent_event").map((e) => {
            const ie = e.investigation_event;
            return (
              <div key={e.seq} className="flex gap-3 py-0.5" style={{ borderTop: `1px solid ${C.rule}` }}>
                <span style={{ color: C.mute, width: 70 }}>+{new Date(e.at).getTime() - t0} ms</span>
                <span style={{ width: 190 }}>{nice(e.stage)}</span>
                <span style={{ color: C.body }}>
                  {ie ? `${SHORT[ie.cop] || ie.cop}${ie.receiver ? " → " + (SHORT[ie.receiver] || ie.receiver) : ""}${ie.status ? " · " + nice(ie.status) : ""}`
                    : e.receiver ? `${SHORT[e.receiver]} · ${nice(e.status)}`
                    : e.decision ? `${nice(e.decision)} · support ${e.support}` : ""}
                </span>
              </div>);
          })}
        </div>
      </Card>
    </div>
  );
}
