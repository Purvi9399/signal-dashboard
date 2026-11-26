# Classification & Tagging: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["classification_tagging"]`: writes
and reads `classification_example`, scope tenant (unchanged). Governance Intelligence also reads it.

## What it writes

`graph.validate_memory_update` writes one record for a substantive finding whose label is not UNCLASSIFIED (the
quiet label): VERIFIED when the finding verified, else UNVALIDATED; and an UNVALIDATED record for a rejected
model candidate (shared behaviour). `cop_specific.memory_payload` adds:

- `answers`: the five answers;
- `candidates` and `indicated`: the candidate and indicated families;
- `taxonomy_version`;
- `decided_by`: package or model;
- `entities`: the associated event refs;
- `precedents_read`: the validated records this decision listed.

UNCLASSIFIED and INCONCLUSIVE write nothing.

## What it reads

`assess_classification_questions` reads `classification_example` through `_memory_lookup`. VALIDATED records
are listed in CT-5 `basis.precedents` with `use: comparison only, never proof`. They change no answer, label,
confidence or candidate (scenario memory_precedent). The model can read validated examples through
`compare_validated_examples` (unchanged); its use of them is checked by the shared verifiers like any claim.

## Isolation

A record becomes VALIDATED only through `record_feedback` by an independent reviewer. This COP never validates
its own records and nothing it writes changes the taxonomy.
