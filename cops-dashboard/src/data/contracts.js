/* Data contracts for the COPS prototype.
 *
 * The four files in public/data are written by
 *   cd collector && python3 -m cops.dashboard_export --out ../cops-dashboard/public/data
 * and are the only data this prototype reads. Nothing here computes a result:
 * the loader checks the contract version and the fields each renderer needs,
 * and refuses to render a file that does not match.
 */

export const CONTRACTS = {
  network: "cops-network/1",
  decisions: "cops-decisions/1",
  retrieval: "cops-retrieval/1",
  activation: "cops-activation/1",
};

const REQUIRED = {
  network: ["cops", "metrics", "metric_edges", "cop_edges", "hierarchy", "relationship_rules", "versions", "meta"],
  decisions: ["decisions", "sessions", "coverage_states", "meta"],
  retrieval: ["data_access_audit", "query_types", "consumer_assessment", "meta"],
  activation: ["status", "states", "mapping", "registry", "scenarios", "meta"],
};

const DECISION_FIELDS = ["decision_id", "cop", "source", "execution_mode", "session_ids", "timestamp", "trigger",
  "finding", "evidence_coverage", "investigation", "determination", "action", "resolution_attempts"];

export const RELATIONSHIP_TYPES = ["observes", "interprets", "validates", "triggers", "acts"];
export const COP_EDGE_KINDS = ["consumes", "referral", "specialist_support", "declared_handoff"];

export function validate(name, obj) {
  const errs = [];
  if (!obj || obj.contract !== CONTRACTS[name])
    errs.push(`${name}.json: contract ${obj && obj.contract} is not ${CONTRACTS[name]}`);
  for (const k of REQUIRED[name] || []) if (!(k in (obj || {}))) errs.push(`${name}.json: missing ${k}`);
  if (name === "decisions" && obj && Array.isArray(obj.decisions))
    obj.decisions.forEach((d, i) => DECISION_FIELDS.forEach((k) => {
      if (!(k in d)) errs.push(`decisions[${i}] missing ${k}`);
    }));
  return errs;
}

async function fetchJson(name) {
  const r = await fetch(`${import.meta.env.BASE_URL}data/${name}.json`);
  if (!r.ok) throw new Error(`${name}.json: HTTP ${r.status}. Run the export first.`);
  return r.json();
}

export async function loadAll() {
  const [network, decisions, retrieval, activation] = await Promise.all(
    ["network", "decisions", "retrieval", "activation"].map(fetchJson));
  const errors = [...validate("network", network), ...validate("decisions", decisions),
    ...validate("retrieval", retrieval), ...validate("activation", activation)];
  return { network, decisions, retrieval, activation, errors };
}

/* A value in an export is one of: a plain value, {unavailable: why}, {undefined: missing contract}. */
export const isUnavailable = (v) => v && typeof v === "object" && !Array.isArray(v) && "unavailable" in v;
export const isUndefinedMarker = (v) => v && typeof v === "object" && !Array.isArray(v) && "undefined" in v;
