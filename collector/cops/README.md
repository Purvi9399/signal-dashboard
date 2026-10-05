# COPS

    observables ─► signal_traces.build ─► signal_metrics.py ─► metric_events ─┐
                   (paths, lanes,          (every metric,       (one row per   │
                    effects, approvals)     once, deterministic)  session×metric)│
                                                                             ▼
                          DashboardMetricReader.read_metrics ─► COP (spec + interpreter)
                          DashboardMetricReader.read_context ◄─ narrow, listed queries only
                                                                             │
                                                                     cop_decisions

Analysis is deterministic and happens once, in `signal_metrics.py`. A COP is a spec
(`specs.py`): a question, the metric ids it reads, the context it may ask for, what it
may do. One runner executes all of them. All 18 are specified; three are active.

    python3 signal_metrics.py --dry-run          compute, summarise, write nothing
    python3 signal_metrics.py                    write metric_events
    python3 -m cops.run                          active COPS over the latest 10 sessions
    python3 -m cops.run --local                  same, computing metrics in memory
    python3 -m cops.run --write                  also write cop_decisions
    python3 -m cops.run --llm                    LLM interpreter (COPS_MODEL, ANTHROPIC_API_KEY)
    python3 -m cops.run --activate interaction_discovery,policy_enforcement

States: NORMAL, WARNING, EXCEPTION, CRITICAL, UNKNOWN (could have happened, could not
see), NOT_APPLICABLE (the condition never arose). Thresholds live in metrics_config.json.
Mode is dry-run only; PAUSE/BLOCK are recorded as recommendations, never executed.
