# Classification & Tagging: connections

## Upstream

`specs.SPEC_CONSUMES`: Anomaly Detection and Agent Lifecycle Management (canonical, unchanged). On purvi's
instruction (2026-11-26) `specs.TOPOLOGY_CONSUMES` adds Policy Enforcement, Interaction Discovery and Resource
Management, so the case graph delivers their findings (D-CT9). No upstream finding indicates a family or changes
a confidence. Each is listed in CT-5 `basis.upstream` with present, label, finding id, verified, status,
lineage, used and counted_as.

| Producer | Reads | Effect |
|---|---|---|
| Policy Enforcement | verified finding | context only. Correlated: its metric_keys are this COP's four counted metrics. Its label is contrary evidence on whether the event is permitted, not on its kind |
| Interaction Discovery | ID-2 answer and RECORDED delegation links (as Resource Management RS-4) | CT-5 entity linkage: a subagent lane is linked to its parent only through a recorded link on an answered, FULLY_RECONSTRUCTED ID-2. Otherwise NOT_LINKED, a limitation; no discount |
| Anomaly Detection | verified finding above NORMAL | CT-4 upstream concern, never counted. Unverified (for example no model): listed as not used |
| Resource Management | verified finding above NORMAL | CT-4 upstream concern, never counted |
| Agent Lifecycle Management | verified finding | listed as context |

An unverified or non-substantive finding is "not used". `rules.independent_upstream`, plus a metric_keys
overlap, decides correlated or independent.

## Downstream

| Consumer | Reads | Effect today |
|---|---|---|
| Governance Intelligence | the verified finding (`specs.CONSUMES`), `classification_example` memory | a model COP; with no provider it stops `llm_unavailable`. The consumption record shows it received the finding |
| Incident Response | the verified finding (`rules.incident_response`, TOPOLOGY_CONSUMES) | listed in `upstream_used`; its rule reading is the same with and without this COP (scenario downstream_consumers) |
| Notified | `cop_specific.recipients` | Governance Intelligence when a family is classified |

## Verification

- The shared verifiers run unchanged.
- For a model reading:
  - DS-03 rejects a quiet label over an indicated family.
  - RP-01 compares the model's label with the precedence baseline.
  - SC-01 checks the claims.
- VS-01 builds its components from `specs.CONSUMES`, so the three added edges add three supporting upstream
  components. On every harness world the VS-01 decision was the same as with the canonical upstream only
  (`LEGACY_COMPARISON.json`).

## Tested

- `tests/test_cop_package_classification_tagging.py`.
- The harness.
- Evaluation case C08, which still reads INTEGRITY through the model.
