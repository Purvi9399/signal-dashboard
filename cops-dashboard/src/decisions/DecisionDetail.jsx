import { C, F, Json, Panel, Pill, Src, Val, nice, short, tone } from "../ui.jsx";
import { isUnavailable } from "../data/contracts.js";
import { go } from "../nav.js";

/* The four-step explanation of one recorded decision: Trigger, Investigation,
 * Determination, Action. Every value is a field of the decision record in
 * decisions.json; nothing is written here to fill a gap. */

const n4 = (v) => (typeof v === "number" ? Number(v.toFixed(4)) : v);

function Step({ n, title, children, sub }) {
  return (
    <Panel title={`${n}. ${title}`} sub={sub}>
      <div className="flex flex-col gap-0">{children}</div>
    </Panel>
  );
}

function Table({ rows, cols }) {
  if (!rows || !rows.length) return <div className="text-xs italic py-1" style={{ color: C.mute }}>none recorded</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead><tr style={{ color: C.mute }}>{cols.map(([k, t]) => <th key={k} className="text-left font-normal py-1 pr-3">{t}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} className="align-top" style={{ borderTop: `1px solid ${C.rule}` }}>
            {cols.map(([k, , fn]) => <td key={k} className="py-1 pr-3 min-w-0">{fn ? fn(r) : <Val v={r[k]} />}</td>)}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function Sub({ t, children }) {
  return (
    <div className="pt-2">
      <div className="text-xs font-medium mb-1" style={{ color: C.ink }}>{t}</div>
      {children}
    </div>
  );
}

function Link({ id, children }) {
  return <button className="mono text-xs underline" style={{ color: C.accent }} onClick={() => go("decisions", { id })}>{children || short(id)}</button>;
}

function Box({ title, children, color }) {
  return (
    <div className="rounded p-3 min-w-0" style={{ border: `1px solid ${color || C.rule}`, background: C.panel }}>
      <div className="text-xs uppercase tracking-wide mb-1.5" style={{ color: color || C.mute }}>{title}</div>
      {children}
    </div>
  );
}

export default function DecisionDetail({ d, data, back }) {
  const t = d.trigger, inv = d.investigation, det = d.determination, a = d.action;
  const sup = det.support || {};
  const cov = d.evidence_coverage;
  const related = (d.related || []).filter((r) => r.decision_id);
  const sessionKey = (s) => `${d.estate}/${s}`;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <button className="text-xs underline" style={{ color: C.accent }} onClick={back}>← all decisions</button>
      </div>
      <Panel title={`${d.cop} · ${d.finding.label}`} sub={`decision ${d.decision_id}`}>
        <div className="grid gap-x-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <div>
            <F k="Decision ID" v={d.decision_id} src={d.decision_id_source} mono />
            <F k="Source" v={d.source.kind} src={d.source} />
            <F k="Execution mode" v={d.execution_mode} />
            <F k="Timestamp" v={isUnavailable(d.timestamp) ? d.timestamp : d.timestamp.value} src={d.timestamp.kind || d.timestamp.note} mono />
          </div>
          <div>
            <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr", borderBottom: `1px solid ${C.rule}` }}>
              <div className="text-xs" style={{ color: C.mute }}>Session IDs</div>
              <div className="text-xs">
                {d.session_ids.map((s) => {
                  const others = (data.decisions.sessions[sessionKey(s)] || []).length;
                  return <div key={s}><span className="mono">{s}</span>{" "}
                    <button className="underline" style={{ color: C.accent }} onClick={() => go("decisions", { session: sessionKey(s) })}>
                      {others} decision{others === 1 ? "" : "s"} in this session</button></div>;
                })}
                {d.session_source && <Src s={d.session_source} />}
              </div>
            </div>
            <F k="Baseline sessions" v={d.baseline_session_ids && d.baseline_session_ids.length ? d.baseline_session_ids : { unavailable: "no baseline session ids recorded for this run" }} src="CaseData.other_sessions(): the sessions every baseline tool compares against" mono />
            <F k="Estate" v={d.estate} mono />
            <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr" }}>
              <div className="text-xs" style={{ color: C.mute }}>Related decisions</div>
              <div className="text-xs flex flex-col">{related.length ? related.map((r) => (
                <span key={r.relation + r.decision_id}>{nice(r.relation)} <Link id={r.decision_id} />{r.cop ? ` (${r.cop})` : ""}</span>))
                : <span className="italic" style={{ color: C.mute }}>none linked</span>}</div>
            </div>
          </div>
        </div>
        <details className="mt-2"><summary className="text-xs cursor-pointer" style={{ color: C.accent }}>execution mode detail</summary>
          <Json v={d.mode_detail} open /></details>
        {d.ground_truth && <F k="Ground truth (Harbor)" v={d.ground_truth} src={d.ground_truth.file || "experiments/harbor/ground_truth"} />}
      </Panel>

      <Step n={1} title="Trigger" sub="the event, metric or condition that started this decision">
        <F k="Trigger ID" v={t.id} mono />
        <F k="Kind" v={t.kind} />
        <F k="Description" v={t.description} src={t.description_source} />
        <F k="Time" v={t.at} mono />
        <F k="Sessions" v={t.sessions} mono />
        <F k="Condition / threshold" v={t.condition} />
        {t.telemetry && <F k="Telemetry" v={t.telemetry} />}
        <Sub t="What was observed">
          <Table rows={t.observed} cols={Object.keys((t.observed || [])[0] || { metric: 1 }).map((k) => [k, nice(k)])} />
        </Sub>
        {t.all_signals && <Sub t="All metric signals at decision time"><Json v={t.all_signals} /></Sub>}
        {t.source && <div className="pt-1"><Src s={t.source} /></div>}
      </Step>

      <Step n={2} title="Investigation" sub="evidence coverage is shown separately from support (step 3)">
        <div className="flex flex-wrap items-center gap-2 py-1">
          <span className="text-xs" style={{ color: C.mute }}>Evidence coverage</span>
          <Pill color={tone(cov.state)}>{cov.state}</Pill>
          <span className="text-xs">{cov.basis}</span>
        </div>
        <F k="Coverage counts" v={cov.counts} src={cov.rule} />
        <Sub t="Metrics inspected">
          <Table rows={inv.metrics_inspected} cols={[["metric", "metric"], ["state", "state"], ["value", "value"],
            ["evidence", "evidence"], ["sufficiency", "sufficiency", (r) => r.sufficiency ? <Pill color={tone(r.sufficiency)}>{r.sufficiency}</Pill> : <Val v={r.sufficiency} />]]} />
        </Sub>
        <Sub t="Records inspected"><Table rows={inv.records} cols={Object.keys((inv.records || [])[0] || { ref: 1 }).map((k) => [k, nice(k)])} /></Sub>
        <Sub t="Tools called and what they returned">
          <Table rows={inv.tools} cols={[["tool", "tool", (r) => <span className="mono">{r.tool}</span>], ["by", "requested by"], ["status", "status"],
            ["args", "args", (r) => <Val v={r.args} />], ["result", "returned", (r) => <Json v={r.result} />],
            ["rejection", "rejection"]]} />
        </Sub>
        <Sub t="Model steps">
          <Table rows={inv.llm_steps} cols={[["purpose", "purpose"], ["model", "model"], ["simulated", "simulated"],
            ["hypothesis", "hypothesis"], ["decision", "decision"]]} />
        </Sub>
        <Sub t="COPs consulted">
          <Table rows={inv.cops_consulted} cols={[["cop", "COP", (r) => r.cop || r.item], ["label", "label"], ["status", "status"],
            ["verified", "verified"], ["present", "present"], ["role", "role"]]} />
        </Sub>
        <Sub t="Additional evidence requested"><Val v={inv.evidence_requested} /></Sub>
        <Sub t="Missing dependencies"><Val v={inv.missing_dependencies} /></Sub>
        <Sub t="SQL retrieval"><F k="Performed" v={inv.sql_retrieval.performed} src={inv.sql_retrieval.basis} /></Sub>
        <Sub t="What remained unavailable"><Val v={inv.unavailable} /></Sub>
        <Sub t="Retrieval records">
          <Table rows={inv.retrievals} cols={[["requesting_cop", "COP"], ["query_or_tool", "query / tool"], ["reason", "reason"],
            ["scope", "scope", (r) => <Val v={r.scope} />], ["data_source", "data source"], ["returned_refs", "returned refs"],
            ["status", "status"], ["latency_ms", "ms"], ["effect_on_determination", "effect", (r) => <Val v={r.effect_on_determination} />]]} />
        </Sub>
        <Sub t="Resolution attempts (only where a runtime event exists)">
          <Table rows={d.resolution_attempts} cols={[["kind", "kind"], ["for", "for / receiver", (r) => <Val v={r.for || r.receiver || r.items} />],
            ["status", "status", (r) => <Val v={r.status || r.state_after} />], ["reason", "reason / question", (r) => <Val v={r.reason || r.question || (r.tools || []).join(", ")} />],
            ["event", "runtime event"]]} />
        </Sub>
      </Step>

      <Step n={3} title="Determination" sub="the conclusion and how well the evidence supports it">
        <div className="flex flex-wrap items-center gap-2 py-1">
          <Pill color={tone(det.label)}>{det.label}</Pill>
          <Pill color={tone(sup.decision)}>{sup.decision || "no VS-01 record"}</Pill>
          {sup.score != null && <span className="mono text-xs">support {n4(sup.score)} · against {n4(sup.against)} · unknown {n4(sup.unknown)}</span>}
          {det.quotient && det.quotient.value != null && <span className="mono text-xs">· quotient {n4(det.quotient.value)}</span>}
          {det.graded && det.graded.score != null && <span className="mono text-xs">· grade {det.graded.score}/5</span>}
        </div>
        <F k="Support basis" v={sup.basis} />
        <F k="Grade meaning" v={det.graded && det.graded.meaning} src={det.graded && det.graded.rule} />
        <F k="Rationale" v={d.finding.rationale} />
        <F k="Confidence" v={d.finding.confidence} />
        {d.finding.rule_baseline && <F k="Rule baseline label" v={d.finding.rule_baseline} />}
        <Sub t="Supporting evidence"><Table rows={det.supporting} cols={[["kind", "component"], ["evidence", "evidence"], ["contribution", "contribution"]]} /></Sub>
        <Sub t="Limiting evidence"><Table rows={det.limiting} cols={[["kind", "kind"], ["item", "item"], ["detail", "detail"], ["material", "material"]]} /></Sub>
        <F k="Contradictions" v={det.contradictions} />
        <Sub t="Changed after investigation">
          {det.changed ? (
            <div className="text-xs">
              <div className="flex flex-wrap gap-2 items-center">
                <span>{det.changed.from.label} {det.changed.from.support && det.changed.from.support.decision} support <span className="mono">{n4(det.changed.from.support && det.changed.from.support.support)}</span></span>
                <span>→</span>
                <span>{det.changed.to.label} {det.changed.to.support && det.changed.to.support.decision} support <span className="mono">{n4(det.changed.to.support && det.changed.to.support.support)}</span></span>
                <Pill color={tone(det.changed.resolution)}>{det.changed.resolution}</Pill>
              </div>
              <div className="mt-1">{det.changed.reason}</div>
              <Json v={det.changed.component_changes} />
              <Src s={det.changed.source} />
            </div>) : <span className="text-xs italic" style={{ color: C.mute }}>no reassessment recorded for this decision</span>}
        </Sub>
        <details className="pt-2"><summary className="text-xs cursor-pointer" style={{ color: C.accent }}>VS-01 dimensions, thresholds and checks</summary>
          <F k="Dimensions" v={sup.dimensions} />
          <F k="Thresholds" v={sup.thresholds} />
          <F k="Hard checks" v={sup.hard_checks} />
          <F k="Graded checks" v={sup.graded_checks} />
          <F k="Quotient components" v={det.quotient && det.quotient.components} />
        </details>
        <details className="pt-1"><summary className="text-xs cursor-pointer" style={{ color: C.accent }}>claims and verifier results ({(det.verifier_results || []).length})</summary>
          <Table rows={det.verifier_results} cols={[["check_id", "check"], ["result", "result", (r) => <Pill color={tone(r.result)}>{r.result}</Pill>],
            ["reason_code", "reason"], ["effect", "effect"], ["detail", "detail"]]} />
          <Json v={det.claims} />
        </details>
      </Step>

      <Step n={4} title="Action" sub="proposal, selection, authority and execution are recorded separately">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
          <Box title="Proposed by the COP">
            <div className="text-sm"><Val v={a.proposed.action} /></div>
            <div className="text-xs mt-1"><Val v={a.proposed.reason} /></div>
            {a.proposed.authority_tier && <div className="text-xs mt-1" style={{ color: C.mute }}>tier {a.proposed.authority_tier}</div>}
          </Box>
          <Box title="Selected (triage)">
            <div className="text-sm"><Val v={a.selected.typed_action} /></div>
            <div className="text-xs mt-1"><Val v={a.selected.reason} /></div>
            <div className="text-xs mt-1" style={{ color: C.mute }}>route {a.route.tier || "—"}{a.route.human_needed ? " · human needed" : ""}</div>
            <div className="text-xs"><Val v={a.route.reason} /></div>
          </Box>
          <Box title="Authority" color={a.authority.final_authorised === false ? C.danger : undefined}>
            <div className="text-sm">{a.authority.stage || "no stage recorded"}</div>
            <div className="text-xs mt-1">selected action authorised: <Val v={a.authority.selected_authorised} /></div>
            <div className="text-xs">final action: <Val v={a.authority.final_action} /> · authorised: <Val v={a.authority.final_authorised} /></div>
            <div className="text-xs">approval required: <Val v={a.requires_approval} /></div>
            {a.authority.substitute && <div className="text-xs mt-1">substitute: {a.authority.substitute.action} ({a.authority.substitute.source})</div>}
            {a.authority.reason && <div className="text-xs mt-1"><Val v={a.authority.reason} /></div>}
          </Box>
          <Box title="Execution and outcome" color={tone(a.execution.status) === C.good ? C.good : undefined}>
            <div className="text-sm"><Pill color={tone(a.execution.status)}>{a.execution.status}</Pill></div>
            <div className="text-xs mt-1">mode <Val v={a.execution.mode} /></div>
            {a.gateway && !isUnavailable(a.gateway) && (a.gateway.history || (a.gateway.actions || [])[0]) &&
              <div className="text-xs mt-1">gateway: {(a.gateway.history || a.gateway.actions[0].history).join(" → ")}</div>}
            {isUnavailable(a.gateway) && <div className="text-xs mt-1"><Val v={a.gateway} /></div>}
          </Box>
        </div>
        <F k="Route" v={a.route} />
        <F k="Approval" v={a.approval} />
        <F k="Approval and review events" v={a.approval_events} />
        <F k="Delivery" v={a.delivery} src={a.delivery && a.delivery.source} />
        <F k="Gateway record" v={a.gateway} src={a.gateway && a.gateway.source} />
        <F k="Receipt / outcome" v={a.receipt} />
        <F k="Human task" v={a.human_task} />
        <F k="Referral (router)" v={a.referral} />
        <F k="Authority checks" v={a.authority.checks} />
      </Step>
    </div>
  );
}
