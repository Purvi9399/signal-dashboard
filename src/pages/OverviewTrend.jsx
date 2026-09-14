import { useMemo, useState } from "react";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";

/* ==================================================================
 * The movement behind the figures above.
 *
 * One measure at a time, chosen from the same list as the tiles, so
 * the reader can go from a number to its shape without leaving the
 * page. Trend on the left, composition on the right.
 * ================================================================== */

const SOLUTION = [
  [/\.tf$|\.tfvars$|terraform/i, "Infrastructure as code"],
  [/k8s|kubernetes|deployment\.ya?ml|kustomiz/i, "Kubernetes"],
  [/\.github\/workflows|\.gitlab-ci|jenkinsfile/i, "CI/CD"],
  [/dockerfile|docker-compose/i, "Containers"],
  [/\.(jsx|tsx|vue|svelte|html|css|scss)$/i, "Web application"],
  [/test_|_test\.|\.test\.|spec\.|\.tftest/i, "Tests"],
  [/\.(md|rst|txt)$/i, "Documentation"],
  [/package\.json|requirements|pyproject|go\.mod|cargo\.toml/i, "Dependencies"],
  [/\.(ya?ml|json|toml|ini|conf|env)$/i, "Configuration"],
  [/\.sql$|migration/i, "Database"],
  [/\.(py|js|ts|go|rb|java|rs|sh)$/i, "Application code"],
];

const TRIGGER = [
  [/audit|security|vulnerab|secret|compliance|cve/i, "Security review"],
  [/refactor|restructure|modular|clean ?up/i, "Refactoring"],
  [/test|coverage|assert|pytest/i, "Testing"],
  [/deploy|pipeline|ci\/cd|workflow|release/i, "Deployment"],
  [/document|readme|runbook|postmortem|explain/i, "Documentation"],
  [/fix|bug|error|fail|debug|broken|outage/i, "Fixing a defect"],
  [/migrat|upgrade|port |convert/i, "Migration"],
  [/instrument|observab|telemetry|monitor|metric/i, "Observability"],
  [/build|create|write|implement|add /i, "New feature"],
];

const classify = (t, table, fb) => {
  for (const [re, l] of table) if (re.test(t || "")) return l;
  return fb;
};

const daily = (rows, fn) => {
  const g = {};
  rows.forEach((r) => {
    if (!r.occurred_at) return;
    const d = r.occurred_at.slice(0, 10);
    g[d] ||= { date: d, value: 0 };
    g[d].value += fn(r);
  });
  return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ ...d, date: d.date.slice(5) }));
};

const dailyDistinct = (rows, key) => {
  const g = {};
  rows.forEach((r) => {
    if (!r.occurred_at || !r[key]) return;
    const d = r.occurred_at.slice(0, 10);
    g[d] ||= { date: d, s: new Set() };
    g[d].s.add(r[key]);
  });
  return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ date: d.date.slice(5), value: d.s.size }));
};

const tally = (rows, keyFn, limit = 10) => {
  const g = {};
  rows.forEach((r) => {
    const k = keyFn(r);
    if (k == null) return;
    g[k] = (g[k] || 0) + 1;
  });
  return Object.entries(g).map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value).slice(0, limit);
};

const byAgent = (rows, valFn) => {
  const g = {};
  rows.forEach((r) => {
    const v = valFn(r);
    if (!v) return;
    const k = AGENT[r.tool] || "Unattributed";
    g[k] = (g[k] || 0) + v;
  });
  return Object.entries(g).map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
};

/* The same subjects as the tiles above, in the same order. */
const MEASURES = [
  { id: "sessions", group: "Scale", label: "Sessions",
    trend: (r) => dailyDistinct(r, "session_id"),
    trendLabel: "Sessions started each day",
    split: (r) => {
      const g = {};
      const seen = new Set();
      r.forEach((x) => {
        if (!x.session_id || seen.has(x.session_id)) return;
        seen.add(x.session_id);
        const k = AGENT[x.tool] || "Unattributed";
        g[k] = (g[k] || 0) + 1;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value);
    },
    splitLabel: "Sessions by agent" },

  { id: "users", group: "Scale", label: "Users",
    trend: (r) => dailyDistinct(r, "operator_username"),
    trendLabel: "People active each day",
    split: (r) => tally(r, (x) => x.operator_username || x.operator_email || null),
    splitLabel: "Activity by person" },

  { id: "actions", group: "Scale", label: "Actions and steps",
    trend: (r) => daily(r, (x) => (has(x.tool_name) ? 1 : 0)),
    trendLabel: "Actions taken each day",
    split: (r) => byAgent(r, (x) => (has(x.tool_name) ? 1 : 0)),
    splitLabel: "Actions by agent" },

  { id: "hours", group: "Scale", label: "Hours in use",
    trend: (r) => {
      const g = {};
      r.forEach((x) => {
        if (!x.occurred_at || !x.session_id) return;
        const d = x.occurred_at.slice(0, 10);
        const t = +new Date(x.occurred_at);
        g[d] ||= { date: d, s: new Map() };
        const cur = g[d].s.get(x.session_id) || [t, t];
        g[d].s.set(x.session_id, [Math.min(cur[0], t), Math.max(cur[1], t)]);
      });
      return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
        .map((d) => ({ date: d.date.slice(5),
          value: +([...d.s.values()].reduce((a, [s, e]) => a + (e - s) / 36e5, 0)).toFixed(1) }));
    },
    trendLabel: "Hours of agent time each day",
    split: null },

  { id: "usecases", group: "Nature of the work", label: "Types of use case",
    trend: (r) => daily(r, (x) => (has(x.prompt_text) ? 1 : 0)),
    trendLabel: "Instructions given each day",
    split: (r) => tally(r.filter((x) => has(x.prompt_text)),
                        (x) => classify(x.prompt_text, TRIGGER, "Unclassified")),
    splitLabel: "By what was asked for",
    note: "Read from the instruction text by keyword. An instruction covering several intents is counted once, under the first that matches." },

  { id: "solutions", group: "Nature of the work", label: "Types of solution built",
    trend: (r) => daily(r, (x) => (has(x.file_path) ? 1 : 0)),
    trendLabel: "File operations each day",
    split: (r) => {
      const seen = new Set(); const g = {};
      r.forEach((x) => {
        if (!has(x.file_path) || seen.has(x.file_path)) return;
        seen.add(x.file_path);
        const k = classify(x.file_path, SOLUTION, "Other");
        g[k] = (g[k] || 0) + 1;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value);
    },
    splitLabel: "By kind of artefact",
    note: "Inferred from file paths and extensions. Agents do not declare what they are building, so this reads what they left behind." },

  { id: "tools", group: "Nature of the work", label: "Tool calls",
    trend: (r) => daily(r, (x) => (has(x.tool_name) ? 1 : 0)),
    trendLabel: "Tool calls each day",
    split: (r) => tally(r, (x) => x.tool_name || null),
    splitLabel: "Most used tools" },

  { id: "mcp", group: "Reach and consumption", label: "MCP calls and connectors",
    trend: (r) => daily(r, (x) => (has(x.mcp_server) ? 1 : 0)),
    trendLabel: "Calls beyond the machine each day",
    split: (r) => tally(r, (x) => x.mcp_server || null),
    splitLabel: "By connector" },

  { id: "tokens", group: "Reach and consumption", label: "Token consumption",
    trend: (r) => daily(r, (x) => Number(x.total_tokens) || 0),
    trendLabel: "Tokens consumed each day",
    split: (r) => byAgent(r, (x) => Number(x.total_tokens) || 0),
    splitLabel: "Tokens by agent",
    note: "Counts only actions where the agent reports usage. A low figure may mean less reporting rather than less work." },

  { id: "cost", group: "Reach and consumption", label: "Spend",
    trend: (r) => daily(r, (x) => Number(x.cost_usd) || 0),
    trendLabel: "Spend each day",
    split: (r) => byAgent(r, (x) => Number(x.cost_usd) || 0),
    splitLabel: "Spend by agent" },

  { id: "human", group: "Oversight and rework", label: "Human intervention",
    trend: (r) => daily(r, (x) => (x.reverted_to_earlier ? 1 : 0)),
    trendLabel: "Work put back each day",
    split: (r) => {
      const sessions = uniq(r, "session_id");
      return [
        { label: "Follow-up instruction",
          value: Math.max(0, cnt(r, (x) => has(x.prompt_text)) - sessions) },
        { label: "Work put back", value: cnt(r, (x) => x.reverted_to_earlier) },
        { label: "Approval requested",
          value: cnt(r, (x) => x.observable_type === "permission_request") },
      ].filter((d) => d.value);
    },
    splitLabel: "How people stepped in",
    note: "No agent emits an intervention event. This counts instructions after the first in a session, and files a person returned to earlier content." },

  { id: "permissions", group: "Oversight and rework", label: "Permissions",
    trend: (r) => daily(r, (x) => (x.observable_type === "permission_request" ? 1 : 0)),
    trendLabel: "Approvals requested each day",
    split: (r) => tally(r.filter((x) => x.observable_type === "permission_request"),
                        (x) => x.tool_name || "unnamed"),
    splitLabel: "What approval was asked for",
    note: "Refusals are not emitted by any agent here. A request with no matching action is the only available signal that something was declined." },

  { id: "rework", group: "Oversight and rework", label: "Revisions and rework",
    trend: (r) => daily(r, (x) => (x.reverted_to_earlier ? 1 : 0)),
    trendLabel: "Reversions each day",
    split: (r) => tally(r.filter((x) => x.reverted_to_earlier),
                        (x) => (x.file_path || "").split("/").pop() || null),
    splitLabel: "Files most often put back" },

  { id: "latency", group: "Oversight and rework", label: "Latency and lead time",
    trend: (r) => {
      const g = {};
      r.forEach((x) => {
        const d = Number(x.tool_duration_ms);
        if (!(d > 0) || !x.occurred_at) return;
        const k = x.occurred_at.slice(0, 10);
        g[k] ||= { date: k, n: 0, t: 0 };
        g[k].n++; g[k].t += d;
      });
      return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
        .map((d) => ({ date: d.date.slice(5), value: Math.round(d.t / d.n) }));
    },
    trendLabel: "Mean action duration each day, milliseconds",
    split: (r) => {
      const g = {};
      r.forEach((x) => {
        const d = Number(x.tool_duration_ms);
        if (!(d > 0) || !x.tool_name) return;
        g[x.tool_name] ||= { n: 0, t: 0 };
        g[x.tool_name].n++; g[x.tool_name].t += d;
      });
      return Object.entries(g).map(([label, v]) => ({ label, value: Math.round(v.t / v.n) }))
        .sort((a, b) => b.value - a.value).slice(0, 10);
    },
    splitLabel: "Mean duration by tool" },
];

export default function OverviewTrend({ rows }) {
  const [id, setId] = useState("sessions");
  const [agent, setAgent] = useState("all");

  const view = useMemo(
    () => (agent === "all" ? rows : rows.filter((r) => r.tool === agent)), [rows, agent]);

  const m = MEASURES.find((x) => x.id === id) || MEASURES[0];
  const trend = useMemo(() => m.trend(view), [m, view]);
  const split = useMemo(() => (m.split ? m.split(view) : []), [m, view]);

  const agents = useMemo(
    () => [...new Set(rows.map((r) => r.tool))].filter((t) => AGENT[t]), [rows]);
  const groups = [...new Set(MEASURES.map((x) => x.group))];

  return (
    <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      <header className="px-5 py-3 flex flex-wrap items-center justify-between gap-3"
              style={{ borderBottom: `1px solid ${C.rule}` }}>
        <h3 className="text-sm">Movement behind the figures</h3>
        <div className="flex gap-2">
          <select value={id} onChange={(e) => setId(e.target.value)}
                  className="text-sm px-2 py-1.5 min-w-48"
                  style={{ border: `1px solid ${C.accent}55`, background: C.soft, color: C.ink }}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {MEASURES.filter((x) => x.group === g).map((x) => (
                  <option key={x.id} value={x.id}>{x.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <select value={agent} onChange={(e) => setAgent(e.target.value)}
                  className="text-sm px-2 py-1.5"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
            <option value="all">All agents</option>
            {agents.map((t) => <option key={t} value={t}>{AGENT[t]}</option>)}
          </select>
        </div>
      </header>

      <div className="p-5 grid gap-6"
           style={{ gridTemplateColumns: "minmax(300px,1.25fr) minmax(260px,1fr)" }}>
        <div>
          <div className="text-xs mb-3" style={{ color: C.body }}>{m.trendLabel}</div>
          {trend.length ? (
            <ResponsiveContainer width="100%" height={230}>
              <AreaChart data={trend} margin={{ left: -18, right: 8, top: 6 }}>
                <defs>
                  <linearGradient id="ovfill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={C.accent} stopOpacity={0.22} />
                    <stop offset="100%" stopColor={C.accent} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: C.mute }}
                       axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={28} />
                <YAxis tick={{ fontSize: 11, fill: C.mute }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                <Area type="monotone" dataKey="value" name={m.label}
                      stroke={C.accent} strokeWidth={1.6} fill="url(#ovfill)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-xs" style={{ color: C.mute }}>
              Nothing recorded for this measure in the current selection.
            </p>
          )}
        </div>

        <div>
          <div className="text-xs mb-3" style={{ color: C.body }}>
            {m.split ? m.splitLabel : "No breakdown for this measure"}
          </div>
          {split.length ? (
            <ResponsiveContainer width="100%" height={Math.max(180, split.length * 26)}>
              <BarChart data={split} layout="vertical" margin={{ left: 88, right: 16 }}>
                <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: C.mute }}
                       axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="label" width={84}
                       tick={{ fontSize: 11, fill: C.body }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                <Bar dataKey="value" name={m.splitLabel}>
                  {split.map((_, i) => (
                    <Cell key={i} fill={C.accent} fillOpacity={1 - i * 0.07} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-xs" style={{ color: C.mute }}>
              {m.split ? "Nothing to break down here." : "This measure has no natural composition."}
            </p>
          )}
        </div>
      </div>

      {m.note && (
        <p className="px-5 py-3 text-xs leading-relaxed"
           style={{ color: C.mute, borderTop: `1px solid ${C.rule}` }}>{m.note}</p>
      )}
    </section>
  );
}
