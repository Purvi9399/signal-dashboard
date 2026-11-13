import { useMemo } from "react";
import { C, F, Panel, Pill, Val, nice, tone } from "../ui.jsx";

/* Activation view. Renders activation.json (contract cops-activation/1), which the export copies
 * from the router's own output in docs/cops/evaluation/activation. Three states per COP:
 *   defined   the COP has a policy in the approved, active mapping version
 *   eligible  an ELIGIBLE activation record exists for the COP in the case
 *   active    an ACTIVATED activation record exists for the COP in the case
 * The "after record" control replays the recorded records in their sequence order. It does not
 * animate, interpolate or add any transition: each step is one record from the file. */

const VERDICT = ["ELIGIBLE", "NOT_ELIGIBLE", "UNKNOWN"];
const G = { mx: 8, mw: 210, mh: 22, mgap: 6, top: 30, cx0: 330, cdx: 200, cw: 170, ch: 74 };

function depthOf(policies) {
  const byCop = Object.fromEntries(policies.map((p) => [p.cop, p]));
  const memo = {};
  const d = (c, seen = new Set()) => {
    if (c in memo) return memo[c];
    if (seen.has(c)) return 0;
    seen.add(c);
    const deps = (byCop[c].depends_on || []).filter((x) => byCop[x]);
    return (memo[c] = deps.length ? 1 + Math.max(...deps.map((x) => d(x, seen))) : 0);
  };
  return Object.fromEntries(policies.map((p) => [p.cop, d(p.cop)]));
}

/* State of each COP after the first n records, read from those records only. */
function stateAt(records, n) {
  const s = {};
  for (const r of records.slice(0, n)) {
    const x = (s[r.cop] = s[r.cop] || { verdict: null, activated: false, last: null, record: null });
    if (VERDICT.includes(r.transition)) x.verdict = r.transition;
    if (r.transition === "ACTIVATED") x.activated = true;
    x.last = r.transition;
    x.record = r;
  }
  return s;
}

export default function ActivationView({ act, params, set }) {
  const scenarios = act.scenarios;
  const sc = scenarios.find((s) => s.scenario === params.scenario) || scenarios[0];
  const policies = act.mapping.policies;
  const slice = policies.map((p) => p.cop);
  const recs = sc.records;
  const n = params.at != null && params.at !== "" ? Math.max(0, Math.min(recs.length, Number(params.at))) : recs.length;
  const st = stateAt(recs, n);
  const sel = params.acop || "";

  const L = useMemo(() => {
    const metrics = [...new Set(act.mapping.edges.filter((e) => slice.includes(e.cop)).map((e) => e.metric_id))].sort();
    const pos = {};
    metrics.forEach((m, i) => { pos["m:" + m] = { x: G.mx, y: G.top + i * (G.mh + G.mgap), w: G.mw, h: G.mh }; });
    const depth = depthOf(policies);
    const cols = {};
    slice.forEach((c) => { (cols[depth[c]] = cols[depth[c]] || []).push(c); });
    const height = Math.max(G.top + metrics.length * (G.mh + G.mgap), 360);
    Object.entries(cols).forEach(([d, cs]) => {
      const gap = (height - G.top - cs.length * G.ch) / (cs.length + 1);
      cs.forEach((c, i) => { pos["c:" + c] = { x: G.cx0 + Number(d) * G.cdx, y: G.top + gap * (i + 1) + i * G.ch, w: G.cw, h: G.ch }; });
    });
    const width = G.cx0 + (Math.max(...Object.keys(cols).map(Number)) + 1) * G.cdx + 10;
    return { metrics, pos, width, height: height + 10 };
  }, [act, policies, slice]);

  const triggers = new Set(policies.flatMap((p) => p.conditions.map((c) => `${p.cop}|${c.metric_id}`)));
  const verdictRec = (cop) => recs.slice(0, n).filter((r) => r.cop === cop && VERDICT.includes(r.transition)).pop();
  const evState = (cop, metric) => {
    const r = verdictRec(cop);
    const e = r && (r.evidence || []).find((x) => x.metric === metric);
    return e ? e.state : null;
  };
  const completed = (cop) => recs.slice(0, n).find((r) => r.cop === cop && r.transition === "COMPLETED");
  const consultUsed = (c) => {
    const r = completed(c.initiator);
    if (!r || !r.detail || !r.detail.consultation) return false;
    try { return JSON.parse(r.detail.consultation).responses.some((x) => x.receiver === c.receiver); } catch { return false; }
  };

  const mPath = (m, c) => {
    const a = L.pos["m:" + m], b = L.pos["c:" + c];
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2, mx = (x1 + x2) / 2;
    return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  };
  const cPath = (from, to, bend) => {
    const a = L.pos["c:" + from], b = L.pos["c:" + to];
    if (!a || !b) return null;
    const left = a.x < b.x ? a : b, right = a.x < b.x ? b : a;
    const x1 = left.x + left.w, y1 = left.y + left.h / 2 + bend, x2 = right.x, y2 = right.y + right.h / 2 + bend;
    if (a.x === b.x) {
      const x = a.x + a.w, ya = a.y + a.h / 2, yb = b.y + b.h / 2;
      return `M${x},${ya} C${x + 50 + bend},${ya} ${x + 50 + bend},${yb} ${x},${yb}`;
    }
    const mx = (x1 + x2) / 2;
    return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  };

  const stateColor = (s) => !s ? C.mute : s.activated ? C.accent : s.verdict === "ELIGIBLE" ? C.good
    : s.verdict === "UNKNOWN" ? C.warn : C.mute;
  const pol = sel && policies.find((p) => p.cop === sel);
  const reg = act.registry.versions.find((v) => v.version === sc.mapping_version) || {};

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs" style={{ color: C.mute }}>Scenario</span>
        {scenarios.map((s) => (
          <button key={s.scenario} onClick={() => set({ scenario: s.scenario, at: "", acop: "" })} className="text-xs px-2.5 py-1 rounded"
                  style={s === sc ? { background: C.soft, color: C.accent, border: `1px solid ${C.accent}` } : { border: `1px solid ${C.rule}`, color: C.body, background: C.panel }}>{nice(s.scenario)}</button>))}
        <label className="text-xs flex items-center gap-2 ml-2" style={{ color: C.mute }}>
          State after record
          <input type="range" min="0" max={recs.length} value={n} onChange={(e) => set({ at: e.target.value })} />
          <span className="mono" style={{ color: C.ink }}>{n} / {recs.length}</span>
        </label>
        {n < recs.length && <button className="text-xs underline" style={{ color: C.accent }} onClick={() => set({ at: "" })}>show all records</button>}
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(320px, 420px)" }}>
        <Panel title={`${nice(sc.scenario)} · ${sc.case_id}`}
               sub={`Mapping ${sc.mapping_version} (${reg.status || "?"}, approved by ${reg.approved_by || "?"}) · hash chain ${sc.chain_verified ? "verified" : "NOT verified"} · replay with pinned version ${sc.replay.identical ? "identical" : "DIFFERS"} · ${sc.simulated.session_rows}`}>
          <div className="overflow-auto">
            <svg width={L.width} height={L.height} role="img" aria-label="COP activation graph from recorded activation records">
              <text x={G.mx} y={18} fontSize="11" fill={C.mute}>Metrics on approved edges</text>
              <text x={G.cx0} y={18} fontSize="11" fill={C.mute}>COPs in the slice (columns: depends_on depth)</text>
              <defs>
                <marker id="aarr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" fill={C.body} />
                </marker>
              </defs>
              <g fill="none">
                {act.mapping.edges.filter((e) => slice.includes(e.cop)).map((e) => {
                  const trig = triggers.has(`${e.cop}|${e.metric_id}`);
                  const ev = evState(e.cop, e.metric_id);
                  const hot = trig && ev && ["WARNING", "EXCEPTION", "CRITICAL"].includes(ev);
                  const dim = sel && sel !== e.cop;
                  return (
                    <path key={e.edge_id} d={mPath(e.metric_id, e.cop)} stroke={hot ? C.good : trig ? C.body : C.rule}
                          strokeWidth={trig ? 1.6 : 1} strokeDasharray={trig ? "" : "3 3"} strokeOpacity={dim ? 0.12 : trig ? 0.75 : 0.9}>
                      <title>{`${e.metric_id} → ${e.cop} · ${e.interpretation_status} · role ${e.role}${trig ? " · activation condition" : ""}${ev ? " · recorded state " + ev : ""}`}</title>
                    </path>);
                })}
                {policies.flatMap((p) => (p.depends_on || []).filter((d) => slice.includes(d)).map((d) => (
                  <path key={`dep${d}${p.cop}`} d={cPath(d, p.cop, -6)} stroke={C.ink} strokeWidth="1.5"
                        strokeOpacity={sel && sel !== p.cop && sel !== d ? 0.15 : 0.7} markerEnd="url(#aarr)">
                    <title>{`${p.cop} depends on ${d} (policy depends_on, within specs.CONSUMES)`}</title>
                  </path>)))}
                {act.mapping.consultations.filter((c) => slice.includes(c.initiator) && slice.includes(c.receiver)).map((c) => {
                  const used = consultUsed(c);
                  return (
                    <path key={c.contract_id} d={cPath(c.initiator, c.receiver, 8)} stroke={used ? C.warn : C.mute}
                          strokeWidth={used ? 2 : 1} strokeDasharray="5 3" strokeOpacity={sel && sel !== c.initiator && sel !== c.receiver ? 0.15 : 0.85}>
                      <title>{`consultation ${c.initiator} → ${c.receiver} (${c.kind})${used ? " · answered in this case" : ""}`}</title>
                    </path>);
                })}
              </g>
              {L.metrics.map((m) => {
                const p = L.pos["m:" + m];
                return (
                  <g key={m} transform={`translate(${p.x},${p.y})`}>
                    <rect width={p.w} height={p.h} rx="3" fill={C.panel} stroke={C.rule} />
                    <text x="6" y={p.h / 2 + 4} fontSize="10.5" className="mono" fill={C.ink}>{m}</text>
                  </g>);
              })}
              {slice.map((c) => {
                const p = L.pos["c:" + c], s = st[c];
                const col = stateColor(s);
                return (
                  <g key={c} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }}
                     onClick={() => set({ acop: sel === c ? "" : c })} opacity={sel && sel !== c ? 0.55 : 1}>
                    <rect width={p.w} height={p.h} rx="4" fill={sel === c ? C.soft : C.panel} stroke={col}
                          strokeWidth={s && s.activated ? 2.2 : 1.2} />
                    <text x="8" y="15" fontSize="11" fill={C.ink}>{nice(c)}</text>
                    {[["defined", true], ["eligible", s && s.verdict === "ELIGIBLE"], ["active", s && s.activated]].map(([k, on], i) => (
                      <g key={k} transform={`translate(${8 + i * 54},24)`}>
                        <rect width="50" height="16" rx="3" fill={on ? (k === "active" ? C.accent : k === "eligible" ? C.good : C.ink) : C.panel}
                              stroke={on ? "none" : C.rule} />
                        <text x="25" y="11.5" fontSize="9.5" textAnchor="middle" fill={on ? "#fff" : C.mute}>{k}</text>
                      </g>))}
                    <text x="8" y="58" fontSize="9.5" fill={C.mute}>
                      {s ? `${s.verdict ? s.verdict + " · " : ""}last: ${s.last}` : "no record yet"}
                    </text>
                    {s && s.last === "COMPLETED" && sc.summary.labels[c] &&
                      <text x="8" y="69" fontSize="9.5" fill={tone(sc.summary.labels[c])}>finding {sc.summary.labels[c]}</text>}
                  </g>);
              })}
            </svg>
          </div>
          <div className="flex flex-wrap gap-3 mt-2 text-xs" style={{ color: C.mute }}>
            <span className="flex items-center gap-1"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.good} strokeWidth="1.6" /></svg>activation condition, recorded state WARNING or above</span>
            <span className="flex items-center gap-1"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.body} strokeOpacity="0.75" strokeWidth="1.6" /></svg>activation condition</span>
            <span className="flex items-center gap-1"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.mute} strokeDasharray="3 3" /></svg>defined edge, not a trigger</span>
            <span className="flex items-center gap-1"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.ink} strokeWidth="1.5" /></svg>depends_on</span>
            <span className="flex items-center gap-1"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.warn} strokeDasharray="5 3" strokeWidth="2" /></svg>consultation contract (bold: answered in this case)</span>
          </div>
        </Panel>

        <div className="flex flex-col gap-3 min-w-0">
          {pol ? (
            <Panel title={`Policy · ${nice(pol.cop)}`} sub={pol.policy_id}>
              <F k="Owner" v={pol.owner} mono />
              <F k="Rule" v={pol.rule_ref} mono />
              <F k="Conditions" v={pol.conditions.map((c) => `${c.metric_id} ≥ ${c.at_least}`)} />
              <F k="Required context" v={pol.required_context} />
              <F k="Missing evidence" v={pol.missing_evidence} mono />
              <F k="Depends on" v={pol.depends_on.map((d) => slice.includes(d) ? d : `${d} (outside this slice: not scheduled)`)} />
              <F k="Rationale" v={pol.rationale} />
              <F k="Consultations" v={act.mapping.consultations.filter((c) => c.initiator === pol.cop).map((c) => `${c.receiver} (${c.kind}, depth ≤ ${c.max_depth}, ${c.timeout_s}s)`)} />
              <div className="text-xs mt-3 mb-1" style={{ color: C.mute }}>Records for this COP up to record {n}</div>
              {recs.slice(0, n).filter((r) => r.cop === pol.cop).map((r) => (
                <div key={r.record_id} className="py-1.5" style={{ borderTop: `1px solid ${C.rule}` }}>
                  <div className="flex gap-2 items-center"><span className="mono text-xs" style={{ color: C.mute }}>#{r.seq}</span><Pill color={tone(r.transition)}>{r.transition}</Pill></div>
                  <div className="text-xs mt-1">{r.reasons.join("; ")}</div>
                  {r.evidence.length > 0 && <div className="text-xs mono mt-0.5" style={{ color: C.body }}>{r.evidence.map((e) => `${e.metric}=${e.state}${e.value != null ? " (" + e.value + ")" : ""}`).join(" · ")}</div>}
                  {r.decision_id && <div className="text-xs mono mt-0.5" style={{ color: C.mute }}>decision {r.decision_id}</div>}
                </div>))}
            </Panel>
          ) : (
            <Panel title="Activation contract" sub="Click a COP for its policy and its records.">
              <F k="States" v={Object.entries(act.states).map(([k, v]) => `${k}: ${v}`)} />
              <F k="Mapping version" v={act.mapping.version} mono />
              <F k="Registry" v={act.registry.versions.map((v) => `${v.version} ${v.status} (proposed by ${v.proposed_by}, approved by ${v.approved_by || "none"})`)} />
              <F k="Bundle validation" v={act.validation_errors.length ? act.validation_errors : "no errors"} />
              <F k="Edges" v={`${act.mapping.edges.length} (${act.mapping.edges.filter((e) => e.interpretation_status === "DEFINED").length} DEFINED); only DEFINED, non-supporting edges can be activation conditions`} />
              <F k="Simulated" v={Object.entries(sc.simulated).map(([k, v]) => `${nice(k)}: ${v}`)} />
              <F k="Final transitions" v={Object.entries(sc.summary.final).map(([k, v]) => `${k}: ${v}`)} />
              <F k="Source" v={sc.source} mono />
            </Panel>
          )}
        </div>
      </div>

      <Panel title={`Activation records · ${recs.length}`} sub="Append-only, hash-chained (prev_record_id). Click a row to show the graph state after that record.">
        <div className="overflow-auto" style={{ maxHeight: "40vh" }}>
          <table className="w-full text-xs">
            <thead><tr style={{ color: C.mute }} className="text-left">
              {["#", "COP", "transition", "reasons", "decision", "record"].map((h) => <th key={h} className="py-1 pr-3 font-normal">{h}</th>)}
            </tr></thead>
            <tbody>
              {recs.map((r) => (
                <tr key={r.record_id} onClick={() => set({ at: String(r.seq) })} style={{ cursor: "pointer", borderTop: `1px solid ${C.rule}`, background: r.seq === n ? C.soft : undefined, opacity: r.seq > n ? 0.4 : 1 }}>
                  <td className="py-1 pr-3 mono">{r.seq}</td>
                  <td className="py-1 pr-3">{nice(r.cop)}</td>
                  <td className="py-1 pr-3"><Pill color={tone(r.transition)}>{r.transition}</Pill></td>
                  <td className="py-1 pr-3">{r.reasons.join("; ")}</td>
                  <td className="py-1 pr-3 mono" style={{ color: C.mute }}>{r.decision_id ? r.decision_id.slice(0, 8) : ""}</td>
                  <td className="py-1 pr-3 mono" style={{ color: C.mute }}>{r.record_id.slice(0, 8)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
