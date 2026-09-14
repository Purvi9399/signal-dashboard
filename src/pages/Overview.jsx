import { useMemo } from "react";
import { C, AGENT, has, uniq, sum, cnt, num } from "../theme";
import OverviewTrend from "./OverviewTrend";

/* ==================================================================
 * Overview
 *
 * Descriptive only. Fifteen measures, grouped by what they describe,
 * each with the figure and the shape behind it. No interpretation and
 * no judgement: what needs attention belongs on Insights.
 * ================================================================== */

const SOLUTION = [
  [/\.tf$|\.tfvars$|terraform/i, "infrastructure as code"],
  [/k8s|kubernetes|deployment\.ya?ml|kustomiz/i, "Kubernetes"],
  [/\.github\/workflows|\.gitlab-ci|jenkinsfile/i, "CI/CD"],
  [/dockerfile|docker-compose/i, "containers"],
  [/\.(jsx|tsx|vue|svelte|html|css|scss)$/i, "web application"],
  [/test_|_test\.|\.test\.|spec\.|\.tftest/i, "tests"],
  [/\.(md|rst|txt)$/i, "documentation"],
  [/package\.json|requirements|pyproject|go\.mod|cargo\.toml/i, "dependencies"],
  [/\.(ya?ml|json|toml|ini|conf|env)$/i, "configuration"],
  [/\.sql$|migration/i, "database"],
  [/\.(py|js|ts|go|rb|java|rs|sh)$/i, "application code"],
];

const TRIGGER = [
  [/audit|security|vulnerab|secret|compliance|cve/i, "security review"],
  [/refactor|restructure|modular|clean ?up/i, "refactoring"],
  [/test|coverage|assert|pytest/i, "testing"],
  [/deploy|pipeline|ci\/cd|workflow|release/i, "deployment"],
  [/document|readme|runbook|postmortem|explain/i, "documentation"],
  [/fix|bug|error|fail|debug|broken|outage/i, "fixing a defect"],
  [/migrat|upgrade|port |convert/i, "migration"],
  [/instrument|observab|telemetry|monitor|metric/i, "observability"],
  [/build|create|write|implement|add /i, "new feature"],
];

const classify = (text, table, fallback) => {
  for (const [re, l] of table) if (re.test(text || "")) return l;
  return fallback;
};

/* distinct kinds, and which kind dominates */
const spread = (items, table, fallback) => {
  const g = {};
  items.forEach((t) => {
    const k = classify(t, table, fallback);
    g[k] = (g[k] || 0) + 1;
  });
  const sorted = Object.entries(g).sort((a, b) => b[1] - a[1]);
  return {
    kinds: sorted.length,
    top: sorted[0]?.[0] || "\u2014",
    topShare: sorted.length
      ? Math.round((sorted[0][1] / items.length) * 100) : 0,
  };
};

function Tile({ label, value, unit, sub }) {
  return (
    <div className="px-5 py-4" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
      <div className="text-xs mb-2" style={{ color: C.mute }}>{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl leading-none tabular-nums">{value}</span>
        {unit && <span className="text-xs" style={{ color: C.mute }}>{unit}</span>}
      </div>
      <div className="text-xs mt-2 leading-relaxed" style={{ color: C.body }}>{sub}</div>
    </div>
  );
}

function Group({ title, children }) {
  return (
    <section>
      <h2 className="text-xs mb-2" style={{ color: C.mute }}>{title}</h2>
      <div className="grid gap-3"
           style={{ gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))" }}>
        {children}
      </div>
    </section>
  );
}

export default function Overview({ rows }) {
  const k = useMemo(() => {
    const sessions = new Map();
    rows.forEach((r) => {
      if (!r.session_id) return;
      let s = sessions.get(r.session_id);
      if (!s) { s = { tool: r.tool, e: [] }; sessions.set(r.session_id, s); }
      if (AGENT[r.tool]) s.tool = r.tool;
      s.e.push(r);
    });
    const sList = [...sessions.values()];
    const nS = sList.length || 1;

    /* time in use, measured per session then totalled */
    const minutes = sList.reduce((a, s) => {
      const t = s.e.map((x) => +new Date(x.occurred_at)).filter((n) => !isNaN(n));
      return a + (t.length ? (Math.max(...t) - Math.min(...t)) / 6e4 : 0);
    }, 0);

    const people = uniq(rows, "operator_username") || uniq(rows, "operator_email");
    const prompts = rows.filter((r) => has(r.prompt_text));
    const filePaths = [...new Set(rows.map((r) => r.file_path).filter(Boolean))];
    const durations = rows.map((r) => Number(r.tool_duration_ms))
      .filter((n) => n > 0).sort((a, b) => a - b);

    const permTools = rows.filter((r) => r.observable_type === "permission_request");
    const permKinds = {};
    permTools.forEach((r) => {
      const t = r.tool_name || "unnamed";
      permKinds[t] = (permKinds[t] || 0) + 1;
    });
    const permTop = Object.entries(permKinds).sort((a, b) => b[1] - a[1])[0];

    const connectors = {};
    rows.forEach((r) => {
      if (!has(r.mcp_server)) return;
      connectors[r.mcp_server] = (connectors[r.mcp_server] || 0) + 1;
    });
    const connTop = Object.entries(connectors).sort((a, b) => b[1] - a[1])[0];

    const toolKinds = {};
    rows.forEach((r) => {
      if (!has(r.tool_name)) return;
      toolKinds[r.tool_name] = (toolKinds[r.tool_name] || 0) + 1;
    });
    const toolTop = Object.entries(toolKinds).sort((a, b) => b[1] - a[1])[0];

    const follows = Math.max(0, prompts.length - sList.length);
    const reverts = cnt(rows, (r) => r.reverted_to_earlier);
    const edits = cnt(rows, (r) => r.file_change_kind === "modify");

    return {
      sessions: sList.length,
      people,
      agents: uniq(rows.filter((r) => AGENT[r.tool]), "tool"),
      actions: cnt(rows, (r) => has(r.tool_name)),
      actionsPer: cnt(rows, (r) => has(r.tool_name)) / nS,
      turns: uniq(rows, "turn_id"),
      useCases: spread(prompts.map((r) => r.prompt_text), TRIGGER, "unclassified"),
      triggerCount: prompts.length,
      toolKinds: Object.keys(toolKinds).length,
      toolTop: toolTop ? `${toolTop[0]}, ${num(toolTop[1])} calls` : "\u2014",
      toolCallsPer: cnt(rows, (r) => has(r.tool_name)) / nS,
      solutions: spread(filePaths, SOLUTION, "other"),
      files: filePaths.length,
      mcp: cnt(rows, (r) => has(r.mcp_server)),
      connectors: Object.keys(connectors).length,
      connTop: connTop ? connTop[0] : "\u2014",
      tokens: sum(rows, "total_tokens"),
      tokensPer: sum(rows, "total_tokens") / nS,
      cost: sum(rows, "cost_usd"),
      estimated: cnt(rows, (r) => r.cost_is_estimated),
      follows,
      reverts,
      interventions: follows + reverts,
      reworkRate: edits ? (reverts / edits) * 100 : 0,
      permissions: permTools.length,
      permKinds: Object.keys(permKinds).length,
      permTop: permTop ? permTop[0] : "\u2014",
      hours: minutes / 60,
      hoursPerPerson: people ? minutes / 60 / people : 0,
      medianMs: durations.length ? durations[Math.floor(durations.length / 2)] : 0,
      p95Ms: durations.length ? durations[Math.floor(durations.length * 0.95)] : 0,
      timed: durations.length,
      first: rows[0]?.occurred_at?.slice(0, 10),
      last: rows[rows.length - 1]?.occurred_at?.slice(0, 10),
    };
  }, [rows]);

  return (
    <div className="space-y-6 max-w-7xl">
      <div>
        <h1 className="text-lg">Overview</h1>
        <p className="text-sm mt-1" style={{ color: C.body }}>
          {k.agents} coding agents, {num(k.sessions)} sessions, {k.first} to {k.last}.
        </p>
      </div>

      <Group title="Scale">
        <Tile label="Sessions" value={num(k.sessions)}
              sub={`${num(k.turns)} turns of work across the estate`} />
        <Tile label="Users" value={k.people || "\u2014"}
              sub={k.people
                ? `${(k.sessions / k.people).toFixed(1)} sessions each`
                : "operator identity is not emitted by every agent"} />
        <Tile label="Actions and steps" value={num(k.actions)}
              sub={`${k.actionsPer.toFixed(1)} per session on average`} />
        <Tile label="Hours in use" value={k.hours.toFixed(1)} unit="h"
              sub={k.people
                ? `${k.hoursPerPerson.toFixed(1)} hours per person`
                : "measured from first to last event in each session"} />
      </Group>

      <Group title="Nature of the work">
        <Tile label="Types of use case" value={k.useCases.kinds}
              sub={`${k.useCases.top} is the largest, ${k.useCases.topShare}% of ${num(k.triggerCount)} instructions`} />
        <Tile label="Use case triggers" value={num(k.triggerCount)}
              sub="instructions given, classified by what they ask for" />
        <Tile label="Types of solution built" value={k.solutions.kinds}
              sub={`mostly ${k.solutions.top}, across ${num(k.files)} distinct files`} />
        <Tile label="Tool calls per session" value={k.toolCallsPer.toFixed(1)}
              sub={`${k.toolKinds} distinct tools, most used ${k.toolTop}`} />
      </Group>

      <Group title="Reach and consumption">
        <Tile label="MCP calls" value={num(k.mcp)}
              sub={k.connectors
                ? `${k.connectors} connectors reached, most used ${k.connTop}`
                : "no external connectors reached"} />
        <Tile label="Connectors" value={k.connectors}
              sub={k.connectors ? `nature of the most active: ${k.connTop}` : "none configured or none used"} />
        <Tile label="Token consumption" value={num(k.tokens)}
              sub={`${num(k.tokensPer)} per session, where usage is reported`} />
        <Tile label="Spend" value={"$" + k.cost.toFixed(2)}
              sub={k.estimated
                ? `${num(k.estimated)} records derived from token counts`
                : "as billed by the agents"} />
      </Group>

      <Group title="Oversight and rework">
        <Tile label="Human intervention" value={num(k.interventions)}
              sub={`${num(k.follows)} follow-up instructions, ${num(k.reverts)} pieces of work put back`} />
        <Tile label="Permissions provided" value={num(k.permissions)}
              sub={k.permissions
                ? `${k.permKinds} kinds, most often for ${k.permTop}`
                : "no approval events recorded"} />
        <Tile label="Revisions and rework" value={num(k.reverts)}
              sub={k.reworkRate
                ? `${k.reworkRate.toFixed(1)}% of file edits were later undone`
                : "no reversions recorded"} />
        <Tile label="Latency and lead time"
              value={k.medianMs ? num(k.medianMs) : "\u2014"} unit={k.medianMs ? "ms" : ""}
              sub={k.timed
                ? `median action, ${num(k.p95Ms)} ms at the 95th percentile`
                : "no durations reported by these agents"} />
      </Group>

      <OverviewTrend rows={rows} />

      <p className="text-xs leading-relaxed max-w-3xl" style={{ color: C.mute }}>
        Use cases, triggers and solution types are read from instruction text and
        file paths, since no agent declares what it is working on. Human
        intervention counts instructions after the first in a session and files a
        person returned to earlier content, because no agent emits an
        intervention event. Where a figure reads zero, it may mean the agents in
        use do not report that signal rather than that nothing happened.
      </p>
    </div>
  );
}
