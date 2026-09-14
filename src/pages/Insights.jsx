import { useMemo, useState } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";

/* ==================================================================
 * Insights
 *
 * Only what looks wrong. Everything descriptive lives on the other
 * pages; this one runs a fixed set of checks across every session and
 * reports what they caught.
 *
 * Each check is a rule with a stated condition, not a score. Where a
 * rule compares against the estate's own middle rather than an
 * external standard, it says so, because a few weeks of one team's
 * work is a provisional baseline.
 * ================================================================== */

const GROUPS = {
  all:       "Everything",
  boundary:  "Boundary and exposure",
  oversight: "Oversight",
  loops:     "Repetition and stalling",
  outcome:   "Outcomes",
  integrity: "Change integrity",
  cost:      "Cost and duration",
};

/* Sessions are needed by several checks, so shape them once. */
function shapeSessions(rows) {
  const g = new Map();
  rows.forEach((r) => {
    if (!r.session_id) return;
    let s = g.get(r.session_id);
    if (!s) { s = { id: r.session_id, tool: r.tool, e: [] }; g.set(r.session_id, s); }
    if (AGENT[r.tool]) s.tool = r.tool;
    s.e.push(r);
  });
  return [...g.values()].map((s) => {
    const t = s.e.map((x) => +new Date(x.occurred_at)).filter((n) => !isNaN(n));
    return {
      ...s,
      minutes: t.length ? (Math.max(...t) - Math.min(...t)) / 6e4 : 0,
      tokens: sum(s.e, "total_tokens"),
      actions: cnt(s.e, (x) => has(x.tool_name)),
      approvals: cnt(s.e, (x) => x.observable_type === "permission_request"),
      sensitive: cnt(s.e, (x) => Number(x.sensitivity_tier) >= 3),
    };
  });
}

const median = (xs) => {
  const s = xs.filter((n) => n > 0).sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : 0;
};

/* ---------- the checks ---------- */

const CHECKS = [
  {
    id: "repetition", group: "loops",
    title: "The same action repeated inside one turn",
    meaning: "An agent issuing an identical call with identical arguments more than once in a single turn is not making progress. This is the most frequently reported failure in studies of agentic systems.",
    rule: "Identical tool name and arguments, seen more than once within one turn.",
    find: (rows) => {
      const seen = new Set(); const out = [];
      rows.forEach((r) => {
        if (!has(r.tool_name) || !r.turn_id) return;
        const k = `${r.turn_id}|${r.tool_name}|${JSON.stringify(r.tool_arguments || "")}`;
        if (seen.has(k)) out.push(r); else seen.add(k);
      });
      return out;
    },
  },
  {
    id: "retry", group: "loops",
    title: "A failed command reissued without change",
    meaning: "Running the same command again after it failed, with nothing altered, means the agent has not read the failure. Repeated cycles of this are how sessions burn time without moving.",
    rule: "A command with a non-zero exit, followed by the same command in the same session.",
    find: (rows) => {
      const failed = new Map();
      const out = [];
      [...rows].sort((a, b) => String(a.occurred_at).localeCompare(String(b.occurred_at)))
        .forEach((r) => {
          if (!has(r.command) || !r.session_id) return;
          const k = `${r.session_id}|${String(r.command).trim()}`;
          if (failed.has(k)) out.push(r);
          if (has(r.exit_code) && Number(r.exit_code) !== 0) failed.set(k, true);
        });
      return out;
    },
  },
  {
    id: "stalled", group: "loops",
    title: "Execution left hanging",
    meaning: "A process still alive but producing nothing. Structured telemetry reports on completion, so a hang is invisible to it. Only the terminal sees this state.",
    rule: "Silence recorded while a process remained running.",
    find: (rows) => rows.filter((r) => has(r.hung_seconds)),
  },
  {
    id: "outside", group: "boundary",
    title: "Work reaching outside its workspace",
    meaning: "An agent writing or reading beyond the directory it was given. Sometimes intended, often not, and worth knowing either way.",
    rule: "The target path resolves outside the workspace root.",
    find: (rows) => rows.filter((r) => r.outside_workspace),
  },
  {
    id: "sensitive_unasked", group: "boundary",
    title: "Credentials and keys reached without asking",
    meaning: "A file matching credential patterns was opened in a session where the agent never once requested approval. The absence of any approval in the whole session is what makes this notable.",
    rule: "A sensitive path touched in a session containing no approval request.",
    find: (rows) => {
      const s = shapeSessions(rows);
      const noApproval = new Set(s.filter((x) => x.approvals === 0).map((x) => x.id));
      return rows.filter((r) => Number(r.sensitivity_tier) >= 3 && noApproval.has(r.session_id));
    },
  },
  {
    id: "secrets", group: "boundary",
    title: "Secrets appearing in content the agent handled",
    meaning: "Key-shaped material read into an agent's context. Once there it may travel to a model provider, into a transcript, or into a commit.",
    rule: "Content matching key, token or credential patterns.",
    find: (rows) => rows.filter((r) => r.secret_detected),
  },
  {
    id: "blocked", group: "oversight",
    title: "Actions stopped by policy",
    meaning: "Attempts that a rule refused. Worth reading as evidence of what agents reach for when nothing stops them, not only as evidence that the control worked.",
    rule: "A policy rule matched and the action was refused.",
    find: (rows) => rows.filter((r) => has(r.policy_rule_matched)),
  },
  {
    id: "no_oversight", group: "oversight",
    title: "Long sessions where nobody was ever asked",
    meaning: "Substantial runs that never once paused for a person. Either the work genuinely needed no judgement, or the agent was configured not to ask.",
    rule: "More than forty actions and no approval requested in the whole session.",
    find: (rows) => {
      const s = shapeSessions(rows);
      const ids = new Set(s.filter((x) => x.actions > 40 && x.approvals === 0).map((x) => x.id));
      return rows.filter((r) => ids.has(r.session_id) && r.observable_type === "session_end");
    },
    unit: "sessions",
  },
  {
    id: "reverted", group: "integrity",
    title: "Agent work put back afterwards",
    meaning: "A file returned to content it held earlier. No agent emits an undo event, so this is recovered by comparing content over time, and it is the closest available signal that someone rejected the output.",
    rule: "File content returned to a previously recorded state.",
    find: (rows) => rows.filter((r) => r.reverted_to_earlier),
  },
  {
    id: "lockfile", group: "integrity",
    title: "Dependencies changed without the lockfile",
    meaning: "A manifest edited while its lockfile was left alone. What resolves on the next install is then unpredictable, which is how an unintended version reaches production.",
    rule: "A dependency manifest changed in a session where no lockfile changed.",
    find: (rows) => {
      const withLock = new Set(rows.filter((r) => r.is_lockfile).map((r) => r.session_id));
      return rows.filter((r) => r.is_dependency_manifest && !withLock.has(r.session_id));
    },
  },
  {
    id: "failed", group: "outcome",
    title: "Actions that failed outright",
    meaning: "Where effort was spent and nothing came of it. Clusters around one tool or one command family usually point at configuration rather than at the agent.",
    rule: "The action reported failure.",
    find: (rows) => rows.filter((r) => r.success === false),
  },
  {
    id: "unattributed", group: "integrity",
    title: "Changes with no agent behind them",
    meaning: "Files that changed on disk with no matching agent action. Usually a person editing directly, which is invisible to every agent-facing channel.",
    rule: "A file change recorded with no tool event to account for it.",
    find: (rows) => rows.filter((r) => r.attribution === "unknown" && has(r.file_path)),
  },
  {
    id: "token_outlier", group: "cost",
    title: "Sessions consuming far beyond the usual",
    meaning: "Compared against the middle of this estate rather than any external figure. A short history makes this provisional, and it is meant to prompt a look rather than settle anything.",
    rule: "Session token use above five times the estate median.",
    find: (rows) => {
      const s = shapeSessions(rows);
      const m = median(s.map((x) => x.tokens));
      if (!m) return [];
      const ids = new Set(s.filter((x) => x.tokens > m * 5).map((x) => x.id));
      return rows.filter((r) => ids.has(r.session_id) && has(r.total_tokens));
    },
    unit: "sessions",
  },
  {
    id: "duration_outlier", group: "cost",
    title: "Actions taking far longer than the rest",
    meaning: "Individual calls well beyond the typical duration for this estate. Often a network wait or a command that never really finished.",
    rule: "Duration above ten times the median recorded duration.",
    find: (rows) => {
      const m = median(rows.map((r) => Number(r.tool_duration_ms)));
      if (!m) return [];
      return rows.filter((r) => Number(r.tool_duration_ms) > m * 10);
    },
  },
];

/* ---------- shaping ---------- */
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

const tally = (rows, keyFn, limit = 10) => {
  const g = {};
  rows.forEach((r) => {
    const k = keyFn(r);
    if (k == null || k === "") return;
    g[k] = (g[k] || 0) + 1;
  });
  return Object.entries(g).map(([label, value]) => ({ label: String(label).slice(0, 24), value }))
    .sort((a, b) => b.value - a.value).slice(0, limit);
};

/* ================================================================== */

export default function Insights({ rows }) {
  const [group, setGroup] = useState("all");
  const [agent, setAgent] = useState("all");
  const [openId, setOpenId] = useState(null);

  const scoped = useMemo(
    () => (agent === "all" ? rows : rows.filter((r) => r.tool === agent)), [rows, agent]);

  const results = useMemo(() => CHECKS.map((c) => {
    const hits = c.find(scoped);
    return {
      ...c,
      hits,
      count: hits.length,
      sessions: uniq(hits, "session_id"),
      agents: [...new Set(hits.map((h) => AGENT[h.tool]).filter(Boolean))],
    };
  }), [scoped]);

  const found = results.filter((r) => r.count > 0)
    .filter((r) => group === "all" || r.group === group)
    .sort((a, b) => b.count - a.count);
  const clear = results.filter((r) => r.count === 0)
    .filter((r) => group === "all" || r.group === group);

  const open = results.find((r) => r.id === openId);
  const agents = useMemo(
    () => [...new Set(rows.map((r) => r.tool))].filter((t) => AGENT[t]), [rows]);

  const trend = useMemo(() => (open ? daily(open.hits) : []), [open]);
  const byAgent = useMemo(
    () => (open ? tally(open.hits, (r) => AGENT[r.tool] || "Unattributed") : []), [open]);
  const byThing = useMemo(() => (open ? tally(open.hits, (r) =>
    r.tool_name || (r.file_path || "").split("/").pop() ||
    String(r.command || "").trim().split(/\s+/)[0] || r.observable_type) : []), [open]);

  const totalSessions = uniq(scoped, "session_id");

  return (
    <div className="space-y-5 max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg">Insights</h1>
          <p className="text-sm mt-1 max-w-2xl" style={{ color: C.body }}>
            What the checks caught across every session recorded.
          </p>
        </div>
        <div className="flex gap-2">
          <select value={group} onChange={(e) => { setGroup(e.target.value); setOpenId(null); }}
                  className="text-sm px-3 py-2 min-w-52"
                  style={{ border: `1px solid ${C.accent}55`, background: C.soft, color: C.ink }}>
            {Object.entries(GROUPS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select value={agent} onChange={(e) => { setAgent(e.target.value); setOpenId(null); }}
                  className="text-sm px-2 py-2"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
            <option value="all">All agents</option>
            {agents.map((t) => <option key={t} value={t}>{AGENT[t]}</option>)}
          </select>
        </div>
      </div>

      {!found.length ? (
        <div className="px-6 py-10 text-center"
             style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <p className="text-sm" style={{ color: C.body }}>
            Nothing caught under this filter across {num(totalSessions)} sessions.
          </p>
        </div>
      ) : (
        <div style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          {found.map((f) => {
            const active = openId === f.id;
            return (
              <div key={f.id} style={{ borderBottom: `1px solid ${C.rule}` }}>
                <button onClick={() => setOpenId(active ? null : f.id)}
                        className="w-full text-left px-5 py-4 block"
                        style={{ background: active ? C.soft : "transparent" }}>
                  <div className="flex items-baseline justify-between gap-6">
                    <span className="text-sm" style={{ color: C.ink }}>{f.title}</span>
                    <span className="text-sm tabular-nums whitespace-nowrap"
                          style={{ color: C.body }}>
                      {num(f.count)}{f.unit === "sessions" ? " sessions" : ""}
                      {f.unit !== "sessions" && f.sessions
                        ? ` · ${f.sessions} sessions` : ""}
                    </span>
                  </div>
                  <div className="text-xs mt-1.5" style={{ color: C.mute }}>
                    {f.agents.length ? f.agents.join(", ") : "unattributed activity"}
                  </div>
                </button>

                {active && (
                  <div className="px-5 pb-5">
                    <p className="text-sm mb-1 max-w-3xl" style={{ color: C.body }}>
                      {f.meaning}
                    </p>
                    <p className="text-xs mb-4" style={{ color: C.mute }}>
                      Condition: {f.rule}
                    </p>

                    <div className="grid gap-4 mb-4"
                         style={{ gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))" }}>
                      <div>
                        <div className="text-xs mb-2" style={{ color: C.mute }}>
                          When it happened
                        </div>
                        {trend.length ? (
                          <ResponsiveContainer width="100%" height={160}>
                            <AreaChart data={trend} margin={{ left: -24, right: 6, top: 4 }}>
                              <defs>
                                <linearGradient id="insFill" x1="0" y1="0" x2="0" y2="1">
                                  <stop offset="0%" stopColor={C.warn} stopOpacity={0.22} />
                                  <stop offset="100%" stopColor={C.warn} stopOpacity={0.02} />
                                </linearGradient>
                              </defs>
                              <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                              <XAxis dataKey="label" tick={{ fontSize: 10, fill: C.mute }}
                                     axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={26} />
                              <YAxis tick={{ fontSize: 10, fill: C.mute }}
                                     axisLine={false} tickLine={false} />
                              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0,
                                                       border: `1px solid ${C.rule}` }} />
                              <Area type="monotone" dataKey="value" stroke={C.warn}
                                    strokeWidth={1.6} fill="url(#insFill)" />
                            </AreaChart>
                          </ResponsiveContainer>
                        ) : (
                          <p className="text-xs" style={{ color: C.mute }}>No timestamps recorded.</p>
                        )}
                      </div>

                      <div>
                        <div className="text-xs mb-2" style={{ color: C.mute }}>
                          Which agent
                        </div>
                        <ResponsiveContainer width="100%" height={Math.max(120, byAgent.length * 28)}>
                          <BarChart data={byAgent} layout="vertical" margin={{ left: 80, right: 14 }}>
                            <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
                            <XAxis type="number" tick={{ fontSize: 10, fill: C.mute }}
                                   axisLine={false} tickLine={false} />
                            <YAxis type="category" dataKey="label" width={76}
                                   tick={{ fontSize: 10, fill: C.body }}
                                   axisLine={false} tickLine={false} />
                            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0,
                                                     border: `1px solid ${C.rule}` }} />
                            <Bar dataKey="value">
                              {byAgent.map((_, i) => (
                                <Cell key={i} fill={C.warn} fillOpacity={0.85 - i * 0.08} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>

                      <div>
                        <div className="text-xs mb-2" style={{ color: C.mute }}>
                          What was involved
                        </div>
                        <ResponsiveContainer width="100%" height={Math.max(120, byThing.length * 24)}>
                          <BarChart data={byThing} layout="vertical" margin={{ left: 88, right: 14 }}>
                            <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
                            <XAxis type="number" tick={{ fontSize: 10, fill: C.mute }}
                                   axisLine={false} tickLine={false} />
                            <YAxis type="category" dataKey="label" width={84}
                                   tick={{ fontSize: 10, fill: C.body }}
                                   axisLine={false} tickLine={false} />
                            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0,
                                                     border: `1px solid ${C.rule}` }} />
                            <Bar dataKey="value">
                              {byThing.map((_, i) => (
                                <Cell key={i} fill={C.accent} fillOpacity={1 - i * 0.07} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    </div>

                    <div className="text-xs mb-2" style={{ color: C.mute }}>
                      The activity itself
                    </div>
                    <div className="overflow-x-auto"
                         style={{ border: `1px solid ${C.rule}` }}>
                      <table className="w-full text-xs">
                        <thead>
                          <tr style={{ color: C.mute }}>
                            {["When", "Agent", "Session", "Turn", "What", "Detail"].map((h) => (
                              <th key={h} className="text-left font-normal px-3 py-2">{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {f.hits.slice(0, 30).map((r, i) => (
                            <tr key={i} style={{ borderTop: `1px solid ${C.rule}` }}>
                              <td className="px-3 py-2 tabular-nums" style={{ color: C.body }}>
                                {String(r.occurred_at || "").slice(5, 16).replace("T", " ")}
                              </td>
                              <td className="px-3 py-2">{AGENT[r.tool] || "unattributed"}</td>
                              <td className="px-3 py-2 tabular-nums" style={{ color: C.mute }}>
                                {String(r.session_id || "\u2014").slice(0, 8)}
                              </td>
                              <td className="px-3 py-2 tabular-nums" style={{ color: C.mute }}>
                                {String(r.turn_id || "\u2014").slice(0, 8)}
                              </td>
                              <td className="px-3 py-2">{r.tool_name || r.observable_type}</td>
                              <td className="px-3 py-2 truncate max-w-sm" style={{ color: C.body }}>
                                {r.command || r.file_path || r.mcp_server ||
                                 String(r.prompt_text || "").slice(0, 60) || "\u2014"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {f.hits.length > 30 && (
                      <p className="text-xs mt-2" style={{ color: C.mute }}>
                        Thirty of {num(f.hits.length)} shown.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {clear.length > 0 && (
        <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <header className="px-5 py-3" style={{ borderBottom: `1px solid ${C.rule}` }}>
            <h3 className="text-sm">Checks that caught nothing</h3>
          </header>
          <div className="px-5 py-4 flex flex-wrap gap-2">
            {clear.map((c) => (
              <span key={c.id} className="text-xs px-2 py-1"
                    style={{ color: C.body, border: `1px solid ${C.rule}` }}>
                {c.title}
              </span>
            ))}
          </div>
          <p className="px-5 py-3 text-xs leading-relaxed"
             style={{ color: C.mute, borderTop: `1px solid ${C.rule}` }}>
            A check that caught nothing may mean the behaviour did not occur, or
            that the agents in use do not report the signal it depends on. The
            Coverage view separates the two.
          </p>
        </section>
      )}
    </div>
  );
}
