# Interaction Discovery: memory

Access goes through `MemoryGateway` (`cops/runtime/memory.py`) under `POLICIES["interaction_discovery"]`:
it writes and reads `route_record`, scoped to the agent.

## What it writes

`graph.validate_memory_update` writes one `route_record` for a finding that is not CONTAINED: VERIFIED when the
finding verified, else UNVALIDATED. Besides the common payload it carries `routes`: the session's interaction
edges (`a>b`, at most 20, as `interaction_edge_count` lists them), the new routes and `mcp:<server>` for each new
server. These come from `cop_specific.memory_payload`, the only COP-specific addition to the payload.

A CONTAINED finding writes nothing (the existing rule for every COP's quiet label), so the routes of an ordinary
session are known from the estate's history, not from memory.

## What it reads

`assess_interaction_questions` reads `route_record`. In ID-3:

- routes in a VALIDATED record are known, and a finding that relies on one cites it as a memory precedent, which
  SC-01 checks is still VALIDATED;
- routes in a VERIFIED record are listed as pending and still count as new;
- UNVALIDATED records are counted only.

Before this package the payload never held `routes`, so `compare_known_routes` read nothing from memory.

## Isolation and evolution

Tenant and agent scoping are the gateway's. A record becomes VALIDATED only through `record_feedback` by an
independent reviewer. Nothing here changes a threshold or a baseline.
