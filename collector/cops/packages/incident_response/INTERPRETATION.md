# Incident Response: interpretation

Observation (`cop_package.observe_incident_response`) answers the five questions from the metrics, the exposure
check (`tools.t_check_exposure`), the verified upstream findings and validated memory. Interpretation
(`decide_incident_response`) turns the answers into the label, severity, action and confidence. Both are
deterministic. The model is consulted for one reading only (D-IR11, U-IR1).

## IR-1: grounds, counted once

The grounds are taken in a fixed order:

1. This COP's own exposure: insights.secrets or insights.sensitive_unasked in EXCEPTION or CRITICAL, on rows the
   collectors do not dispute.
2. The deny rule: insights.blocked in a deviation state.
3. Each verified upstream ground, with producers in `specs.topological_order`.

Each ground carries its source items (`rk_source_items`): rows, metrics, decision items and quota records. A
ground whose items are all carried by earlier grounds is correlated. It is listed (`basis.correlated`, with the
CORRELATED_GROUND limitation), but it is not a second incident. A case has at most one incident
(`basis.incidents`).

The answers:
- INCIDENT_INDICATED: any incident ground (exposure, violation, failed or irreversible recovery).
- FINDING_WITHOUT_INCIDENT: only finding grounds (an anomaly, a deny rule fired, a pending authorization, a
  partial recovery).
- NO_INCIDENT_INDICATED: no ground, every required metric judged, at least one consumed finding verified,
  Runtime Monitoring not INSUFFICIENT, no unverified incident-relevant label, and no disputed exposure.
- INCONCLUSIVE otherwise, with each reason.

Context producers (Interaction Discovery, Workflow Orchestration, Resource Management, Classification & Tagging)
are listed in `basis.context` and never ground anything. A new route or a token spike alone reads NO_INCIDENT.

## IR-2: severity and scope

Severity:
- HIGH: a violation, a failed or irreversible recovery, or an exposure together with a verified anomaly that has
  a source item of its own.
- MEDIUM: an exposure, or an anomaly that Anomaly Detection alerts on.
- LOW: other findings.

Scope is reported as entities, agents (lanes), resources (paths, MCP servers, commands, proposed operations) and
sensitivity tiers. Impact stays NOT_CALCULATED because no asset inventory exists.

## IR-3: state

- ONGOING: a denied operation still pending (Policy Enforcement's own proposal is BLOCK, read from the published
  `proposed_action.requested_action`, D-IR19), a failed recovery, or outbound calls after a secret.
- CONTAINED: a deny rule stopped an operation and nothing was exposed.
- RESOLVED: never, because no resolution record exists.
- INCONCLUSIVE ("still uncertain") for an incident with none of these, with why.

## IR-4: intervention

INTERVENTION_JUSTIFIED needs an intervention ground (a pending block, a failed recovery, or an exposure with an
independent anomaly) and an IR-3 answer other than CONTAINED. Every intervention ground is a verified upstream
finding. Other incidents are ATTENTION_JUSTIFIED. When there is no incident, the answer is NOT_JUSTIFIED. IR-4
also records evidence quality (Runtime Monitoring, the five telemetry measures) and the confidence: partial
evidence reduces certainty and does not erase a ground.

## IR-5: response

IR-5 answers in this order of precedence:
1. REQUEST_APPROVAL for a pending block. The containment is "block after a person approves; nothing is blocked
   before".
2. ESCALATE_TO_PERSON for the other intervention grounds.
3. ALERT for attention incidents and for MEDIUM findings.
4. WATCH for LOW findings.
5. NO_RESPONSE.

Validated precedents are listed with their recurrence, and they have no effect. `executed` is always empty.

## Where the model comes in

| Reading | Who decides | Model |
|---|---|---|
| NO_INCIDENT, WATCH, INCONCLUSIVE (IR-1), attention INCIDENT | the package (`stop_reason` DECIDED_WITHOUT_MODEL) | not called |
| INCIDENT with IR-4 INTERVENTION_JUSTIFIED | the package decides label, severity and response; the model scopes the incident and drafts the containment plan | called (scripted in tests and the harness, simulated) |

After verification, `interpreted_incident_response` (`graph.INTERPRETED`) reads the model's conclusion back:

| Model conclusion | Result |
|---|---|
| INCIDENT, verified, with the package's action | accepted. The plan and scope go to IR-5 `basis`. Severity is the package's; confidence is the lower of the model's and the package's |
| INCIDENT with a different action (e.g. BLOCK) | accepted, with the action replaced by the package's. MODEL_ACTION_REPLACED is recorded |
| any other label, rejected by a verifier, or no model | INCONCLUSIVE. The package's answers are kept, the model's label goes to `rejected_candidate`, the category is INCIDENT_UNCONFIRMED, the action is WATCH, and CONTAINMENT_PLAN_NOT_CONFIRMED is recorded |

The model can never produce NO_INCIDENT, a lower severity, or a stronger action than the package's.

## Legacy rule

The legacy reading (`cops/interpret.py`, `rules.incident_response`) is computed first and kept in
`cop_specific.legacy_reading`. When it reads differently, a PREVIOUS_READING_DIFFERS limitation names both readings.
