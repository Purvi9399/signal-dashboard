# Context & Memory: interpretation

Context & Memory interprets nothing that a record says. Its label describes the retrieval, not the agent.

## One request

| Retrieval status | When | Label | CM-2 | CM-4 |
|---|---|---|---|---|
| RETRIEVED | relevant records returned; nothing withheld for provenance, integrity, entitlement or limit | MEMORY_RETURNED | RELEVANT_MEMORY_FOUND | COMPLETE |
| PARTIAL | records returned and something set apart (INSUFFICIENTLY_SUPPORTED, INSTRUCTION_SHAPED_CONTENT, NOT_ENTITLED, LIMIT_REACHED) | MEMORY_PARTIAL | RELEVANT_MEMORY_FOUND | PARTIAL |
| NO_MEMORY_IN_SCOPE | the store answered with nothing in scope | NO_RELEVANT_MEMORY | NO_MEMORY_IN_SCOPE | NOTHING_RETURNABLE |
| NO_RELEVANT_MEMORY | records in scope, none relevant to the request | NO_RELEVANT_MEMORY | NO_RELEVANT_MEMORY | NOTHING_RETURNABLE |
| ACCESS_DENIED | no asked type readable, or the gateway denied the read (cross-tenant) | MEMORY_UNAVAILABLE | ACCESS_DENIED | UNAVAILABLE |
| STORAGE_UNAVAILABLE | no gateway bound, or the store raised | MEMORY_UNAVAILABLE | STORAGE_UNAVAILABLE | UNAVAILABLE |
| INVALID_REQUEST | a required identifier is missing | MEMORY_UNAVAILABLE | INCONCLUSIVE | UNAVAILABLE |

Duplicates, expired, rejected, future, out-of-session and irrelevant records do not make a request PARTIAL: they
were not what was asked. CM-3 is PARTLY_ATTRIBUTABLE or NONE_ATTRIBUTABLE when provenance or integrity set records
apart, NOTHING_TO_VALIDATE when nothing was checked.

## The COP in a case

`rules.context_memory` runs `assess_context_memory_questions`: one request per current-cycle consumer
(`specs.consumers("context_memory")`, today Behavior Analytics), each under that consumer's own memory policy. The COP
label is the worst request label (`label_order`: MEMORY_UNAVAILABLE, MEMORY_PARTIAL, MEMORY_RETURNED,
NO_RELEVANT_MEMORY). Severity NONE and action NONE for every label. Confidence is 0.7, the previous rule's
CONTEXT_OK constant, uncalibrated (U-CM6). The shared precheck still withholds the quiet NO_RELEVANT_MEMORY when
Runtime Monitoring verifies telemetry INSUFFICIENT (D-CM16).

## Standing of a record

| Standing | From | Use |
|---|---|---|
| ESTABLISHED | VALIDATED with independent reviewer feedback, required provenance present | validated precedent; the conclusion stays the producing COP's |
| VERIFIED_NOT_VALIDATED | VERIFIED, provenance.verified not false | context, not precedent |
| UNVERIFIED_OBSERVATION | UNVALIDATED | never evidence |
| INSUFFICIENTLY_SUPPORTED | a required field missing, or a status its own provenance does not support | never evidence |

`validation_status` is always returned unchanged beside the standing. A payload field never changes either.

## What a requester may do with a record

- `may_corroborate` is true only for an ESTABLISHED record owned by another COP.
- A record the requester produced is OWN_EARLIER_OUTPUT: `independent_of_requester` false; it never verifies the
  requester's current finding.
- A similar event is not a verified precedent: matching is by named entities or by scope only (`matching_basis`).
- Records that disagree (one type, one session, different labels) are listed in `disagreements` unresolved. No
  label such as CONTRADICTORY is produced.
- Missing memory is never evidence that nothing happened (`absence_is_evidence` false, limitation
  ABSENCE_NOT_EVIDENCE).

## Operations

Every operation is deterministic (`METRICS.yaml operations`). No model is called: the COP stays in
`profiles.LLM_COPS` (the fixed allocation) and is decided without a model in every reading (U-CM5). Semantic
relevance is UNAVAILABLE.
