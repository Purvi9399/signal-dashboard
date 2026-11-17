import { C } from "../theme";

/* Renders a value from src/data/cops/*.json exactly as stored. A {missing: reason} value is
 * shown as "MISSING: reason". Nothing is summarised or reworded here; keys are only
 * de-underscored for reading. */

export const nice = (s) => String(s ?? "").replace(/_/g, " ");
export const isMissing = (v) => v && typeof v === "object" && !Array.isArray(v) && "missing" in v;

export function Missing({ v }) {
  return <span className="text-sm" style={{ color: C.warn }}>MISSING: {v.missing}</span>;
}

const scalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);

function Scalar({ v }) {
  if (v === null || v === undefined || v === "") return <span className="text-sm" style={{ color: C.mute }}>none</span>;
  if (typeof v === "boolean") return <span className="text-sm">{v ? "yes" : "no"}</span>;
  return <span className="text-sm break-words">{String(v)}</span>;
}

export function V({ v, depth = 0 }) {
  if (isMissing(v)) return <Missing v={v} />;
  if (scalar(v) || v === undefined) return <Scalar v={v} />;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="text-sm" style={{ color: C.mute }}>none</span>;
    if (v.every(scalar)) return <span className="text-sm break-words">{v.map(String).join(", ")}</span>;
    const keys = [...new Set(v.flatMap((r) => (r && typeof r === "object" ? Object.keys(r) : [])))];
    const flat = v.every((r) => r && typeof r === "object" && !Array.isArray(r)
      && Object.values(r).every((x) => scalar(x) || isMissing(x) || (Array.isArray(x) && x.every(scalar))));
    if (flat && keys.length <= 7 && depth < 2)
      return (
        <div className="overflow-x-auto">
          <table className="text-sm w-full">
            <thead><tr>{keys.map((k) => <th key={k} className="text-left font-normal pr-4 pb-1" style={{ color: C.mute }}>{nice(k)}</th>)}</tr></thead>
            <tbody>{v.map((r, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${C.rule}` }}>
                {keys.map((k) => <td key={k} className="pr-4 py-1 align-top"><V v={r[k]} depth={depth + 1} /></td>)}
              </tr>))}</tbody>
          </table>
        </div>);
    return <div className="flex flex-col gap-2">{v.map((x, i) => (
      <div key={i} className="pl-3" style={{ borderLeft: `2px solid ${C.rule}` }}><V v={x} depth={depth + 1} /></div>))}</div>;
  }
  if (depth >= 3)
    return (
      <details className="text-sm"><summary className="cursor-pointer" style={{ color: C.accent }}>show stored record</summary>
        <pre className="text-xs whitespace-pre-wrap break-all p-2 mt-1 rounded" style={{ background: C.ground }}>{JSON.stringify(v, null, 1)}</pre>
      </details>);
  return (
    <div className="flex flex-col">
      {Object.entries(v).map(([k, x]) => (
        <div key={k} className="grid gap-3 py-1" style={{ gridTemplateColumns: "11rem minmax(0,1fr)", borderTop: depth ? "none" : `1px solid ${C.rule}` }}>
          <div className="text-sm" style={{ color: C.mute }}>{nice(k)}</div>
          <div className="min-w-0"><V v={x} depth={depth + 1} /></div>
        </div>))}
    </div>);
}
