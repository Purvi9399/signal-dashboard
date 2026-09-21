#!/usr/bin/env python3
"""
Signal Collect — domain coverage per tool

For each of the ten behavioural domains, how many of its fields are actually
populated per tool. Reads the subset definitions from observables_subsets.sql
so the field lists cannot drift from the schema, then counts against captured
records.

    python3 build_domain_coverage.py              # the table
    python3 build_domain_coverage.py --fields     # which fields, per domain
    python3 build_domain_coverage.py --sql        # equivalent SQL for Supabase
    python3 build_domain_coverage.py --latex

A field counts as populated if it carries a value in at least one record for
that tool. This is a permissive test on purpose: it establishes availability,
not prevalence. A field present once and a field present in every record are
both reported as available here, and the distinction is made in the 35-field
evidence matrix, which applies applicability and replication tests.
"""

import argparse
import importlib.util
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".signal", "sessions"),
                "signal_sessions", "signal_sessions/cursor", "data", ".",
                os.path.join(HOME, "project-signal-dashboard", "signal_sessions")]

TOOLS = ["claude-code", "cursor", "codex", "copilot", "antigravity"]

# What counts as native is a question about the origin of the data, not about
# which of our collectors picked it up. A record is native when the vendor
# produced it about itself:
#
#   local hooks   the tool constructs a payload and hands it to us
#   OpenTelemetry the tool emits it
#   transcripts   the tool writes its own session record to disk; our file
#                 system watcher reads that file but does not author it
#   artifacts     same, for tools that write plans, recordings or outputs
#
# A record is not native when we produced it by observing:
#
#   CLI wrapper   bytes we intercepted on a pseudo-terminal
#   MCP proxy     traffic we intercepted on the JSON-RPC boundary
#   FS watcher    workspace changes we detected, which the tool did not report
#   derived       fields we computed during curation
#
# The distinction matters because the two are not the same claim. Excluding
# the watcher wholesale would discard the transcript, which is the vendor's
# own record and on some tools the only place tokens appear.
NATIVE_COLLECTORS = {"local_hooks", "otel"}
NATIVE_WATCHER_TYPES = {"transcript_appended", "artifact_written"}


def is_native(r):
    """Did the vendor produce this record about itself?"""
    contributing = set(r.get("contributing") or [])
    if r.get("collector"):
        contributing.add(r["collector"])
    if contributing & NATIVE_COLLECTORS:
        return True
    if ("fs_watcher" in contributing
            and r.get("observable_type") in NATIVE_WATCHER_TYPES):
        return True
    return False
LABEL = {"claude-code": "Claude Code", "cursor": "Cursor", "codex": "Codex",
         "copilot": "Copilot", "antigravity": "Antigravity"}

QUESTION = {
    "Agent Identity": "Who acted?",
    "Capability / Restrictions": "What was permitted, and what was refused?",
    "Task & Execution": "What was asked, and what happened?",
    "A2A Interaction": "How was work delegated and attributed?",
    "Performance Factors": "What temporal structure characterised execution?",
    "Tool Use": "What tools were invoked, with what inputs and results?",
    "Model Factors": "What model configuration and costs shaped execution?",
    "Runtime Factors": "Where did execution occur, and under what conditions?",
    "State Management": "What context and instructions did the agent carry?",
    "Data & Security": "What was touched, and at what exposure?",
}


def read_subsets():
    """Parse the subset views so field lists come from the schema itself."""
    path = os.path.join(HERE, "observables_subsets.sql")
    if not os.path.exists(path):
        sys.exit("observables_subsets.sql must sit beside this script")
    sql = open(path).read()
    subsets = []
    for m in re.finditer(
            r'-- (\d+)\. ([A-Z0-9 &/]+)\n.*?create or replace view (\w+) as\n'
            r'select\n(.*?)\nfrom observables', sql, re.S):
        num, title, view, body = m.groups()
        fields = []
        for line in body.split("\n"):
            s = line.strip()
            if not s or s.startswith("--"):
                continue
            name = s.split(",")[0].split(" ")[0].strip()
            if re.match(r"^[a-z_]+$", name) and name not in fields:
                fields.append(name)
        title = title.strip().title().replace("A2A", "A2A")
        subsets.append((int(num), title, view, fields))
    return sorted(subsets)


def load(dirs, native_only=False):
    """Curate from source rows, optionally restricting to native sources.

    Filtering must happen before curation, not after. A record assembled from
    a hook payload and a wrapper observation would otherwise count as native
    in full, including the fields only the wrapper supplied.
    """
    p = os.path.join(HERE, "signal_curate.py")
    if not os.path.exists(p):
        sys.exit("signal_curate.py must sit beside this script")
    spec = importlib.util.spec_from_file_location("sc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    loaded, _ = m.load_all(dirs)

    if native_only:
        keep = []
        for coll, f, raw in loaded:
            if coll in ("local_hooks", "otel"):
                keep.append((coll, f, raw))
            elif coll == "fs_watcher" and f.get("observable_type") in (
                    "transcript_appended", "artifact_written"):
                # the vendor wrote this file; we only read it
                keep.append((coll, f, raw))
        loaded = keep

    records, _c = m.curate(loaded)
    return records


def analyse(records, subsets):
    by_tool = defaultdict(list)
    for r in records:
        if r.get("tool") in TOOLS:
            by_tool[r["tool"]].append(r)

    result = {}
    for num, title, view, fields in subsets:
        per_tool = {}
        for tool in TOOLS:
            rows = by_tool.get(tool, [])
            present, counts = [], {}
            for f in fields:
                n = sum(1 for r in rows if r.get(f) not in (None, "", [], {}))
                if n:
                    present.append(f)
                    counts[f] = n
            per_tool[tool] = {"present": present, "counts": counts,
                              "total": len(fields), "records": len(rows)}
        result[title] = {"view": view, "fields": fields, "tools": per_tool}
    return result, by_tool


def print_table(res, by_tool, subsets, native_only=False):
    w = 14
    if native_only:
        print("\nField coverage from native vendor telemetry only\n")
        print("Restricted to local hooks and OpenTelemetry, the two channels "
              "the vendor provides.\nThe CLI wrapper, MCP proxy, file system "
              "watcher and derived fields are excluded.\n")
    else:
        print("\nField coverage by behavioural domain, all six collectors\n")
        print("How many of each domain's fields carry a value in captured "
              "records, per tool.\n")
    hdr = f"{'Domain':<28}{'Fields':>7}" + "".join(
        f"{LABEL[t][:12]:>{w}}" for t in TOOLS)
    print(hdr)
    print("-" * len(hdr))

    tot_fields = 0
    tot = {t: 0 for t in TOOLS}
    for num, title, view, fields in subsets:
        d = res[title]
        tot_fields += len(fields)
        line = f"{title:<28}{len(fields):>7}"
        for t in TOOLS:
            n = len(d["tools"][t]["present"])
            tot[t] += n
            line += f"{n:>8}/{len(fields):<{w-9}}"
        print(line)

    print("-" * len(hdr))
    line = f"{'Total field slots':<28}{tot_fields:>7}"
    for t in TOOLS:
        line += f"{tot[t]:>8}/{tot_fields:<{w-9}}"
    print(line)
    line = f"{'':<28}{'':>7}"
    for t in TOOLS:
        pct = 100 * tot[t] / tot_fields if tot_fields else 0
        line += f"{pct:>12.0f}% "
    print(line)

    print("\nField slots rather than distinct fields: a field appearing in "
          "three domains is\ncounted three times, because each domain poses "
          "its own question of the data.")

    print(f"\n\n{'Capture basis':<28}{'':>7}" +
          "".join(f"{LABEL[t][:12]:>{w}}" for t in TOOLS))
    print("-" * len(hdr))
    for label, fn in (("records", lambda rows: len(rows)),
                      ("sessions", lambda rows: len({r.get("session_id")
                                                     for r in rows
                                                     if r.get("session_id")})),
                      ("collectors", lambda rows: len({c for r in rows for c in
                                                       (r.get("contributing")
                                                        or [r.get("collector")])
                                                       if c}))):
        line = f"{label:<28}{'':>7}"
        for t in TOOLS:
            line += f"{fn(by_tool.get(t, [])):>{w}}"
        print(line)


CHANNELS = [
    ("local hooks",  "local_hooks",  None),
    ("OpenTelemetry", "otel",        None),
    ("transcript",   "fs_watcher",   "transcript_appended"),
    ("artifacts",    "fs_watcher",   "artifact_written"),
]
ADDED = [
    ("CLI wrapper",  "cli_wrapper",  None),
    ("MCP proxy",    "mcp_proxy",    None),
    ("FS watcher",   "fs_watcher",   "workspace"),
    ("derived",      "derived",      None),
]


def print_wide(res, subsets, native_only=False):
    """One row per tool, one column per domain, in the paper's shape."""
    order = [t for t in TOOLS]
    heads = [(title, len(fields)) for _, title, _, fields in subsets]
    total_fields = sum(n for _, n in heads)

    print("\n" + ("Native emission only" if native_only
                   else "All six collectors combined"))
    print()
    name_w = 14
    line = f"{'Agent':<{name_w}}"
    for title, n in heads:
        short = "".join(w[0] for w in title.replace("/", " ").split())[:4]
        line += f"{short + '/' + str(n):>10}"
    line += f"{'Total/' + str(total_fields):>12}"
    print(line)
    print("-" * len(line))
    for t in order:
        line = f"{LABEL[t]:<{name_w}}"
        tot = 0
        for title, n in heads:
            c = len(res[title]["tools"][t]["present"])
            tot += c
            line += f"{str(c) + '/' + str(n):>10}"
        line += f"{str(tot) + '/' + str(total_fields):>12}"
        print(line)
    print()
    print("  " + "   ".join(
        f"{''.join(w[0] for w in title.replace('/', ' ').split())[:4]}={title}"
        for title, _ in heads[:5]))
    print("  " + "   ".join(
        f"{''.join(w[0] for w in title.replace('/', ' ').split())[:4]}={title}"
        for title, _ in heads[5:]))


def print_channels(by_tool):
    """Which channels each tool actually produced, from the data."""
    w = 14
    print("\nChannels observed per tool\n")
    print("Native channels are those the vendor produces about itself. "
          "Added channels are\ninstrumentation we placed around the tool.\n")
    print(f"{'Channel':<18}{'':<8}" + "".join(f"{LABEL[t][:12]:>{w}}"
                                              for t in TOOLS))
    print("-" * (26 + w * len(TOOLS)))

    def count(rows, coll, otype):
        n = 0
        for r in rows:
            c = set(r.get("contributing") or [])
            if r.get("collector"):
                c.add(r["collector"])
            if coll not in c:
                continue
            if otype == "workspace":
                if r.get("observable_type") in NATIVE_WATCHER_TYPES:
                    continue
            elif otype and r.get("observable_type") != otype:
                continue
            n += 1
        return n

    for label, group in (("NATIVE", CHANNELS), ("ADDED", ADDED)):
        print(f"\n{label}")
        for name, coll, otype in group:
            line = f"  {name:<16}{'':<8}"
            for t in TOOLS:
                n = count(by_tool.get(t, []), coll, otype)
                line += f"{(str(n) if n else '.'):>{w}}"
            print(line)


def print_compare(nat, allc, subsets, nat_bt, all_bt):
    """Native emission against total recovery. The gap is the contribution of
    instrumentation the vendor did not provide."""
    w = 16
    print("\nNative vendor telemetry against all six collectors\n")
    print(f"{'Domain':<28}{'F':>4}" + "".join(f"{LABEL[t][:14]:>{w}}"
                                              for t in TOOLS))
    print("-" * (32 + w * len(TOOLS)))
    tot_f = 0
    tn = {t: 0 for t in TOOLS}
    ta = {t: 0 for t in TOOLS}
    for num, title, view, fields in subsets:
        tot_f += len(fields)
        line = f"{title:<28}{len(fields):>4}"
        for t in TOOLS:
            n = len(nat[title]["tools"][t]["present"])
            a_ = len(allc[title]["tools"][t]["present"])
            tn[t] += n
            ta[t] += a_
            line += f"{n:>7} -> {a_:<{w-11}}"
        print(line)
    print("-" * (32 + w * len(TOOLS)))
    line = f"{'Total field slots':<28}{tot_f:>4}"
    for t in TOOLS:
        line += f"{tn[t]:>7} -> {ta[t]:<{w-11}}"
    print(line)
    line = f"{'':<28}{'':>4}"
    for t in TOOLS:
        pn = 100 * tn[t] / tot_f if tot_f else 0
        pa = 100 * ta[t] / tot_f if tot_f else 0
        line += f"{pn:>6.0f}% ->{pa:>5.0f}%  "
    print(line)
    line = f"{'recovered by instrumentation':<28}{'':>4}"
    for t in TOOLS:
        line += f"{ta[t] - tn[t]:>{w}}"
    print(line)
    print("\nLeft of the arrow: fields the tool reports about itself, through "
          "local hooks\nand OpenTelemetry. Right: fields recovered once the "
          "CLI wrapper, MCP proxy,\nfile system watcher and derivation layer "
          "are added. The difference is what\nthe vendor does not tell you and "
          "an enterprise can still obtain.")


def print_fields(res, subsets):
    for num, title, view, fields in subsets:
        d = res[title]
        print(f"\n\n{title}   ({len(fields)} fields)   {QUESTION.get(title,'')}")
        print("-" * 104)
        print(f"{'field':<26}" + "".join(f"{LABEL[t][:11]:>15}" for t in TOOLS))
        for f in fields:
            line = f"{f:<26}"
            for t in TOOLS:
                n = d["tools"][t]["counts"].get(f, 0)
                line += f"{(str(n) if n else '.'):>15}"
            print(line)


def print_latex(res, subsets):
    print(r"\begin{table}[t]")
    print(r"\centering\small")
    print(r"\caption{Field coverage by behavioural domain. Each cell reports "
          r"the number of fields in that domain carrying a value in captured "
          r"records for that tool. Availability, not prevalence: a field "
          r"present in one record and one present in every record are both "
          r"counted. Domain totals sum to field slots rather than distinct "
          r"fields, since a field serving several domains is counted once per "
          r"domain.}")
    print(r"\label{tab:domain-coverage}")
    print(r"\begin{tabular}{lr" + "r" * len(TOOLS) + "}")
    print(r"\toprule")
    print(r"Domain & Fields & " + " & ".join(LABEL[t] for t in TOOLS) + r" \\")
    print(r"\midrule")
    tot_fields = 0
    tot = {t: 0 for t in TOOLS}
    for num, title, view, fields in subsets:
        d = res[title]
        tot_fields += len(fields)
        cells = []
        for t in TOOLS:
            n = len(d["tools"][t]["present"])
            tot[t] += n
            cells.append(str(n))
        print(f"{title} & {len(fields)} & " + " & ".join(cells) + r" \\")
    print(r"\midrule")
    print(r"\textbf{Total} & " + str(tot_fields) + " & " +
          " & ".join(str(tot[t]) for t in TOOLS) + r" \\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\end{table}")


def print_sql(subsets):
    """Equivalent query, so the table can be reproduced in Supabase."""
    print("-- Field coverage by behavioural domain, per tool.")
    print("-- Counts fields carrying a value in at least one record.\n")
    parts = []
    for num, title, view, fields in subsets:
        checks = " + ".join(
            f"(count({f}) > 0)::int" for f in fields)
        parts.append(
            f"  select '{title}' as domain, {len(fields)} as fields, tool,\n"
            f"         {checks} as populated\n"
            f"  from observables group by tool")
    print("with cov as (\n" + "\n  union all\n".join(parts) + "\n)")
    print("select domain, fields,")
    for t in TOOLS:
        print(f"  max(populated) filter (where tool = '{t}') as \"{LABEL[t]}\",")
    print("  0 as _")
    print("from cov group by domain, fields order by domain;")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--fields", action="store_true")
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--sql", action="store_true")
    ap.add_argument("--native", action="store_true",
                    help="restrict to vendor channels: hooks and OTel only")
    ap.add_argument("--wide", action="store_true",
                    help="one row per tool, one column per domain")
    ap.add_argument("--compare", action="store_true",
                    help="native and all-six side by side, with the gap")
    a = ap.parse_args()

    subsets = read_subsets()
    if a.sql:
        print_sql(subsets)
        return 0

    records = load(a.dirs or DEFAULT_DIRS, native_only=a.native)
    if not records:
        sys.exit("no captured data found")
    if a.compare:
        nat, nat_bt = analyse(load(a.dirs or DEFAULT_DIRS, native_only=True),
                              subsets)
        allc, all_bt = analyse(records, subsets)
        print_channels(all_bt)
        print()
        print_compare(nat, allc, subsets, nat_bt, all_bt)
        return 0

    res, by_tool = analyse(records, subsets)

    if a.wide:
        print_wide(res, subsets, a.native)
    elif a.latex:
        print_latex(res, subsets)
    elif a.fields:
        print_fields(res, subsets)
    else:
        print_table(res, by_tool, subsets, a.native)
    return 0


if __name__ == "__main__":
    sys.exit(main())
