# Anomaly Detection: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["anomaly_detection"]`: it writes
`anomaly_observation` and reads `anomaly_observation` and `behaviour_baseline`, scoped to the agent.

## What it writes

`graph.validate_memory_update` writes one `anomaly_observation` for a substantive finding that is not NORMAL
(VERIFIED when the finding verified, else UNVALIDATED), and an UNVALIDATED one for a rejected candidate. A verified
legitimate variation (NORMAL with AD-5 LEGITIMATE_VARIATION) is also written, VERIFIED (`remember_quiet`, D-AD20),
so that a reviewer can validate it. A package NORMAL and an INCONCLUSIVE finding write nothing. Besides the common payload, `cop_specific.memory_payload` adds:

- `deviating_metrics`: the measures in EXCEPTION or CRITICAL against a sufficient baseline;
- `answers`: the outcome of each of AD-1 to AD-5;
- `baselines`: each measure's baseline as `metric@version`, so a later case can tell whether a precedent was
  judged against the same comparison group.

## What it reads

`assess_anomaly_questions` reads `anomaly_observation` through `tools._memory_lookup`.

- A VALIDATED record is listed in AD-5's `basis.precedents` with the deviating metrics it shares with this
  session and `use: comparison only, never proof` (scenario memory_precedent_interpreted). The model sees it among
  the baseline tool results. It never changes an answer: AD-1 to AD-4 come from the metrics, AD-5 from them or the
  model's verified interpretation.
- A VALIDATED record labelled NORMAL that names a deviating measure is legitimacy context for that measure (AD-5
  `basis.context`, source validated_precedent). VS-01 and DS-03 re-check it from the memory view (scenarios
  validated_legitimate_precedent, unvalidated_legitimate_precedent, precedent_for_another_metric).
- VERIFIED and UNVALIDATED records are not precedent (the harness supplies one of each and checks they are not
  listed).
- `behaviour_baseline` is readable under the policy and not used by this package.

`rules._pattern_persistence` still reads this COP's own earlier verified finding from the feedback store (an
earlier cycle), unchanged.

## Isolation and evolution

Tenant and agent scoping are the gateway's. A record becomes VALIDATED only through `record_feedback` by an
independent reviewer (`memory.independent_reviewer`); this COP never validates its own records. A REFUTED review of
a verified finding rejects its record (scenario contradictory in `cops/runtime/scenario.py`). Nothing here changes a
baseline, a threshold or a label.
