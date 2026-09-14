import { useMemo, useState } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";

/* ==================================================================
 * Session analysis
 *
 * Two axes, following the enquiry model.
 *
 *   Type of enquiry is fixed. Seven questions, asked of anything:
 *   what is held, where it runs, how it is used, when, how much,
 *   whether anything is attempted beyond what is held, and what
 *   follows from all of it.
 *
 *   Subject of enquiry is chosen. API calls, tool use, connectors,
 *   shell, filesystem, web search, coordination between agents,
 *   environment sensing, multi-modal input.
 *
 * The seven charts never change. The subject decides what they are
 * looking at. A subject with no activity leaves them empty, and that
 * emptiness is itself the answer: this estate does not do that.
 * ================================================================== */

const WEB   = /web|search|fetch|browse|http|url|curl/i;
const SENSE = /glob|grep|find|list|ls\b|tree|read|view|explore|search_file/i;
const EDIT  = /write|edit|create|apply|patch|replace|insert/i;

const SUBJECTS = [
  {
    id: "tools", label: "Tool use",
    match: (r) => has(r.tool_name),
    item: (r) => r.tool_name,
    itemLabel: "Tools invoked",
    how: (r) => (EDIT.test(r.tool_name) ? "Changing something"
      : SENSE.test(r.tool_name) ? "Looking at something"
      : WEB.test(r.tool_name) ? "Reaching the network"
      : has(r.command) ? "Running a command" : "Other"),
    howLabel: "Manner of use",
    beyond: (r) => has(r.policy_rule_matched) || r.success === false,
    beyondLabel: "Refused or failed",
  },
  {
    id: "api", label: "Model and API calls",
    match: (r) => has(r.model) || r.observable_type === "model_request",
    item: (r) => r.model || "unnamed model",
    itemLabel: "Models called",
    how: (r) => (Number(r.cache_read_tokens) > 0 ? "Context reused" : "Context rebuilt"),
    howLabel: "How context was handled",
    beyond: (r) => r.success === false,
    beyondLabel: "Calls that failed",
  },
  {
    id: "connectors", label: "Connectors",
    match: (r) => has(r.mcp_server),
    item: (r) => r.mcp_server,
    itemLabel: "Connectors reached",
    how: (r) => r.mcp_tool || r.observable_type || "call",
    howLabel: "What was called on them",
    beyond: (r) => r.success === false || has(r.policy_rule_matched),
    beyondLabel: "Refused or unavailable",
  },
  {
    id: "shell", label: "Shell execution",
    match: (r) => has(r.command),
    item: (r) => String(r.command).trim().split(/\s+/)[0] || "unknown",
    itemLabel: "Commands issued",
    how: (r) => {
      const c = String(r.command || "");
      return /rm |chmod|chown|sudo|kill/i.test(c) ? "Altering the system"
        : /install|npm|pip|apt|brew/i.test(c) ? "Installing"
        : /test|pytest|jest|lint|build|make/i.test(c) ? "Building or checking"
        : /git/i.test(c) ? "Version control"
        : /curl|wget|ssh|nc /i.test(c) ? "Reaching the network" : "Other";
    },
    howLabel: "Kind of command",
    beyond: (r) => (has(r.exit_code) && Number(r.exit_code) !== 0) || has(r.policy_rule_matched),
    beyondLabel: "Failed or blocked",
  },
  {
    id: "files", label: "Filesystem",
    match: (r) => has(r.file_path),
    item: (r) => "." + (String(r.file_path).split(".").pop() || "none").slice(0, 12),
    itemLabel: "File types touched",
    how: (r) => (r.file_change_kind === "create" ? "Created"
      : r.file_change_kind === "modify" ? "Modified"
      : r.file_change_kind === "delete" ? "Deleted"
      : r.observable_type === "file_read" ? "Read" : "Touched"),
    howLabel: "Nature of the change",
    beyond: (r) => r.outside_workspace || Number(r.sensitivity_tier) >= 3,
    beyondLabel: "Outside the workspace or sensitive",
  },
  {
    id: "web", label: "Web and network",
    match: (r) => WEB.test(r.tool_name || "") || WEB.test(r.command || ""),
    item: (r) => r.tool_name || String(r.command || "").trim().split(/\s+/)[0],
    itemLabel: "How the network was reached",
    how: (r) => (has(r.command) ? "From the shell" : "Through a tool"),
    howLabel: "Route taken",
    beyond: (r) => r.success === false || has(r.policy_rule_matched),
    beyondLabel: "Refused or failed",
  },
  {
    id: "coordination", label: "Coordination between agents",
    match: (r) => has(r.agent_id) || String(r.observable_type || "").includes("subagent"),
    item: (r) => r.agent_type || r.agent_id?.slice(0, 10) || "unnamed agent",
    itemLabel: "Agents involved",
    how: (r) => (String(r.observable_type || "").includes("start") ? "Work handed off"
      : String(r.observable_type || "").includes("stop") ? "Work returned"
      : "Acting"),
    howLabel: "Point in the handoff",
    beyond: (r) => r.success === false,
    beyondLabel: "Delegated work that failed",
  },
  {
    id: "sensing", label: "Environment sensing",
    match: (r) => SENSE.test(r.tool_name || ""),
    item: (r) => r.tool_name,
    itemLabel: "How the agent looked around",
    how: (r) => (has(r.file_path) ? "At a specific file" : "Across the workspace"),
    howLabel: "Scope of the look",
    beyond: (r) => r.outside_workspace,
    beyondLabel: "Looked outside the workspace",
  },
  {
    id: "multimodal", label: "Multi-modal input",
    match: (r) => /\.(png|jpe?g|gif|webp|svg|pdf|mp4|mov|wav)$/i.test(r.file_path || ""),
    item: (r) => "." + String(r.file_path).split(".").pop().toLowerCase(),
    itemLabel: "Kinds of media handled",
    how: (r) => (r.file_change_kind ? "Produced" : "Read"),
    howLabel: "Direction",
    beyond: (r) => r.outside_workspace,
    beyondLabel: "Outside the workspace",
  },
];

/* ---------- shared shaping ---------- */
const tally = (rows, keyFn, limit = 10) => {
  const g = {};
  rows.forEach((r) => {
    const k = keyFn(r);
    if (k == null || k === "") return;
    g[k] = (g[k] || 0) + 1;
  });
  return Object.entries(g).map(([label, value]) => ({ label: String(label).slice(0, 22), value }))
    .sort((a, b) => b.value - a.value).slice(0, limit);
};

const daily = (rows) => {
  const g = {};
  rows.forEach((r) => {
    if (!r.occurred_at) return;
    const d = r.occurred_at.slice(0, 10);
    g[d] = (g[d] || 0) + 1;
  });
  return Object.entries(g).sort((a, b) => a[0].localeCompare(b[0]))
    .map(([d, value]) => ({ label: d.slice(5), value }));
};

/* where an action falls within its own session, which is more telling
   than the hour of day: early activity is orientation, late activity
   is verification or repair */
const position = (rows, all) => {
  const bounds = new Map();
  all.forEach((r) => {
    if (!r.session_id || !r.occurred_at) return;
    const t = +new Date(r.occurred_at);
    const b = bounds.get(r.session_id) || [t, t];
    bounds.set(r.session_id, [Math.min(b[0], t), Math.max(b[1], t)]);
  });
  const buckets = [0, 0, 0, 0, 0];
  rows.forEach((r) => {
    const b = bounds.get(r.session_id);
    if (!b || b[1] === b[0]) return;
    const f = (+new Date(r.occurred_at) - b[0]) / (b[1] - b[0]);
    buckets[Math.min(4, Math.floor(f * 5))]++;
  });
  return ["First fifth", "Second", "Middle", "Fourth", "Final fifth"]
    .map((label, i) => ({ label, value: buckets[i] }));
};

/* ---------- chart shells ---------- */
function Chart({ question, note, data, kind = "bar", empty }) {
  return (
    <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      <header className="px-4 py-3" style={{ borderBottom: `1px solid ${C.rule}` }}>
        <h3 className="text-sm">{question}</h3>
        {note && <p className="text-xs mt-1" style={{ color: C.mute }}>{note}</p>}
      </header>
      <div className="p-4">
        {!data.length ? (
          <p className="text-xs py-8 text-center" style={{ color: C.mute }}>{empty}</p>
        ) : kind === "area" ? (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={data} margin={{ left: -22, right: 6, top: 4 }}>
              <defs>
                <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.accent} stopOpacity={0.22} />
                  <stop offset="100%" stopColor={C.accent} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: C.mute }}
                     axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={24} />
              <YAxis tick={{ fontSize: 10, fill: C.mute }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
              <Area type="monotone" dataKey="value" stroke={C.accent}
                    strokeWidth={1.6} fill="url(#areaFill)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : kind === "column" ? (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data} margin={{ left: -22, right: 6, top: 4 }}>
              <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: C.mute }}
                     axisLine={{ stroke: C.rule }} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: C.mute }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
              <Bar dataKey="value">
                {data.map((_, i) => <Cell key={i} fill={C.accent} fillOpacity={0.85} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(150, data.length * 24)}>
            <BarChart data={data} layout="vertical" margin={{ left: 84, right: 14 }}>
              <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10, fill: C.mute }}
                     axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="label" width={80}
                     tick={{ fontSize: 10, fill: C.body }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
              <Bar dataKey="value">
                {data.map((_, i) => (
                  <Cell key={i} fill={C.accent} fillOpacity={1 - i * 0.07} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

/* ================================================================== */

export default function Sessions({ rows, onOpen }) {
  const [subjectId, setSubjectId] = useState("tools");
  const [agent, setAgent] = useState("all");
  const [session, setSession] = useState("all");

  /* Sessions worth choosing between, ordered by how much happened in
     them. A session with three events tells you very little. */
  const sessionList = useMemo(() => {
    const g = new Map();
    rows.forEach((r) => {
      if (!r.session_id) return;
      let x = g.get(r.session_id);
      if (!x) {
        x = { id: r.session_id, n: 0, tool: r.tool,
              when: r.occurred_at, operator: null };
        g.set(r.session_id, x);
      }
      x.n++;
      if (AGENT[r.tool]) x.tool = r.tool;
      x.operator ||= r.operator_username || r.operator_email;
      if (r.occurred_at && r.occurred_at < x.when) x.when = r.occurred_at;
    });
    return [...g.values()]
      .filter((x) => (agent === "all" ? true : x.tool === agent))
      .sort((a, b) => b.n - a.n)
      .slice(0, 120);
  }, [rows, agent]);

  const scoped = useMemo(() => {
    let r = agent === "all" ? rows : rows.filter((x) => x.tool === agent);
    if (session !== "all") r = r.filter((x) => x.session_id === session);
    return r;
  }, [rows, agent, session]);

  /* a session chosen under one agent filter should not vanish silently
     when the agent filter changes */
  const validSession = session === "all" ||
    sessionList.some((x) => x.id === session);

  const subject = SUBJECTS.find((s) => s.id === subjectId) || SUBJECTS[0];
  const matched = useMemo(() => scoped.filter(subject.match), [scoped, subject]);

  const stats = useMemo(() => {
    const sessions = uniq(matched, "session_id");
    const allSessions = uniq(scoped, "session_id") || 1;
    return {
      events: matched.length,
      sessions,
      reach: Math.round((sessions / allSessions) * 100),
      kinds: new Set(matched.map(subject.item).filter(Boolean)).size,
      beyond: cnt(matched, subject.beyond),
      perSession: sessions ? matched.length / sessions : 0,
    };
  }, [matched, scoped, subject]);

  const charts = useMemo(() => ({
    what: tally(matched, subject.item),
    where: tally(matched, (r) =>
      (r.workspace || "").split("/").pop() ||
      (r.file_path || "").split("/").slice(0, -1).pop() || null),
    how: tally(matched, subject.how),
    when: position(matched, scoped),
    much: daily(matched),
    beyond: tally(matched.filter(subject.beyond), (r) =>
      has(r.policy_rule_matched) ? "Blocked by policy"
        : r.outside_workspace ? "Outside the workspace"
        : Number(r.sensitivity_tier) >= 3 ? "Sensitive target"
        : has(r.exit_code) && Number(r.exit_code) !== 0 ? "Non-zero exit"
        : "Failed"),
    implications: [
      { label: "Failed", value: cnt(matched, (r) => r.success === false) },
      { label: "Put back later", value: cnt(matched, (r) => r.reverted_to_earlier) },
      { label: "Needed approval",
        value: cnt(matched, (r) => r.observable_type === "permission_request") },
      { label: "Stalled", value: cnt(matched, (r) => has(r.hung_seconds)) },
      { label: "Sensitive", value: cnt(matched, (r) => Number(r.sensitivity_tier) >= 3) },
    ].filter((d) => d.value),
  }), [matched, scoped, subject]);

  /* Tokens and elapsed time attach to a session, not to a single action:
     a file write carries no token count. Across sessions these describe
     the work surrounding a capability. Within one session they are that
     session's own figures. */
  const resource = useMemo(() => {
    const ids = new Set(matched.map((r) => r.session_id).filter(Boolean));
    const inSessions = session !== "all"
      ? scoped
      : scoped.filter((r) => ids.has(r.session_id));

    const perDay = {};
    inSessions.forEach((r) => {
      const t = Number(r.total_tokens) || 0;
      if (!t || !r.occurred_at) return;
      const d = r.occurred_at.slice(0, 10);
      perDay[d] ||= { label: d.slice(5), value: 0 };
      perDay[d].value += t;
    });

    const costBy = {};
    inSessions.forEach((r) => {
      const c = Number(r.cost_usd) || 0;
      if (!c) return;
      const k = AGENT[r.tool] || "Unattributed";
      costBy[k] = (costBy[k] || 0) + c;
    });

    const bounds = new Map();
    const toolOf = new Map();
    inSessions.forEach((r) => {
      if (!r.session_id || !r.occurred_at) return;
      const t = +new Date(r.occurred_at);
      const b = bounds.get(r.session_id) || [t, t];
      bounds.set(r.session_id, [Math.min(b[0], t), Math.max(b[1], t)]);
      if (AGENT[r.tool]) toolOf.set(r.session_id, r.tool);
    });
    const hoursBy = {};
    bounds.forEach(([a, b], id) => {
      const k = AGENT[toolOf.get(id)] || "Unattributed";
      hoursBy[k] = (hoursBy[k] || 0) + (b - a) / 36e5;
    });

    /* latency measured on the capability itself, where the agent reports it */
    const durations = matched
      .map((r) => Number(r.tool_duration_ms)).filter((n) => n > 0).sort((a, b) => a - b);
    const latencyByThing = {};
    matched.forEach((r) => {
      const d = Number(r.tool_duration_ms);
      if (!(d > 0)) return;
      const k = subject.item(r);
      if (!k) return;
      latencyByThing[k] ||= { n: 0, t: 0 };
      latencyByThing[k].n++; latencyByThing[k].t += d;
    });
    const latencyOverTime = {};
    matched.forEach((r) => {
      const d = Number(r.tool_duration_ms);
      if (!(d > 0) || !r.occurred_at) return;
      const k = r.occurred_at.slice(0, 10);
      latencyOverTime[k] ||= { label: k.slice(5), n: 0, t: 0 };
      latencyOverTime[k].n++; latencyOverTime[k].t += d;
    });

    const minutes = [...bounds.values()].reduce((a, [x, y]) => a + (y - x) / 6e4, 0);

    return {
      sessions: session !== "all" ? 1 : ids.size,
      tokens: sum(inSessions, "total_tokens"),
      cost: sum(inSessions, "cost_usd"),
      hours: minutes / 60,
      estimated: cnt(inSessions, (r) => r.cost_is_estimated),
      median: durations.length ? durations[Math.floor(durations.length / 2)] : 0,
      p95: durations.length ? durations[Math.floor(durations.length * 0.95)] : 0,
      timed: durations.length,
      tokenTrend: Object.entries(perDay).sort((a, b) => a[0].localeCompare(b[0]))
        .map(([, v]) => v),
      costByAgent: Object.entries(costBy)
        .map(([label, value]) => ({ label, value: +value.toFixed(2) }))
        .sort((a, b) => b.value - a.value),
      hoursByAgent: Object.entries(hoursBy)
        .map(([label, value]) => ({ label, value: +value.toFixed(1) }))
        .filter((d) => d.value > 0).sort((a, b) => b.value - a.value),
      latencyByThing: Object.entries(latencyByThing)
        .map(([label, v]) => ({ label: String(label).slice(0, 22), value: Math.round(v.t / v.n) }))
        .sort((a, b) => b.value - a.value).slice(0, 10),
      latencyTrend: Object.entries(latencyOverTime).sort((a, b) => a[0].localeCompare(b[0]))
        .map(([, v]) => ({ label: v.label, value: Math.round(v.t / v.n) })),
    };
  }, [matched, scoped, subject, session]);

  const agents = useMemo(
    () => [...new Set(rows.map((r) => r.tool))].filter((t) => AGENT[t]), [rows]);

  const empty = `No ${subject.label.toLowerCase()} recorded in this selection.`;

  return (
    <div className="space-y-5 max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg">Session analysis</h1>
          {session !== "all" && (
            <p className="text-sm mt-1" style={{ color: C.body }}>
              Session {String(session).slice(0, 14)}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <select value={subjectId} onChange={(e) => setSubjectId(e.target.value)}
                  className="text-sm px-3 py-2 min-w-52"
                  style={{ border: `1px solid ${C.accent}55`, background: C.soft, color: C.ink }}>
            {SUBJECTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <select value={agent}
                  onChange={(e) => { setAgent(e.target.value); setSession("all"); }}
                  className="text-sm px-2 py-2"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
            <option value="all">All agents</option>
            {agents.map((t) => <option key={t} value={t}>{AGENT[t]}</option>)}
          </select>
          <select value={validSession ? session : "all"}
                  onChange={(e) => setSession(e.target.value)}
                  className="text-sm px-2 py-2 max-w-64"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
            <option value="all">All sessions</option>
            {sessionList.map((x) => (
              <option key={x.id} value={x.id}>
                {x.id.slice(0, 10)} · {AGENT[x.tool] || "unattributed"}
                {x.when ? " · " + x.when.slice(5, 10) : ""} · {x.n} events
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
        {[
          ["Occurrences", num(stats.events), `of ${subject.label.toLowerCase()}`],
          [session === "all" ? "Sessions involved" : "In this session",
            session === "all" ? num(stats.sessions) : num(stats.events),
            session === "all" ? `${stats.reach}% of all sessions`
              : `of ${num(scoped.length)} recorded actions`],
          ["Distinct kinds", num(stats.kinds), subject.itemLabel.toLowerCase()],
          ["Per session", stats.perSession.toFixed(1), "where it appears at all"],
          ["Beyond reach", num(stats.beyond), subject.beyondLabel.toLowerCase()],
        ].map(([l, v, s], i) => (
          <div key={l} className="px-5 py-4 flex-1 min-w-40"
               style={{ borderLeft: i ? `1px solid ${C.rule}` : "none" }}>
            <div className="text-xs mb-1.5" style={{ color: C.mute }}>{l}</div>
            <div className="text-xl leading-none tabular-nums">{v}</div>
            <div className="text-xs mt-1.5" style={{ color: C.body }}>{s}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-4"
           style={{ gridTemplateColumns: "repeat(auto-fit,minmax(330px,1fr))" }}>
        <Chart question="What does the agent hold?"
               note={subject.itemLabel} data={charts.what} empty={empty} />
        <Chart question="Where does it execute?"
               note="By workspace and directory" data={charts.where} empty={empty} />
        <Chart question="How is it being used?"
               note={subject.howLabel} data={charts.how} empty={empty} />
        <Chart question="When within a session?"
               note="Position in the run, not clock time" data={charts.when}
               kind="column" empty={empty} />
        <Chart question="How much, and how often?"
               note="Occurrences each day" data={charts.much} kind="area" empty={empty} />
        <Chart question="Is anything attempted beyond what it holds?"
               note={subject.beyondLabel} data={charts.beyond}
               empty={`Nothing refused, blocked or failed for ${subject.label.toLowerCase()}.`} />
        <Chart question="What follows from it?"
               note="Consequences recorded alongside" data={charts.implications}
               empty="Nothing of consequence recorded alongside this activity." />
      </div>

      <div>
        <h2 className="text-sm mb-1">What this costs, and how long it takes</h2>
        <p className="text-xs mb-3 max-w-3xl" style={{ color: C.mute }}>
          {session === "all"
            ? `Consumption across the ${num(resource.sessions)} sessions in which ${subject.label.toLowerCase()} appears. Tokens and elapsed time belong to a session rather than to a single action, so these describe the work surrounding this capability. Latency is measured on the capability itself.`
            : "This session's own consumption. At single-session scope the figures are exact rather than surrounding context."}
        </p>

        <div className="flex flex-wrap mb-4"
             style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          {[
            ["Tokens", num(resource.tokens), "where usage is reported"],
            ["Spend", "$" + resource.cost.toFixed(2),
              resource.estimated ? "part derived from tokens" : "as billed"],
            ["Hours", resource.hours.toFixed(1), "of agent time"],
            ["Median latency", resource.median ? num(resource.median) + " ms" : "\u2014",
              resource.timed ? `${num(resource.p95)} ms at the 95th percentile` : "no durations reported"],
          ].map(([l, v, sub], i) => (
            <div key={l} className="px-5 py-4 flex-1 min-w-40"
                 style={{ borderLeft: i ? `1px solid ${C.rule}` : "none" }}>
              <div className="text-xs mb-1.5" style={{ color: C.mute }}>{l}</div>
              <div className="text-xl leading-none tabular-nums">{v}</div>
              <div className="text-xs mt-1.5" style={{ color: C.body }}>{sub}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-4"
             style={{ gridTemplateColumns: "repeat(auto-fit,minmax(330px,1fr))" }}>
          <Chart question="Token consumption over time"
                 note="In sessions using this capability" kind="area"
                 data={resource.tokenTrend}
                 empty="No usage reported by the agents here." />
          <Chart question="Spend by agent"
                 note="Reported, or derived from token counts"
                 data={resource.costByAgent}
                 empty="No cost reported or derivable." />
          <Chart question="Hours by agent"
                 note="Elapsed time in these sessions"
                 data={resource.hoursByAgent}
                 empty="No durations recorded." />
          <Chart question="Latency by what was called"
                 note="Mean duration, milliseconds"
                 data={resource.latencyByThing}
                 empty="These agents do not report how long this takes." />
          <Chart question="Latency over time"
                 note="Mean duration each day, milliseconds" kind="area"
                 data={resource.latencyTrend}
                 empty="No durations recorded for this capability." />
        </div>
      </div>

      <p className="text-xs leading-relaxed max-w-3xl" style={{ color: C.mute }}>
        The seven questions do not change. The subject decides what they are
        asked about, so a chart left empty means this estate does not exercise
        that capability rather than that the measure is missing. Position within
        a session is used instead of clock time, because early activity is
        orientation and late activity is verification or repair.
      </p>
    </div>
  );
}
