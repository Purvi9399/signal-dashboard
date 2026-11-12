import { C } from "../../src/theme.js";
import { isUnavailable, isUndefinedMarker } from "./data/contracts.js";

export { C };

export const nice = (s) => (s == null ? "" : String(s).replace(/_/g, " "));
export const short = (id) => (id && id.length > 12 && /^[0-9a-f-]{20,}$/.test(id) ? id.slice(0, 8) : id);

export function tone(s) {
  const t = String(s || "");
  if (/^(VERIFIED|EXECUTED_SANDBOX|SUPPORTED|COMPLETE|PASS|ANSWERED_VERIFIED|CONFIRMED|AUTHORISED)/.test(t)) return C.good;
  if (/(REJECTED|FAIL|ABSENT|TIMED_OUT|DENIED|BLOCK)/.test(t)) return C.danger;
  if (/(PARTIAL|PROVISIONAL|INCONCLUSIVE|WARN|PENDING|NOT_OPENED|AWAITING|UNVERIFIED|MISSING)/.test(t)) return C.warn;
  return C.body;
}

export function Pill({ children, color, title }) {
  return (
    <span title={title} className="inline-block text-xs px-1.5 py-0.5 rounded whitespace-nowrap"
          style={{ color: color || C.body, border: `1px solid ${color || C.rule}` }}>{children}</span>
  );
}

export function Src({ s }) {
  if (!s) return null;
  const text = typeof s === "string" ? s : [s.file, s.pointer].filter(Boolean).join(" · ");
  return <span className="mono text-[11px] break-all" style={{ color: C.mute }}>{text}</span>;
}

/* Renders any exported value. Unavailable and UNDEFINED markers are shown as such, never as blanks. */
export function Val({ v, mono }) {
  if (v === null || v === undefined || v === "")
    return <span className="text-xs italic" style={{ color: C.mute }}>not recorded</span>;
  if (isUnavailable(v))
    return <span className="text-xs" style={{ color: C.warn }}>Unavailable: {v.unavailable}</span>;
  if (isUndefinedMarker(v))
    return <span className="text-xs" style={{ color: C.danger }}>UNDEFINED · missing contract: {v.undefined}</span>;
  if (typeof v === "boolean") return <span className="mono text-xs">{v ? "true" : "false"}</span>;
  if (typeof v === "number") return <span className="mono text-xs">{v}</span>;
  if (typeof v === "string") return <span className={(mono ? "mono " : "") + "text-xs break-words"}>{v}</span>;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="text-xs italic" style={{ color: C.mute }}>none</span>;
    if (v.every((x) => typeof x !== "object" || x === null))
      return <span className="text-xs break-words">{v.join(", ")}</span>;
    return <Json v={v} />;
  }
  return <Json v={v} />;
}

export function Json({ v, open }) {
  return (
    <details open={open} className="text-xs">
      <summary className="cursor-pointer" style={{ color: C.accent }}>
        {Array.isArray(v) ? `${v.length} item${v.length === 1 ? "" : "s"}` : "details"}
      </summary>
      <pre className="mono text-[11px] whitespace-pre-wrap break-all mt-1 p-2 rounded max-h-96 overflow-auto"
           style={{ background: C.ground, color: C.body }}>{JSON.stringify(v, null, 1)}</pre>
    </details>
  );
}

/* One labelled field with its source reference. */
export function F({ k, v, src, mono }) {
  return (
    <div className="grid gap-x-3 py-1" style={{ gridTemplateColumns: "10rem 1fr", borderBottom: `1px solid ${C.rule}` }}>
      <div className="text-xs" style={{ color: C.mute }}>{k}</div>
      <div className="min-w-0 text-xs leading-snug">
        <Val v={v} mono={mono} />
        {src && <div><Src s={src} /></div>}
      </div>
    </div>
  );
}

export function Panel({ title, sub, children, right }) {
  return (
    <section className="rounded p-4" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      {(title || right) && (
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <div>
            {title && <h3 className="text-sm font-medium" style={{ color: C.ink }}>{title}</h3>}
            {sub && <p className="text-xs mt-0.5" style={{ color: C.mute }}>{sub}</p>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Select({ value, onChange, options, label }) {
  return (
    <label className="text-xs flex items-center gap-1.5" style={{ color: C.mute }}>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
              className="text-xs px-1.5 py-1 rounded" style={{ border: `1px solid ${C.rule}`, color: C.ink, background: C.panel }}>
        {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
    </label>
  );
}
