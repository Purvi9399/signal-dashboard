"""
Writes docs/cops/evaluation/COPS_TEST_BENCH.html from test_bench_results.json.

    python -m cops.runtime.bench_report        # from collector/, after python -m cops.runtime.test_bench

The page is one self-contained file: the results are embedded as JSON and
rendered by inline script, with no network request. Nothing on the page is
typed by hand: every number and label comes from the results file.
"""

import json, os, sys

DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "cops", "evaluation")

# The three investigations shown first in the demo (DEMO.md). Picked after the
# run: real-model cases, one per kind, all from one COP so the story is the same
# question asked three ways.
DEMO = [("anomaly_detection:abnormal", "A correctly detected abnormality"),
        ("anomaly_detection:normal", "A correctly classified normal situation"),
        ("anomaly_detection:missing", "Insufficient evidence prevents a definitive decision")]


def _slim(doc):
    for f in doc.get("fleet", []):
        for v in (f.get("per_cop") or {}).values():
            v.pop("replies", None)
    return doc


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>COPS Test Bench</title>
<style>
:root{--bg:#fbfaf8;--panel:#ffffff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--soft:#f3f1ec;
--pass:#1f7a4d;--pass-bg:#e3f3ea;--fail:#b4232a;--fail-bg:#fbe6e6;--inc:#8a5a00;--inc-bg:#fbf0d9;
--unt:#5c5f66;--unt-bg:#eceef1;--live:#1d4ed8;--live-bg:#e3ebfd;--sim:#6d4bb5;--sim-bg:#eee8fa;
--det:#3c4a57;--det-bg:#e7ebef;--accent:#c2410c}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe6;
--muted:#a3a29b;--line:#34332f;--soft:#262623;--pass:#5fd39a;--pass-bg:#16372a;--fail:#f08a8f;--fail-bg:#3d1c1e;
--inc:#f0c165;--inc-bg:#3a2e14;--unt:#b5b8bf;--unt-bg:#2c2e33;--live:#8fb0ff;--live-bg:#1b2747;--sim:#c4adf5;
--sim-bg:#2c2440;--det:#bfcad4;--det-bg:#283039;--accent:#fb923c}}
:root[data-theme="dark"]{--bg:#161615;--panel:#1f1f1d;--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--soft:#262623;
--pass:#5fd39a;--pass-bg:#16372a;--fail:#f08a8f;--fail-bg:#3d1c1e;--inc:#f0c165;--inc-bg:#3a2e14;--unt:#b5b8bf;
--unt-bg:#2c2e33;--live:#8fb0ff;--live-bg:#1b2747;--sim:#c4adf5;--sim-bg:#2c2440;--det:#bfcad4;--det-bg:#283039;--accent:#fb923c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 8px}h3{font-size:16px;margin:18px 0 6px}
p{margin:6px 0}.muted{color:var(--muted)}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.tile b{display:block;font-size:24px}.tile span{color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.PASS{color:var(--pass);background:var(--pass-bg)}.FAIL{color:var(--fail);background:var(--fail-bg)}
.INCONCLUSIVE{color:var(--inc);background:var(--inc-bg)}.UNTESTED{color:var(--unt);background:var(--unt-bg)}
.src-live{color:var(--live);background:var(--live-bg)}.src-replay,.src-sim{color:var(--sim);background:var(--sim-bg)}
.src-det{color:var(--det);background:var(--det-bg)}
.tbl{width:100%;overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
td.cell{min-width:150px}.lab{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
details{border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--panel)}
summary{cursor:pointer;padding:10px 12px;list-style:none}summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:var(--muted)}details[open]>summary:before{content:"▾ "}
.body{padding:0 14px 14px}.dims{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.dim{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 8px}
.dim.y{border-color:var(--pass);color:var(--pass)}.dim.n{border-color:var(--fail);color:var(--fail)}.dim.na{color:var(--muted)}
.step{border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:10px 0}
.step h4{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--accent)}
.step ul{margin:4px 0;padding-left:18px}.step li{margin:2px 0}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;font-size:14px}
.demo{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.demo a{color:inherit;text-decoration:none}.demo .tile:hover{border-color:var(--accent)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
button{font:inherit;font-size:13px;padding:4px 10px;border-radius:6px;border:1px solid var(--line);
background:var(--panel);color:var(--ink);cursor:pointer}button.on{border-color:var(--accent);color:var(--accent)}
.legend span{margin-right:10px}
</style>
</head>
<body>
<main>
<h1>COPS test bench</h1>
<p class="muted" id="gen"></p>

<div class="panel">
<p><b>What this is.</b> COPS is a fleet of 18 governance COPs. Each COP answers one behavioural question
about an AI agent's activity, such as "is resource consumption within entitlement?". Every COP reads the
agent's telemetry and authoritative records (policies, quotas, registries), forms a finding, has that finding
checked by independent verifiers, and may propose an action. Enforcement is in <b>dry-run</b>: an action is
recorded, never executed.</p>
<p>Ten COPs use a language model to investigate: it plans, asks for tools, and proposes a finding that the
verifiers then check. Eight COPs are rule-based and make no model call. Each COP was tested on three
fictional cases: a <b>normal</b> one, an <b>abnormal</b> one, and one where the evidence it needs is
<b>missing</b>. Each case's expected outcome was fixed before it ran and never shown to the COP.</p>
<p class="legend"><b>Where a result comes from:</b>
<span class="badge src-live">LIVE</span> real model calls, recorded with request ids and measured tokens
<span class="badge src-replay">REPLAY</span> the recorded real replies fed back in to rebuild the trace (no model call)
<span class="badge src-det">DETERMINISTIC</span> rules only, run for real, no model
<span class="badge src-sim">SIMULATED</span> scripted replies</p>
<p class="legend"><b>Verdicts:</b>
<span class="badge PASS">PASS</span> ran, matched the expected outcome, stayed within authority
<span class="badge FAIL">FAIL</span> wrong finding, or did not run correctly
<span class="badge INCONCLUSIVE">INCONCLUSIVE</span> held back where a definite answer was expected
<span class="badge UNTESTED">UNTESTED</span> not run</p>
</div>

<div class="tiles" id="tiles"></div>

<h2>Demo: three investigations</h2>
<p class="muted">Real model calls. Click one to open its trace below.</p>
<div class="demo" id="demo"></div>

<h2>All 18 COPs</h2>
<p class="muted">One row per COP. Each cell shows the verdict, then what the COP said against what was expected.</p>
<div class="tbl"><table id="fleet"></table></div>

<h2>Fleet integration</h2>
<p class="muted">Several COPs investigating the same event in one case graph. Downstream COPs read upstream findings.</p>
<div id="fleetint"></div>

<h2>Every investigation</h2>
<div class="toolbar" id="filters"></div>
<div id="cases"></div>

<h2>Known problems</h2>
<div class="panel" id="issues"></div>

<h2>Reproduce</h2>
<div class="panel"><pre class="mono" style="white-space:pre-wrap;margin:0">cd collector
pip install -r requirements-cops-runtime.txt
python3 -m cops.runtime.test_bench                                   # 54 cases + fleet re-check, no model call, no cost
python3 -m cops.runtime.test_bench --trace anomaly_detection:abnormal # one investigation's trace (replay of the real reply)
python3 -m cops.runtime.test_bench --trace recovery_resilience:abnormal
python3 -m cops.runtime.bench_report                                 # rewrites this page
cd .. && python3 -m pytest -p no:cacheprovider tests/ -q</pre></div>
</main>

<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const DEMO = __DEMO__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const badge = v => `<span class="badge ${v}">${v}</span>`;
const srcBadge = s => s === 'live' ? '<span class="badge src-live">LIVE</span>'
  : s === 'deterministic' ? '<span class="badge src-det">DETERMINISTIC</span>'
  : s === 'replay' ? '<span class="badge src-replay">REPLAY</span>' : '<span class="badge src-sim">SIMULATED</span>';
const S = D.summary, cops = Object.fromEntries(D.cops.map(c => [c.key, c]));
const money = x => x == null ? 'n/a' : '$' + Number(x).toFixed(4);
const anchor = id => 'case-' + id.replace(/[^a-z0-9]/gi, '-');

document.getElementById('gen').textContent = `Generated ${D.generated} from test_bench_results.json. ` +
  (D.live_model ? `Live model: ${D.live_model}.` : 'No live run recorded.');

const tiles = [
  [`${S.cops_executing_all_three} / 18`, 'COPs executed all three cases'],
  [`${S.PASS} / ${S.cases}`, 'cases passed'],
  [S.FAIL, 'failed'], [S.INCONCLUSIVE, 'inconclusive'], [S.UNTESTED, 'untested'],
  [S.real_llm_calls + S.fleet_real_llm_calls, 'real model calls (cases + fleet)'],
  [money(S.real_llm_cost_usd + S.fleet_real_cost_usd), 'measured model cost'],
  [Object.entries(S.fleet).map(([k, v]) => `${k} ${v}`).join(', '), 'fleet scenarios']];
document.getElementById('tiles').innerHTML = tiles.map(([b, s]) => `<div class="tile"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');

const byId = Object.fromEntries(D.cases.map(c => [c.id, c]));
document.getElementById('demo').innerHTML = DEMO.map(([id, why]) => {
  const c = byId[id]; if (!c) return '';
  return `<a href="#${anchor(id)}" onclick="openCase('${id}')"><div class="tile"><span>${esc(why)}</span>
  <b style="font-size:16px">${esc(cops[c.cop].name)}: ${esc(c.kind)}</b>
  <p>${esc(c.title)}</p><p>${badge(c.verdict)} ${srcBadge(c.source)} said <span class="lab">${esc(c.label)}</span>,
  expected <span class="lab">${esc(c.expected.join(' / '))}</span></p></div></a>`;
}).join('');

const cell = c => !c ? `<td class="cell">${badge('UNTESTED')}</td>` :
  `<td class="cell"><a href="#${anchor(c.id)}" onclick="openCase('${c.id}')" style="color:inherit">${badge(c.verdict)}</a><br>
   <span class="lab">${esc(c.label ?? 'not run')}</span>${c.inconclusive_by === 'verifier' ? ' <span class="muted">(verifier)</span>' : ''}
   <br><span class="muted">expected ${esc(c.expected.join(' / '))}</span></td>`;
let rows = `<tr><th>COP</th><th>Type</th><th>Normal</th><th>Abnormal</th><th>Missing evidence</th><th>Model usage (3 cases)</th></tr>`;
for (const k of D.cops) {
  const cs = D.cases.filter(c => c.cop === k.key), get = kind => cs.find(c => c.kind === kind);
  const us = cs.map(c => c.usage).filter(u => u);
  const real = us.filter(u => u.simulated === false);
  const usage = k.classification !== 'llm' ? '<span class="muted">none: rules only</span>' :
    real.length ? `<span class="badge src-live">LIVE</span> ${real.reduce((a, u) => a + u.calls, 0)} calls<br>
      ${real.reduce((a, u) => a + u.input_tokens, 0).toLocaleString()} in / ${real.reduce((a, u) => a + u.output_tokens, 0).toLocaleString()} out<br>
      ${(real.reduce((a, u) => a + u.latency_ms, 0) / 1000).toFixed(1)} s, ${money(real.reduce((a, u) => a + (u.cost_usd || 0), 0))}`
    : '<span class="muted">no real call</span>';
  rows += `<tr><td><b>${esc(k.name)}</b><br><span class="muted">${esc(k.question)}</span></td>
    <td>${k.classification === 'llm' ? 'LLM' : 'deterministic'}</td>${cell(get('normal'))}${cell(get('abnormal'))}${cell(get('missing'))}
    <td>${usage}</td></tr>`;
}
document.getElementById('fleet').innerHTML = rows;

const DIMS = [['execution', 'executed'], ['evidence_accessed', 'evidence accessed'], ['llm_investigation', 'model investigation'],
  ['claims_valid', 'claims valid'], ['verified', 'verified'], ['ground_truth', 'matches ground truth'],
  ['uncertainty', 'uncertainty handled'], ['authority', 'within authority']];
const dims = d => `<div class="dims">${DIMS.filter(([k]) => k in d).map(([k, n]) =>
  `<span class="dim ${d[k] === true ? 'y' : d[k] === false ? 'n' : 'na'}">${d[k] === true ? '✓' : d[k] === false ? '✗' : '–'} ${n}${d[k] == null ? ' (n/a)' : ''}</span>`).join('')}</div>`;
const list = (xs, f) => xs && xs.length ? `<ul>${xs.map(x => `<li>${f(x)}</li>`).join('')}</ul>` : '<p class="muted">none</p>';

function trace(t) {
  if (!t) return '<p class="muted">No trace recorded.</p>';
  const a = t.authority, d = t.decision, i = t.interpretation;
  return `
  <div class="step"><h4>1 · Signals read</h4>${list(t.signals, s => `<span class="mono">${esc(s.metric)}</span> ${esc(s.state)}
     ${s.value != null ? '= ' + esc(s.value) : ''} <span class="muted">(${esc(s.evidence)})</span>`)}
   ${t.missing_evidence.length ? `<p><b>Missing evidence:</b></p>${list(t.missing_evidence, m => esc(m.source || (m.metric ? m.metric + ' is ' + m.evidence : JSON.stringify(m))) + (m.reason ? ` <span class="muted">(${esc(m.reason)})</span>` : ''))}` : ''}
   ${t.missing_dependencies.length ? `<p><b>Missing records:</b> ${esc(t.missing_dependencies.map(m => m.artifact || m.dependency || JSON.stringify(m)).join(', '))}</p>` : ''}
   ${t.upstream_used.length ? `<p><b>Upstream findings used:</b> ${esc(t.upstream_used.map(u => u.cop + '=' + u.label).join(', '))}</p>` : ''}</div>
  <div class="step"><h4>2 · Evidence gathered (tool calls)</h4>${list(t.tools, x => `<span class="mono">${esc(x.tool)}</span> by ${esc(x.by)}: ${esc(x.status)}${x.rejection ? ' (' + esc(x.rejection) + ')' : ''}`)}</div>
  ${t.llm.length ? `<div class="step"><h4>3 · Model investigation</h4>${t.llm.map(c => `<p><b>${esc(c.purpose)}</b>
     ${c.simulated ? '<span class="badge src-replay">recorded reply</span>' : '<span class="badge src-live">live</span>'}
     ${c.decision ? 'decision <span class="lab">' + esc(c.decision) + '</span>' : ''} ${c.candidate_label ? '· proposed <span class="lab">' + esc(c.candidate_label) + '</span>' : ''}</p>
     ${c.hypothesis ? `<p>${esc(c.hypothesis)}</p>` : ''}
     ${c.tool_requests.length ? `<p class="muted">asked for: ${esc(c.tool_requests.map(q => q.tool).join(', '))}</p>` : ''}
     ${c.error ? `<p style="color:var(--fail)">error: ${esc(c.error)}</p>` : ''}`).join('')}</div>` : ''}
  <div class="step"><h4>${t.llm.length ? 4 : 3} · Interpretation</h4><div class="kv">
     <span>rule baseline</span><span class="lab">${esc(i.rule_baseline ?? '–')}</span>
     <span>rejected candidate</span><span class="lab">${esc(i.rejected_candidate ?? '–')}</span></div>
     <p>${esc(i.rationale)}</p><p><b>Claims:</b></p>${list(t.claims, c => `${esc(c.statement)} <span class="muted">[${esc(c.kind)}]</span>
       ${(c.cites || []).concat(c.refs || []).length ? `<br><span class="mono muted">cites ${esc((c.cites || []).concat((c.refs || []).slice(0, 3)).join(' · '))}</span>` : ''}`)}</div>
  <div class="step"><h4>${t.llm.length ? 5 : 4} · Verification</h4><p>${t.verification.map(v =>
     `<span class="badge ${v.result === 'PASS' ? 'PASS' : v.result === 'FAIL' ? 'FAIL' : 'UNTESTED'}">${esc(v.check)} ${esc(v.result)}</span>`).join(' ')}</p>
     ${list(t.verification.filter(v => v.result === 'FAIL'), v => `<b>${esc(v.check)}</b> ${esc(v.reason)}: ${esc(v.detail)}`)}</div>
  <div class="step"><h4>${t.llm.length ? 6 : 5} · Authority and final decision</h4><div class="kv">
     <span>requested action</span><span class="lab">${esc(a.requested)}</span>
     <span>final action</span><span class="lab">${esc(a.final)}</span>
     <span>authorised</span><span>${esc(a.authorised)}</span>
     <span>execution</span><span class="lab">${esc(a.execution_status)}</span>
     ${a.reason ? `<span>authority note</span><span>${esc(a.reason)}</span>` : ''}
     <span>permitted for this COP</span><span class="lab">${esc(a.permitted.join(', '))}</span>
     <span>decision</span><span><span class="lab">${esc(d.label)}</span> · ${esc(d.status)} · confidence ${esc(d.confidence)} · verified ${esc(d.verified)}</span></div></div>`;
}

function caseBlock(c) {
  const u = c.usage;
  return `<details id="${anchor(c.id)}" data-v="${c.verdict}" data-t="${cops[c.cop].classification}">
  <summary>${badge(c.verdict)} ${srcBadge(c.source)} <b>${esc(cops[c.cop].name)}</b> · ${esc(c.kind)} · ${esc(c.title)}
   <span class="muted"> → <span class="lab">${esc(c.label ?? 'not run')}</span></span></summary><div class="body">
  <div class="kv"><span>expected (fixed before the run)</span><span><span class="lab">${esc(c.expected.join(' / '))}</span> · ${esc(c.ground_truth)}</span>
   <span>COP said</span><span><span class="lab">${esc(c.label)}</span> · ${esc(c.status)}${c.inconclusive_by ? ' · held back by the ' + esc(c.inconclusive_by === 'verifier' ? 'verifier (the COP proposed a label that failed checks)' : 'COP itself') : ''}</span>
   <span>outcome</span><span>${esc(c.outcome)}</span>
   ${c.replay ? `<span>replay under current code</span><span><span class="lab">${esc(c.replay.label)}</span> (${c.replay.same_label ? 'same as live' : 'differs from live'})</span>` : ''}
   ${u ? `<span>model usage</span><span>${u.simulated ? '<span class="badge src-sim">simulated</span>' : '<span class="badge src-live">real</span>'}
     ${u.calls} calls · ${esc(u.provider)} / ${esc(u.model)} · ${u.input_tokens.toLocaleString()} in / ${u.output_tokens.toLocaleString()} out ·
     ${(u.latency_ms / 1000).toFixed(1)} s · ${money(u.cost_usd)}</span>` : `<span>model usage</span><span class="muted">none</span>`}
   ${c.trace_source ? `<span>trace</span><span class="muted">${esc(c.trace_source)}</span>` : ''}</div>
  ${dims(c.dimensions)}${trace(c.trace)}</div></details>`;
}
document.getElementById('cases').innerHTML = D.cases.map(caseBlock).join('');

const F = [['all', 'All'], ['PASS', 'Passed'], ['FAIL', 'Failed'], ['INCONCLUSIVE', 'Inconclusive'], ['llm', 'LLM COPs'], ['deterministic', 'Deterministic COPs']];
document.getElementById('filters').innerHTML = F.map(([k, n]) => `<button data-f="${k}" class="${k === 'all' ? 'on' : ''}">${n}</button>`).join('');
document.getElementById('filters').addEventListener('click', e => {
  const k = e.target.dataset.f; if (!k) return;
  document.querySelectorAll('#filters button').forEach(b => b.classList.toggle('on', b.dataset.f === k));
  document.querySelectorAll('#cases details').forEach(el => {
    el.style.display = k === 'all' || el.dataset.v === k || el.dataset.t === k ? '' : 'none'; });
});
function openCase(id) { const el = document.getElementById(anchor(id)); if (el) { el.open = true; el.style.display = ''; } }

document.getElementById('fleetint').innerHTML = D.fleet.map(f => `<div class="panel">
  <h3>${badge(f.verdict)} ${f.source === 'live' ? '<span class="badge src-live">LIVE</span>' : '<span class="badge src-sim">SIMULATED</span>'} ${esc(f.id)}: ${esc(f.title)}</h3>
  <p>${esc(f.event || f.skipped || '')}</p>
  ${f.usage ? `<p class="muted">${f.usage.calls} model calls · ${f.usage.input_tokens.toLocaleString()} in / ${f.usage.output_tokens.toLocaleString()} out ·
    ${(f.usage.latency_ms / 1000).toFixed(1)} s · ${money(f.usage.cost_usd)}</p>` : ''}
  <div class="tbl"><table><tr><th>COP</th><th>Finding</th><th>Expected</th><th>Verified</th><th>Action (requested → final)</th><th>Execution</th></tr>
  ${Object.entries(f.per_cop || {}).map(([k, v]) => `<tr><td>${esc(cops[k].name)} <span class="muted">${v.classification === 'llm' ? 'LLM' : 'rules'}</span></td>
    <td>${(v.expected || []).includes(v.label) ? badge('PASS') : badge(v.label === 'INCONCLUSIVE' ? 'INCONCLUSIVE' : 'FAIL')} <span class="lab">${esc(v.label)}</span></td>
    <td class="lab">${esc((v.expected || []).join(' / '))}</td><td>${esc(v.verified)}</td>
    <td class="lab">${esc(v.requested_action)} → ${esc(v.action)}</td><td class="lab">${esc(v.execution_status)}</td></tr>`).join('')}</table></div>
  <h3>Fleet checks</h3>${list(f.checks, c => `${c.passed ? '✓' : '✗'} ${esc(c.check)}${c.passed ? '' : ': <span class="mono">' + esc(JSON.stringify(c.actual)) + '</span>'}`)}
  ${Object.entries(f.per_cop || {}).map(([k, v]) => `<details><summary>${esc(cops[k].name)} trace</summary><div class="body">${trace(v.trace)}</div></details>`).join('')}
  </div>`).join('');

const fails = D.cases.filter(c => c.verdict !== 'PASS');
document.getElementById('issues').innerHTML = `<p><b>Not passing (${fails.length}):</b></p>${list(fails, c =>
  `${badge(c.verdict)} <a href="#${anchor(c.id)}" onclick="openCase('${c.id}')" style="color:inherit">${esc(c.id)}</a>: said <span class="lab">${esc(c.label)}</span>, expected
   <span class="lab">${esc(c.expected.join(' / '))}</span>${c.rejected_candidate ? `; the model proposed <span class="lab">${esc(c.rejected_candidate)}</span>, which a verifier rejected` : ''}`)}
  <p><b>Correct only because a verifier stepped in (${S.correct_inconclusive_forced_by_verifier.length}):</b> the COP proposed a
  definite label on insufficient evidence and a verifier turned it into INCONCLUSIVE.</p>
  ${list(S.correct_inconclusive_forced_by_verifier, id => `<a href="#${anchor(id)}" onclick="openCase('${id}')" style="color:inherit">${esc(id)}</a>`)}`;
</script>
</body>
</html>
"""


def main(argv=None):
    src = os.path.join(DOCS, "test_bench_results.json")
    doc = _slim(json.load(open(src)))
    data = json.dumps(doc, default=str).replace("</", "<\\/")
    html = PAGE.replace("__DATA__", data).replace("__DEMO__", json.dumps(DEMO))
    out = os.path.join(DOCS, "COPS_TEST_BENCH.html")
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out} ({len(html) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
