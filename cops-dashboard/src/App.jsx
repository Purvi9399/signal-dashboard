import { useEffect, useState } from "react";
import { C } from "./ui.jsx";
import { loadAll } from "./data/contracts.js";
import NetworkPage from "./network/NetworkPage.jsx";
import DecisionsPage from "./decisions/DecisionsPage.jsx";
import { PAGES, parseHash, go } from "./nav.js";

/* Two pages only. Routing is the URL hash so a view can be linked:
 *   #/network            #/network?view=hierarchy     #/network?cop=<id>&edge=<id>
 *   #/decisions          #/decisions?id=<decision id> #/decisions?session=<estate/session>
 */
export default function App() {
  const [route, setRoute] = useState(parseHash());
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const f = () => setRoute(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => { loadAll().then(setData).catch((e) => setErr(String(e))); }, []);

  const meta = data && data.network.meta;
  return (
    <div className="min-h-screen" style={{ background: C.ground }}>
      <header className="px-4 md:px-6 py-3 flex flex-wrap items-center gap-x-6 gap-y-2"
              style={{ background: C.panel, borderBottom: `1px solid ${C.rule}` }}>
        <div className="text-sm font-medium" style={{ color: C.ink }}>COPS prototype</div>
        <nav className="flex gap-1">
          {PAGES.map(([p, t]) => (
            <button key={p} onClick={() => go(p)} className="text-sm px-3 py-1 rounded"
                    style={route.page === p ? { background: C.soft, color: C.accent } : { color: C.body }}>{t}</button>
          ))}
        </nav>
        {meta && (
          <div className="text-xs mono ml-auto" style={{ color: C.mute }}>
            export {data.network.contract} · {data.decisions.contract} · commit {meta.git_commit}
          </div>
        )}
      </header>
      <main className="px-4 md:px-6 py-4">
        {err && <div className="text-sm" style={{ color: C.danger }}>{err}</div>}
        {data && data.errors.length > 0 && (
          <div className="text-sm mb-3" style={{ color: C.danger }}>
            Data contract check failed; nothing is rendered from a file that does not match.
            <ul className="list-disc ml-5 text-xs">{data.errors.slice(0, 20).map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        {!data && !err && <div className="text-sm" style={{ color: C.mute }}>Loading export…</div>}
        {data && !data.errors.length && route.page === "network" && <NetworkPage data={data} params={route.params} />}
        {data && !data.errors.length && route.page === "decisions" && <DecisionsPage data={data} params={route.params} />}
      </main>
    </div>
  );
}
