import { useMemo, useState } from "react";
import {
  LineChart, Line, BarChart, Bar, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from "recharts";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";

/* ==================================================================
 * Insights
 *
 * One analysis at a time, chosen from a dropdown, because no single
 * person reads all of this. Each analysis states the question it
 * answers, how the figure should be read, and shows the movement over
 * time alongside the breakdown.
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

const label = (text, table, fallback) => {
  for (const [re, l] of table) if (re.test(text || "")) return l;
  return fallback;
};

/* daily series for any predicate or accumulator */
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

const tally = (rows, keyFn) => {
  const g = {};
  rows.forEach((r) => {
    const k = keyFn(r);
    if (k == null) return;
    g[k] = (g[k] || 0) + 1;
  });
  return Object.entries(g).map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value).slice(0, 12);
};

const sessionsOf = (rows) => {
  const g = new Map();
  rows.forEach((r) => {
    if (!r.session_id) return;
    let s = g.get(r.session_id);
    if (!s) { s = { id: r.session_id, tool: r.tool, e: [] }; g.set(r.session_id, s); }
    if (AGENT[r.tool]) s.tool = r.tool;
    s.e.push(r);
  });
  return [...g.values()];
};

/* ==================================================================
 * The analyses. Each is one of the measures asked for.
 * ================================================================== */

const ANALYSES = [
  {
    id: "sessions", group: "Scale", label: "Sessions and users",
    question: "How much is the estate being used, and by how many people?",
    read: "trend",
    stat: (r) => {
      const s = sessionsOf(r);
      const people = uniq(r, "operator_username") || uniq(r, "operator_email");
      return { value: num(s.length), unit: "sessions",
               sub: `${people || "—"} people, ${(s.length / (people || 1)).toFixed(1)} sessions each` };
    },
    trend: (r) => {
      const g = {};
      r.forEach((x) => {
        if (!x.occurred_at || !x.session_id) return;
        const d = x.occurred_at.slice(0, 10);
        g[d] ||= { date: d, s: new Set() };
        g[d].s.add(x.session_id);
      });
      return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
        .map((d) => ({ date: d.date.slice(5), value: d.s.size }));
    },
    breakdown: (r) => {
      const g = {};
      sessionsOf(r).forEach((s) => {
        const k = AGENT[s.tool] || "Unattributed";
        g[k] = (g[k] || 0) + 1;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value);
    },
    breakdownLabel: "Sessions by agent",
  },
  {
    id: "triggers", group: "Work", label: "What triggers a session",
    question: "What kind of work are people bringing to the agents?",
    read: "trend",
    stat: (r) => ({ value: num(cnt(r, (x) => has(x.prompt_text))), unit: "instructions",
                    sub: "classified by what they ask for" }),
    breakdown: (r) => tally(r.filter((x) => has(x.prompt_text)),
                            (x) => label(x.prompt_text, TRIGGER, "Unclassified")),
    breakdownLabel: "Nature of the request",
    trend: (r) => daily(r, (x) => (has(x.prompt_text) ? 1 : 0)),
    note: "Read from the instruction text by keyword. An instruction covering several intents is counted once, under the first match.",
  },
  {
    id: "solutions", group: "Work", label: "What is being built",
    question: "What kind of artefacts are the agents producing?",
    read: "trend",
    stat: (r) => ({ value: num(uniq(r, "file_path")), unit: "distinct files",
                    sub: `${num(cnt(r, (x) => has(x.file_path)))} file operations` }),
    breakdown: (r) => {
      const seen = new Set(); const g = {};
      r.forEach((x) => {
        if (!has(x.file_path) || seen.has(x.file_path)) return;
        seen.add(x.file_path);
        const k = label(x.file_path, SOLUTION, "Other");
        g[k] = (g[k] || 0) + 1;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value);
    },
    breakdownLabel: "Type of solution",
    trend: (r) => daily(r, (x) => (has(x.file_path) ? 1 : 0)),
    note: "Inferred from file paths and extensions. Agents do not declare what they are building, so this reads what they left behind.",
  },
  {
    id: "actions", group: "Scale", label: "Actions and steps",
    question: "How much work does a session take?",
    read: "aberration",
    stat: (r) => {
      const s = sessionsOf(r);
      const total = cnt(r, (x) => has(x.tool_name));
      return { value: (total / (s.length || 1)).toFixed(1), unit: "actions per session",
               sub: `${num(total)} actions across ${s.length} sessions` };
    },
    breakdown: (r) => {
      const buckets = [[1, "1–5"], [6, "6–20"], [21, "21–50"], [51, "51–100"], [101, "over 100"]];
      const g = {};
      sessionsOf(r).forEach((s) => {
        const n = cnt(s.e, (x) => has(x.tool_name));
        let k = "none";
        buckets.forEach(([min, l]) => { if (n >= min) k = l; });
        g[k] = (g[k] || 0) + 1;
      });
      return ["none", "1–5", "6–20", "21–50", "51–100", "over 100"]
        .filter((k) => g[k]).map((k) => ({ label: k, value: g[k] }));
    },
    breakdownLabel: "Sessions by number of actions",
    trend: (r) => daily(r, (x) => (has(x.tool_name) ? 1 : 0)),
  },
  {
    id: "tools", group: "Work", label: "Tool calls",
    question: "Which capabilities do the agents reach for?",
    read: "trend",
    stat: (r) => ({ value: num(uniq(r, "tool_name")), unit: "distinct tools",
                    sub: `${num(cnt(r, (x) => has(x.tool_name)))} invocations` }),
    breakdown: (r) => tally(r, (x) => x.tool_name || null),
    breakdownLabel: "Most used tools",
    trend: (r) => daily(r, (x) => (has(x.tool_name) ? 1 : 0)),
  },
  {
    id: "mcp", group: "Work", label: "MCP calls and connectors",
    question: "How much work reaches beyond the machine, and through what?",
    read: "trend",
    stat: (r) => ({ value: num(cnt(r, (x) => has(x.mcp_server))), unit: "MCP calls",
                    sub: `${uniq(r, "mcp_server")} connectors reached` }),
    breakdown: (r) => tally(r, (x) => x.mcp_server || null),
    breakdownLabel: "Connectors by volume",
    trend: (r) => daily(r, (x) => (has(x.mcp_server) ? 1 : 0)),
  },
  {
    id: "tokens", group: "Resources", label: "Token consumption",
    question: "What is being consumed, and is it rising?",
    read: "trend",
    stat: (r) => {
      const s = sessionsOf(r);
      const t = sum(r, "total_tokens");
      return { value: num(t), unit: "tokens",
               sub: `${num(t / (s.length || 1))} per session on average` };
    },
    breakdown: (r) => {
      const g = {};
      r.forEach((x) => {
        const t = Number(x.total_tokens) || 0;
        if (!t) return;
        const k = AGENT[x.tool] || "Unattributed";
        g[k] = (g[k] || 0) + t;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value);
    },
    breakdownLabel: "Tokens by agent",
    trend: (r) => daily(r, (x) => Number(x.total_tokens) || 0),
    note: "Only counts actions where the agent reports usage. Coverage differs by agent, so a low figure may mean less reporting rather than less work.",
  },
  {
    id: "cost", group: "Resources", label: "Spend",
    question: "What is the estate costing?",
    read: "trend",
    stat: (r) => {
      const c = sum(r, "cost_usd");
      const est = cnt(r, (x) => x.cost_is_estimated);
      return { value: "$" + c.toFixed(2), unit: "",
               sub: est ? `${num(est)} records derived from token counts` : "as billed" };
    },
    breakdown: (r) => {
      const g = {};
      r.forEach((x) => {
        const c = Number(x.cost_usd) || 0;
        if (!c) return;
        const k = AGENT[x.tool] || "Unattributed";
        g[k] = (g[k] || 0) + c;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value: +value.toFixed(2) }))
        .sort((a, b) => b.value - a.value);
    },
    breakdownLabel: "Spend by agent",
    trend: (r) => daily(r, (x) => Number(x.cost_usd) || 0),
  },
  {
    id: "hours", group: "Resources", label: "Hours run per user",
    question: "How much time is the estate actually in use?",
    read: "trend",
    stat: (r) => {
      const s = sessionsOf(r);
      const mins = s.reduce((a, x) => {
        const t = x.e.map((y) => +new Date(y.occurred_at)).filter((n) => !isNaN(n));
        return a + (t.length ? (Math.max(...t) - Math.min(...t)) / 6e4 : 0);
      }, 0);
      const people = uniq(r, "operator_username") || uniq(r, "operator_email") || 1;
      return { value: (mins / 60).toFixed(1), unit: "hours",
               sub: `${(mins / 60 / people).toFixed(1)} hours per person` };
    },
    breakdown: (r) => {
      const g = {};
      sessionsOf(r).forEach((s) => {
        const t = s.e.map((y) => +new Date(y.occurred_at)).filter((n) => !isNaN(n));
        if (!t.length) return;
        const k = AGENT[s.tool] || "Unattributed";
        g[k] = (g[k] || 0) + (Math.max(...t) - Math.min(...t)) / 36e5;
      });
      return Object.entries(g).map(([label, value]) => ({ label, value: +value.toFixed(1) }))
        .sort((a, b) => b.value - a.value);
    },
    breakdownLabel: "Hours by agent",
    trend: (r) => {
      const g = {};
      r.forEach((x) => {
        if (!x.occurred_at || !x.session_id) return;
        const d = x.occurred_at.slice(0, 10);
        g[d] ||= { date: d, s: new Map() };
        const t = +new Date(x.occurred_at);
        const cur = g[d].s.get(x.session_id) || [t, t];
        g[d].s.set(x.session_id, [Math.min(cur[0], t), Math.max(cur[1], t)]);
      });
      return Object.values(g).sort((a, b) => a.date.localeCompare(b.date))
        .map((d) => ({ date: d.date.slice(5),
          value: +([...d.s.values()].reduce((a, [s, e]) => a + (e - s) / 36e5, 0)).toFixed(1) }));
    },
  },
  {
    id: "permissions", group: "Oversight", label: "Permissions",
    question: "How often is a person asked, and what are they asked about?",
    read: "trend",
    stat: (r) => {
      const p = cnt(r, (x) => x.observable_type === "permission_request");
      const a = cnt(r, (x) => has(x.tool_name));
      return { value: num(p), unit: "approvals requested",
               sub: a ? `one for every ${(a / (p || 1)).toFixed(0)} actions` : "" };
    },
    breakdown: (r) => tally(r.filter((x) => x.observable_type === "permission_request"),
                            (x) => x.tool_name || "unnamed"),
    breakdownLabel: "What approval was asked for",
    trend: (r) => daily(r, (x) => (x.observable_type === "permission_request" ? 1 : 0)),
    note: "Refusals are not emitted by any agent in the estate. A request with no matching action is the only available signal that something was declined.",
  },
  {
    id: "human", group: "Oversight", label: "Human intervention",
    question: "How much are people stepping in?",
    read: "trend",
    stat: (r) => {
      const follow = Math.max(0, cnt(r, (x) => has(x.prompt_text)) - sessionsOf(r).length);
      const undo = cnt(r, (x) => x.reverted_to_earlier);
      return { value: num(follow + undo), unit: "interventions",
               sub: `${num(follow)} follow-up instructions, ${num(undo)} reversions` };
    },
    breakdown: (r) => ([
      { label: "Follow-up instruction",
        value: Math.max(0, cnt(r, (x) => has(x.prompt_text)) - sessionsOf(r).length) },
      { label: "Work put back", value: cnt(r, (x) => x.reverted_to_earlier) },
      { label: "Approval requested", value: cnt(r, (x) => x.observable_type === "permission_request") },
    ].filter((d) => d.value)),
    breakdownLabel: "Nature of the intervention",
    trend: (r) => daily(r, (x) => (x.reverted_to_earlier ? 1 : 0)),
    note: "No agent emits an intervention event. This counts instructions after the first in a session, and files a person returned to earlier content.",
  },
  {
    id: "rework", group: "Oversight", label: "Rework and revision",
    question: "How much agent work has to be done again?",
    read: "aberration",
    stat: (r) => {
      const rev = cnt(r, (x) => x.reverted_to_earlier);
      const edits = cnt(r, (x) => x.file_change_kind === "modify");
      return { value: num(rev), unit: "reversions",
               sub: edits ? `${((rev / edits) * 100).toFixed(1)}% of file edits` : "" };
    },
    breakdown: (r) => tally(r.filter((x) => x.reverted_to_earlier),
                            (x) => (x.file_path || "").split("/").pop() || null),
    breakdownLabel: "Files most often put back",
    trend: (r) => daily(r, (x) => (x.reverted_to_earlier ? 1 : 0)),
  },
  {
    id: "latency", group: "Performance", label: "Latency and lead time",
    question: "How long do actions take, and is it getting worse?",
    read: "aberration",
    stat: (r) => {
      const d = r.map((x) => Number(x.tool_duration_ms)).filter((n) => n > 0).sort((a, b) => a - b);
      if (!d.length) return { value: "—", unit: "", sub: "no durations recorded" };
      const p95 = d[Math.floor(d.length * 0.95)];
      return { value: num(d[Math.floor(d.length / 2)]), unit: "ms median",
               sub: `${num(p95)} ms at the 95th percentile` };
    },
    breakdown: (r) => {
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
    breakdownLabel: "Mean duration by tool, milliseconds",
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
  },
  {
    id: "failures", group: "Performance", label: "Failures and stalls",
    question: "Where is effort being spent without a result?",
    read: "aberration",
    stat: (r) => {
      const f = cnt(r, (x) => x.success === false);
      return { value: num(f), unit: "failed actions",
               sub: `${num(cnt(r, (x) => has(x.hung_seconds)))} executions stalled` };
    },
    breakdown: (r) => tally(r.filter((x) => x.success === false),
                            (x) => x.tool_name || x.observable_type || null),
    breakdownLabel: "Where failures occur",
    trend: (r) => daily(r, (x) => (x.success === false ? 1 : 0)),
  },
  {
    id: "exposure", group: "Risk", label: "Sensitive access and boundaries",
    question: "What did the agents touch that they perhaps should not have?",
    read: "aberration",
    stat: (r) => {
      const s = cnt(r, (x) => Number(x.sensitivity_tier) >= 3);
      return { value: num(s), unit: "sensitive accesses",
               sub: `${num(cnt(r, (x) => x.outside_workspace))} outside the workspace, ` +
                    `${num(cnt(r, (x) => x.secret_detected))} secrets seen` };
    },
    breakdown: (r) => ([
      { label: "Sensitive path", value: cnt(r, (x) => Number(x.sensitivity_tier) >= 3) },
      { label: "Outside workspace", value: cnt(r, (x) => x.outside_workspace) },
      { label: "Secret in content", value: cnt(r, (x) => x.secret_detected) },
      { label: "Blocked by policy", value: cnt(r, (x) => has(x.policy_rule_matched)) },
    ].filter((d) => d.value)),
    breakdownLabel: "Nature of the exposure",
    trend: (r) => daily(r, (x) => (Number(x.sensitivity_tier) >= 3 ? 1 : 0)),
  },
];

const READ = {
  trend: ["trend", C.accent, "whether it is moving"],
  aberration: ["aberration", C.warn, "whether it sits outside what is normal here"],
  event: ["event", C.body, "what happened"],
};

/* ================================================================== */

export default function Insights({ rows }) {
  const [id, setId] = useState("sessions");
  const [agent, setAgent] = useState("all");

  const view = useMemo(
    () => (agent === "all" ? rows : rows.filter((r) => r.tool === agent)),
    [rows, agent]);

  const a = ANALYSES.find((x) => x.id === id) || ANALYSES[0];
  const stat = useMemo(() => a.stat(view), [a, view]);
  const trend = useMemo(() => (a.trend ? a.trend(view) : []), [a, view]);
  const breakdown = useMemo(() => (a.breakdown ? a.breakdown(view) : []), [a, view]);
  const avg = trend.length
    ? trend.reduce((s, d) => s + d.value, 0) / trend.length : 0;

  const agents = useMemo(
    () => [...new Set(rows.map((r) => r.tool))].filter((t) => AGENT[t]), [rows]);

  const groups = [...new Set(ANALYSES.map((x) => x.group))];
  const [readLabel, readColour, readHint] = READ[a.read];

  return (
    <div className="space-y-5 max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg">Insights</h1>
          <p className="text-sm mt-1" style={{ color: C.body }}>
            One measure at a time, with the movement behind it.
          </p>
        </div>
        <div className="flex gap-2">
          <select value={id} onChange={(e) => setId(e.target.value)}
                  className="text-sm px-3 py-2 min-w-56"
                  style={{ border: `1px solid ${C.accent}55`, background: C.soft, color: C.ink }}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {ANALYSES.filter((x) => x.group === g).map((x) => (
                  <option key={x.id} value={x.id}>{x.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <select value={agent} onChange={(e) => setAgent(e.target.value)}
                  className="text-sm px-2 py-2"
                  style={{ border: `1px solid ${C.rule}`, background: C.panel }}>
            <option value="all">All agents</option>
            {agents.map((t) => <option key={t} value={t}>{AGENT[t]}</option>)}
          </select>
        </div>
      </div>

      {/* headline */}
      <div className="px-6 py-5" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="text-sm mb-3" style={{ color: C.body }}>{a.question}</p>
            <div className="flex items-baseline gap-2">
              <span className="text-4xl leading-none tabular-nums">{stat.value}</span>
              <span className="text-sm" style={{ color: C.mute }}>{stat.unit}</span>
            </div>
            <div className="text-sm mt-2" style={{ color: C.body }}>{stat.sub}</div>
          </div>
          <div className="text-right">
            <span className="text-xs px-2 py-1"
                  style={{ color: readColour, border: `1px solid ${readColour}40` }}>
              read as {readLabel}
            </span>
            <div className="text-xs mt-2 max-w-48" style={{ color: C.mute }}>
              ask {readHint}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-5" style={{ gridTemplateColumns: "minmax(320px,1.2fr) minmax(300px,1fr)" }}>
        {/* trend */}
        <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <header className="px-5 py-3" style={{ borderBottom: `1px solid ${C.rule}` }}>
            <h3 className="text-sm">Over time</h3>
          </header>
          <div className="p-5">
            {trend.length ? (
              <ResponsiveContainer width="100%" height={240}>
                {a.read === "aberration" ? (
                  <LineChart data={trend} margin={{ left: -18, right: 8, top: 6 }}>
                    <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: C.mute }}
                           axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={28} />
                    <YAxis tick={{ fontSize: 11, fill: C.mute }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                    <ReferenceLine y={avg} stroke={C.warn} strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="value" name={a.label}
                          stroke={C.accent} strokeWidth={1.6} dot={false} />
                  </LineChart>
                ) : (
                  <AreaChart data={trend} margin={{ left: -18, right: 8, top: 6 }}>
                    <defs>
                      <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={C.accent} stopOpacity={0.22} />
                        <stop offset="100%" stopColor={C.accent} stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="2 5" stroke={C.rule} vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: C.mute }}
                           axisLine={{ stroke: C.rule }} tickLine={false} minTickGap={28} />
                    <YAxis tick={{ fontSize: 11, fill: C.mute }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                    <Area type="monotone" dataKey="value" name={a.label}
                          stroke={C.accent} strokeWidth={1.6} fill="url(#fill)" />
                  </AreaChart>
                )}
              </ResponsiveContainer>
            ) : (
              <p className="text-xs" style={{ color: C.mute }}>
                This measure describes a state rather than a movement.
              </p>
            )}
            {a.read === "aberration" && trend.length > 0 && (
              <p className="text-xs mt-3" style={{ color: C.mute }}>
                The dashed line is this estate's own average, not an external
                standard. With a window this short, treat it as provisional.
              </p>
            )}
          </div>
        </section>

        {/* breakdown */}
        <section style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
          <header className="px-5 py-3" style={{ borderBottom: `1px solid ${C.rule}` }}>
            <h3 className="text-sm">{a.breakdownLabel || "Breakdown"}</h3>
          </header>
          <div className="p-5">
            {breakdown.length ? (
              <ResponsiveContainer width="100%" height={Math.max(180, breakdown.length * 26)}>
                <BarChart data={breakdown} layout="vertical" margin={{ left: 96, right: 16 }}>
                  <CartesianGrid strokeDasharray="2 5" stroke={C.rule} horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11, fill: C.mute }}
                         axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="label" width={92}
                         tick={{ fontSize: 11, fill: C.body }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 0, border: `1px solid ${C.rule}` }} />
                  <Bar dataKey="value" name={a.breakdownLabel}>
                    {breakdown.map((_, i) => (
                      <Cell key={i} fill={C.accent} fillOpacity={1 - i * 0.06} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-xs" style={{ color: C.mute }}>
                Nothing recorded for this measure in the current selection.
              </p>
            )}
          </div>
        </section>
      </div>

      {a.note && (
        <p className="text-xs leading-relaxed max-w-3xl" style={{ color: C.mute }}>
          {a.note}
        </p>
      )}
    </div>
  );
}
