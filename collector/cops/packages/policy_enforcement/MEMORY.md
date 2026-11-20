# Policy Enforcement: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["policy_enforcement"]`: it writes
`enforcement_record` and reads `enforcement_record` and `route_record`, scoped to the agent.

## What it writes

`graph.validate_memory_update` writes one `enforcement_record` for a substantive finding that is not ALLOWED
(VERIFIED when the finding verified, else UNVALIDATED), and an UNVALIDATED one for a rejected candidate. Besides
the common payload, `cop_specific.memory_payload` adds:

- `policy_version`: the version the operations were evaluated against;
- `decisions`: up to 20 denied, proposed-denied, approval-needed and no-rule operations (action, resource,
  decision, rules);
- `routes`: the new routes PE-4 evaluated;
- `precedents_read`: the validated records this decision listed.

ALLOWED and INCONCLUSIVE write nothing.

## What it reads

`assess_policy_questions` reads both record types through `_memory_lookup`.

- `enforcement_record` (PE-5): a VALIDATED record whose decisions name an operation of this session is listed as
  a precedent, SAME_POLICY when it was decided under the version now in force, SUPERSEDED otherwise. A superseded
  precedent is contrary evidence. A precedent never permits or denies anything: the policy in force decides
  (scenario policy_changed).
- `route_record` (PE-4): a route in a VALIDATED record is noted as `established_in_memory`. Established is not
  permitted; the route is still evaluated.
- VERIFIED and UNVALIDATED records are not precedent.

## Isolation and evolution

Tenant and agent scoping are the gateway's. A record becomes VALIDATED only through `record_feedback` by an
independent reviewer (`memory.independent_reviewer`); this COP never validates its own records. Nothing here
changes a policy, a rule or a threshold.
