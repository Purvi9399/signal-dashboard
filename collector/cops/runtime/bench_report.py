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
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px;overflow-wrap:anywhere}
.toolbar label{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}select{max-width:100%}
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
.kv{display:grid;grid-template-columns:minmax(0,max-content) minmax(0,1fr);gap:2px 12px;font-size:14px}
.demo{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.demo a{color:inherit;text-decoration:none}.demo .tile:hover{border-color:var(--accent)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
button{font:inherit;font-size:13px;padding:4px 10px;border-radius:6px;border:1px solid var(--line);
background:var(--panel);color:var(--ink);cursor:pointer}button.on{border-color:var(--accent);color:var(--accent)}
.legend span{margin-right:10px}
.st{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11.5px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.st-NO_ACTION_PROPOSED{color:var(--unt)}.st-AUTHORISED_DRY_RUN,.st-AUTHORISED_NO_EFFECT{color:var(--live)}
.st-AWAITING_APPROVAL{color:var(--sim)}.st-REJECTED_BY_AUTHORITY,.st-APPROVAL_REJECTED{color:var(--inc)}
.st-PROPOSAL_WITHDRAWN{color:var(--muted)}.st-EXECUTED{color:var(--pass)}.st-FAILED_OR_UNCONFIRMED{color:var(--fail)}
.chain{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:13px}.chain .arrow{color:var(--muted)}
.rc{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11.5px;font-weight:600;background:var(--soft);border:1px solid var(--line);margin:1px 2px 1px 0;white-space:nowrap}
.rc-EXPECTED_INCONCLUSIVE{color:var(--pass)}.rc-MISSING_EVIDENCE,.rc-MISSING_DEPENDENCY{color:var(--inc)}
.rc-VERIFIER_REJECTED{color:var(--sim)}.rc-MODEL_UNCERTAIN{color:var(--live)}.rc-EVALUATION_MISMATCH{color:var(--fail)}
.ev{display:inline-block;padding:1px 6px;border-radius:5px;font-size:11px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.ev-RETRIEVED_AND_VALIDATED{color:var(--pass)}.ev-AVAILABLE{color:var(--live)}.ev-MISSING_BUT_RETRIEVABLE{color:var(--sim)}
.ev-MISSING_AND_UNAVAILABLE{color:var(--fail)}.ev-NOT_APPLICABLE{color:var(--muted)}.ev-PRESENT_BUT_UNVERIFIED{color:var(--inc)}
select{font:inherit;font-size:13px;padding:3px 6px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.journey{counter-reset:j}.journey .step h4:before{counter-increment:j;content:counter(j) " · "}
.vx td,.vx th{font-size:13px;padding:5px 6px}
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
<span class="badge src-replay">REPLAY</span> the recorded real replies fed back in under the current code (tools and verifiers re-run, no model call, no cost; never counted as a new live call)
<span class="badge src-live">NEW LIVE CALL</span> a fresh model call made after the fixes (none yet)
<span class="badge src-det">DETERMINISTIC</span> rules only, run for real, no model
<span class="badge src-sim">SIMULATED</span> scripted replies</p>
<p class="legend"><b>Verdicts:</b>
<span class="badge PASS">PASS</span> ran, matched the expected outcome, stayed within authority
<span class="badge FAIL">FAIL</span> wrong finding, or did not run correctly
<span class="badge INCONCLUSIVE">INCONCLUSIVE</span> held back where a definite answer was expected
<span class="badge UNTESTED">UNTESTED</span> not run</p>
<p class="legend"><b>What happened to the action:</b>
<span class="st st-NO_ACTION_PROPOSED">NO ACTION PROPOSED</span> the COP proposed NONE
<span class="st st-AWAITING_APPROVAL">PROPOSED, AWAITING APPROVAL</span> waiting for a human
<span class="st st-PROPOSAL_WITHDRAWN">PROPOSAL WITHDRAWN</span> the finding failed verification, so its action was withdrawn
<span class="st st-REJECTED_BY_AUTHORITY">REJECTED BY AUTHORITY</span> not permitted for this COP or mode (a substitute may stand)
<span class="st st-APPROVAL_REJECTED">APPROVAL REJECTED</span> a reviewer said no
<span class="st st-AUTHORISED_DRY_RUN">AUTHORISED (DRY-RUN)</span> allowed; a dry-run adapter recorded what it would do, nothing applied
<span class="st st-EXECUTED">EXECUTED</span> applied by the enforcement gateway (here only the mock one)
<span class="st st-FAILED_OR_UNCONFIRMED">FAILED OR UNCONFIRMED</span> the gateway failed or the outcome was not observed</p>
<p class="muted">Not every abnormal finding calls for an intervention: an ALERT for a reviewer, or WATCH, is often the right action. No real blocking, notification or other external effect exists in this runtime.</p>
</div>

<div class="tiles" id="tiles"></div>

<h2>Why cases are inconclusive</h2>
<p class="muted">An INCONCLUSIVE is correct when the evidence the COP needs is missing. It is a problem when a definite answer
was expected. Decision status, root cause and evaluation verdict are kept separate; one case can have several root causes.</p>
<div class="tiles" id="inctiles"></div>
<div class="panel" id="rootcauses"></div>

<h2>Before and after the root-cause fixes</h2>
<p class="muted">The original result is the one recorded before the fixes (test_bench_results_before_fixes.json). The current
result says where it came from. A replay re-uses the recorded real model replies; it is never presented as a new live call.</p>
<div class="tbl"><table id="beforeafter"></table></div>

<h2>Demo: three investigations</h2>
<p class="muted">Real model calls. Click one to open its trace below.</p>
<div class="demo" id="demo"></div>

<h2>All 18 COPs</h2>
<p class="muted">One row per COP. Each cell shows the verdict, then what the COP said against what was expected.</p>
<div class="tbl"><table id="fleet"></table></div>

<h2>Fleet integration</h2>
<p class="muted">Several COPs investigating the same event in one case graph. Downstream COPs read upstream findings.</p>
<div id="fleetint"></div>

<h2>Action lifecycle demonstrations</h2>
<p class="muted">Dry-run never executes anything, so the later lifecycle states can only be shown in supervised mode. These four
runs use Policy Enforcement on the restricted-access session, in supervised mode, against the <b>mock</b> enforcement gateway
(it records the call and applies nothing), with a scripted reviewer decision. They are SIMULATED.</p>
<div id="lifecycle"></div>

<h2>Every investigation</h2>
<p class="muted">Each investigation opens as an evidence journey: input evidence → deterministic baseline → model planning →
tool calls → model candidate → verifier results → final decision → action lifecycle.</p>
<div class="toolbar" id="filters"></div><p class="muted" id="fcount"></p>
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
const RS = {deterministic_rerun: ['src-det', 'DETERMINISTIC RE-RUN'], recorded_reply_replay: ['src-replay', 'REPLAY OF RECORDED REAL REPLY'],
  live_new_call: ['src-live', 'NEW LIVE CALL'], live_recorded: ['src-live', 'LIVE (as recorded)']};
const rsBadge = r => { const x = RS[r] || ['src-sim', r || 'unknown']; return `<span class="badge ${x[0]}">${esc(x[1])}</span>`; };
const rc = xs => (xs || []).map(x => `<span class="rc rc-${x}">${esc(x.replace(/_/g, ' '))}</span>`).join('');
const evb = st => `<span class="ev ev-${st}">${esc(String(st || '').replace(/_/g, ' '))}</span>`;
const val = v => v === undefined || v === null ? '<span class="muted">none</span>' : `<span class="mono">${esc(typeof v === 'string' ? v : JSON.stringify(v))}</span>`;
const S = D.summary, cops = Object.fromEntries(D.cops.map(c => [c.key, c]));
const money = x => x == null ? 'n/a' : '$' + Number(x).toFixed(4);
const anchor = id => 'case-' + id.replace(/[^a-z0-9]/gi, '-');
const STAGE_NAME = {NO_ACTION_PROPOSED: 'no action proposed', PROPOSAL_WITHDRAWN: 'proposal withdrawn',
  REJECTED_BY_AUTHORITY: 'rejected by authority', AWAITING_APPROVAL: 'proposed, awaiting approval',
  APPROVAL_REJECTED: 'approval rejected', AUTHORISED_DRY_RUN: 'authorised (dry-run)',
  AUTHORISED_NO_EFFECT: 'authorised (no effect)', EXECUTED: 'executed', FAILED_OR_UNCONFIRMED: 'failed or unconfirmed'};
const stage = s => s ? `<span class="st st-${s}">${esc(STAGE_NAME[s] || s)}</span>` : '<span class="muted">not recorded</span>';
const lcOf = c => (((c || {}).trace || {}).authority || {}).lifecycle;
const actLine = lc => !lc ? '' : lc.stage === 'NO_ACTION_PROPOSED' ? stage(lc.stage) :
  `<span class="lab">${esc(lc.candidate_action)}${lc.final_action !== lc.candidate_action ? ' → ' + esc(lc.final_action) : ''}</span> ${stage(lc.stage)}` +
  (lc.substitute ? ` <span class="muted">then</span> ${stage(lc.substitute.stage)}` : '');
function lifecycleBlock(lc) {
  if (!lc) return '<p class="muted">No lifecycle recorded.</p>';
  const r = lc.dry_run_receipt, x = lc.execution_outcome, ap = lc.approval;
  return `<div class="chain"><span>candidate <span class="lab">${esc(lc.candidate_action)}</span></span><span class="arrow">→</span>
    <span>after verification <span class="lab">${esc(lc.verified_action)}</span></span><span class="arrow">→</span>
    <span>after authority <span class="lab">${esc(lc.final_action)}</span></span><span class="arrow">→</span>
    <span class="lab">${esc(lc.execution_status)}</span><span class="arrow">=</span>${stage(lc.stage)}
    ${lc.substitute ? `<span class="muted">; substitute <span class="lab">${esc(lc.substitute.action)}</span></span> ${stage(lc.substitute.stage)}` : ''}</div>
   ${lc.withdrawn_reason ? `<p>Withdrawn: ${esc(lc.withdrawn_reason)}</p>` : ''}
   ${lc.authority_reason ? `<p>Authority: ${esc(lc.authority_reason)}</p>` : ''}
   ${lc.checks && lc.checks.length ? `<p class="muted">checks: ${esc(lc.checks.map(c => c.check_id + ' ' + c.result + (c.reason_code ? ' (' + c.reason_code + ')' : '')).join(', '))}</p>` : ''}
   ${r ? `<p>Dry-run adapter: ${esc(r.would)} <span class="muted">(${esc(r.action)} on ${esc(r.target)}; external effect: ${esc(r.external_effect)})</span></p>` : ''}
   ${ap ? `<p>Reviewer decision: <span class="lab">${esc(ap.decision)}</span> by ${esc(ap.reviewer)}</p>` : ''}
   ${x ? `<p>Gateway: ${esc(x.detail)} <span class="muted">(applied ${esc(x.applied)}, outcome confirmed ${esc(x.outcome_confirmed)}, simulated ${esc(x.simulated)})</span></p>` : ''}
   ${lc.derived_from_recorded_fields ? '<p class="muted">Derived from the authority fields recorded at run time.</p>' : ''}`;
}

document.getElementById('gen').textContent = `Generated ${D.generated} from test_bench_results.json. ` +
  (D.live_model ? `Live model: ${D.live_model}.` : 'No live run recorded.');

const tiles = [
  [`${S.cops_executing_all_three} / 18`, 'COPs executed all three cases'],
  [`${S.PASS} / ${S.cases}`, 'cases passed'],
  [S.FAIL, 'failed'], [S.INCONCLUSIVE, 'inconclusive'], [S.UNTESTED, 'untested'],
  [S.real_llm_calls + S.fleet_real_llm_calls, 'real model calls (cases + fleet)'],
  [money(S.real_llm_cost_usd + S.fleet_real_cost_usd), 'measured model cost'],
  [Object.entries(S.fleet).map(([k, v]) => `${k} ${v}`).join(', '), 'fleet scenarios'],
  [Object.entries(S.action_lifecycle || {}).filter(([k]) => k !== 'NO_ACTION_PROPOSED').reduce((a, [, n]) => a + n, 0) + ' / ' + S.cases,
   'cases where the COP proposed an action']];
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
   <br><span class="muted">expected ${esc(c.expected.join(' / '))}</span><br>${actLine(lcOf(c))}</td>`;
let rows = `<tr><th>COP</th><th>Type and allowed actions</th><th>Normal</th><th>Abnormal</th><th>Missing evidence</th><th>Model usage (3 cases)</th></tr>`;
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
    <td>${k.classification === 'llm' ? 'LLM' : 'deterministic'}<br><span class="lab muted">${esc(k.permitted_actions.join(', '))}</span></td>${cell(get('normal'))}${cell(get('abnormal'))}${cell(get('missing'))}
    <td>${usage}</td></tr>`;
}
document.getElementById('fleet').innerHTML = rows;

const DIMS = [['execution', 'executed'], ['evidence_accessed', 'evidence accessed'], ['llm_investigation', 'model investigation'],
  ['claims_valid', 'claims valid'], ['verified', 'verified'], ['ground_truth', 'matches ground truth'],
  ['uncertainty', 'uncertainty handled'], ['authority', 'within authority']];
const dims = d => `<div class="dims">${DIMS.filter(([k]) => k in d).map(([k, n]) =>
  `<span class="dim ${d[k] === true ? 'y' : d[k] === false ? 'n' : 'na'}">${d[k] === true ? '✓' : d[k] === false ? '✗' : '–'} ${n}${d[k] == null ? ' (n/a)' : ''}</span>`).join('')}</div>`;
const list = (xs, f) => xs && xs.length ? `<ul>${xs.map(x => `<li>${f(x)}</li>`).join('')}</ul>` : '<p class="muted">none</p>';

function verifierTable(t) {
  const rows = [];
  for (const v of t.verification.filter(v => v.result === 'FAIL')) {
    const fs = v.failures && v.failures.length ? v.failures : [null];
    for (const f of fs) rows.push(f ? `<tr><td><b>${esc(v.check)}</b></td><td>claim ${esc(f.claim)} <span class="muted">${esc(f.kind)}</span><br>${esc(f.statement)}</td>
      <td class="mono">${esc(f.field ?? '–')}</td><td>${val(f.asserted)}</td><td>${val(f.observed)}</td><td><span class="lab">${esc(f.code)}</span></td>
      <td>${f.recoverable ? 'yes' : 'no'}</td></tr>`
      : `<tr><td><b>${esc(v.check)}</b></td><td colspan="4">${esc(v.reason)}: ${esc(v.detail)}</td><td></td><td>${v.check === 'DS-03' ? 'yes' : 'no'}</td></tr>`);
  }
  return rows.length ? `<div class="tbl"><table class="vx"><tr><th>Check</th><th>Claim</th><th>Field</th><th>Asserted</th><th>Observed</th><th>Code</th><th>Recoverable</th></tr>${rows.join('')}</table></div>` : '';
}

function sufficiency(t) {
  const s = t.sufficiency;
  if (!s) return '';
  const items = s.items.filter(x => x.kind !== 'METRIC');
  return `<p><b>Evidence sufficiency</b> (precheck before any conclusion): quiet label ${s.quiet_label_permitted ? 'permitted' : '<b>not permitted</b>'}</p>
   ${items.length ? `<div class="tbl"><table class="vx"><tr><th>Item</th><th>Kind</th><th>State</th><th>Backs / note</th></tr>${items.map(x => `<tr>
     <td class="mono">${esc(x.item)}</td><td>${esc(x.kind)}${x.role ? ' · ' + esc(x.role.replace(/_/g, ' ')) : ''}</td><td>${evb(x.state)}</td>
     <td>${esc(x.backs || (x.label ? x.label + ' (' + x.status + ')' : ''))}${x.retrieve_with && x.retrieve_with.length ? ` <span class="muted">· via ${esc(x.retrieve_with.join(', '))}</span>` : ''}</td></tr>`).join('')}</table></div>` : ''}
   ${s.reasons.length ? `<p class="muted">${s.reasons.map(r => esc(r.code + ': ' + r.item + ' — ' + r.detail)).join('<br>')}</p>` : ''}`;
}

function trace(t) {
  if (!t) return '<p class="muted">No trace recorded.</p>';
  const a = t.authority, d = t.decision, i = t.interpretation;
  const plan = t.llm.filter(c => c.purpose === 'plan'), assess = t.llm.filter(c => c.purpose !== 'plan');
  const llmCall = c => `<p><b>${esc(c.purpose)}</b>
     ${c.simulated ? '<span class="badge src-replay">recorded real reply</span>' : '<span class="badge src-live">live</span>'}
     ${c.decision ? 'decision <span class="lab">' + esc(c.decision) + '</span>' : ''} ${c.candidate_label ? '· proposed <span class="lab">' + esc(c.candidate_label) + '</span>' : ''}</p>
     ${c.hypothesis ? `<p>${esc(c.hypothesis)}</p>` : ''}
     ${c.tool_requests.length ? `<p class="muted">asked for: ${esc(c.tool_requests.map(q => q.tool).join(', '))}</p>` : ''}
     ${c.error ? `<p style="color:var(--fail)">error: ${esc(c.error)}</p>` : ''}`;
  const rec = t.recovery;
  return `<div class="journey">
  <div class="step"><h4>Input evidence</h4>${list(t.signals, s => `<span class="mono">${esc(s.metric)}</span> ${esc(s.state)}
     ${s.value != null ? '= ' + esc(s.value) : ''} <span class="muted">(${esc(s.evidence)})</span>`)}
   ${sufficiency(t)}
   ${t.missing_evidence.length ? `<p><b>Missing evidence:</b></p>${list(t.missing_evidence, m => esc(m.source || (m.metric ? m.metric + ' is ' + m.evidence : JSON.stringify(m))) + (m.reason ? ` <span class="muted">(${esc(m.reason)})</span>` : ''))}` : ''}
   ${t.missing_dependencies.length ? `<p><b>Missing records:</b> ${esc(t.missing_dependencies.map(m => m.artifact || m.dependency || JSON.stringify(m)).join(', '))}</p>` : ''}
   ${t.upstream_used.length ? `<p><b>Upstream findings used:</b> ${esc(t.upstream_used.map(u => u.cop + '=' + u.label).join(', '))}</p>` : ''}</div>
  <div class="step"><h4>Deterministic baseline</h4><p>Rules read <span class="lab">${esc(i.rule_baseline ?? (t.llm.length ? '–' : d.label))}</span>${i.gated_label ? ` · <span class="lab">${esc(i.gated_label)}</span> withheld by the evidence precheck` : ''}
     ${t.llm.length ? '<span class="muted"> (recorded beside the model; never substituted for it)</span>' : '<span class="muted"> (the decision for a rule-based COP)</span>'}</p>
     ${list((t.tools || []).filter(x => x.by === 'rules'), x => `<span class="mono">${esc(x.tool)}</span>: ${esc(x.status)}`)}</div>
  ${t.llm.length ? `<div class="step"><h4>Model planning</h4>${plan.map(llmCall).join('') || '<p class="muted">none</p>'}</div>` : ''}
  <div class="step"><h4>Tool calls</h4>${list(t.tools, x => `<span class="mono">${esc(x.tool)}</span> by ${esc(x.by)}: ${esc(x.status)}${x.rejection ? ' (' + esc(x.rejection) + ')' : ''}`)}
   ${(t.tool_results || []).length ? `<details><summary class="muted">tool results (${t.tool_results.length})</summary><div class="body">${t.tool_results.map(x =>
     `<p><span class="mono">${esc(x.tool)}</span> <span class="muted">${esc((x.tool_call_id || '').slice(0, 8))} · ${esc(x.by)}</span><br><span class="mono">${esc(typeof x.result === 'string' ? x.result : JSON.stringify(x.result))}</span></p>`).join('')}</div></details>` : ''}</div>
  ${t.llm.length ? `<div class="step"><h4>Model candidate</h4>${assess.map(llmCall).join('') || '<p class="muted">no assessment</p>'}</div>` : ''}
  <div class="step"><h4>Verifier results</h4><p>${t.verification.map(v =>
     `<span class="badge ${v.result === 'PASS' ? 'PASS' : v.result === 'FAIL' ? 'FAIL' : 'UNTESTED'}">${esc(v.check)} ${esc(v.result)}</span>`).join(' ')}</p>
     ${verifierTable(t)}
     ${rec ? `<p><b>Recovery:</b> ${rec.attempted ? 'one re-assessment ran' + (rec.outcome ? ' → ' + esc(rec.outcome) : '') : 'not attempted'}${rec.recoverable === false ? ' · not recoverable' : rec.recoverable ? ' · recoverable' : ''}${rec.reason ? ' — ' + esc(rec.reason) : ''}</p>` : ''}</div>
  <div class="step"><h4>Final decision</h4><p><span class="lab">${esc(d.label)}</span> · ${esc(d.status)} · confidence ${esc(d.confidence)} · verified ${esc(d.verified)}
     ${i.rejected_candidate ? ` · rejected candidate <span class="lab">${esc(i.rejected_candidate)}</span>` : ''}</p>
     ${(t.reason_codes || []).length ? `<p>reason codes: ${rc(t.reason_codes)}</p>` : ''}
     <p>${esc(i.rationale)}</p><p><b>Claims:</b></p>${list(t.claims, c => `${esc(c.statement)} <span class="muted">[${esc(c.kind)}]</span>
       ${(c.cites || []).concat(c.refs || []).length ? `<br><span class="mono muted">cites ${esc((c.cites || []).concat((c.refs || []).slice(0, 3)).join(' · '))}</span>` : ''}`)}</div>
  <div class="step"><h4>Action lifecycle</h4>${lifecycleBlock(a.lifecycle)}<div class="kv">
     <span>requested action</span><span class="lab">${esc(a.requested)}</span>
     ${a.original_proposal ? `<span>original proposal</span><span class="lab">${esc(a.original_proposal.action)}</span>` : ''}
     <span>final action</span><span class="lab">${esc(a.final)}</span>
     <span>authorised</span><span>${esc(a.authorised)}</span>
     <span>execution</span><span class="lab">${esc(a.execution_status)}</span>
     ${a.reason ? `<span>authority note</span><span>${esc(a.reason)}</span>` : ''}
     <span>permitted for this COP</span><span class="lab">${esc(a.permitted.join(', '))}</span></div>
     <p class="muted">Dry-run: nothing was blocked, paused or sent; a dry-run receipt records only what the action would do.</p></div>
  </div>`;
}

function caseBlock(c) {
  const u = c.usage;
  const g = c.diagnosis || {}, o = c.original;
  const vf = (c.trace && c.trace.verification || []).some(v => v.result === 'FAIL' && !['AU-01', 'PE-01', 'AU-02', 'EX-01'].includes(v.check));
  return `<details id="${anchor(c.id)}" data-v="${c.verdict}" data-t="${cops[c.cop].classification}" data-cop="${c.cop}" data-k="${c.kind}"
   data-inc="${(g.evaluation || {}).inconclusive_class || ''}" data-vf="${vf ? 1 : 0}">
  <summary>${badge(c.verdict)} ${rsBadge(c.result_source)} <b>${esc(cops[c.cop].name)}</b> · ${esc(c.kind)} · ${esc(c.title)}
   <span class="muted"> → <span class="lab">${esc(c.label ?? 'not run')}</span></span> ${actLine(lcOf(c))}</summary><div class="body">
  <div class="kv"><span>expected (fixed before the run)</span><span><span class="lab">${esc(c.expected.join(' / '))}</span> · ${esc(c.ground_truth)}</span>
   <span>COP said</span><span><span class="lab">${esc(c.label)}</span> · ${esc(c.status)}${c.inconclusive_by ? ' · held back by the ' + esc(c.inconclusive_by === 'verifier' ? 'verifier (the COP proposed a label that failed checks)' : 'COP itself') : ''}</span>
   <span>outcome</span><span>${esc(c.outcome)}</span>
   <span>root causes</span><span>${rc(g.root_causes) || '<span class="muted">none</span>'}</span>
   <span>result source</span><span>${rsBadge(c.result_source)}${c.current_model_calls ? ` <span class="muted">${esc(c.current_model_calls.calls)} model calls, answered by ${esc(c.current_model_calls.answered_by)}</span>` : ''}</span>
   ${c.live_recorded ? `<span>live run (2026-11-02)</span><span><span class="lab">${esc(c.live_recorded.label)}</span> · ${esc(c.live_recorded.status)}</span>` : ''}
   ${o ? `<span>before the fixes</span><span>${badge(o.verdict)} <span class="lab">${esc(o.label)}</span>${c.changed ? ' <b>(changed)</b>' : ''}</span>` : ''}
   ${g.note ? `<span>analysis</span><span>${esc(g.note.cause)}<br><b>Fix:</b> ${esc(g.note.fix)}${g.note.remaining ? `<br><b>Still needed:</b> ${esc(g.note.remaining)}` : ''}</span>` : ''}
   ${u ? `<span>model usage</span><span>${u.simulated ? '<span class="badge src-sim">simulated</span>' : '<span class="badge src-live">real</span>'}
     ${u.calls} calls · ${esc(u.provider)} / ${esc(u.model)} · ${u.input_tokens.toLocaleString()} in / ${u.output_tokens.toLocaleString()} out ·
     ${(u.latency_ms / 1000).toFixed(1)} s · ${money(u.cost_usd)}</span>` : `<span>model usage</span><span class="muted">none</span>`}
   ${c.trace_source ? `<span>trace</span><span class="muted">${esc(c.trace_source)}</span>` : ''}</div>
  ${dims(c.dimensions)}${trace(c.trace)}</div></details>`;
}
document.getElementById('cases').innerHTML = D.cases.map(caseBlock).join('');

const FILTERS = [
  ['cop', 'COP', [['', 'all COPs']].concat(D.cops.map(k => [k.key, k.name]))],
  ['t', 'type', [['', 'LLM and deterministic'], ['llm', 'LLM'], ['deterministic', 'deterministic']]],
  ['k', 'case', [['', 'normal, abnormal, missing'], ['normal', 'normal'], ['abnormal', 'abnormal'], ['missing', 'missing evidence']]],
  ['x', 'show', [['', 'every case'], ['inc-expected', 'expected inconclusive'], ['inc-unexpected', 'unexpected inconclusive'],
                 ['vf', 'a verifier failed a check'], ['fail', 'actual failure (FAIL)'], ['changed', 'changed by the fixes']]]];
document.getElementById('filters').innerHTML = FILTERS.map(([k, n, opts]) =>
  `<label class="muted">${n} <select data-f="${k}">${opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('')}</select></label>`).join(' ');
function applyFilters() {
  const f = Object.fromEntries([...document.querySelectorAll('#filters select')].map(x => [x.dataset.f, x.value]));
  let n = 0;
  document.querySelectorAll('#cases details').forEach(el => {
    const c = byId[el.id.replace(/^case-/, '')] || D.cases.find(z => anchor(z.id) === el.id);
    const x = f.x;
    const ok = (!f.cop || el.dataset.cop === f.cop) && (!f.t || el.dataset.t === f.t) && (!f.k || el.dataset.k === f.k) &&
      (!x || (x === 'inc-expected' && el.dataset.inc === 'expected') || (x === 'inc-unexpected' && el.dataset.inc === 'unexpected') ||
       (x === 'vf' && el.dataset.vf === '1') || (x === 'fail' && el.dataset.v === 'FAIL') || (x === 'changed' && c && c.changed));
    el.style.display = ok ? '' : 'none'; n += ok ? 1 : 0;
  });
  document.getElementById('fcount').textContent = `${n} of ${D.cases.length} investigations shown`;
}
document.getElementById('filters').addEventListener('change', applyFilters);
applyFilters();
function openCase(id) { const el = document.getElementById(anchor(id)); if (el) { el.open = true; el.style.display = ''; } }

document.getElementById('fleetint').innerHTML = D.fleet.map(f => `<div class="panel">
  <h3>${badge(f.verdict)} ${f.source === 'live' ? '<span class="badge src-live">LIVE</span>' : '<span class="badge src-sim">SIMULATED</span>'} ${esc(f.id)}: ${esc(f.title)}</h3>
  <p>${esc(f.event || f.skipped || '')}</p>
  ${f.usage ? `<p class="muted">${f.usage.calls} model calls · ${f.usage.input_tokens.toLocaleString()} in / ${f.usage.output_tokens.toLocaleString()} out ·
    ${(f.usage.latency_ms / 1000).toFixed(1)} s · ${money(f.usage.cost_usd)}</p>` : ''}
  <div class="tbl"><table><tr><th>COP</th><th>Finding</th><th>Expected</th><th>Verified</th><th>Action (requested → final)</th><th>Execution</th><th>Action lifecycle</th></tr>
  ${Object.entries(f.per_cop || {}).map(([k, v]) => `<tr><td>${esc(cops[k].name)} <span class="muted">${v.classification === 'llm' ? 'LLM' : 'rules'}</span></td>
    <td>${(v.expected || []).includes(v.label) ? badge('PASS') : badge(v.label === 'INCONCLUSIVE' ? 'INCONCLUSIVE' : 'FAIL')} <span class="lab">${esc(v.label)}</span></td>
    <td class="lab">${esc((v.expected || []).join(' / '))}</td><td>${esc(v.verified)}</td>
    <td class="lab">${esc(v.requested_action)} → ${esc(v.action)}</td><td class="lab">${esc(v.execution_status)}</td><td>${stage(((v.trace || {}).authority || {}).lifecycle?.stage)}</td></tr>`).join('')}</table></div>
  <h3>Fleet checks</h3>${list(f.checks, c => `${c.passed ? '✓' : '✗'} ${esc(c.check)}${c.passed ? '' : ': <span class="mono">' + esc(JSON.stringify(c.actual)) + '</span>'}`)}
  ${Object.entries(f.per_cop || {}).map(([k, v]) => `<details><summary>${esc(cops[k].name)} trace <span class="muted">(${esc(v.trace_source || '')})</span></summary><div class="body">${trace(v.trace)}</div></details>`).join('')}
  </div>`).join('');

document.getElementById('lifecycle').innerHTML = (D.lifecycle_demos || []).map(x => `<div class="panel">
  <h3><span class="badge src-sim">SIMULATED</span> ${esc(x.id)}: ${esc(x.title)}</h3>
  <p>Policy Enforcement read <span class="lab">${esc(x.label)}</span> and proposed <span class="lab">${esc(x.action)}</span>. Result: ${stage(x.stage)}
  · mock gateway calls recorded: ${x.mock_applied.length}</p>
  ${x.trace ? lifecycleBlock(x.trace.authority.lifecycle) : `<p>The case graph is paused at the approval step, holding the request:
    <span class="mono">${esc(JSON.stringify({cop: x.approval_request.cop, action: x.approval_request.action, reason: x.approval_request.reason}))}</span></p>`}
  </div>`).join('');

const IB = S.inconclusive_breakdown || {};
document.getElementById('inctiles').innerHTML = [
  [IB.expected, 'expected inconclusive (correct uncertainty handling)'],
  [IB.expected_held_back_by_cop, 'of those, the COP held back itself'],
  [IB.expected_forced_by_verifier, 'of those, a verifier had to reject a candidate'],
  [IB.unexpected, 'unexpected inconclusive (a definite answer was expected)'],
  [S.FAIL, 'wrong finding (FAIL)']].map(([b, t]) => `<div class="tile"><b>${esc(b ?? 0)}</b><span>${esc(t)}</span></div>`).join('') +
  Object.entries(IB.unexpected_by_cause || {}).map(([k, v]) => `<div class="tile"><b>${esc(v)}</b><span>unexpected, root cause ${rc([k])}</span></div>`).join('');

const nonpass = D.cases.filter(c => c.verdict !== 'PASS');
const fleetNP = D.fleet.flatMap(f => Object.entries(f.per_cop || {}).filter(([k, v]) => v.diagnosis).map(([k, v]) => ({fid: f.id, cop: k, v})));
const rcRow = (name, href, g, extra) => `<tr><td>${href}</td><td class="lab">${esc((g.expected || []).join(' / '))}</td>
  <td class="lab">${esc(g.candidate ?? '–')}</td><td class="lab">${esc(g.final)}</td><td>${esc((g.responsible_checks || []).join(', ') || '–')}</td>
  <td>${(g.checks || []).map(x => esc(x.sentence)).join('<br>') || '<span class="muted">no check failed</span>'}</td>
  <td>${(g.missing || []).slice(0, 3).map(m => esc(String(m.source || m.dependency || m.metric || JSON.stringify(m)).slice(0, 110))).join('<br>') || '<span class="muted">none</span>'}${(g.missing || []).length > 3 ? `<br><span class="muted">+${g.missing.length - 3} more in the trace</span>` : ''}</td>
  <td>${g.recoverable === true ? 'yes' : g.recoverable === false ? 'no' : '–'}</td>
  <td>${rc(g.root_causes)}${g.note ? `<br>${esc(g.note.fix)}${g.note.remaining ? `<br><span class="muted">still needed: ${esc(g.note.remaining)}</span>` : ''}` : ''}</td></tr>`;
document.getElementById('rootcauses').innerHTML = `<p><b>Every case not passing now (${nonpass.length} cases, ${fleetNP.length} fleet COP).</b>
  The fix column is this branch's analysis, from the recorded trace.</p><div class="tbl"><table class="vx">
  <tr><th>Case</th><th>Expected</th><th>Candidate</th><th>Final</th><th>Check</th><th>Rejection reason</th><th>Missing or contradictory evidence</th><th>Recoverable</th><th>Root cause and fix</th></tr>
  ${nonpass.map(c => rcRow(c.id, `<a href="#${anchor(c.id)}" onclick="openCase('${c.id}')" style="color:inherit">${esc(c.id)}</a> ${badge(c.verdict)}`, c.diagnosis || {})).join('')}
  ${fleetNP.map(x => rcRow(x.fid, `fleet ${esc(x.fid)}: ${esc(cops[x.cop].name)}`, x.v.diagnosis)).join('')}</table></div>`;

const ba = D.cases.filter(c => c.changed || (c.original && c.original.verdict !== 'PASS') || c.verdict !== 'PASS');
document.getElementById('beforeafter').innerHTML = `<tr><th>Case</th><th>Before the fixes</th><th>Now</th><th>Where the current result comes from</th><th>What changed it</th></tr>` +
  ba.map(c => `<tr><td><a href="#${anchor(c.id)}" onclick="openCase('${c.id}')" style="color:inherit">${esc(c.id)}</a></td>
  <td>${c.original ? badge(c.original.verdict) + ' <span class="lab">' + esc(c.original.label) + '</span>' : '–'}</td>
  <td>${badge(c.verdict)} <span class="lab">${esc(c.label)}</span></td><td>${rsBadge(c.result_source)}</td>
  <td>${c.changed ? esc(((c.diagnosis || {}).note || {}).fix || '') : '<span class="muted">unchanged: ' + esc(((c.diagnosis || {}).note || {}).remaining || 'no change expected') + '</span>'}</td></tr>`).join('') +
  D.fleet.map(f => `<tr><td>fleet ${esc(f.id)}</td><td>${f.original ? badge(f.original.verdict) : '–'}</td><td>${badge(f.verdict)}</td>
  <td><span class="badge src-live">LIVE (as recorded)</span></td><td class="muted">${f.verdict === 'PASS' ? '' : 'recorded before reply recording: cannot be replayed; needs a live re-run'}</td></tr>`).join('');

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
