import { useMemo } from "react";
import { C, Panel, Pill, Select, nice } from "../ui.jsx";
import { RELATIONSHIP_TYPES, COP_EDGE_KINDS } from "../data/contracts.js";
import { G, layout, metricEdgePath, copEdgePath, nameLines } from "./layout.js";
import { EdgeDetail, CopEdgeDetail, CopDetail, MetricDetail, Overview } from "./Details.jsx";
import HierarchyView from "./HierarchyView.jsx";
import { go } from "../nav.js";

/* Page 1. Renders network.json (contract cops-network/1). Every node, edge and field
 * comes from the export; this file only filters, positions and highlights. */

const KIND_STYLE = {
  consumes: { stroke: C.accent, dash: "" },
  referral: { stroke: C.warn, dash: "5 3" },
  specialist_support: { stroke: C.mute, dash: "2 3" },
  declared_handoff: { stroke: C.danger, dash: "1 4" },
};

const csv = (s) => (s ? s.split(",").filter(Boolean) : null);

export default function NetworkPage({ data, params }) {
  const net = data.network;
  const view = params.view === "hierarchy" ? "hierarchy" : "network";
  const mode = params.mode === "exercised" ? "exercised" : "configured";
  const rels = csv(params.rel) || RELATIONSHIP_TYPES;
  const kinds = csv(params.kinds) || ["consumes", "referral"];
  const copF = params.copf || "";
  const catF = params.cat || "";
  const set = (p) => go("network", { ...params, ...p });

  const L = useMemo(() => layout(net), [net]);
  const copById = useMemo(() => Object.fromEntries(net.cops.map((c) => [c.id, c])), [net]);
  const metricById = useMemo(() => Object.fromEntries(net.metrics.map((m) => [m.id, m])), [net]);
  const cats = useMemo(() => [...new Set(net.metrics.map((m) => m.category))].sort(), [net]);

  const mEdges = net.metric_edges.filter((e) =>
    (mode === "configured" || e.exercised.count > 0)
    && e.relationships.some((r) => rels.includes(r.type))
    && (!copF || e.cop_id === copF)
    && (!catF || (metricById[e.metric_id] || {}).category === catF));
  const cEdges = net.cop_edges.map((e, i) => ({ ...e, idx: i })).filter((e) =>
    kinds.includes(e.kind) && (mode === "configured" || e.exercised.count > 0)
    && (!copF || e.from === copF || e.to === copF));

  const selCop = params.cop, selMetric = params.metric, selEdge = params.edge, selCE = params.ce;
  const focus = (e) => {
    if (selEdge) return e.id === selEdge;
    if (selCop) return e.cop_id === selCop;
    if (selMetric) return e.metric_id === selMetric;
    return null;
  };
  const focusC = (e) => {
    if (selCE != null) return String(e.idx) === selCE;
    if (selCop) return e.from === selCop || e.to === selCop;
    if (selMetric || selEdge) return false;
    return null;
  };
  const litCops = new Set(), litMetrics = new Set();
  mEdges.forEach((e) => { if (focus(e)) { litCops.add(e.cop_id); litMetrics.add(e.metric_id); } });
  cEdges.forEach((e) => { if (focusC(e)) { litCops.add(e.from); litCops.add(e.to); } });
  const anyFocus = selCop || selMetric || selEdge || selCE != null;

  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]).join(",");

  let side;
  if (selEdge) side = <EdgeDetail edge={net.metric_edges.find((e) => e.id === selEdge)} net={net} />;
  else if (selCE != null) side = <CopEdgeDetail edge={net.cop_edges[Number(selCE)]} />;
  else if (selCop) side = <CopDetail cop={copById[selCop]} net={net} mode={mode} select={set} />;
  else if (selMetric) side = <MetricDetail metric={metricById[selMetric]} net={net} mode={mode} select={set} />;
  else side = <Overview net={net} />;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {[["network", "Network"], ["hierarchy", "Hierarchy"]].map(([v, t]) => (
            <button key={v} onClick={() => set({ view: v })} className="text-xs px-2.5 py-1 rounded"
                    style={view === v ? { background: C.ink, color: "#fff" } : { border: `1px solid ${C.rule}`, color: C.body, background: C.panel }}>{t}</button>
          ))}
        </div>
        {view === "network" && <>
          <div className="flex gap-1">
            {[["configured", "Configured"], ["exercised", "Exercised in recorded runs"]].map(([v, t]) => (
              <button key={v} onClick={() => set({ mode: v })} className="text-xs px-2.5 py-1 rounded"
                      style={mode === v ? { background: C.soft, color: C.accent, border: `1px solid ${C.accent}` } : { border: `1px solid ${C.rule}`, color: C.body, background: C.panel }}>{t}</button>
            ))}
          </div>
          <Select label="COP" value={copF} onChange={(v) => set({ copf: v })}
                  options={[["", "all"], ...net.cops.map((c) => [c.id, c.name])]} />
          <Select label="Metric category" value={catF} onChange={(v) => set({ cat: v })}
                  options={[["", "all"], ...cats.map((c) => [c, c])]} />
          <div className="flex flex-wrap items-center gap-1 text-xs" style={{ color: C.mute }}>
            Relationship
            {RELATIONSHIP_TYPES.map((r) => (
              <label key={r} className="flex items-center gap-0.5 ml-1" style={{ color: C.body }}>
                <input type="checkbox" checked={rels.includes(r)} onChange={() => set({ rel: toggle(rels, r) })} />{r}
              </label>))}
          </div>
          <div className="flex flex-wrap items-center gap-1 text-xs" style={{ color: C.mute }}>
            COP–COP
            {COP_EDGE_KINDS.map((k) => (
              <label key={k} className="flex items-center gap-0.5 ml-1" style={{ color: C.body }}>
                <input type="checkbox" checked={kinds.includes(k)} onChange={() => set({ kinds: toggle(kinds, k) })} />{nice(k)}
              </label>))}
          </div>
          {anyFocus && <button className="text-xs underline" style={{ color: C.accent }}
                               onClick={() => set({ cop: "", metric: "", edge: "", ce: "" })}>clear selection</button>}
        </>}
      </div>

      {view === "hierarchy" ? <HierarchyView net={net} /> : (
        <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(320px, 420px)" }}>
          <Panel title={`${net.cops.length} COPs · ${net.metrics.length} metrics · ${mEdges.length} metric edges · ${cEdges.length} COP edges shown`}
                 sub={mode === "exercised" ? net.exercised_rule : "Configured relationships from the export; columns are dependency depth in the executed case graph, not the COP hierarchy."}>
            <div className="overflow-auto" style={{ maxHeight: "78vh" }}>
              <svg width={L.width} height={L.height} role="img" aria-label="COP–metric network">
                <text x={G.metricX} y={18} fontSize="11" fill={C.mute}>Metrics (by category)</text>
                <text x={G.copX0} y={18} fontSize="11" fill={C.mute}>COPs (columns: dependency depth 0–{L.maxDepth}, from cop.consumes)</text>
                {L.cats.map((c) => (
                  <text key={c.name} x={G.metricX} y={c.y + 13} fontSize="10" fill={C.mute} style={{ textTransform: "uppercase", letterSpacing: 1 }}>{c.name}</text>))}
                <g fill="none">
                  {mEdges.map((e) => {
                    const d = metricEdgePath(L.pos, e.metric_id, e.cop_id);
                    if (!d) return null;
                    const f = focus(e);
                    const w = mode === "exercised" ? Math.min(1 + Math.log2(1 + e.exercised.count), 4) : 1.2;
                    return (
                      <g key={e.id} onClick={() => set({ edge: e.id, cop: "", metric: "", ce: "" })} style={{ cursor: "pointer" }}>
                        <path d={d} stroke="transparent" strokeWidth="8" />
                        <path d={d} stroke={f ? C.accent : C.body} strokeWidth={f ? w + 0.8 : w}
                              strokeOpacity={f === null ? 0.28 : f ? 0.95 : 0.06}>
                          <title>{`${e.metric_id} → ${e.cop_id}: ${e.relationships.map((r) => r.type).join(", ")}`}</title>
                        </path>
                      </g>);
                  })}
                  {cEdges.map((e) => {
                    const d = copEdgePath(L.pos, e.from, e.to, e.kind);
                    if (!d) return null;
                    const f = focusC(e), st = KIND_STYLE[e.kind] || KIND_STYLE.consumes;
                    return (
                      <g key={"ce" + e.idx} onClick={() => set({ ce: String(e.idx), edge: "", cop: "", metric: "" })} style={{ cursor: "pointer" }}>
                        <path d={d} stroke="transparent" strokeWidth="8" />
                        <path d={d} stroke={st.stroke} strokeDasharray={st.dash} strokeWidth={f ? 2.2 : 1.3}
                              strokeOpacity={f === null ? 0.55 : f ? 1 : 0.07} markerEnd="url(#arr)">
                          <title>{`${e.from} → ${e.to} (${e.kind})`}</title>
                        </path>
                      </g>);
                  })}
                </g>
                <defs>
                  <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                    <path d="M0,0 L8,4 L0,8 z" fill={C.body} />
                  </marker>
                </defs>
                {net.metrics.map((m) => {
                  const p = L.pos["m:" + m.id];
                  const lit = litMetrics.has(m.id) || selMetric === m.id;
                  return (
                    <g key={m.id} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }}
                       onClick={() => set({ metric: m.id, cop: "", edge: "", ce: "" })}>
                      <rect width={p.w} height={p.h} rx="3" fill={selMetric === m.id ? C.soft : C.panel}
                            stroke={lit ? C.accent : C.rule} opacity={anyFocus && !lit ? 0.45 : 1} />
                      <text x="6" y={p.h / 2 + 4} fontSize="11" className="mono" fill={C.ink}
                            opacity={anyFocus && !lit ? 0.45 : 1}>{m.id}</text>
                    </g>);
                })}
                {net.cops.map((c) => {
                  const p = L.pos["c:" + c.id];
                  const lit = litCops.has(c.id) || selCop === c.id;
                  return (
                    <g key={c.id} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }}
                       onClick={() => set({ cop: c.id, metric: "", edge: "", ce: "" })}>
                      <rect width={p.w} height={p.h} rx="4" fill={selCop === c.id ? C.soft : C.panel}
                            stroke={lit ? C.accent : C.ink} strokeOpacity={lit ? 1 : 0.35} strokeWidth={lit ? 1.6 : 1}
                            opacity={anyFocus && !lit ? 0.45 : 1} />
                      {nameLines(c.name).map((ln, i) => (
                        <text key={i} x="7" y={14 + i * 12} fontSize="11" fill={C.ink} opacity={anyFocus && !lit ? 0.45 : 1}>{ln}</text>))}
                      <text x="7" y={p.h - 7} fontSize="9.5" fill={C.mute} opacity={anyFocus && !lit ? 0.45 : 1}>
                        {c.classification} · {c.metric_keys.length} metrics{c.referral_receiver ? " · recv" : ""}
                      </text>
                    </g>);
                })}
              </svg>
            </div>
            <div className="flex flex-wrap gap-3 mt-2 text-xs" style={{ color: C.mute }}>
              {Object.entries(KIND_STYLE).map(([k, st]) => (
                <span key={k} className="flex items-center gap-1">
                  <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={st.stroke} strokeDasharray={st.dash} strokeWidth="1.5" /></svg>
                  {nice(k)}
                </span>))}
              <span className="flex items-center gap-1">
                <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={C.body} strokeOpacity="0.5" strokeWidth="1.2" /></svg>
                metric → COP</span>
              {mode === "exercised" && <Pill color={C.accent}>line width: number of recorded decisions</Pill>}
            </div>
          </Panel>
          <div className="flex flex-col gap-3 min-w-0">{side}</div>
        </div>
      )}
    </div>
  );
}
