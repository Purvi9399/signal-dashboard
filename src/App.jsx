import { useState, useEffect } from "react";
import { C } from "./theme";
import Overview from "./pages/Overview";
import Sessions from "./pages/Sessions";
import Insights from "./pages/Insights";
import SessionObservability from "./pages/SessionObservability";

const URL = import.meta.env.VITE_SUPABASE_URL;
const KEY = import.meta.env.VITE_SUPABASE_KEY;

const COLUMNS = [
  "session_id","tool","collector","operator_username","operator_email",
  "observable_type","occurred_at","turn_id","agent_id","total_tokens",
  "input_tokens","cache_read_tokens","cost_usd","cost_is_estimated",
  "tool_name","tool_duration_ms","mcp_server","file_path","file_change_kind",
  "command","exit_code","permission_decision","sensitivity_tier",
  "secret_detected","reverted_to_earlier","outside_workspace","success",
  "hung_seconds","prompt_text","contributing","attribution",
  "policy_rule_matched","is_dependency_manifest","is_lockfile","workspace",
  "model","sandboxed","plan_item",
].join(",");

const NAV = [
  ["overview", "Overview", "Overall view of your multi-agent systems"],
  ["sessions", "Session analysis", "A detailed view of every session"],
  ["insights", "Insights", "Trends and analysis across the estate"],
  ["sessionobs", "Session observability", "The same activity at three levels"],
];

export default function App() {
  const [page, setPage] = useState("overview");
  const [openSession, setOpenSession] = useState(null);
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let dead = false;
    (async () => {
      const all = [];
      try {
        for (let from = 0; from < 90000; from += 1000) {
          const res = await fetch(
            `${URL}/rest/v1/observables?select=${COLUMNS}&order=occurred_at.asc`,
            { headers: { apikey: KEY, Authorization: `Bearer ${KEY}`,
                         Range: `${from}-${from + 999}` } });
          if (!res.ok) throw new Error(res.status);
          const b = await res.json();
          all.push(...b);
          if (!dead) setStatus(`${all.length.toLocaleString()} records`);
          if (b.length < 1000) break;
        }
        if (!dead) { setRows(all); setStatus("ready"); }
      } catch (e) { if (!dead) setStatus("failed: " + e.message); }
    })();
    return () => { dead = true; };
  }, []);

  return (
    <div className="min-h-screen flex" style={{ background: C.ground, color: C.ink }}>
      <aside className="w-56 shrink-0 flex flex-col"
             style={{ background: C.panel, borderRight: `1px solid ${C.rule}` }}>
        <div className="px-5 py-5" style={{ borderBottom: `1px solid ${C.rule}` }}>
          <div className="flex items-baseline gap-2">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: C.accent }} />
            <span className="text-base">Project Signal</span>
          </div>
          <div className="text-xs mt-1" style={{ color: C.mute }}>
            Coding agent observability
          </div>
        </div>
        <nav className="py-2">
          {NAV.map(([id, label, hint]) => {
            const on = page === id;
            return (
              <button key={id} onClick={() => setPage(id)}
                      className="w-full text-left px-5 py-2.5 block"
                      style={{ background: on ? C.soft : "transparent",
                               borderLeft: `2px solid ${on ? C.accent : "transparent"}` }}>
                <div className="text-sm" style={{ color: on ? C.ink : C.body }}>{label}</div>
                <div className="text-xs mt-0.5" style={{ color: C.mute }}>{hint}</div>
              </button>
            );
          })}
        </nav>
        <div className="mt-auto px-5 py-4 text-xs"
             style={{ borderTop: `1px solid ${C.rule}`, color: C.mute }}>
          {status}
        </div>
      </aside>

      <main className="flex-1 min-w-0 p-6">
        {status !== "ready" ? (
          <p className="text-sm" style={{ color: C.body }}>{status}</p>
        ) : (
          page === "overview" ? <Overview rows={rows} />
            : page === "sessions" ? <Sessions rows={rows} onOpen={setOpenSession} />
            : page === "insights" ? <Insights rows={rows} />
            : page === "sessionobs" ? <SessionObservability rows={rows} onOpen={setOpenSession} />
            : <div className="p-8" style={{ background: C.panel, border: `1px solid ${C.rule}` }}>
                <p className="text-sm" style={{ color: C.body }}>{page} is next.</p>
              </div>
        )}
      </main>
    </div>
  );
}
