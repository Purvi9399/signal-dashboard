"""
The decision table: interpretation beside evidence status, for every COP.

Interpretation answers the COP's behavioural question. Evidence status answers
how well it could answer it. They are orthogonal, which is why a COP can read
EXCESSIVE while its evidence is DEGRADED, and why Audit can read BROKEN_TRACE
while its own status is DECIDED: a broken trace is its answer, not its
obstacle.

    python3 demo_table.py                       all three scenarios
    python3 demo_table.py blocking_evidence     one
    python3 demo_table.py --markdown            paste into a doc

Runs the real interpreters where one exists. A COP with no interpreter is
marked rather than invented.
"""

import json, pathlib, sys

from .evidence import EvidenceAvailabilityService, DECIDED, DEGRADED, BLOCKED
from .authority import check as authority_check
from .decide import finalise
from .gap_registry import GapRegistry, learning_plan
from .harness import COPAgent, Registry
from .harness_configs import default_for

REGISTRY = Registry()        # no tools wired in the table view
from .cop_metrics import ASKS, NAMES, ORDER

STAGES = ("expected", "collector_alive", "emitted", "ingested",
          "normalised", "session_linked", "usable_by_metrics")

# Which metrics each COP asks about. Mirrors specs.metric_keys.
NAMES = {"runtime_monitoring": "Runtime Monitoring", "anomaly_detection": "Anomaly Detection",
         "resource_management": "Resource Management", "interaction_discovery": "Interaction Discovery",
         "policy_enforcement": "Policy Enforcement", "audit_traceability": "Audit & Traceability",
         "classification_tagging": "Classification", "incident_response": "Incident Response"}


def load_cops():
    """The real package if it is importable, otherwise run evidence-only."""
    try:
        from cops.specs import BY_KEY
        from cops.interpret import RuleInterpreter
        from cops.models import MetricEvent
        return BY_KEY, RuleInterpreter(), MetricEvent
    except Exception:
        try:
            from specs import BY_KEY
            from interpret import RuleInterpreter
            from models import MetricEvent
            return BY_KEY, RuleInterpreter(), MetricEvent
        except Exception:
            return None, None, None


def stages_failing_at(stage):
    out, broken = {}, False
    for s in STAGES:
        if broken:
            out[s] = None
        elif s == stage:
            out[s], broken = False, True
        else:
            out[s] = True
    return out


def run(path, as_markdown=False, registry=None):
    doc = json.loads(pathlib.Path(path).read_text())
    conditions = {tuple(k.split("|")): v for k, v in doc["evidence_conditions"].items()}
    rows_by_metric = {m["metric_id"]: m for m in doc["metric_events"]}

    probe = lambda src, f: (stages_failing_at(conditions[(src, f)])
                            if (src, f) in conditions else dict.fromkeys(STAGES, True))
    svc = EvidenceAvailabilityService(probe, lambda e: True)

    BY_KEY, interpreter, MetricEvent = load_cops()
    rows, gaps, decisions = [], [], {}

    for cop in ORDER:
        metrics = ASKS[cop]
        env = svc.compile(doc["session_id"], cop, metrics)
        gaps += list(env.gaps)
        v = env.for_cop()

        interp, conf, action, note = "—", None, "—", ""
        if BY_KEY and cop in BY_KEY:
            spec = BY_KEY[cop]
            events = [MetricEvent.from_row(rows_by_metric[m])
                      for m in metrics if m in rows_by_metric]
            # one path only: the harness. Calling the interpreter directly
            # from here is what let the two disagree.
            agent = COPAgent(spec, default_for(cop), interpreter, svc, REGISTRY,
                             finalise, authority_check, gap_registry=None)
            d = agent.run(doc["session_id"], events, upstream=decisions)
            if d["interpretation"] is None:
                interp = ("no interpreter" if d["stop_reason"] is None
                          else d["stop_reason"])
            else:
                interp = d["interpretation"]
                decisions[cop] = d
            conf = d["confidence"]
            action = d["recommended_action"] or "—"
            note = d.get("authority_note") or ""
        if conf is None:
            conf = round(env.confidence_factor, 2)

        rows.append({
            "key": cop, "note": (decisions.get(cop) or {}).get("action_reason", "") or note,
            "cop": NAMES[cop], "interpretation": interp,
            "status": env.decision_status, "confidence": conf,
            "missing": ", ".join(v["missing"]) or "—",
            "blocking": ", ".join(v["blocking"]),
            "action": action,
        })

    if registry is not None:
        blocked = {r["key"] for r in rows if r["status"] == BLOCKED}
        for g in gaps:
            registry.record([g], doc["session_id"],
                            "BLOCKED" if g["cop"] in blocked else "DEGRADED")

    _print(doc, rows, gaps, as_markdown)


def _print(doc, rows, gaps, md):
    head = f"{doc['scenario'].replace('_', ' ').upper()}  —  {doc['note']}"
    if md:
        print(f"\n**{head}**\n")
        print("| COP | Interpretation | Evidence status | Confidence | Missing evidence | Action |")
        print("|---|---|---|---:|---|---|")
        for r in rows:
            miss = r["missing"]
            if r["blocking"]:
                miss = f"**{r['blocking']}** (blocking)" + (
                    f", {r['missing'].replace(r['blocking'], '').strip(', ')}"
                    if r["missing"] != r["blocking"] else "")
            status = f"**`{r['status']}`**" if r["status"] == BLOCKED else f"`{r['status']}`"
            print(f"| {r['cop']} | `{r['interpretation']}` | {status} | "
                  f"{r['confidence']} | {miss} | {r['action']} |")
    else:
        print(f"\n{'=' * 112}\n{head}\n{'=' * 112}")
        print(f"{'COP':<23}{'interpretation':<22}{'evidence':<21}{'conf':>5}  "
              f"{'missing':<32}action")
        print("-" * 112)
        for r in rows:
            print(f"{r['cop']:<23}{r['interpretation']:<22}{r['status']:<21}"
                  f"{r['confidence']:>5}  {r["missing"][:30]:<32}{r['action'][:28]}")

    notes = [r for r in rows if r.get("note")]
    if notes:
        print("\nwhy those actions:")
        for r in notes:
            print(f"  {r['cop']}: {r['note']}")

    if gaps:
        seen = {}
        for g in gaps:
            seen.setdefault(g["recurrence_key"], g)
        print(f"\nevidence gaps: {len(gaps)} raised across the COPS, {len(seen)} distinct")
        for g in seen.values():
            print(f"  {g['observable']:<18}{g['gap_type']:<24}"
                  f"expected from {', '.join(g['expected_collectors'])}")
            print(f"    needed because: {g['reason_required']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    md = "--markdown" in sys.argv
    here = pathlib.Path(__file__).parent / "fixtures"
    paths = ([str(here / f"{a}.json") for a in args] if args
             else sorted(str(p) for p in here.glob("*.json")))
    reg = GapRegistry(str(pathlib.Path(__file__).parent / "fixtures" / "gap_registry.json"))
    for p in paths:
        run(p, md, registry=reg)

    rows = reg.report()
    for e in rows:
        if e["learning_signal"] or e["path"] == "type_2":
            plan = learning_plan(e)
            print(f"\n  {e['observable']} ({e['path'].replace('_', ' ')})")
            for k, v in plan.items():
                if k != "gap":
                    print(f"    {k:<14}{v}")
    reg.save()
