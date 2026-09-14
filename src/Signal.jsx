import React, { useState, useEffect, useMemo } from "react";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";

/* ==================================================================
 * Signal — coding agent observability
 * projectsignal.org
 *
 * Built on the enquiry framework. A domain is selected, then a subject
 * within it, and the questions for that subject are answered. Every
 * answer declares how it should be read: as a trend, as an event, or
 * as an aberration against what is normal here.
 *
 * Aggregation happens late. Every figure is a count over events that
 * still carry session, turn, agent and operator, so any number can be
 * opened into the activity behind it and followed elsewhere.
 * ================================================================== */

const URL = import.meta.env?.VITE_SUPABASE_URL || "";
const KEY = import.meta.env?.VITE_SUPABASE_KEY || "";

const C = {
  ink: "#12212E", body: "#46566A", mute: "#8695A6",
  rule: "#E3E8EC", ground: "#F6F8F9", panel: "#FFFFFF",
  accent: "#0B6E6E", soft: "#E6F1F0",
  warn: "#B4690E", danger: "#9B2C2C", good: "#2F6B4F",
};

const AGENT = {
  "claude-code": "Claude Code", cursor: "Cursor", codex: "Codex",
  copilot: "GitHub Copilot", antigravity: "Antigravity",
  custom: "Unidentified", unknown: "Unidentified",
};

const COLUMNS = [
  "session_id","tool","collector","operator_username","operator_email",
  "observable_type","occurred_at","turn_id","agent_id","total_tokens",
  "input_tokens","cache_read_tokens","cost_usd","cost_is_estimated",
  "tool_name","tool_arguments","tool_duration_ms","mcp_server","file_path",
  "file_change_kind","command","exit_code","permission_decision",
  "permission_scope","sensitivity_tier","secret_detected","reverted_to_earlier",
  "outside_workspace","success","status","hung_seconds","prompt_text",
  "reasoning_text","contributing","attribution","policy_rule_matched",
  "is_dependency_manifest","is_lockfile","workspace","model","sandboxed",
  "plan_item","batch_size",
].join(",");

/* ---------------- primitives ---------------- */
const has = (v) => v !== null && v !== undefined && v !== "" &&
  !(Array.isArray(v) && !v.length);
const uniq = (r, k) => new Set(r.map((x) => x[k]).filter(Boolean)).size;
const sum = (r, k) => r.reduce((a, x) => a + (Number(x[k]) || 0), 0);
const cnt = (r, f) => r.filter(f).length;
const num = (n) => n == null || Number.isNaN(n) ? "\u2014"
  : n >= 1e6 ? (n / 1e6).toFixed(1) + "M"
  : n >= 1e4 ? Math.round(n / 1e3) + "k"
  : Math.round(n).toLocaleString();
const pct = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "\u2014");

/* A daily series is what turns a count into a trend */
function daily(rows, fn) {
  const g = {};
  rows.forEach((r) => {
    if (!r.occurred_at) return;
    const d = r.occurred_at.slice(0, 10);
    g[d] ||= { date: d, count: 0, total: 0 };
    g[d].total++;
    if (fn(r)) g[d].count++;
  });
  return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ ...d, date: d.date.slice(5) }));
}

/* ==================================================================
 * The enquiry model. Domains hold subjects; subjects hold questions.
 * Adding a capability is adding a subject, not rewriting the page.
 * ================================================================== */

const count = (fn) => (r) => ({ value: num(cnt(r, fn)), rows: r.filter(fn), series: daily(r, fn) });
const distinct = (k) => (r) => ({ value: num(uniq(r, k)), rows: r.filter((x) => has(x[k])) });
const share = (fn, of) => (r) => {
  const b = of ? cnt(r, of) : r.length;
  return { value: pct(cnt(r, fn), b), rows: r.filter(fn), series: daily(r, fn) };
};

const DOMAINS = [
  { id: "capability", label: "Capability and restrictions",
    ask: "What can the agents do, what do they reach for, and what is refused?",
    subjects: [
      { id: "connectors", label: "Connectors and external services", qs: [
        { q: "External services reachable", type: "Have", read: "event", fn: distinct("mcp_server") },
        { q: "Calls crossing the boundary", type: "How much", read: "trend",
          fn: count((r) => has(r.mcp_server)) },
        { q: "Share of all actions going outside", type: "How much", read: "trend",
          fn: share((r) => has(r.mcp_server), (r) => has(r.tool_name) || has(r.mcp_server)) },
      ]},
      { id: "shell", label: "Shell execution", qs: [
        { q: "Commands run on the machine", type: "How much", read: "trend",
          fn: count((r) => has(r.command)) },
        { q: "Commands that failed", type: "Implications", read: "aberration",
          fn: count((r) => has(r.exit_code) && Number(r.exit_code) !== 0) },
        { q: "Commands stopped by policy", type: "Attempting", read: "aberration",
          fn: count((r) => has(r.policy_rule_matched)) },
      ]},
      { id: "filesystem", label: "Filesystem reach", qs: [
        { q: "Actions outside the workspace", type: "Where", read: "aberration",
          fn: count((r) => r.outside_workspace) },
        { q: "Share of file work outside it", type: "Where", read: "aberration",
          fn: share((r) => r.outside_workspace, (r) => has(r.file_path)) },
      ]},
      { id: "oversight", label: "Human oversight", qs: [
        { q: "Moments a person was asked", type: "How much", read: "trend",
          fn: count((r) => r.observable_type === "permission_request") },
        { q: "Approvals per action taken", type: "How", read: "trend",
          fn: share((r) => r.observable_type === "permission_request", (r) => has(r.tool_name)) },
        { q: "Sensitive access recorded", type: "Attempting", read: "aberration",
          fn: count((r) => Number(r.sensitivity_tier) >= 3) },
      ]},
      { id: "isolation", label: "Isolation", qs: [
        { q: "Commands run sandboxed", type: "Have", read: "aberration",
          fn: share((r) => r.sandboxed === true, (r) => has(r.command)) },
      ]},
    ]},

  { id: "identity", label: "Agent identity",
    ask: "Who acted, under whose authority, and was work delegated?",
    subjects: [
      { id: "agents", label: "Agents", qs: [
        { q: "Distinct agents observed", type: "Have", read: "event", fn: distinct("agent_id") },
        { q: "Actions naming an agent", type: "Have", read: "aberration",
          fn: share((r) => has(r.agent_id)) },
        { q: "Delegation events", type: "How", read: "event",
          fn: count((r) => String(r.observable_type || "").includes("subagent")) },
      ]},
      { id: "people", label: "People", qs: [
        { q: "Identified operators", type: "Have", read: "event", fn: distinct("operator_username") },
        { q: "Actions traceable to a person", type: "Have", read: "aberration",
          fn: share((r) => has(r.operator_username) || has(r.operator_email)) },
      ]},
      { id: "attribution", label: "Attribution", qs: [
        { q: "Activity tied to no agent", type: "Implications", read: "aberration",
          fn: share((r) => r.attribution === "unknown") },
        { q: "Seen by more than one channel", type: "How", read: "trend",
          fn: share((r) => (r.contributing || []).length > 1) },
      ]},
    ]},

  { id: "execution", label: "Task and execution",
    ask: "What was asked, how did it proceed, and how did it end?",
    subjects: [
      { id: "instruction", label: "Instructions", qs: [
        { q: "Instructions given by people", type: "How much", read: "trend",
          fn: count((r) => has(r.prompt_text)) },
      ]},
      { id: "steps", label: "Execution steps", qs: [
        { q: "Actions taken", type: "How much", read: "trend", fn: count((r) => has(r.tool_name)) },
        { q: "Repeated identical actions in a turn", type: "How", read: "aberration",
          fn: (r) => {
            const seen = new Set(); const dup = [];
            r.forEach((x) => {
              if (!has(x.tool_name) || !x.turn_id) return;
              const k = `${x.turn_id}|${x.tool_name}|${JSON.stringify(x.tool_arguments || "")}`;
              if (seen.has(k)) dup.push(x); else seen.add(k);
            });
            return { value: num(dup.length), rows: dup, series: daily(r, () => false) };
          } },
      ]},
      { id: "outcome", label: "Completion and failure", qs: [
        { q: "Actions that failed", type: "How much", read: "trend",
          fn: count((r) => r.success === false) },
        { q: "Failure rate", type: "Implications", read: "aberration",
          fn: share((r) => r.success === false) },
      ]},
      { id: "planning", label: "Planning", qs: [
        { q: "Plans recorded", type: "Have", read: "event", fn: count((r) => has(r.plan_item)) },
      ]},
    ]},

  { id: "tools", label: "Tool use",
    ask: "Which capabilities are invoked, and to what effect?",
    subjects: [
      { id: "usage", label: "Usage", qs: [
        { q: "Distinct tools invoked", type: "Have", read: "event", fn: distinct("tool_name") },
        { q: "Total invocations", type: "How much", read: "trend", fn: count((r) => has(r.tool_name)) },
      ]},
      { id: "balance", label: "Looking against changing", qs: [
        { q: "Reading and searching", type: "How", read: "trend",
          fn: count((r) => /read|search|grep|glob|list|view/i.test(r.tool_name || "")) },
        { q: "Writing and editing", type: "How", read: "trend",
          fn: count((r) => /write|edit|create|apply|patch/i.test(r.tool_name || "")) },
      ]},
      { id: "failure", label: "Tool failure", qs: [
        { q: "Invocations that failed", type: "Implications", read: "aberration",
          fn: count((r) => has(r.tool_name) && r.success === false) },
      ]},
    ]},

  { id: "model", label: "Model and cost",
    ask: "What configuration and spend shaped the work?",
    subjects: [
      { id: "selection", label: "Model selection", qs: [
        { q: "Models in use", type: "Have", read: "event", fn: distinct("model") },
      ]},
      { id: "consumption", label: "Consumption", qs: [
        { q: "Tokens consumed", type: "How much", read: "trend",
          fn: (r) => ({ value: num(sum(r, "total_tokens")),
                        rows: r.filter((x) => has(x.total_tokens)),
                        series: daily(r, (x) => has(x.total_tokens)) }) },
        { q: "Actions reporting usage", type: "Have", read: "aberration",
          fn: share((r) => has(r.total_tokens)) },
        { q: "Context reused rather than rebuilt", type: "How", read: "trend",
          fn: (r) => { const i = sum(r, "input_tokens"), c = sum(r, "cache_read_tokens");
            return { value: pct(c, i + c), rows: [] }; } },
      ]},
      { id: "cost", label: "Cost", qs: [
        { q: "Spend", type: "How much", read: "trend",
          fn: (r) => ({ value: "$" + sum(r, "cost_usd").toFixed(2), rows: [],
                        series: daily(r, (x) => has(x.cost_usd)) }) },
        { q: "Spend derived rather than billed", type: "Implications", read: "aberration",
          fn: share((r) => r.cost_is_estimated) },
      ]},
    ]},

  { id: "data", label: "Data and security",
    ask: "What was touched, and at what exposure?",
    subjects: [
      { id: "files", label: "Files", qs: [
        { q: "File operations", type: "How much", read: "trend", fn: count((r) => has(r.file_path)) },
        { q: "Distinct files touched", type: "Where", read: "event", fn: distinct("file_path") },
      ]},
      { id: "sensitive", label: "Credentials and sensitive paths", qs: [
        { q: "Highest-sensitivity accesses", type: "Attempting", read: "aberration",
          fn: count((r) => Number(r.sensitivity_tier) >= 3) },
        { q: "Secrets detected in content", type: "Attempting", read: "aberration",
          fn: count((r) => r.secret_detected) },
      ]},
      { id: "dependencies", label: "Dependencies", qs: [
        { q: "Dependency manifests changed", type: "How much", read: "trend",
          fn: count((r) => r.is_dependency_manifest) },
        { q: "Lockfiles changed alongside", type: "Implications", read: "aberration",
          fn: count((r) => r.is_lockfile) },
      ]},
      { id: "rework", label: "Rework", qs: [
        { q: "Files put back to earlier content", type: "Implications", read: "trend",
          fn: count((r) => r.reverted_to_earlier) },
      ]},
    ]},

  { id: "performance", label: "Performance",
    ask: "Where did the time go?",
    subjects: [
      { id: "latency", label: "Latency", qs: [
        { q: "Median action duration", type: "How much", read: "trend",
          fn: (r) => { const d = r.map((x) => Number(x.tool_duration_ms))
              .filter((n) => n > 0).sort((a, b) => a - b);
            return { value: d.length ? num(d[Math.floor(d.length / 2)]) + " ms" : "\u2014", rows: [] }; } },
        { q: "Slowest recorded action", type: "Implications", read: "aberration",
          fn: (r) => { const d = r.filter((x) => Number(x.tool_duration_ms) > 0)
              .sort((a, b) => Number(b.tool_duration_ms) - Number(a.tool_duration_ms));
            return { value: d.length ? num(Number(d[0].tool_duration_ms)) + " ms" : "\u2014",
                     rows: d.slice(0, 40) }; } },
      ]},
      { id: "stalls", label: "Stalls", qs: [
        { q: "Executions that hung", type: "Have", read: "event",
          fn: count((r) => has(r.hung_seconds)) },
      ]},
    ]},
];

const READ = {
  trend: { label: "trend", hint: "is it moving?", colour: C.accent },
  event: { label: "event", hint: "what happened?", colour: C.body },
  aberration: { label: "aberration", hint: "outside normal?", colour: C.warn },
};

/* ==================================================================
 * Components
 * ================================================================== */

function Rail({ page, setPage }) {
  const nav = [
    ["overview", "Overview", "What the estate is doing"],
    ["sessions", "Sessions", "Where to look closely"],
    ["insights", "Insights", "What needs attention"],
  ];
  return (
    <aside className="w-56 shrink-0 hidden lg:flex flex-col"
           style={{ background: C.panel, borderRight: `1px solid ${C.rule}` }}>
      <div className="px-5 py-5" style={{ borderBottom: `1px solid ${C.rule}` }}>
        <div className="flex items-baseline gap-2">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: C.accent }} />
          <span className="text-base tracking-tight">Signal</span>
        </div>
        <div className="text-xs mt-1" style={{ color: C.mute }}>Coding agent observability</div>
      </div>
      <nav className="py-2">
        {nav.map(([id, label, hint]) => {
          const on = page === id;
          return (
            <button key={id} onClick={() => setPage(id)} className="w-full text-left px-5 py-2.5 block"
                    style={{ background: on ? C.soft : "transparent",
                             borderLeft: `2px solid ${on ? C.accent : "transparent"}` }}>
              <div className="text-sm" style={{ color: on ? C.ink : C.body }}>{label}</div>
              <div className="text-xs mt-0.5" style={{ color: C.mute }}>{hint}</div>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

function Card({ title, right, note, children }) {
  return (
    <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      <header className="px-5 py-3 flex items-baseline justify-between gap-4 flex-wrap"
              style={{ borderBottom: `1px solid ${C.rule}` }}>
        <h3 className="text-sm" style={{ color: C.ink }}>{title}</h3>
        {right}
      </header>
      <div className="p-5">{children}</div>
      {note && <p className="px-5 py-3 text-xs leading-relaxed"
                  style={{ color: C.mute, borderTop: `1px solid ${C.rule}` }}>{note}</p>}
    </section>
  );
}

function Answer({ q, active, onOpen }) {
  const r = READ[q.read];
  const openable = q.result.rows?.length > 0;
  return (
    <button onClick={() => openable && onOpen(q)} disabled={!openable}
            className="w-full text-left py-3 block"
            style={{ borderBottom: `1px solid ${C.rule}`,
                     background: active ? C.soft : "transparent",
                     cursor: openable ? "pointer" : "default" }}>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm" style={{ color: C.body }}>{q.q}</span>
        <span className="text-lg tabular-nums whitespace-nowrap">{q.result.value}</span>
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <span className="text-xs px-1.5 py-0.5"
              style={{ color: r.colour, border: `1px solid ${r.colour}40` }}>{r.label}</span>
        <span className="text-xs" style={{ color: C.mute }}>{q.type}</span>
        {openable && <span className="text-xs ml-auto" style={{ color: C.accent }}>
          {num(q.result.rows.length)} events
        </span>}
      </div>
    </button>
  );
}

/* ================================================================== */

export default function Signal() {
  const [page, setPage] = useState("overview");
  const [level, setLevel] = useState("organization");
  const [scope, setScope] = useState("all");
  const [agent, setAgent] = useState("all");
  const [domainId, setDomainId] = useState("capability");
  const [subjectId, setSubjectId] = useState("connectors");
  const [drill, setDrill] = useState(null);

  const [rows, setRows] = useState([]);
  const [state, setState] = useState("loading");
  const [err, setErr] = useState("");

  useEffect(() => {
    let dead = false;
    (async () => {
      if (!URL || !KEY) { setState("nokey"); return; }
      try {
        const all = [];
        for (let from = 0; from < 90000; from += 1000) {
          const res = await fetch(
            `${URL}/rest/v1/observables?select=${COLUMNS}&order=occurred_at.asc`,
            { headers: { apikey: KEY, Authorization: `Bearer ${KEY}`,
                         Range: `${from}-${from + 999}` } });
          if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
          const b = await res.json();
          all.push(...b);
          if (b.length < 1000) break;
        }
        if (!dead) { setRows(all); setState(all.length ? "ready" : "empty"); }
      } catch (e) { if (!dead) { setErr(String(e.message || e)); setState("error"); } }
    })();
    return () => { dead = true; };
  }, []);

  const agents = useMemo(() => [...new Set(rows.map((r) => r.tool).filter(Boolean))], [rows]);
  const people = useMemo(() => [...new Set(
    rows.map((r) => r.operator_username || r.operator_email).filter(Boolean))], [rows]);
  const sessionIds = useMemo(() => {
    const m = new Map();
    rows.forEach((r) => r.session_id && m.set(r.session_id, (m.get(r.session_id) || 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 100).map((e) => e[0]);
  }, [rows]);

  useEffect(() => { setScope("all"); }, [level]);
  const domain = DOMAINS.find((d) => d.id === domainId) || DOMAINS[0];
  useEffect(() => { setSubjectId(domain.subjects[0].id); setDrill(null); }, [domainId]);
  const subject = domain.subjects.find((s) => s.id === subjectId) || domain.subjects[0];

  const view = useMemo(() => {
    let r = rows;
    if (agent !== "all") r = r.filter((x) => x.tool === agent);
    if (level === "user" && scope !== "all")
      r = r.filter((x) => (x.operator_username || x.operator_email) === scope);
    if (level === "session" && scope !== "all") r = r.filter((x) => x.session_id === scope);
    return r;
  }, [rows, agent, level, scope]);

  const answers = useMemo(
    () => subject.qs.map((q) => ({ ...q, result: q.fn(view) })), [subject, view]);

  const chart = useMemo(() => {
    if (drill?.result?.series?.length) return drill;
    return answers.find((a) => a.result.series?.some((d) => d.count > 0)) || null;
  }, [answers, drill]);

  const k = useMemo(() => {
    if (!view.length) return null;
    const t = view.map((r) => +new Date(r.occurred_at)).filter((n) => !isNaN(n));
    return {
      sessions: uniq(view, "session_id"), turns: uniq(view, "turn_id"),
      agents: uniq(view, "tool"), people: uniq(view, "operator_username") || uniq(view, "operator_email"),
      actions: cnt(view, (r) => has(r.tool_name)), files: uniq(view, "file_path"),
      tokens: sum(view, "total_tokens"), cost: sum(view, "cost_usd"),
      approvals: cnt(view, (r) => r.observable_type === "permission_request"),
      hours: t.length ? (Math.max(...t) - Math.min(...t)) / 36e5 : 0,
    };
  }, [view]);

  const estate = useMemo(() => {
    const g = {};
    view.forEach((r) => {
      const t = r.tool || "unknown";
      g[t] ||= { t, s: new Set(), a: 0, tok: 0, ch: new Set() };
      if (has(r.tool_name)) g[t].a++;
      g[t].tok += Number(r.total_tokens) || 0;
      if (r.session_id) g[t].s.add(r.session_id);
      (r.contributing?.length ? r.contributing : [r.collector]).forEach((c) => c && g[t].ch.add(c));
    });
    return Object.values(g).map((x) => ({
      name: AGENT[x.t] || x.t, sessions: x.s.size, actions: x.a,
      tokens: x.tok, channels: x.ch.size,
    })).sort((a, b) => b.sessions - a.sessions);
  }, [view]);

  const activity = useMemo(() => daily(view, () => true), [view]);

  if (state !== "ready") {
    const msg = {
      loading: "Loading the estate\u2026",
      nokey: "No database configured. Add VITE_SUPABASE_URL and VITE_SUPABASE_KEY to .env.local, then restart the dev server.",
      empty: "No activity recorded yet. Run the collectors, then the curation loader.",
      error: `Could not reach the database. ${err}`,
    }[state];
    return (
      <div className="min-h-screen flex" style={{ background: C.ground }}>
        <Rail page={page} setPage={setPage} />
        <div className="flex-1 flex items-center justify-center p-8">
          <p className="text-sm max-w-md text-center"
             style={{ color: /error|nokey/.test(state) ? C.danger : C.body }}>{msg}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex" style={{ background: C.ground, color: C.ink }}>
      <Rail page={page} setPage={setPage} />
      <div className="flex-1 min-w-0">
        <div className="px-6 py-3 flex flex-wrap items-center gap-x-4 gap-y-2"
             style={{ background: C.panel, borderBottom: `1px solid ${C.rule}` }}>
          <h1 className="text-sm">Overview</h1>
          <span className="text-xs" style={{ color: C.mute }}>
            {level === "organization" ? "Whole estate"
              : scope === "all" ? `All ${level}s` : String(scope).slice(0, 22)}
          </span>
          <div className="flex ml-auto" style={{ border: `1px solid ${C.rule}` }}>
            {["session", "user", "organization"].map((l) => (
              <button key={l} onClick={() => setLevel(l)} className="px-3 py-1.5 text-xs capitalize"
                      style={{ background: level === l ? C.ink : "transparent",
                               color: level === l ? "#fff" : C.body }}>{l}</button>
            ))}
          </div>
          {level !== "organization" && (
            <select value={scope} onChange={(e) => setScope(e.target.value)}
                    className="text-xs px-2 py-1.5 max-w-56"
                    style={{ border: `1px solid ${C.rule}`, background: C.panel, color: C.body }}>
              <option value="all">All {level === "user" ? "people" : "sessions"}</option>
              {(level === "user" ? people : sessionIds).map((s) =>
                <option key={s} value={s}>{String(s).slice(0, 24)}</option>)}
            </select>
          )}
          <select value={agent} onChange={(e) => setAgent(e.target.value)}
                  className="text-xs px-2 py-1.5"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel, color: C.body }}>
            <option value="all">All agents</option>
            {agents.map((t) => <option key={t} value={t}>{AGENT[t] || t}</option>)}
          </select>
        </div>

        <main className="p-6 space-y-5 max-w-7xl">
          {!k ? (
            <p className="text-sm" style={{ color: C.body }}>
              Nothing matches this selection. Widen the filters.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
                {[
                  ["Sessions", num(k.sessions), `${num(k.turns)} turns of work`],
                  ["Agents in use", k.agents, `${k.people || "\u2014"} people`],
                  ["Actions taken", num(k.actions), `${num(k.files)} files touched`],
                  ["Approvals asked", num(k.approvals), "people consulted"],
                  ["Tokens", num(k.tokens), "where usage is reported"],
                  ["Spend", "$" + k.cost.toFixed(0), "part derived"],
                  ["Elapsed", k.hours.toFixed(0) + "h", "across the window"],
                ].map(([l, v, s], i) => (
                  <div key={l} className="px-6 py-5 flex-1 min-w-36"
                       style={{ borderLeft: i ? `1px solid ${C.rule}` : "none" }}>
                    <div className="text-xs mb-2" style={{ color: C.mute }}>{l}</div>
                    <div className="text-2xl leading-none tabular-nums">{v}</div>
                    <div className="text-xs mt-2" style={{ color: C.body }}>{s}</div>
                  </div>
                ))}
              </div>

              <div className="grid gap-5" style={{ gridTemplateColumns: "minmax(320px,1fr) minmax(320px,1fr)" }}>
                <Card title="Activity across the estate">
                  <ResponsiveContainer width="100%" height={190}>
                    <AreaChart data={activity} margin={{ left: -24, right: 6, top: 4 }}>
                      <defs><linearGradient id="a" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={C.accent} stopOpacity={0.2} />
                        <stop offset="100%" stopColor={C.accent} stopOpacity={0.02} />
                      </linearGradient></defs>
                      <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                      <XAxis dataKey="date" tick={{ fontSize: 11, fill: C.mute }}
                             axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={26} />
                      <YAxis tick={{ fontSize: 11, fill: C.mute }} axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                      <Area type="monotone" dataKey="total" name="Actions" stroke={C.accent}
                            strokeWidth={1.5} fill="url(#a)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </Card>

                <Card title="Agents in the estate"
                      note="Channels counts the independent sources of evidence each agent exposes. Fewer channels means less of its behaviour can be seen, not that it is used less.">
                  <table className="w-full text-sm">
                    <thead><tr className="text-xs" style={{ color: C.mute }}>
                      <th className="text-left font-normal pb-2">Agent</th>
                      <th className="text-right font-normal pb-2">Sessions</th>
                      <th className="text-right font-normal pb-2">Actions</th>
                      <th className="text-right font-normal pb-2">Channels</th>
                    </tr></thead>
                    <tbody>
                      {estate.map((e) => (
                        <tr key={e.name} style={{ borderTop: `1px solid ${C.rule}` }}>
                          <td className="py-2">{e.name}</td>
                          <td className="py-2 text-right tabular-nums">{e.sessions}</td>
                          <td className="py-2 text-right tabular-nums">{num(e.actions)}</td>
                          <td className="py-2 text-right tabular-nums"
                              style={{ color: e.channels >= 4 ? C.good : e.channels >= 2 ? C.warn : C.danger }}>
                            {e.channels} of 6
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              </div>

              <Card
                title={domain.ask}
                right={
                  <div className="flex gap-2">
                    <select value={domainId} onChange={(e) => setDomainId(e.target.value)}
                            className="text-xs px-2 py-1.5"
                            style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
                      {DOMAINS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
                    </select>
                    <select value={subjectId}
                            onChange={(e) => { setSubjectId(e.target.value); setDrill(null); }}
                            className="text-xs px-2 py-1.5"
                            style={{ border: `1px solid ${C.accent}40`, background: C.soft }}>
                      {domain.subjects.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                    </select>
                  </div>
                }
                note="Each answer says how to read it. A trend asks whether something is moving, an event asks what happened, an aberration asks whether it sits outside what is normal here. Select an answer to see the activity behind it."
              >
                <div className="grid gap-6" style={{ gridTemplateColumns: "minmax(290px,1fr) minmax(300px,1.05fr)" }}>
                  <div>
                    {answers.map((q) => (
                      <Answer key={q.q} q={q} active={drill?.q === q.q}
                              onOpen={(x) => setDrill(drill?.q === x.q ? null : x)} />
                    ))}
                  </div>
                  <div>
                    {chart ? (() => {
                      const s = chart.result.series;
                      const avg = s.reduce((a, d) => a + d.count, 0) / (s.length || 1);
                      return (
                        <>
                          <div className="text-xs mb-2" style={{ color: C.body }}>{chart.q}</div>
                          <ResponsiveContainer width="100%" height={200}>
                            <LineChart data={s} margin={{ left: -24, right: 8, top: 4 }}>
                              <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                              <XAxis dataKey="date" tick={{ fontSize: 11, fill: C.mute }}
                                     axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={26} />
                              <YAxis tick={{ fontSize: 11, fill: C.mute }} axisLine={false} tickLine={false} />
                              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                              {chart.read === "aberration" &&
                                <ReferenceLine y={avg} stroke={C.warn} strokeDasharray="3 3" />}
                              <Line type="monotone" dataKey="count" name={chart.q}
                                    stroke={C.accent} strokeWidth={1.5} dot={false} />
                            </LineChart>
                          </ResponsiveContainer>
                          {chart.read === "aberration" && (
                            <p className="text-xs mt-2" style={{ color: C.mute }}>
                              The dashed line is this estate's own average, not an external
                              standard. With a baseline this size, treat it as provisional.
                            </p>
                          )}
                        </>
                      );
                    })() : (
                      <p className="text-xs" style={{ color: C.mute }}>
                        These answers describe a state rather than a movement, so there
                        is no series to plot.
                      </p>
                    )}
                  </div>
                </div>
              </Card>

              {drill?.result?.rows?.length > 0 && (
                <Card title={drill.q}
                      right={<button onClick={() => setDrill(null)} className="text-xs"
                                     style={{ color: C.accent }}>Close</button>}
                      note="Every row carries the session, turn and agent it belongs to, so any of these can be followed into the other views.">
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead><tr style={{ color: C.mute }}>
                        {["When", "Agent", "Session", "Turn", "What", "Detail"].map((h) =>
                          <th key={h} className="text-left font-normal pb-2 pr-4">{h}</th>)}
                      </tr></thead>
                      <tbody>
                        {drill.result.rows.slice(0, 40).map((r, i) => (
                          <tr key={i} style={{ borderTop: `1px solid ${C.rule}` }}>
                            <td className="py-2 pr-4 tabular-nums" style={{ color: C.body }}>
                              {String(r.occurred_at || "").slice(5, 16).replace("T", " ")}
                            </td>
                            <td className="py-2 pr-4">{AGENT[r.tool] || r.tool}</td>
                            <td className="py-2 pr-4 tabular-nums" style={{ color: C.mute }}>
                              {String(r.session_id || "\u2014").slice(0, 8)}
                            </td>
                            <td className="py-2 pr-4 tabular-nums" style={{ color: C.mute }}>
                              {String(r.turn_id || "\u2014").slice(0, 8)}
                            </td>
                            <td className="py-2 pr-4">{r.tool_name || r.observable_type}</td>
                            <td className="py-2 pr-4 truncate max-w-md" style={{ color: C.body }}>
                              {r.command || r.file_path || r.mcp_server ||
                               String(r.prompt_text || "").slice(0, 70) || "\u2014"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {drill.result.rows.length > 40 && (
                    <p className="text-xs mt-3" style={{ color: C.mute }}>
                      Showing 40 of {num(drill.result.rows.length)}.
                    </p>
                  )}
                </Card>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
