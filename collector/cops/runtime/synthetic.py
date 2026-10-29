"""
Synthetic, timestamped observables for the evaluation harness.

Everything here is SYNTHETIC and labelled so: the CaseData carries synthetic=True and
every artifact carries "synthetic": true. Rows are shaped like the
observables table (collector/observables_schema.sql) so they go through
signal_traces and signal_metrics exactly as collected rows do. No expected
label is ever an input: scenarios supply events and artifacts only.
"""

import hashlib, json

OPS = "dev1@tenant-a.example"
TENANT = "tenant-a"
NORMAL_TOOLS = ["Read", "Grep", "Read", "Edit", "Bash"]


def sha(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class Session:
    """Builds one session's rows with strictly increasing timestamps."""

    def __init__(self, sid, day, collectors=("local_hooks", "otel"), operator=OPS, tool="claude-code",
                 month="2026-10"):
        self.sid, self.day, self.collectors = sid, day, collectors
        self.operator, self.tool, self.month = operator, tool, month
        self.rows, self.n = [], 0

    def ts(self, minute=None, second=0):
        m = self.n if minute is None else minute
        return f"{self.month}-{self.day:02d}T10:{m % 60:02d}:{second:02d}Z"

    def add(self, **k):
        self.n += 1
        base = {"observable_id": f"{self.sid}-{self.n}", "session_id": self.sid, "tool": self.tool,
                "collector": self.collectors[0], "contributing": list(self.collectors),
                "operator_email": self.operator, "workspace": "/ws", "turn_id": f"{self.sid}-t1",
                "occurred_at": self.ts()}
        base.update(k)
        self.rows.append(base)
        return base

    def start(self, tokens=1000):
        self.add(observable_type="session_start")
        self.add(observable_type="user_prompt", total_tokens=tokens)
        return self

    def tools(self, names, **extra):
        for i, t in enumerate(names):
            self.add(observable_type="tool_request", tool_name=t, tool_use_id=f"{self.sid}-u{self.n}",
                     tool_arguments={"i": i, "n": self.n}, tool_duration_ms=200, success=True, **extra)
        return self

    def full_instrumentation(self):
        """Paths, a cross-checked event, the wrapper and the watcher, as in tests/test_cops.py."""
        if len(self.collectors) > 1:
            self.add(observable_type="tool_request", tool_name="Read", file_path="/ws/app.py",
                     tool_arguments={"p": "app.py"}, collectors_agree=True, outside_workspace=False)
            self.add(observable_type="session_start", collector="cli_wrapper")
            # the agent's edit, then the watcher seeing it two seconds later, so the
            # disk change is attributable (signal_traces matches by path and time)
            e = self.add(observable_type="tool_request", tool_name="Edit", file_path="/ws/app.py",
                         tool_arguments={"p": "app.py", "edit": 1}, success=True)
            self.add(observable_type="file_modify", collector="fs_watcher", file_path="/ws/app.py",
                     content_hash="h1", occurred_at=e["occurred_at"].replace(":00Z", ":02Z"))
        return self

    def end(self, minute=None):
        self.add(observable_type="session_end", occurred_at=self.ts(minute if minute is not None else 59, 59))
        return self.rows


def normal_session(sid, day, tokens=1000, operator=OPS, tools=None):
    return Session(sid, day, operator=operator).start(tokens).tools(tools or NORMAL_TOOLS) \
        .full_instrumentation().end()


def baseline_estate(n=7, operator=OPS):
    rows = []
    for d in range(1, n + 1):
        rows += normal_session(f"base{d}", d, operator=operator)
    return rows


# ---------------------------------------------------------------- artifacts (synthetic)

def policy_store(rules, version="pol-2026-10-01", approved=True, extra_versions=(), dependencies=None,
                 precedence=None, authorised=True):
    v = {"version": version, "effective": "2026-10-01", "rules": rules,
         "approved_by": "security-lead@tenant-a.example" if approved else None,
         "dependencies": dependencies or {}, "precedence": precedence}
    versions = list(extra_versions) + [v]
    return {"synthetic": True, "versions": versions,
            "authorisations": [{"version": x["version"]} for x in versions if authorised]}


def risk_register():
    return {"synthetic": True, "version": "risk-2026-09",
            "risks": [{"id": "R-secret-exfil", "impact": 3, "min_sensitivity_tier": 3,
                       "controls": [{"id": "C-block-write", "reversible": True,
                                     "prerequisite": {"collector": "local_hooks"}}]},
                      {"id": "R-general", "impact": 1, "min_sensitivity_tier": 0, "controls": []}],
            # Synthetic thresholds for the synthetic register. The runtime reads
            # these from the register; it has none of its own.
            "thresholds": {"EMERGING": 2, "MATERIAL": 6}}


def taxonomy():
    return {"synthetic": True, "approved": True, "version": "tax-2026-10",
            "families": [
                {"id": "BOUNDARY", "definition": "access to data or resources outside what the task needs"},
                {"id": "INTEGRITY", "definition": "credentials or secrets handled or exposed"},
                {"id": "OVERSIGHT", "definition": "sensitive action without the required human approval"},
                {"id": "OUTCOME", "definition": "work reverted, failed or left inconsistent"}]}


def recovery_record(checkpoint_id, content, checks_passed, checks_total, tamper=False):
    h = sha(content)
    return {"checkpoint_id": checkpoint_id, "checkpoint_content": content,
            "checkpoint_hash": ("0" * 40) if tamper else h, "restored_at": "2026-10-20T10:30:00Z",
            "post_restore_checks": [{"name": f"check{i}", "passed": i < checks_passed}
                                    for i in range(checks_total)], "synthetic": True}


def context_snapshots(constraints_initial, dropped_ids, compaction_at, initial_at):
    after = [c for c in constraints_initial if c["id"] not in dropped_ids]
    return [
        {"snapshot_id": "snap-initial", "at": initial_at, "kind": "initial",
         "instructions_version": "v1", "constraints": constraints_initial,
         "provenance": {"source": "session_start_context", "hash": sha(constraints_initial)}, "synthetic": True},
        {"snapshot_id": "snap-compaction-1", "at": compaction_at, "kind": "compaction",
         "instructions_version": "v1", "constraints": after,
         "provenance": {"source": "compaction_summary", "hash": sha(after)}, "synthetic": True},
    ]
