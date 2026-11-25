# Project Signal — Vision & Architecture (Source of Truth)

> **Provenance.** Every statement in this document is derived **strictly from two team
> transcripts**: `Signal 17Nov` and `Signal 18Nov`. Nothing here is inferred from the code,
> the database, or the CSV. Where a point comes from a specific discussion it is tagged
> `[17Nov]` or `[18Nov]`. If a future decision contradicts this document, update this
> document — it is meant to be the project "bible."

---

## 1. What Project Signal is

Signal is an **agent governance tool**. It governs the behaviour of **coding agents** running
across coding tools. Governance is of two kinds `[17Nov]`:

- **Active governance** — looks at *agent → behaviour* (an individual agent's behaviour against
  a behavioural expectation).
- **Adaptive governance** — looks at *behaviour → agent* (collective behaviour mapped back to
  the agent). It is the reverse order.

The ambition stated by the team: *"You are not providing a governance system. You are
providing a system that **evolves** governance."* — a very adaptive governance that shapes
itself to tomorrow's problems, not just today's `[17Nov]`.

---

## 2. The strict dependency chain

Signal is built as a **one-directional dependency chain**. Each stage can only be derived from
the stage below it; you cannot create something at a stage the lower stage does not support
`[17Nov][18Nov]`:

```
collectors  →  collections (raw data)  →  observables  →  signals / metrics  →  COPs  →  actions
```

- **Collectors** — there are **6 collector mechanisms**. Collectors are **configurable only**
  (not evolvable). "Who collects it can only be configured." `[18Nov]`
- **Collections (raw data)** — what gets collected **can be evolved**, but see the hard rule in
  §3. `[18Nov]`
- **Observables** — the **10 tables**. Observables are a **derivative of the existing collection
  store only**. You cannot trigger more collection *from* the observables layer; if it isn't in
  the collection store, you cannot observe it. `[18Nov]`
- **Signals / metrics** — a signal is a **representative matrix / a number**. The number does
  **not** define right or wrong; it **requires interpretation**. You can only add a metric if the
  underlying signal/observable exists. `[17Nov][18Nov]`
- **COPs** — interpret the metric **in context** (see §6). The same metric can be interpreted
  differently by different COPs, and even by the same COP, depending on context. `[17Nov]`
- **Actions** — what a COP decides to do once it has interpreted the metric against a
  **threshold** (see §7).

**Consequence:** nothing can be changed randomly — a change ripples through the whole chain
and the database structure. "If the collector changes, the data changes; the data changes, the
COP's input changes; the whole thing changes." To change a metric you **start from
observables**, searching for unconnected pieces / additional connections that can feed it.
`[18Nov]`

---

## 3. The one hard rule: only behavioural questions trigger new collection

**Only a behavioural question may trigger new data collection. Nothing else.** `[18Nov]`

- Observables are tightly linked to existing data; you cannot request "additional data" from the
  observables, signals, or metrics layers directly.
- If everyone could request arbitrary new data "everybody will ask every shit and you cannot
  keep collecting everything." The behavioural-question gate enforces consistency. `[18Nov]`
- Therefore: to add data, a behavioural question must drive it, which flows down the chain to a
  change in the collectors.

---

## 4. Metrics: the gap is DATA, not FUNCTIONALITY

Critical framing for any metrics work `[17Nov]`:

- The design has **~200+ metrics**; historically only ~27 were actively used.
- The metrics not currently producing values are a **data-availability gap, not a functionality
  gap**. *"The metric will work if the cases run… whatever data is available, it will work."*
- A metric that expects e.g. 25 data fields **still computes from the 5 that are present**.
- To make a metric produce values you **run cases/scenarios that generate the telemetry** — or,
  for demo purposes, **create dummy data**: *"Even if we create dummy data for all the data, it
  will still show us."*

### 4.1 The reverse direction: create data *from* metrics
*"You are not creating additional metric from data — you are creating additional **data** from
metric. It is a reverse."* `[17Nov]`

The loop:
1. Start from the metric → discover the required data is **not available**.
2. Push that to a **collector agent** which works out **how the collector must be enhanced** and
   **where it will collect the field from** (define a path).
3. A **human validates before implementation** — collector additions must be human-checked
   because the *logic* of collection may be flawed. `[17Nov]`
4. Surfaced on a **dashboard**: data that was not collected + proposed collector fix. The admin
   clicks **Fix** (it gets fixed) or **Ignore** (it works with the data it already has). `[17Nov]`

### 4.2 Do it **post-hoc**, not on the spot
Fixing a collector is a major exercise — it also influences **permissions** and **policy**. Some
fields may not be trackable by any current collector and may need a **local-hook-style
implementation**. So this is handled **post-hoc**, never on the spot. "How many will you fix like
this?" `[17Nov]`

Example given: a `secrets` field (scan for PII / API keys) was referenced by an Anomaly
Detection sub-question but the data field does not yet exist — it would require a collector change
to capture. There could be 10+ such examples; they evolve unpredictably. `[17Nov]`

---

## 5. Sufficiency: three loops + minimum-sufficiency thresholds

A COP must know whether the information it has is **sufficient** to answer its behavioural
question. Sufficiency is judged against **minimum sufficiency, not maximum sufficiency** —
otherwise the loop never ends. The "target of comparison" is the minimum needed to get there.
`[17Nov]`

Three ways the need for more information arises `[17Nov]`:

1. **By design** — the information a COP has is insufficient to answer its behavioural question
   (an *illustrative* match, not a complete one). There must be a mechanism to detect this.
2. **By performance** — COPs repeatedly ask for additional information that isn't available.
   This must be **tracked as a trend over time** (not just a single action) — "what kind of
   information has been asked again and again."
3. **By intervention** — a human adds a new behavioural question, changes the metric↔COP
   mapping, or adds new data fields.

These three correspond to the **DPI** evolution model (§8): Design, Performance, Intervention.

---

## 6. Context: the Purpose / What / How taxonomy

A metric is just a number; its **interpretation depends on context**. Context is captured as a
**strict, self-evolving taxonomy** of three parts `[17Nov]`:

- **Purpose** — *why* they are doing it.
- **What** — *what* they are trying to do (e.g. an app, a functional integration).
- **How** — the *technology details* of how they are doing it.

Together these form a **unique context profile**. COPs use this profile to decide whether to
think differently about a metric when interpreting it.

---

## 7. Thresholds, actions, verifiers

### Thresholds
- Not static. A threshold is **relative to context and time**. `[17Nov]`
- **Time** → the threshold evolves based on observed averages, **but only within the same
  context** (not a generalized context). "Contextually looking at threshold experience." `[17Nov]`

### Actions
- COPs have a **variety of actions**. How actions are *delivered* is secondary; what matters is
  **how the action is decided and what must be performed**. `[17Nov]`
- Deciding + what-to-perform should be **generalized as instructions at the metric→COP
  relationship** ("these are the general actions if it is above your threshold"). The agent then
  **picks up the instructions and applies them as actions**, accounting for the subjective context
  and the evolving threshold. `[17Nov]`

### Verifiers (reliability layer)
A separate verifier layer gives ~**80–90% comfort** that COP performance is not misaligned
`[17Nov]`:
- **Process-based verifier** — what did the COP rely on, and is that a reliable basis?
- **Outcome-based verifier** — for this input, is this output reasonable / causally linked?
- Both contribute to a **collective reliability** score.

### Four COP outcome categories `[17Nov]`
1. COP performs completely, in a comfortable method.
2. COP performs **by asking for additional information** (always being triggered).
3. COP performs using additional information **plus past data / past trends**.
4. COP **cannot perform** → escalate to **human intervention**.

---

## 8. DPI — the evolutionary model for every "box"

Every box in the high-level map must have a way to **evolve**, along three axes **(DPI)**
`[17Nov]`:

- **D — Design** — e.g. a user adds a behavioural question (without specifying metrics; the
  system must then find/derive the metrics itself).
- **P — Performance** — the agent's own performance drives evolution (e.g. repeatedly needing
  information → tracked trend).
- **I — Intervention** — by a human **or by another agent** (one agent asking another for
  information means the *other* agent is the one missing it and must trigger the observable
  work).

**Scope of the project** is to **design how the application will evolve** — the conceptual flow
and its boundaries — **not to design the whole application**. "Any input that comes in, it
evolves." `[17Nov]`

---

## 9. Stakeholders & delivery

Results are represented to **three stakeholders** `[17Nov]`:
- **Human** — UI: alerts + a dashboard to intervene.
- **Agent** — COPs provide **inputs as actions** directly to the coding agent as it runs (the most
  efficient alignment path).
- **Platform** — subjective/less specified.

All of this is **cyclical**.

---

## 10. Cost, latency, and triage

- Target cost: **$1 per session**. `[17Nov]`
- Latency is a real concern. A full run of all 18 COPs on a session took **~7–8 minutes**; but at
  the **single-action / event level** many run in parallel (milliseconds), so **intervention is
  feasible**. `[17Nov]`
- Use a **triage mechanism** based on the agent's **estimated task time**. Only **long-horizon
  tasks** get full governance; **short-horizon tasks** (e.g. under ~2 minutes) are skipped — too
  little time to intervene. This is **runtime governance for runtime**. `[17Nov]`
- Risk-based triage was considered but rejected as too complex up front, because the concern is
  **agent performance, not the input**. Simple time-estimate triage is preferred. `[17Nov]`

---

## 11. The product UI (18 Nov)

### Pages / tabs
Each stage of the chain is a **tab** on a **configuration-and-evolution page** `[18Nov]`:
- **Configuration exists ONLY in collectors.** There is **no configuration** in behavioural
  questions, metrics, signals, or observables — those pages are **search + evolve** only. `[18Nov]`

### Search + Add, at every stage
Every stage page has two features `[18Nov]`:
1. **Search** — natural-language search over a **RAG** ("composite RAG") built from everything
   at that stage. It is a floating **AI chatbot** scoped to the **current page** by default, with an
   option to switch to **global (all pages)** search.
2. **Add** — a **retrieval process** across **two RAGs**: the *current-stage* RAG says whether the
   thing already exists; the *previous-stage* RAG says **what can be converted into it**. The user
   then gets an **actionable route** to add it. Adding = "unconnected pieces or additional
   connections."

### Behavioural-question page
- Do **not** dump details on the user. Show **~10–12 broad categories** of the behaviours being
  monitored; clicking one gives a **mind-map / drill-down** view. The real interaction is
  **search**, not drill-down. `[18Nov]`
- When a user asks for a **new behavioural question**, the backend runs the **whole chain**
  automatically — checks whether metrics / signals / observables / collections exist for it, and if
  viable presents "this is possible, would you like to add it." Saying **yes changes the code of
  the collectors**. `[18Nov]`
- Out-of-scope questions: classify against generic quadrants (is it behavioural? is it contextual
  to the app? is it relevant to agent governance?). If not, stop gracefully: "This doesn't seem to
  be configurable within this environment." This is accepted as a **~5% error problem** — the
  feature is only **~5% of user time**; the existing brief covers ~90–95%. `[18Nov]`

### Branches (multi-tenant evolution)
Collector code is organized as **branches** `[18Nov]`:
- **Common code** — handled by all; a change to common code **propagates down** to every
  branch.
- **Special code** — driven by the entity using it; changes in a branch **do not** propagate up.
- Hierarchy: **Signal (main) → Enterprise A / Enterprise B** branches. Only **administrators**
  make changes; an admin's change affects **only their environment**, while Signal's change
  affects all. `[18Nov]`

---

## 12. The build plan: 1 chatbot + 3 skill-file pipelines

The near-term implementation reduces to **one chatbot and three pipelines (skill files)**
`[18Nov]`:

- **Pipeline 1 — Search.** Search current-stage data, previous-stage data, and all data. The
  *travel/search* part is a **chatbot**; the *action* part is a **skill file**.
- **Pipeline 2 — Auto-link on add.** When something is added, **automate the manual work** of
  linking a metric→COP and the subsequent steps (threshold, how the COP interprets it, what
  action it takes) and writing it to the database. A **skill file** that executes the steps in order.
- **Pipeline 3 — Behavioural question → nearest COP.** Given a new behavioural question, search
  everything, gather/search previous signals, go **up to changing the collectors**, and **link it to
  the nearest COP**. A **pipeline / skill file**.

When something is added via these pipelines, you must still check **reliability** (verifiers +
reliability quotient) as a follow-through. `[18Nov]`

### Demo priority (explicit)
1. **Fix the workflow** and make it smooth.
2. Generate **enough sample data** so the flow runs (simulate ~**20 agents** that have been
   running). `[18Nov]`
3. Build the **dashboard** with: a **configuration tab**, a **dashboard** of all the pieces, a
   **performance view** (session-level stats of what the COPs have done), and a **human-
   intervention** view. `[18Nov]`

**Go high-level first for the media board, then drill down only where required.** Do **not** get
lost in detail; the evolutionary mechanisms give *direction* and are **not** all priority to
implement now. Estimated ~**1.5–2 weeks** to pull the demo together. `[17Nov][18Nov]`

---

## 13. Immediate working context (how this maps to current tasks)

- We are generating **scenario/task prompts to be run by real coding-agent sessions** (across the
  5 coding tools) so that agents exhibit the behaviours our metrics measure → collectors capture
  telemetry → observables populate → **gap metrics finally compute and land in the database.**
- This is exactly the "**run cases so the metrics work**" path from §4 — closing the
  **data-availability** gap, not a functionality gap.
