import { C, F, Panel, Pill } from "../ui.jsx";

/* The documented COP hierarchy, rendered only from network.hierarchy
 * (docs/cops/cop_hierarchy.json, contract cops-hierarchy/1). When the contract is
 * MISSING, no level is drawn and no COP is placed: nothing is inferred from names
 * or from the dependency graph. */

export default function HierarchyView({ net }) {
  const h = net.hierarchy;
  const byId = Object.fromEntries(net.cops.map((c) => [c.id, c]));
  if (h.status !== "DOCUMENTED") {
    return (
      <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(320px, 420px)" }}>
        <Panel title="COP hierarchy: MISSING" sub="The seven-primary-COP hierarchy is not drawn because its source was not found.">
          <F k="Missing contract" v={h.missing_contract} />
          <F k="Searched" v={h.searched} />
          <F k="Not reachable" v={h.not_reachable} />
          <F k="Contract file" v={h.placeholder_file} mono />
          <F k="Expected shape" v={h.schema} />
        </Panel>
        <Panel title={`All ${net.cops.length} COPs`} sub="hierarchy level for each: UNDEFINED until the contract is supplied">
          {net.cops.map((c) => (
            <div key={c.id} className="flex justify-between text-xs py-1" style={{ borderBottom: `1px solid ${C.rule}` }}>
              <span>{c.name} <span className="mono" style={{ color: C.mute }}>{c.id}</span></span>
              <Pill color={C.danger}>UNDEFINED</Pill>
            </div>))}
        </Panel>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Panel title="COP hierarchy" sub={`source: ${h.source_document}`}>
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${h.levels.length}, minmax(0,1fr))` }}>
          {h.levels.map((lv) => (
            <div key={lv.level}>
              <div className="text-xs uppercase tracking-wide mb-2" style={{ color: C.mute }}>{lv.level} ({lv.cops.length})</div>
              {lv.cops.map((id) => (
                <div key={id} className="text-xs rounded px-2 py-1.5 mb-1.5" style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
                  {(byId[id] || {}).name || id}
                  {h.parent_of && h.parent_of[id] && <div style={{ color: C.mute }}>under {(byId[h.parent_of[id]] || {}).name || h.parent_of[id]}</div>}
                </div>))}
            </div>))}
        </div>
        {h.unplaced && h.unplaced.length > 0 &&
          <F k="Not placed by the source" v={h.unplaced} mono />}
      </Panel>
    </div>
  );
}
