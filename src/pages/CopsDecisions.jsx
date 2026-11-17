import { useEffect, useState } from "react";
import { C } from "../theme";
import NET from "../data/cops/network.json";
import { V } from "./copsValue";

/* Page 2: COP decisions. Renders src/data/cops/decisions.json, written by
 *   cd collector && python3 -m cops.signal_pages_export --out ../src/data/cops
 * Every answer is the stored run record's value; a value the record does not hold is
 * shown as MISSING with the export's one-line reason. */

const COP = Object.fromEntries(NET.cops.map((c) => [c.id, c]));

export default function CopsDecisions() {
  const [D, setD] = useState(null);
  const [caseId, setCase] = useState(null);
  const [decId, setDec] = useState(null);
  useEffect(() => {
    import("../data/cops/decisions.json").then((m) => {
      const d = m.default || m;
      setD(d);
      setCase(d.groups[0].cases[0]);
    });
  }, []);
  if (!D) return <p className="text-sm" style={{ color: C.body }}>Loading recorded decisions…</p>;

  const cs = D.cases[caseId];
  const group = D.groups.find((g) => g.id === cs.group);
  const dec = decId && D.decisions[decId];
  const pick = (id) => { setCase(id); setDec(null); };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg" style={{ color: C.ink }}>COP decisions</h1>
        <p className="text-sm" style={{ color: C.body }}>Pick a recorded case, then a COP that ran in it.</p>
      </div>
      <div className="grid gap-4" style={{ gridTemplateColumns: "260px minmax(0,1fr)" }}>
        <nav className="rounded p-3 self-start overflow-y-auto" style={{ background: C.panel, border: `1px solid ${C.rule}`, maxHeight: "85vh" }}>
          {D.groups.map((g) => (
            <div key={g.id} className="mb-3">
              <div className="text-sm" style={{ color: C.ink }}>{g.label} ({g.cases.length})</div>
              <div className="text-xs mb-1" style={{ color: C.mute }}>{g.description}</div>
              {g.cases.map((id) => {
                const c = D.cases[id];
                return (
                  <button key={id} onClick={() => pick(id)} className="w-full text-left px-2 py-1.5 rounded block"
                          style={{ background: id === caseId ? C.soft : "transparent" }}>
                    <div className="text-sm" style={{ color: C.ink }}>{c.title}</div>
                    <div className="text-xs" style={{ color: C.mute }}>
                      {Object.keys(c.cops).map((k) => COP[k].name).join(", ")}</div>
                  </button>);
              })}
            </div>))}
        </nav>

        <div className="flex flex-col gap-4 min-w-0">
          <section className="rounded p-4" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
            <div className="text-base" style={{ color: C.ink }}>{group.label}: {cs.title}</div>
            <div className="text-sm" style={{ color: C.body }}>{group.description}</div>
            <div className="text-xs mb-3" style={{ color: C.mute }}>session {cs.sessions.join(", ")}</div>
            <table className="text-sm w-full">
              <tbody>
                {D.cop_order.map((id) => {
                  const runs = cs.cops[id] || [];
                  return (
                    <tr key={id} style={{ borderTop: `1px solid ${C.rule}` }}>
                      <td className="py-1.5 pr-3 w-8" style={{ color: C.mute }}>{COP[id].order}</td>
                      <td className="py-1.5 pr-3" style={{ color: runs.length ? C.ink : C.mute }}>{COP[id].name}</td>
                      <td className="py-1.5">
                        {runs.map((r) => (
                          <button key={r.decision_id} onClick={() => setDec(r.decision_id)}
                                  className="mr-2 mb-1 px-2 py-0.5 rounded text-sm"
                                  style={{ border: `1px solid ${r.decision_id === decId ? C.accent : C.rule}`,
                                           background: r.decision_id === decId ? C.soft : C.panel, color: C.ink }}>
                            {r.label}{r.run ? <span style={{ color: C.mute }}> · {r.run.split(":").slice(-1)[0]}</span> : null}
                          </button>))}
                      </td>
                    </tr>);
                })}
              </tbody>
            </table>
          </section>

          {dec && (
            <section className="rounded p-4" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
              <div className="text-base" style={{ color: C.ink }}>{COP[dec.cop].name}: {dec.label}</div>
              <div className="text-xs mb-2" style={{ color: C.mute }}>decision {dec.decision_id}</div>
              {dec.explanation.map((st) => (
                <details key={st.stage + dec.decision_id} open={st.open} className="mt-3">
                  <summary className="cursor-pointer text-sm py-1" style={{ color: C.accent }}>{st.stage}</summary>
                  {st.items.map(([n, q, v]) => (
                    <div key={n} className="py-2 pl-3" style={{ borderTop: `1px solid ${C.rule}` }}>
                      <div className="text-sm mb-1" style={{ color: C.ink }}>{n}. {q}</div>
                      <V v={v} />
                    </div>))}
                </details>))}
            </section>)}
        </div>
      </div>
    </div>
  );
}
