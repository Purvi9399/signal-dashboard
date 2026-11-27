# Risk Mitigation: interpretation

Observation (`observe_risk_mitigation`) answers the four questions from the metrics, the register, the controls'
prerequisites, the upstream findings and memory. Interpretation (`decide_risk_mitigation`) turns the answers into
the label. Both are deterministic. No reading needs a model (D-RK9).

## Four kinds of risk, kept apart

| Kind | What it is | Where |
|---|---|---|
| Observed | a counted metric (`specs.metric_keys`) in EXCEPTION or CRITICAL; an irreversible operation (`tools.IRREVERSIBLE` on command) the collectors do not dispute | RK-1 `basis.observed`, `irreversible` |
| Inferred | a verified substantive upstream finding with a risk-bearing label (`METRICS.yaml upstream`) | RK-1 `basis.inferred`, each marked counted or correlated |
| Verified | the register band reached by independent verified signals | RK-2 `band`, `cop_specific.risk_status.verified` |
| Justified intervention | a register control whose risk applies and whose prerequisites are met, recommended to the owner; escalation to a person for MATERIAL | RK-4 `recommended`, `risk_status.intervention` |

Observed risk without an independent verified signal is not verified risk: a secret read alone scores impact x 0.
An anomaly, a new route or a policy concern is a signal only when it is verified, risk-bearing and independent; it
is never itself grounds for mitigation.

## RK-1

RISK_INDICATED when anything is observed or inferred. NO_RISK_INDICATED needs every counted metric judged and at
least one consumed finding verified. Otherwise INCONCLUSIVE (`MISSING_DATA`), naming the unobserved metrics or
the absence of verified upstream findings. Quiet readings (NORMAL, CONTAINED, ALLOWED, NO_INCIDENT) are listed in
`quiet_not_counted`; undetermined ones (INDETERMINATE, PATH_PARTIAL, UNCERTAIN) and unverified ones are
limitations.

## RK-2

The legacy formula: impact = the highest register impact among risks whose `min_sensitivity_tier` is at or below
the session's highest `sensitivity_tier`; score = impact x (independent risk-bearing signals + 1 if an undisputed
irreversible operation ran). MATERIAL_LEVEL at or above the MATERIAL threshold, EMERGING_LEVEL at or above
EMERGING, else BELOW_THRESHOLDS. Independence is `rules.independent_upstream` over this COP's counted metrics: a
finding whose lineage shares one of them, or shares a signal already counted, is correlated and not counted
(Interaction Discovery always records insights.outside; Incident Response and Resource Management usually record
insights.secrets or insights.token_outlier). No register, or no thresholds: INCONCLUSIVE (`MISSING_REFERENCE`).
The likelihood-context metrics are reported and never counted.

The same signals are what `tools.calculate_risk_components` returns (`cop_package.rk_signal_split`), so the
legacy rule, VS-01's recomputation (`support._risk_recomputation`) and the package agree (D-RK3).

Since revision 1 (A-RK1, U-RK3, D-RK16) independence also needs distinct source evidence. Each risk-bearing finding's
source items are read from its lineage metrics, its claims and its driver questions (`cop_package.rk_source_items`):
observable rows, metrics, a decision item (DENY or REQUIRE_APPROVAL) by its row or, without one, by its proposed
operation, and a quota exceeded. Tool-call ids and telemetry measures are not source items. A finding is counted
only if at least one of its items is not already carried by this COP's own observed evidence (the rows behind its
counted metrics in a deviation state and its undisputed irreversible operations) or by a signal already counted.
Producers are taken first (`specs.topological_order`). The lineage-only reading is recorded
(`basis.lineage_only_reading`, limitation LINEAGE_ONLY_READING_DIFFERS).

APPROVAL_REQUIRED (Policy Enforcement) is a pending authorization condition and WATCH (Incident Response) an
advisory condition (P-RK2, D-RK20): never counted or correlated, kept in RK-1 `basis.conditions` with their reasons
and the verified findings they cite, limitation CONDITION_NOT_COUNTED. Classification & Tagging is context only (U-RK1):
listed in RK-1 `basis.context`, never counted, correlated or scored.

The register must be approved and versioned (U-RK8, `tools.risk_register_problem`). An unapproved or unversioned
register is treated as unavailable: INCONCLUSIVE, status MISSING_DEPENDENCY, the reason named
(`risk_register.approval` or `risk_register.version`).

Impact comes from the evidence supporting the counted risk (P-RK1, D-RK19): the highest recorded sensitivity tier
among this COP's observed rows, its irreversible rows and the rows the counted signals cite. When a counted item has
no recorded tier and the recorded tiers do not reach the register's highest tier, impact is a range
(`basis.impact_range`, `score_range`): the same band at both ends is decided; a LOW lower end with a higher upper end
is INCONCLUSIVE; an EMERGING or MATERIAL lower end stands and the higher band is named as not excluded. The reading
at the session's highest tier is recorded (`basis.session_tier_reading`).

## RK-3

The exposed events (rows behind the observed metrics and the irreversible operations, disputed ones marked) and
findings (inferred, counted or not), with agent lane and resource. There is no asset inventory, so criticality and
ownership are not known (limitation). RK-3 never changes the label.

## RK-4

INCONCLUSIVE when RK-2 is; NOT_REQUIRED below the thresholds. Otherwise the register controls of the applicable
risks: CONTROL_AVAILABLE when at least one has its prerequisites met (recommended, not applied), else
NO_CONTROL_AVAILABLE (the decision is left to a person). Residual risk is NOT_ESTABLISHED: no mitigation
effectiveness is approved and no applied mitigation is recorded. Validated `mitigation_outcome` precedents are
listed for comparison.

## Label

RK-2's band, except that a LOW band with RK-1 INCONCLUSIVE is INCONCLUSIVE (the absence of risk is not shown). An
EMERGING or MATERIAL band stands on its own positive evidence. A missing register or thresholds keeps the legacy
status MISSING_DEPENDENCY. Confidence 0.75 (legacy, uncalibrated). Recipients: Incident Response for EMERGING and
MATERIAL, delivered on the previous-cycle feedback edge.
