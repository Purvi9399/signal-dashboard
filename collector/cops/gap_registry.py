"""
Where missing evidence goes.

One blocked decision is a session problem. Forty decisions blocked on the same
field is a collection problem, and the difference is only visible if the gaps
are kept rather than lost inside a generic unknown.

The registry does three things and no more: keep each gap, count how often the
same one recurs, and classify why the evidence was missing. It never decides
whether anything was dangerous. That is a COP's work, and the whole point of
keeping the two apart is that the collection layer must not become a second
reasoning agent.

Classification follows the checkpoint, not a guess:

    COLLECTION_FAILURE          a source we expected did not deliver
    LINKAGE_FAILURE             it delivered, and it cannot be tied to anything
    COLLECTION_CAPABILITY_GAP   nobody ever designed to collect this
    NOT_EMITTED_BY_TOOL         this vendor does not produce it

The first two are type 1, something we built and it broke: the answer is a
fallback and a reliability record. The last two are type 2, a requirement we
never had: the answer is research, and a proposal a person approves.
"""

import json, pathlib
from collections import Counter

TYPE_1 = ("COLLECTION_FAILURE", "LINKAGE_FAILURE")
TYPE_2 = ("COLLECTION_CAPABILITY_GAP", "NOT_EMITTED_BY_TOOL")

RECURRENCE_THRESHOLD = 10          # below this it is a session; above, a requirement


class GapRegistry:
    def __init__(self, path="fixtures/gap_registry.json"):
        self.path = pathlib.Path(path)
        self.records = (json.loads(self.path.read_text())
                        if self.path.exists() else [])

    def record(self, gaps, session_id, decision_effect):
        for g in gaps:
            self.records.append({**g, "session_id": session_id,
                                 "decision_effect": decision_effect})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, indent=2))

    # ---- what the registry is for -------------------------------------
    def recurring(self):
        """Gaps grouped by what makes them the same gap again."""
        by_key = {}
        for r in self.records:
            k = r["recurrence_key"]
            e = by_key.setdefault(k, {
                "key": k, "observable": r["observable"],
                "gap_type": r["gap_type"], "expected": r["expected_collectors"],
                "reason": r["reason_required"], "count": 0,
                "sessions": set(), "cops": set(), "blocked": 0, "degraded": 0,
            })
            e["count"] += 1
            e["sessions"].add(r["session_id"])
            e["cops"].add(r["cop"])
            e[r.get("decision_effect", "degraded").lower()] = \
                e.get(r.get("decision_effect", "degraded").lower(), 0) + 1
        for e in by_key.values():
            e["sessions"] = len(e["sessions"])
            e["cops"] = sorted(e["cops"])
            e["learning_signal"] = e["count"] >= RECURRENCE_THRESHOLD
            e["path"] = "type_1" if e["gap_type"] in TYPE_1 else "type_2"
        return sorted(by_key.values(), key=lambda e: -e["count"])

    def report(self):
        rows = self.recurring()
        if not rows:
            print("\nno evidence gaps recorded")
            return rows
        print(f"\n{'=' * 96}\nGAP REGISTRY  —  {len(self.records)} gaps, "
              f"{len(rows)} distinct\n{'=' * 96}")
        print(f"{'observable':<18}{'why missing':<24}{'seen':>5}{'sess':>6}  "
              f"{'route':<9}next")
        print("-" * 96)
        for e in rows:
            route = "fallback" if e["path"] == "type_1" else "research"
            nxt = ("recurring: raise a collection requirement"
                   if e["learning_signal"] else
                   f"below {RECURRENCE_THRESHOLD}: treated as a session problem")
            print(f"{e['observable']:<18}{e['gap_type']:<24}{e['count']:>5}"
                  f"{e['sessions']:>6}  {route:<9}{nxt}")
            print(f"{'':18}needed by {', '.join(e['cops'])}")
        return rows


def learning_plan(entry):
    """What follows from a recurring gap. A proposal, never a change."""
    if entry["path"] == "type_1":
        return {
            "gap": entry["key"],
            "diagnosis": f"{entry['observable']} was expected from "
                         f"{', '.join(entry['expected'])} and did not arrive "
                         f"({entry['gap_type'].lower().replace('_', ' ')})",
            "immediate": "resolve from the next source in precedence; "
                         "record the primary's miss against its reliability",
            "if_recurring": "demote the primary only after a replacement has its "
                            "own checkpoint history",
            "approval": "demotion changes the precedence map and needs a person",
        }
    return {
        "gap": entry["key"],
        "diagnosis": f"no collector was designed to supply {entry['observable']}",
        "research": f"identify where {entry['observable']} could come from: hooks, "
                    f"transcripts, process state, proxy, vendor telemetry or API",
        "propose": "source, schema fields, normalisation mapping, collector change, tests",
        "then": "sandbox, checkpoint its reliability over a defined run, compare "
                "against the existing source",
        "approval": "a person promotes it into the precedence map; nothing "
                    "self-deploys",
    }
