import { useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";

/* ==================================================================
 * Session observability
 *
 * The same activity read at three levels: one session at a time, all
 * of a person's sessions together, or the whole of an agent's use.
 * The lens decides which columns matter, because a security reviewer
 * and a finance owner are looking at the same sessions for different
 * reasons.
 * ================================================================== */

const LENS = {
  all: {
    label: "Everything",
    cols: ["sessions", "turns", "actions", "mcp", "files", "tokens", "minutes"],
    flags: ["sensitive", "outside", "blocked", "failures", "reverts", "stalls", "agents"],
  },
  security: {
    label: "Security",
    cols: ["sessions", "actions", "commands", "mcp", "files"],
    flags: ["sensitive", "outside", "blocked", "secrets", "agents"],
    note: "Sensitive paths, boundary crossings, blocked commands, and anything that reached beyond the workspace.",
  },
  governance: {
    label: "Governance",
    cols: ["sessions", "turns", "actions", "approvals", "interventions"],
    flags: ["approvals", "interventions", "reverts", "agents"],
    note: "Where a person was consulted, where a person stepped in unasked, and how much agent work was undone afterwards.",
  },
  business: {
    label: "Cost and usage",
    cols: ["sessions", "turns", "actions", "tokens", "cost", "minutes"],
    flags: ["failures", "stalls"],
    note: "What the estate consumed, and where effort was spent without producing a result.",
  },
  engineering: {
    label: "Engineering",
    cols: ["sessions", "turns", "actions", "commands", "files", "minutes"],
    flags: ["failures", "reverts", "stalls"],
    note: "Volume of work, what failed, and what had to be done a second time.",
  },
};

const COL = {
  sessions:      { head: "Sessions",   get: (s) => s.sessions },
  turns:         { head: "Turns",      get: (s) => s.turns },
  actions:       { head: "Actions",    get: (s) => s.actions },
  commands:      { head: "Commands",   get: (s) => s.commands },
  mcp:           { head: "MCP calls",  get: (s) => s.mcp },
  files:         { head: "Files",      get: (s) => s.files },
  tokens:        { head: "Tokens",     get: (s) => s.tokens },
  cost:          { head: "Cost",       get: (s) => s.cost, money: true },
  minutes:       { head: "Minutes",    get: (s) => Math.round(s.minutes) },
  approvals:     { head: "Approvals",  get: (s) => s.approvals },
  interventions: { head: "Human input", get: (s) => s.interventions },
};

const FLAG = {
  sensitive:     [C.danger, "sensitive"],
  outside:       [C.danger, "outside"],
  secrets:       [C.danger, "secrets"],
  blocked:       [C.warn,   "blocked"],
  failures:      [C.warn,   "failed"],
  reverts:       [C.warn,   "reverted"],
  interventions: [C.accent, "human input"],
  approvals:     [C.accent, "approvals"],
  stalls:        [C.mute,   "stalled"],
  agents:        [C.accent, "agents"],
};

const NUMERIC = ["turns", "actions", "commands", "mcp", "files", "tokens",
  "cost", "minutes", "approvals", "interventions", "failures", "sensitive",
  "secrets", "outside", "reverts", "blocked", "stalls"];

export default function SessionObservability({ rows, onOpen }) {
  const [lens, setLens] = useState("all");
  const [level, setLevel] = useState("session");
  const [agent, setAgent] = useState("all");
  const [sort, setSort] = useState("busiest");
  const [query, setQuery] = useState("");

  /* one record per session, the unit everything else is built from */
  const sessions = useMemo(() => {
    const g = new Map();
    rows.forEach((r) => {
      if (!r.session_id) return;
      let s = g.get(r.session_id);
      if (!s) {
        s = { id: r.session_id, tool: r.tool, events: [],
              operator: null, workspace: null };
        g.set(r.session_id, s);
      }
      s.events.push(r);
      if (AGENT[r.tool]) s.tool = r.tool;
      s.operator ||= r.operator_username || r.operator_email;
      s.workspace ||= r.workspace;
    });

    return [...g.values()].map((s) => {
      const e = s.events;
      const t = e.map((x) => +new Date(x.occurred_at)).filter((n) => !isNaN(n));
      return {
        ...s,
        sessions: 1,
        started: t.length ? new Date(Math.min(...t)) : null,
        minutes: t.length ? (Math.max(...t) - Math.min(...t)) / 6e4 : 0,
        turns: uniq(e, "turn_id"),
        actions: cnt(e, (x) => has(x.tool_name)),
        commands: cnt(e, (x) => has(x.command)),
        mcp: cnt(e, (x) => has(x.mcp_server)),
        files: uniq(e, "file_path"),
        tokens: sum(e, "total_tokens"),
        cost: sum(e, "cost_usd"),
        approvals: cnt(e, (x) => x.observable_type === "permission_request"),
        interventions: Math.max(0, cnt(e, (x) => has(x.prompt_text)) - 1)
                     + cnt(e, (x) => x.reverted_to_earlier),
        failures: cnt(e, (x) => x.success === false),
        sensitive: cnt(e, (x) => Number(x.sensitivity_tier) >= 3),
        secrets: cnt(e, (x) => x.secret_detected),
        outside: cnt(e, (x) => x.outside_workspace),
        reverts: cnt(e, (x) => x.reverted_to_earlier),
        blocked: cnt(e, (x) => has(x.policy_rule_matched)),
        stalls: cnt(e, (x) => has(x.hung_seconds)),
        agents: uniq(e, "agent_id"),
      };
    });
  }, [rows]);

  const filtered = useMemo(() => {
    let s = sessions;
    if (agent !== "all") s = s.filter((x) => x.tool === agent);
    if (query.trim()) {
      const q = query.toLowerCase();
      s = s.filter((x) =>
        x.id.toLowerCase().includes(q) ||
        (x.operator || "").toLowerCase().includes(q) ||
        (x.workspace || "").toLowerCase().includes(q));
    }
    return s;
  }, [sessions, agent, query]);

  /* roll sessions up into whatever the level asks for */
  const roll = (keyFn, labelFn) => {
    const g = new Map();
    filtered.forEach((s) => {
      const k = keyFn(s);
      let x = g.get(k);
      if (!x) {
        x = { id: k, label: labelFn(s, k), tool: s.tool, sessions: 0,
              started: s.started, agents: 0 };
        NUMERIC.forEach((n) => { x[n] = 0; });
        g.set(k, x);
      }
      x.sessions++;
      NUMERIC.forEach((n) => { x[n] += s[n] || 0; });
      x.agents = Math.max(x.agents, s.agents);
      if (s.started && (!x.started || s.started > x.started)) x.started = s.started;
    });
    return [...g.values()];
  };

  const byPerson = useMemo(
    () => roll((s) => s.operator || "unidentified", (s, k) => k), [filtered]);
  const byAgent = useMemo(
    () => roll((s) => s.tool, (s) => AGENT[s.tool] || "Unattributed"), [filtered]);

  const table = level === "session" ? filtered
    : level === "person" ? byPerson : byAgent;

  const shown = useMemo(() => {
    const by = {
      recent:    (a, b) => (b.started || 0) - (a.started || 0),
      longest:   (a, b) => b.minutes - a.minutes,
      busiest:   (a, b) => b.actions - a.actions,
      costliest: (a, b) => b.tokens - a.tokens,
      failures:  (a, b) => b.failures - a.failures,
      sensitive: (a, b) => (b.sensitive + b.outside) - (a.sensitive + a.outside),
      human:     (a, b) => b.interventions - a.interventions,
    }[sort] || (() => 0);
    return [...table].sort(by).slice(0, 150);
  }, [table, sort]);

  /* averages for whatever is currently in view */
  const avg = useMemo(() => {
    const n = filtered.length || 1;
    const people = new Set(filtered.map((s) => s.operator).filter(Boolean));
    const add = (k) => filtered.reduce((a, s) => a + (s[k] || 0), 0);
    return {
      sessions: filtered.length,
      people: people.size,
      actionsPer: add("actions") / n,
      turnsPer: add("turns") / n,
      tokensPer: add("tokens") / n,
      minutesPer: add("minutes") / n,
      mcp: add("mcp"),
      approvals: add("approvals"),
      interventions: add("interventions"),
      hoursPerPerson: people.size ? add("minutes") / 60 / people.size : 0,
    };
  }, [filtered]);

  /* the comparison chart: whatever the lens leads with, across groups */
  const compare = useMemo(() => {
    const metric = LENS[lens].cols.find((c) => c !== "sessions") || "actions";
    const source = level === "session" ? byAgent : table;
    return source
      .map((x) => ({ label: String(x.label || x.id).slice(0, 22),
                     value: Math.round(COL[metric].get(x) || 0) }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value).slice(0, 12);
  }, [lens, level, table, byAgent]);

  const compareHead = COL[LENS[lens].cols.find((c) => c !== "sessions") || "actions"].head;
  const view = LENS[lens];
  const agents = [...new Set(sessions.map((s) => s.tool))].filter((t) => AGENT[t]);

  const Flag = ({ k, n }) => {
    if (!n) return null;
    const [tone, label] = FLAG[k];
    return (
      <span className="text-xs px-1.5 py-0.5 mr-1 whitespace-nowrap inline-block"
            style={{ color: tone, border: `1px solid ${tone}33` }}>
        {n} {label}
      </span>
    );
  };

  return (
    <div className="space-y-5 max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg">Session observability</h1>
          <p className="text-sm mt-1 max-w-2xl" style={{ color: C.body }}>
            The same activity read at three levels, through whichever lens the
            reader needs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: C.mute }}>Viewing as</span>
          <select value={lens} onChange={(e) => setLens(e.target.value)}
                  className="text-sm px-2 py-1.5"
                  style={{ border: `1px solid ${C.accent}55`, background: C.soft, color: C.ink }}>
            {Object.entries(LENS).map(([k, v]) =>
              <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
      </div>

      {view.note && <p className="text-xs" style={{ color: C.mute }}>{view.note}</p>}

      <div className="flex flex-wrap" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
        {[
          ["Sessions", num(avg.sessions), `${avg.people || "\u2014"} people`],
          ["Actions per session", avg.actionsPer.toFixed(1), `${avg.turnsPer.toFixed(1)} turns each`],
          ["Tokens per session", num(avg.tokensPer), `${avg.minutesPer.toFixed(0)} minutes each`],
          ["MCP calls", num(avg.mcp), "beyond the machine"],
          ["Approvals", num(avg.approvals), "agent asked to proceed"],
          ["Human input", num(avg.interventions), "person stepped in"],
          ["Hours per person", avg.hoursPerPerson.toFixed(1), "of agent time"],
        ].map(([l, v, s], i) => (
          <div key={l} className="px-5 py-4 flex-1 min-w-40"
               style={{ borderLeft: i ? `1px solid ${C.rule}` : "none" }}>
            <div className="text-xs mb-1.5" style={{ color: C.mute }}>{l}</div>
            <div className="text-xl leading-none tabular-nums">{v}</div>
            <div className="text-xs mt-1.5" style={{ color: C.body }}>{s}</div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex" style={{ border: `1px solid ${C.rule}` }}>
          {[["session", "By session"], ["person", "By person"], ["agent", "By agent"]]
            .map(([k, l]) => (
              <button key={k} onClick={() => setLevel(k)} className="px-3 py-1.5 text-xs"
                      style={{ background: level === k ? C.ink : C.panel,
                               color: level === k ? "#fff" : C.body }}>{l}</button>
            ))}
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Filter by session, person or workspace"
               className="text-sm px-3 py-1.5 flex-1 min-w-48"
               style={{ border: `1px solid ${C.rule}`, background: C.panel }} />
        <select value={agent} onChange={(e) => setAgent(e.target.value)}
                className="text-sm px-2 py-1.5"
                style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
          <option value="all">All agents</option>
          {agents.map((t) => <option key={t} value={t}>{AGENT[t]}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}
                className="text-sm px-2 py-1.5"
                style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
          <option value="busiest">Most actions</option>
          <option value="recent">Most recent</option>
          <option value="longest">Longest running</option>
          <option value="costliest">Most tokens</option>
          <option value="failures">Most failures</option>
          <option value="sensitive">Most sensitive access</option>
          <option value="human">Most human input</option>
        </select>
      </div>

      {compare.length > 0 && (
        <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <header className="px-5 py-3" style={{ borderBottom: `1px solid ${C.rule}` }}>
            <h3 className="text-sm">
              {compareHead} by {level === "session" ? "agent" : level}
            </h3>
          </header>
          <div className="p-5">
            <ResponsiveContainer width="100%" height={Math.max(160, compare.length * 26)}>
              <BarChart data={compare} layout="vertical" margin={{ left: 100, right: 16 }}>
                <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: C.mute }}
                       axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="label" width={96}
                       tick={{ fontSize: 11, fill: C.body }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                <Bar dataKey="value" name={compareHead}>
                  {compare.map((_, i) => (
                    <Cell key={i} fill={C.accent} fillOpacity={1 - i * 0.06} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <div style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs" style={{ color: C.mute }}>
                <th className="text-left font-normal px-4 py-3">
                  {level === "session" ? "Session" : level === "person" ? "Person" : "Agent"}
                </th>
                {level === "session" && (
                  <th className="text-left font-normal px-2 py-3">Agent</th>
                )}
                {view.cols.map((c) => (
                  <th key={c} className="text-right font-normal px-2 py-3">{COL[c].head}</th>
                ))}
                <th className="text-left font-normal px-4 py-3">Worth a look</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.id}
                    onClick={() => level === "session" && onOpen && onOpen(s.id)}
                    className={level === "session" ? "cursor-pointer hover:bg-slate-50" : ""}
                    style={{ borderTop: `1px solid ${C.rule}` }}>
                  <td className="px-4 py-3">
                    <div className="tabular-nums">
                      {level === "session" ? s.id.slice(0, 12) : s.label}
                    </div>
                    {level === "session" && (
                      <div className="text-xs mt-0.5" style={{ color: C.mute }}>
                        {s.started ? s.started.toISOString().slice(0, 16).replace("T", " ") : "\u2014"}
                        {s.operator ? ` \u00b7 ${s.operator}` : ""}
                      </div>
                    )}
                  </td>
                  {level === "session" && (
                    <td className="px-2 py-3">{AGENT[s.tool] || "Unattributed"}</td>
                  )}
                  {view.cols.map((c) => {
                    const v = COL[c].get(s);
                    return (
                      <td key={c} className="px-2 py-3 text-right tabular-nums">
                        {COL[c].money ? "$" + (v || 0).toFixed(2) : v ? num(v) : "\u2014"}
                      </td>
                    );
                  })}
                  <td className="px-4 py-3">
                    {view.flags.map((f) => (
                      <Flag key={f} k={f}
                            n={f === "agents" ? (s.agents > 1 ? s.agents : 0) : s[f]} />
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs leading-relaxed max-w-3xl" style={{ color: C.mute }}>
        Showing {shown.length} of {num(table.length)}. Approvals are moments the
        agent asked to proceed. Human input counts a person acting of their own
        accord: a follow-up instruction, or agent work they put back. Rows marked
        unattributed are file activity recorded on disk with no matching agent
        action, which usually means a person made the change directly.
      </p>
    </div>
  );
}
