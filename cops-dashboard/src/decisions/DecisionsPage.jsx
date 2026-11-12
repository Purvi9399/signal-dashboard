import { useMemo } from "react";
import { C, Panel, Pill, Select, tone, short, nice } from "../ui.jsx";
import { isUnavailable } from "../data/contracts.js";
import { go } from "../nav.js";
import DecisionDetail from "./DecisionDetail.jsx";

/* Page 2. Renders decisions.json (contract cops-decisions/1). One row is one governance
 * decision. The table only filters and sorts what the export recorded. */

export const authorityStatus = (a) => {
  const au = a.authority || {};
  if (!au.stage && au.final_authorised == null) return "no authority check recorded";
  const sel = (a.selected || {}).typed_action;
  return [au.stage, au.final_action && au.final_action !== sel && `final ${au.final_action}`]
    .filter(Boolean).join(" · ");
};

const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s);

const sel = (a) => (a.selected || {}).typed_action || "—";
const ts = (t) => (isUnavailable(t) ? "unavailable" : (t && t.value) || "—");

/* Starting points, chosen by matching recorded source fields, not by result. */
const EXAMPLES = [
  ["Anomaly Detection abnormal (bench)", (d) => d.source.case === "anomaly_detection:abnormal"],
  ["Policy Enforcement abnormal (bench)", (d) => d.source.case === "policy_enforcement:abnormal"],
  ["Workflow Orchestration abnormal (bench)", (d) => d.source.case === "workflow_orchestration:abnormal"],
  ["Workflow Orchestration STALLED (scenario referral)", (d) => d.source.variant === "successful" && d.cop === "workflow_orchestration"],
  ["Operational scenario: AD with referrals", (d) => d.source.variant === "successful" && /^root:.*:r0$/.test(d.source.run_key || "")],
  ["…its reassessment (revised support)", (d) => d.source.variant === "successful" && /:r1$/.test(d.source.run_key || "") && d.source.run_key.startsWith("root:")],
  ["…its local ALERT (gateway VERIFIED)", (d) => d.source.variant === "successful" && d.source.phase === "act"],
  ["ALERT (gateway recording)", (d) => d.source.recording === "alert"],
  ["REQUEST_APPROVAL (gateway recording)", (d) => d.source.recording === "request_approval"],
  ["HOLD_TOOL_CALL (gateway recording)", (d) => d.source.recording === "hold_tool_call"],
  ["Review escalation (contradictory)", (d) => d.source.variant === "contradictory" && d.source.phase === "act"],
  ["Approval denied", (d) => d.source.variant === "denied_approval" && d.source.phase === "act"],
  ["Harbor H02 Behaviour Analytics (real trial)", (d) => d.source.kind === "harbor" && d.source.case === "h02" && d.cop === "behavior_analytics"],
];

export default function DecisionsPage({ data, params }) {
  const all = data.decisions.decisions;
  const byId = useMemo(() => Object.fromEntries(all.map((d) => [d.decision_id, d])), [all]);
  const set = (p) => go("decisions", { ...params, ...p });

  if (params.id) {
    const d = byId[params.id];
    return d ? <DecisionDetail d={d} data={data} byId={byId} back={() => set({ id: "" })} />
      : <Panel title={`No decision ${params.id} in the export`}><button className="text-xs underline" onClick={() => set({ id: "" })}>back</button></Panel>;
  }

  const q = (params.q || "").toLowerCase();
  const sessionKey = params.session || "";
  const sessionIds = sessionKey ? new Set(data.decisions.sessions[sessionKey] || []) : null;
  const rows = all.filter((d) =>
    (!params.kind || d.source.kind === params.kind) && (!params.cop || d.cop === params.cop)
    && (!sessionIds || sessionIds.has(d.decision_id))
    && (!q || [d.decision_id, d.cop, d.trigger.id, ...d.session_ids, d.finding.label, sel(d.action),
      d.action.execution.status, d.estate, JSON.stringify(d.source)].join(" ").toLowerCase().includes(q)));
  const kinds = [...new Set(all.map((d) => d.source.kind))];
  const cops = [...new Set(all.map((d) => d.cop))].sort();
  const multi = Object.entries(data.decisions.sessions).filter(([, v]) => v.length > 1);

  return (
    <div className="flex flex-col gap-3">
      <Panel title="Examples" sub="each opens the recorded decision">
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map(([t, f]) => {
            const d = all.find(f);
            return d && <button key={t} className="text-xs px-2 py-1 rounded" onClick={() => set({ id: d.decision_id })}
                                style={{ border: `1px solid ${C.rule}`, color: C.accent, background: C.panel }}>{t}</button>;
          })}
        </div>
      </Panel>
      <div className="flex flex-wrap items-center gap-3">
        <input value={params.q || ""} onChange={(e) => set({ q: e.target.value })} placeholder="Search id, COP, trigger, session, finding, action"
               className="text-xs px-2 py-1.5 rounded w-80" style={{ border: `1px solid ${C.rule}`, background: C.panel }} />
        <Select label="Source" value={params.kind || ""} onChange={(v) => set({ kind: v })}
                options={[["", "all"], ...kinds.map((k) => [k, nice(k)])]} />
        <Select label="COP" value={params.cop || ""} onChange={(v) => set({ cop: v })}
                options={[["", "all"], ...cops.map((c) => [c, c])]} />
        <Select label="Session" value={sessionKey} onChange={(v) => set({ session: v })}
                options={[["", "all"], ...multi.map(([k, v]) => [k, `${k} (${v.length} decisions)`])]} />
        <span className="text-xs" style={{ color: C.mute }}>{rows.length} of {all.length} decisions</span>
      </div>
      <Panel>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr style={{ color: C.mute }}>
              {["Decision ID", "COP", "Trigger ID", "Session IDs", "Timestamp", "Finding", "Evidence status",
                "Selected action", "Authority status", "Execution status"].map((h) => (
                <th key={h} className="text-left font-normal pb-2 pr-3 whitespace-nowrap">{h}</th>))}
            </tr></thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.decision_id} className="cursor-pointer align-top" style={{ borderTop: `1px solid ${C.rule}` }}
                    onClick={() => set({ id: d.decision_id })}>
                  <td className="mono py-1.5 pr-3 whitespace-nowrap" style={{ color: C.accent }} title={d.decision_id}>{clip(short(d.decision_id), 26)}</td>
                  <td className="pr-3">{d.cop}</td>
                  <td className="mono pr-3 whitespace-nowrap" title={d.trigger.id}>{clip(d.trigger.id, 30)}</td>
                  <td className="mono pr-3">
                    {d.session_ids.map((s) => (
                      <button key={s} title={s} className="underline mr-1 whitespace-nowrap" onClick={(e) => { e.stopPropagation(); set({ session: `${d.estate}/${s}` }); }}>{clip(short(s), 18)}</button>))}
                    {d.baseline_session_ids && d.baseline_session_ids.length > 0 &&
                      <div style={{ color: C.mute }}>+{d.baseline_session_ids.length} baseline</div>}
                  </td>
                  <td className="mono pr-3 whitespace-nowrap">{String(ts(d.timestamp)).slice(0, 19)}</td>
                  <td className="pr-3"><Pill color={tone(d.finding.label)}>{d.finding.label}</Pill></td>
                  <td className="pr-3 whitespace-nowrap">
                    <Pill color={tone(d.evidence_coverage.state)} title="evidence coverage">{d.evidence_coverage.state}</Pill>{" "}
                    <Pill color={tone((d.determination.support || {}).decision)} title="VS-01 support">{(d.determination.support || {}).decision || "no VS-01"}</Pill>
                  </td>
                  <td className="pr-3">{sel(d.action)}</td>
                  <td className="pr-3"><Pill color={tone(authorityStatus(d.action))}>{authorityStatus(d.action)}</Pill>
                    {d.action.authority.final_authorised === false && <div style={{ color: C.danger }}>not authorised</div>}</td>
                  <td className="pr-3"><Pill color={tone(d.action.execution.status)}>{d.action.execution.status}</Pill></td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
