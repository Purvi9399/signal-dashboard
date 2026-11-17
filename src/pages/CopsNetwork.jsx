import { useMemo, useState } from "react";
import { C } from "../theme";
import NET from "../data/cops/network.json";
import { V, Missing, isMissing } from "./copsValue";

/* Page 1: Metrics ↔ COPs. Renders src/data/cops/network.json, written by
 *   cd collector && python3 -m cops.signal_pages_export --out ../src/data/cops
 * Metrics on the left grouped by family, the 18 COPs on the right in specification order,
 * one straight line per connection. Nothing here is computed except positions. */

const ROW = 22, HEAD = 24, TOP = 10, MX = 0, MW = 240, CX = 440, CW = 250, W = CX + CW + 2;

function layout() {
  const pos = {}, heads = [];
  let y = TOP;
  for (const fam of NET.families) {
    heads.push({ fam, y });
    y += HEAD;
    for (const m of NET.metrics.filter((x) => x.family === fam)) { pos["m:" + m.id] = y; y += ROW; }
  }
  const height = y + 10;
  const step = (height - TOP - ROW) / (NET.cops.length - 1);
  NET.cops.forEach((c, i) => { pos["c:" + c.id] = TOP + i * step; });
  return { pos, heads, height };
}

function Field({ k, children }) {
  return (
    <div className="py-2" style={{ borderTop: `1px solid ${C.rule}` }}>
      <div className="text-xs mb-1" style={{ color: C.mute }}>{k}</div>
      <div className="text-sm">{children}</div>
    </div>);
}

export default function CopsNetwork() {
  const [sel, setSel] = useState(null);          // {kind: "metric"|"cop", id}
  const L = useMemo(layout, []);
  const metric = (id) => NET.metrics.find((m) => m.id === id);
  const cop = (id) => NET.cops.find((c) => c.id === id);
  const lit = (e) => !sel ? null : sel.kind === "metric" ? e.metric_id === sel.id : e.cop_id === sel.id;
  const litM = new Set(), litC = new Set();
  NET.edges.forEach((e) => { if (lit(e)) { litM.add(e.metric_id); litC.add(e.cop_id); } });

  let panel = <p className="text-sm" style={{ color: C.body }}>Select a metric or a COP.</p>;
  if (sel && sel.kind === "metric") {
    const m = metric(sel.id);
    const rows = NET.edges.filter((e) => e.metric_id === m.id);
    panel = (
      <>
        <h3 className="text-base" style={{ color: C.ink }}>{m.name}</h3>
        <div className="text-xs mb-2" style={{ color: C.mute }}>{m.id} · {m.family}</div>
        <Field k="How it is calculated"><V v={m.calculation} /></Field>
        <Field k="Raw telemetry fields used"><V v={m.raw_fields} /></Field>
        <Field k="Where the telemetry comes from"><V v={m.collector_source} /></Field>
        <Field k="Thresholds"><V v={m.thresholds} /></Field>
        <div className="text-xs mt-4 mb-1" style={{ color: C.mute }}>COPs that use it ({rows.length})</div>
        {rows.map((e) => (
          <div key={e.cop_id} className="py-2" style={{ borderTop: `1px solid ${C.rule}` }}>
            <div className="text-sm mb-1" style={{ color: C.ink }}>{cop(e.cop_id).order}. {cop(e.cop_id).name}</div>
            <div className="grid gap-x-3 gap-y-1" style={{ gridTemplateColumns: "9rem minmax(0,1fr)" }}>
              <span className="text-xs" style={{ color: C.mute }}>What it means</span>
              <span>{isMissing(e.meaning) ? <Missing v={e.meaning} /> : <>
                <span className="text-sm">{e.meaning.text}</span>
                {e.meaning.context_changes.map((c, i) => (
                  <div key={i} className="text-sm" style={{ color: C.body }}>When {c.when}: {c.meaning}</div>))}
                <div className="text-xs" style={{ color: C.mute }}>{e.meaning.status} · {e.meaning.source}</div></>}</span>
              <span className="text-xs" style={{ color: C.mute }}>How the COP reads it</span>
              <span>{isMissing(e.interpretation) ? <Missing v={e.interpretation} /> : <>
                <span className="text-sm">{e.interpretation.rule}</span>
                <div className="text-xs break-all" style={{ color: C.mute }}>{e.interpretation.where} · role {e.interpretation.role}</div></>}</span>
              <span className="text-xs" style={{ color: C.mute }}>Implemented</span>
              <span>{isMissing(e.implemented) ? <Missing v={e.implemented} /> :
                <span className="text-sm">{e.implemented.value}: {e.implemented.basis}</span>}</span>
              <span className="text-xs" style={{ color: C.mute }}>Used in recorded decisions</span>
              <span>{isMissing(e.used) ? <Missing v={e.used} /> : <span className="text-sm">{e.used.count}</span>}</span>
            </div>
          </div>))}
      </>);
  } else if (sel && sel.kind === "cop") {
    const c = cop(sel.id);
    panel = (
      <>
        <h3 className="text-base" style={{ color: C.ink }}>{c.order}. {c.name}</h3>
        <Field k="Question">{c.question}</Field>
        <Field k={`Metrics (${c.metrics.length})`}>
          {c.metrics.map((id) => (
            <div key={id}><button className="text-sm underline" style={{ color: C.accent }}
                                  onClick={() => setSel({ kind: "metric", id })}>{id}</button></div>))}
        </Field>
        <div className="text-xs mt-1" style={{ color: C.mute }}>{c.source}</div>
      </>);
  }

  const op = (on) => (on === null ? 0.35 : on ? 1 : 0.06);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg" style={{ color: C.ink }}>Metrics ↔ COPs</h1>
        <p className="text-sm" style={{ color: C.body }}>
          Every metric-to-COP connection configured in collector/cops/specs.py. Click a metric or a COP.
          {sel && <button className="ml-3 underline" style={{ color: C.accent }} onClick={() => setSel(null)}>clear</button>}
        </p>
      </div>
      <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(320px,440px)" }}>
        <div className="rounded p-4 overflow-x-auto" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <svg width={W} height={L.height} role="img" aria-label="Metrics to COPs network">
            {NET.edges.map((e, i) => {
              const y1 = L.pos["m:" + e.metric_id] + ROW / 2 - 2, y2 = L.pos["c:" + e.cop_id] + ROW / 2 - 2;
              const on = lit(e);
              return <line key={i} x1={MX + MW} y1={y1} x2={CX} y2={y2} stroke={on ? C.accent : C.body}
                           strokeWidth={on ? 1.6 : 1} strokeOpacity={op(on)} />;
            })}
            {L.heads.map((h) => (
              <text key={h.fam} x={MX} y={h.y + 15} fontSize="11" fill={C.mute}
                    style={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{h.fam}</text>))}
            {NET.metrics.map((m) => {
              const y = L.pos["m:" + m.id], on = sel ? (sel.kind === "metric" ? sel.id === m.id : litM.has(m.id)) : true;
              return (
                <g key={m.id} transform={`translate(${MX},${y})`} onClick={() => setSel({ kind: "metric", id: m.id })}
                   style={{ cursor: "pointer" }} opacity={on ? 1 : 0.35}>
                  <rect width={MW} height={ROW - 4} rx="2" fill={sel && sel.id === m.id ? C.soft : C.panel} stroke={C.rule} />
                  <text x="6" y={ROW / 2 + 2} fontSize="12" fill={C.ink}>{m.id}</text>
                </g>);
            })}
            {NET.cops.map((c) => {
              const y = L.pos["c:" + c.id], on = sel ? (sel.kind === "cop" ? sel.id === c.id : litC.has(c.id)) : true;
              return (
                <g key={c.id} transform={`translate(${CX},${y})`} onClick={() => setSel({ kind: "cop", id: c.id })}
                   style={{ cursor: "pointer" }} opacity={on ? 1 : 0.35}>
                  <rect width={CW} height={ROW - 4} rx="2" fill={sel && sel.id === c.id ? C.soft : C.panel} stroke={C.ink} strokeOpacity="0.4" />
                  <text x="6" y={ROW / 2 + 2} fontSize="12" fill={C.ink}>{c.order}. {c.name}</text>
                </g>);
            })}
          </svg>
        </div>
        <div className="rounded p-4 self-start" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>{panel}</div>
      </div>
    </div>
  );
}
