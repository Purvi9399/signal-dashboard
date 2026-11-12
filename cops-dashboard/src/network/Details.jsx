import { C, F, Panel, Pill, Src, Val, Json, nice, short } from "../ui.jsx";
import { isUndefinedMarker, isUnavailable } from "../data/contracts.js";
import { go } from "../nav.js";

/* Side panels for page 1. They print the export's fields; none is derived here. */

function TextField({ k, obj, textKey = "text" }) {
  if (!obj || isUndefinedMarker(obj) || isUnavailable(obj)) return <F k={k} v={obj} />;
  const extra = Object.entries(obj).filter(([x]) => ![textKey, "source"].includes(x));
  return (
    <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr", borderBottom: `1px solid ${C.rule}` }}>
      <div className="text-xs" style={{ color: C.mute }}>{k}</div>
      <div className="min-w-0 text-xs leading-snug">
        <Val v={obj[textKey]} />
        {extra.map(([x, v]) => v != null && (
          <div key={x} className="text-xs" style={{ color: C.body }}><span style={{ color: C.mute }}>{nice(x)}: </span><Val v={v} /></div>))}
        {obj.source && <div><Src s={obj.source} /></div>}
      </div>
    </div>
  );
}

function DecisionLinks({ ids }) {
  if (!ids || !ids.length) return <span className="text-xs italic" style={{ color: C.mute }}>not exercised in any recorded decision</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {ids.slice(0, 24).map((id) => (
        <button key={id} className="mono text-[11px] underline" style={{ color: C.accent }}
                onClick={() => go("decisions", { id })}>{short(id)}</button>))}
      {ids.length > 24 && <span className="text-xs" style={{ color: C.mute }}>+{ids.length - 24} more</span>}
    </div>
  );
}

export function EdgeDetail({ edge, net }) {
  if (!edge) return <Panel title="Edge not found" />;
  const cop = net.cops.find((c) => c.id === edge.cop_id);
  return (
    <Panel title={`${edge.metric_id} → ${cop ? cop.name : edge.cop_id}`} sub={`edge ${edge.id}`}>
      <F k="Metric ID" v={edge.metric_id} mono />
      <F k="COP ID" v={edge.cop_id} mono />
      <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr", borderBottom: `1px solid ${C.rule}` }}>
        <div className="text-xs" style={{ color: C.mute }}>Relationship</div>
        <div className="flex flex-col gap-1.5 text-xs leading-snug">
          {edge.relationships.map((r) => (
            <div key={r.type}>
              <Pill color={C.accent}>{r.type}</Pill> <span className="text-xs">{r.rule}</span>
              {r.actions && <span className="text-xs"> ({r.actions.join(", ")})</span>}
              <div><Src s={r.source} /></div>
            </div>))}
        </div>
      </div>
      <TextField k="Why relevant" obj={edge.why_relevant} />
      <TextField k="How the COP interprets it" obj={edge.interpretation} />
      <TextField k="Required / optional / conditional" obj={edge.requirement} textKey="value" />
      <TextField k="Evidence role" obj={edge.evidence_role} textKey="value" />
      <F k="Source collector" v={edge.source_collector} />
      <TextField k="Retrieval tool" obj={edge.retrieval_tool} textKey="tool" />
      <TextField k="Condition / threshold" obj={edge.condition} textKey="thresholds" />
      <F k="Spec version" v={edge.spec_version} />
      <F k="Evidence source" v={edge.evidence_source} />
      <F k="Code references" v={edge.code_refs} mono />
      <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr", borderBottom: `1px solid ${C.rule}` }}>
        <div className="text-xs" style={{ color: C.mute }}>Limitations</div>
        <ul className="text-xs list-disc ml-4">{(edge.limitations || []).map((l) => (
          <li key={l} style={{ color: /^UNDEFINED/.test(l) ? C.danger : C.body }}>{l}</li>))}</ul>
      </div>
      <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr" }}>
        <div className="text-xs" style={{ color: C.mute }}>Exercised in</div>
        <div><div className="text-xs mb-1">{edge.exercised.count} recorded decision{edge.exercised.count === 1 ? "" : "s"}</div>
          <DecisionLinks ids={edge.exercised.decision_ids} /></div>
      </div>
    </Panel>
  );
}

export function CopEdgeDetail({ edge }) {
  if (!edge) return <Panel title="Edge not found" />;
  return (
    <Panel title={`${edge.from} → ${edge.to}`} sub={nice(edge.kind)}>
      <F k="From COP" v={edge.from} mono />
      <F k="To COP" v={edge.to} mono />
      <F k="Kind" v={edge.kind} />
      <F k="Executed by the case graph" v={edge.executed} />
      <F k="Rule" v={edge.rule} src={edge.source} />
      <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr" }}>
        <div className="text-xs" style={{ color: C.mute }}>Exercised in</div>
        <div><div className="text-xs mb-1">{edge.exercised.count} recorded decision(s)</div>
          <DecisionLinks ids={edge.exercised.decision_ids} /></div>
      </div>
    </Panel>
  );
}

function EdgeList({ edges, label, select, mode }) {
  const shown = mode === "exercised" ? edges.filter((e) => e.exercised.count > 0) : edges;
  return (
    <table className="w-full text-xs">
      <thead><tr style={{ color: C.mute }}>
        <th className="text-left font-normal py-1">{label}</th><th className="text-left font-normal">Relationships</th>
        <th className="text-right font-normal">Runs</th></tr></thead>
      <tbody>{shown.map((e) => (
        <tr key={e.id} className="cursor-pointer" style={{ borderTop: `1px solid ${C.rule}` }}
            onClick={() => select({ edge: e.id, cop: "", metric: "", ce: "" })}>
          <td className="mono py-1 pr-2" style={{ color: C.accent }}>{label === "Metric" ? e.metric_id : e.cop_id}</td>
          <td>{e.relationships.map((r) => r.type).join(", ")}</td>
          <td className="text-right mono">{e.exercised.count}</td>
        </tr>))}</tbody>
    </table>
  );
}

export function CopDetail({ cop, net, mode, select }) {
  if (!cop) return <Panel title="COP not found" />;
  const edges = net.metric_edges.filter((e) => e.cop_id === cop.id);
  const out = net.cop_edges.filter((e) => e.from === cop.id), inn = net.cop_edges.filter((e) => e.to === cop.id);
  return (
    <>
      <Panel title={cop.name} sub={cop.id}>
        <F k="Behavioural question" v={cop.behavioural_question} src={cop.source && cop.source.spec} />
        <F k="Classification" v={cop.classification} />
        <F k="Spec version" v={cop.spec_version} />
        <F k="Interpretations" v={cop.interpretations} />
        <F k="Permitted actions" v={cop.permitted_actions} />
        <F k="Tools" v={cop.tools} mono />
        <F k="Consumes (executed)" v={cop.consumes} mono />
        <F k="Missing metrics (spec)" v={cop.missing_metrics} mono />
        <F k="Referral receiver" v={cop.referral_receiver} src={net.referral_scope && net.referral_scope.source} />
        <F k="Specialist" v={cop.specialist} />
        <F k="VS-01 components" v={cop.evidence_components} />
        <F k="Sources" v={cop.source} />
      </Panel>
      <Panel title={`Connected metrics (${edges.length})`} sub="select a row for the edge's metadata">
        <EdgeList edges={edges} label="Metric" select={select} mode={mode} />
      </Panel>
      <Panel title="COP–COP edges">
        {[["Out", out], ["In", inn]].map(([t, es]) => (
          <div key={t} className="mb-2">
            <div className="text-xs mb-1" style={{ color: C.mute }}>{t}</div>
            {es.length ? es.map((e) => (
              <div key={t + e.from + e.to + e.kind} className="text-xs cursor-pointer"
                   onClick={() => select({ ce: String(net.cop_edges.indexOf(e)), cop: "", metric: "", edge: "" })}>
                <span className="mono" style={{ color: C.accent }}>{t === "Out" ? e.to : e.from}</span> · {nice(e.kind)}
                {e.exercised.count ? ` · ${e.exercised.count} runs` : ""}
              </div>)) : <div className="text-xs italic" style={{ color: C.mute }}>none</div>}
          </div>))}
      </Panel>
    </>
  );
}

export function MetricDetail({ metric, net, mode, select }) {
  if (!metric) return <Panel title="Metric not found" />;
  const edges = net.metric_edges.filter((e) => e.metric_id === metric.id);
  return (
    <>
      <Panel title={metric.id} sub={metric.name}>
        <F k="Category" v={metric.category} />
        <F k="Computed by" v={metric.computed_by} src={metric.code_ref} mono />
        <F k="Basis" v={metric.basis} />
        <F k="Required fields" v={metric.required_fields} mono />
        <F k="Applies when" v={metric.applies_when} />
        <F k="Collector source" v={metric.collector_source} />
        <F k="Thresholds" v={metric.thresholds} src="collector/metrics_config.json" />
        <F k="Recomputed by MC-01" v={metric.recomputed_by_mc01} />
      </Panel>
      <Panel title={`Consuming COPs (${edges.length})`} sub="select a row for the edge's metadata">
        <EdgeList edges={edges} label="COP" select={select} mode={mode} />
      </Panel>
    </>
  );
}

export function Overview({ net }) {
  const undef = net.metric_edges.filter((e) => isUndefinedMarker(e.interpretation)).length;
  const undefWhy = net.metric_edges.filter((e) => isUndefinedMarker(e.why_relevant)).length;
  return (
    <>
      <Panel title="How to read this page" sub="Select a COP, a metric or an edge.">
        <div className="text-xs" style={{ color: C.body }}>
          Each metric → COP edge carries the relationship types that the export's rules found in source. Fields with
          no contract in source are marked UNDEFINED with the missing contract named.
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          <Pill color={C.danger}>{undef} / {net.metric_edges.length} edges: interpretation UNDEFINED</Pill>
          <Pill color={C.danger}>{undefWhy} / {net.metric_edges.length} edges: relevance UNDEFINED</Pill>
          <Pill color={net.hierarchy.status === "DOCUMENTED" ? C.good : C.danger}>hierarchy {net.hierarchy.status}</Pill>
        </div>
      </Panel>
      <Panel title="Relationship rules">
        {Object.entries(net.relationship_rules).map(([k, v]) => <F key={k} k={k} v={v} />)}
      </Panel>
      <Panel title="Versions and sources">
        {Object.entries(net.versions).map(([k, v]) => <F key={k} k={k} v={v} mono />)}
        <F k="Git commit" v={net.meta.git_commit} mono />
        <div className="mt-1"><Json v={net.meta.sources} /></div>
      </Panel>
    </>
  );
}
