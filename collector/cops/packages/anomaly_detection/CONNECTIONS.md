# Anomaly Detection: connections

## Upstream

Read only when verified and substantive (`rules.verified`).

| Producer | Reads | Effect |
|---|---|---|
| Runtime Monitoring | label | INSUFFICIENT makes AD-5 UNDETERMINED when no question deviates, so INDETERMINATE (scenario unreliable_telemetry); its evidence level scales the package's confidence; DS-01 still rejects a quiet label on insufficient telemetry |
| Behaviour Analytics | LOOPING or POOR_RECOVERY | recorded in AD-1 `basis.upstream` with whether its lineage is independent of this COP's metrics (`rules.independent_upstream`); it never makes AD-1 deviate by itself (scenario behaviour_analytics_correlated) |

The legacy threshold reading (`cops/interpret.py:anomaly_detection`) still reads both, unchanged, as the recorded
baseline beside a model finding.

## Downstream

| Consumer | Reads | Effect |
|---|---|---|
| Incident Response | the verified label (`rules.incident_response`, recorded in its `upstream_used`) | its rule reading is NO_INCIDENT on a package NORMAL, WATCH on a verified ANOMALOUS and WATCH on INDETERMINATE (harness check incident_response_reads_ad5: AD-5's answer, the label it gives and the label Incident Response consumed, for the normal, interpreted and undetermined worlds) |
| Risk Mitigation | the verified finding and its lineage | an independent signal when lineage does not overlap Risk's own metrics (`insights.token_outlier` overlaps; verifier DS-02) |
| Classification & Tagging, Policy Enforcement | listed in `specs.CONSUMES` | Policy Enforcement does not read it (its CONNECTIONS.md); unusual is never a violation |
| Behaviour Analytics | this COP's earlier verified finding | `rules._pattern_persistence`, unchanged |
| Verifiers | `cop_specific.questions` is not read by any verifier | DS-03 compares a model label with the threshold reading; VS-01 reads the metric states (INTERPRETATION.md, Verification boundary) |

Recipients: a verified ANOMALOUS notifies Incident Response and Risk Mitigation (`cop_specific.recipients`).

## Tested

`tests/test_cop_package_anomaly_detection.py` and the harness (scenarios downstream_incident_response,
unreliable_telemetry, behaviour_analytics_correlated, simultaneous_findings_interpreted). Evaluation cases C02,
C03, C13, C14, C16, E2E and the bench keep their Anomaly Detection expectations; the bench's
`anomaly_detection:missing` case is now INDETERMINATE (accepted by the case, D-AD1).
