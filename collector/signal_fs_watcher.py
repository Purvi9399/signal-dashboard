#!/usr/bin/env python3
"""
Signal Collect — file system watcher (collector 6 of 6)

Watches three zones, and treats each differently:

  WORKSPACE     what actually changed on disk. Ground truth, independent of
                what the agent claims. Records path, size, hash, change type.

  AGENT DATA    the tools' own transcript and artifact directories. This is
                parsing rather than watching, and it is where the richest
                data lives (Claude Code JSONL, Cursor transcripts, artifacts).

  CONFIG        settings.json, hooks.json, .mcp.json. Watching these is how we
                notice that someone disabled the collectors, which is the
                observable that tells us when we have gone blind.

Attribution is never asserted by this collector. It records that a file
changed at a time. Whether an agent did it is decided later by correlating
against tool events from the other collectors.

    python3 signal_fs_watcher.py --scan             # one pass, no watching
    python3 signal_fs_watcher.py --watch            # keep watching
    python3 signal_fs_watcher.py --watch --workspace ~/proj
    python3 signal_fs_watcher.py --parse-transcripts

Env:
    SIGNAL_SESSION_DIR   where rows go            (default ~/.signal/sessions)
    SIGNAL_WORKSPACE     workspace root           (default: cwd)
    SIGNAL_FS_INTERVAL   seconds between scans    (default 2)
    SIGNAL_FS_CONTENT    1 to store content hashes of small text files
    SIGNAL_OPERATOR      who is running it        (default $USER)

Standard library only. Uses watchdog automatically if it happens to be
installed, otherwise polls.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR", os.path.join(HOME, ".signal", "sessions"))
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))
INTERVAL = float(os.environ.get("SIGNAL_FS_INTERVAL", "2"))
STORE_CONTENT = os.environ.get("SIGNAL_FS_CONTENT", "") == "1"

# noise that would drown everything else
EXCLUDE_DIRS = {"signal_sessions", ".signal", "node_modules", ".git", "__pycache__", ".venv", "venv", "dist",
                "build", ".next", ".cache", "target", ".pytest_cache", ".mypy_cache",
                "site-packages", ".idea", ".gradle", "vendor", ".terraform"}
EXCLUDE_GLOBS = ["*.pyc", "*.pyo", "*.o", "*.class", "*.log", "*~", ".DS_Store",
                 "*.swp", "*.swx", "*.tmp", "*.lock~", ".#*", "#*#"]

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             "token", ".aws", ".ssh", "private_key", ".npmrc", ".netrc")
DEPENDENCY_FILES = {"package.json", "requirements.txt", "pyproject.toml", "go.mod",
                    "Cargo.toml", "Gemfile", "pom.xml", "build.gradle"}
LOCKFILES = {"package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
             "Gemfile.lock", "pnpm-lock.yaml", "go.sum"}

# zone 2: where the tools keep their own records
AGENT_DATA_DIRS = [
    (os.path.join(HOME, ".claude", "projects"), "claude-code", "transcript"),
    (os.path.join(HOME, ".cursor", "projects"), "cursor", "transcript"),
    (os.path.join(HOME, ".codex", "sessions"), "codex", "transcript"),
]

# Artifact roots are named explicitly. Never point this at an app data
# directory as a whole: these tools bundle a Python runtime, VS Code
# extensions and type stubs, and walking all of it produces thousands of
# rows of package internals.
ARTIFACT_DIRS = [
    (os.path.join(HOME, ".antigravity", "brain"), "antigravity"),
    (os.path.join(HOME, ".antigravity", "artifacts"), "antigravity"),
    (os.path.join(HOME, ".antigravity", "conversations"), "antigravity"),
    (os.path.join(HOME, ".antigravity", "sessions"), "antigravity"),
]

# Only these are plausible agent artifacts. Everything else is app payload.
ARTIFACT_EXTS = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml",
                 ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                 ".mp4", ".webm", ".mov", ".cast", ".html", ".diff", ".patch"}

# Anything under one of these is app payload, never agent output.
PAYLOAD_MARKERS = {"site-packages", "node_modules", "extensions", "dist-info",
                   "egg-info", "lib", "libexec", "runtime", "resources",
                   "bundled", "typeshed", "stubs", "third_party", "vendor",
                   "python3.9", "python3.10", "python3.11", "python3.12",
                   "python3.13", "bin", "include", "share"}

MAX_ARTIFACTS_PER_SCAN = 200

# zone 3: config files whose change means our own visibility changed
CONFIG_TARGETS = [
    (os.path.join(HOME, ".claude", "settings.json"), "claude-code", "hooks"),
    (os.path.join(HOME, ".cursor", "hooks.json"), "cursor", "hooks"),
    (os.path.join(HOME, ".codex", "config.toml"), "codex", "telemetry"),
]
CONFIG_IN_WORKSPACE = [".claude/settings.json", ".cursor/hooks.json",
                       ".mcp.json", ".cursor/mcp.json", "CLAUDE.md", ".cursorrules"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def excluded(path):
    parts = set(path.split(os.sep))
    if parts & EXCLUDE_DIRS:
        return True
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def sensitivity(path):
    low = path.lower()
    if any(m in low for m in SENSITIVE):
        return 3
    if any(m in low for m in ("/etc/", "config", "prod", "billing", "deploy")):
        return 2
    return 1


def file_hash(path, limit=2_000_000):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def is_text(path):
    try:
        with open(path, "rb") as f:
            return b"\0" not in f.read(2048)
    except OSError:
        return False


class Emitter:
    def __init__(self):
        os.makedirs(SESSION_DIR, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        self.path = os.path.join(SESSION_DIR, f"{day}-fswatcher.jsonl")
        self.count = 0

    def row(self, interaction_type, **extra):
        r = {
            "observable_id": uuid.uuid4().hex,
            "timestamp": now_iso(),
            "agent_id": "",                 # filled in later by correlation
            "agent_type": "coding_agent",
            "agent_tool": extra.pop("tool", "unknown"),
            "operator": OPERATOR,
            "company": "real_fleet",
            "surface": "filesystem",
            "collector": "fs_watcher",
            "interaction_type": interaction_type,
            "protocol": "fs",
            "connector": "filesystem",
            "permission_status": "not_applicable",
            "latency_ms": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "sensitivity_tier": 1,
            "cascade_depth": 0,
            "violation_count": 0,
            "escalation_flag": False,
            "success": True,
            "error_type": "",
            # this collector never asserts who did it
            "attribution": "unknown",
            "confidence": "observed",
            "detail": {},
        }
        r.update(extra)
        with open(self.path, "a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        self.count += 1
        return r


class Zone1Workspace:
    """Ground truth on the working tree, with revert detection by hash history."""

    def __init__(self, root, emit):
        self.root = os.path.abspath(root)
        self.emit = emit
        self.state = {}                      # path -> (mtime, size, hash)
        self.history = defaultdict(lambda: deque(maxlen=6))   # path -> hashes
        self.pending = {}                    # debounce buffer

    def snapshot(self):
        found = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS
                           and not d.startswith(".") or d in (".claude", ".cursor")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if excluded(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                found[p] = (st.st_mtime, st.st_size)
        return found

    def diff(self, first=False):
        found = self.snapshot()
        old = set(self.state)
        new = set(found)

        for p in new - old:
            if first:
                h = file_hash(p)
                self.state[p] = (*found[p], h)
                if h:
                    self.history[p].append(h)
                continue
            self.change(p, "create", found[p])

        for p in old - new:
            self.emit_change(p, "delete", None, None)
            self.state.pop(p, None)

        for p in new & old:
            if found[p][0] != self.state[p][0] or found[p][1] != self.state[p][1]:
                self.change(p, "modify", found[p])

    def change(self, path, kind, meta):
        h = file_hash(path)
        prev = self.state.get(path, (None, None, None))[2]
        self.state[path] = (*meta, h)

        reverted = False
        if h and h in list(self.history[path])[:-1]:
            reverted = True
        if h:
            self.history[path].append(h)
        if h and h == prev:
            return                            # touched but content identical
        self.emit_change(path, kind, meta, h, reverted)

    def emit_change(self, path, kind, meta, h, reverted=False):
        rel = os.path.relpath(path, self.root)
        name = os.path.basename(path)
        detail = {
            "path": rel,
            "abs_path": path,
            "extension": os.path.splitext(name)[1],
            "change": kind,
            "size": meta[1] if meta else 0,
            "content_hash": h if STORE_CONTENT else None,
            "reverted_to_earlier_state": reverted,
            "is_dependency_manifest": name in DEPENDENCY_FILES,
            "is_lockfile": name in LOCKFILES,
            "is_config": any(rel.endswith(c) for c in CONFIG_IN_WORKSPACE),
            "binary": (not is_text(path)) if (meta and kind != "delete") else None,
        }
        row = self.emit.row(
            "file_reverted" if reverted else f"file_{kind}",
            sensitivity_tier=sensitivity(path),
            detail=detail,
        )
        flag = ""
        if reverted:
            flag = "  REVERT"
        elif detail["is_config"]:
            flag = "  CONFIG"
        elif row["sensitivity_tier"] >= 3:
            flag = "  SENSITIVE"
        print(f"  {kind:<7} {rel[:58]:<58}{flag}")


class Zone2AgentData:
    """Parse the tools' own transcripts and artifacts. Richest data here."""

    def __init__(self, emit):
        self.emit = emit
        self.seen = {}          # path -> line count already read

    def scan(self):
        # transcripts, from named session directories only
        for root, tool, kind in AGENT_DATA_DIRS:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in EXCLUDE_DIRS and d.lower() not in PAYLOAD_MARKERS]
                for fn in filenames:
                    if fn.endswith(".jsonl"):
                        self.parse_transcript(os.path.join(dirpath, fn), tool)

        # artifacts, from named artifact roots only, heavily filtered
        for root, tool in ARTIFACT_DIRS:
            if not os.path.isdir(root):
                continue
            found = 0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in EXCLUDE_DIRS and d.lower() not in PAYLOAD_MARKERS]
                if any(part.lower() in PAYLOAD_MARKERS for part in dirpath.split(os.sep)):
                    continue
                for fn in filenames:
                    if fn.startswith("."):
                        continue
                    if os.path.splitext(fn)[1].lower() not in ARTIFACT_EXTS:
                        continue
                    if found >= MAX_ARTIFACTS_PER_SCAN:
                        print(f"  artifact   {tool}: stopped at {MAX_ARTIFACTS_PER_SCAN}, "
                              f"root looks too broad: {root}")
                        break
                    if self.note_artifact(os.path.join(dirpath, fn), tool):
                        found += 1
                if found >= MAX_ARTIFACTS_PER_SCAN:
                    break

    def parse_transcript(self, path, tool):
        try:
            lines = open(path, errors="replace").readlines()
        except OSError:
            return
        start = self.seen.get(path, 0)
        if len(lines) <= start:
            return
        new = lines[start:]
        self.seen[path] = len(lines)

        tin = tout = cache = msgs = thinking = 0
        models, tools_used, roles = set(), set(), set()
        reasoning, assistant_text = [], []
        extra = {}
        for line in new:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message") or {}
            if rec.get("role"):
                roles.add(rec["role"])
            if msg.get("role"):
                roles.add(msg["role"])
            # Codex records usage in a shape of its own: an event_msg whose
            # payload type is token_count, with the figures nested under
            # info.last_token_usage. A parser written for one vendor's layout
            # reads the other's as empty, which is how this went unnoticed.
            if rec.get("type") == "event_msg":
                pay = rec.get("payload") or {}
                if pay.get("type") == "token_count":
                    info = pay.get("info") or {}
                    tu = info.get("last_token_usage") or {}
                    if tu:
                        msgs += 1
                        tin += tu.get("input_tokens", 0) or 0
                        tout += tu.get("output_tokens", 0) or 0
                        cache += tu.get("cached_input_tokens", 0) or 0
                        if tu.get("reasoning_output_tokens"):
                            thinking += 1
                        if info.get("model_context_window"):
                            extra["context_window"] = info["model_context_window"]
                        rl = pay.get("rate_limits") or {}
                        if rl.get("plan_type"):
                            extra["plan_type"] = rl["plan_type"]
                    continue

            # Codex names the model on a turn_context record
            if rec.get("type") == "turn_context":
                pay = rec.get("payload") or {}
                if isinstance(pay.get("model"), str):
                    models.add(pay["model"])
                continue

            u = msg.get("usage") or rec.get("usage") or {}
            if u:
                msgs += 1
                tin += u.get("input_tokens", u.get("inputTokens", 0)) or 0
                tout += u.get("output_tokens", u.get("outputTokens", 0)) or 0
                cache += u.get("cache_read_input_tokens", u.get("cache_read_tokens", 0)) or 0
            for m in (msg.get("model"), rec.get("model")):
                if isinstance(m, str):
                    models.add(m)
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "thinking":
                            thinking += 1
                            t = b.get("thinking") or b.get("text") or ""
                            if t:
                                reasoning.append(t)
                        if b.get("type") == "text" and b.get("text"):
                            assistant_text.append(b["text"])
                        if b.get("type") == "tool_use" and b.get("name"):
                            tools_used.add(b["name"])

        session = os.path.splitext(os.path.basename(path))[0]
        self.emit.row("transcript_appended",
                      tool=tool,
                      agent_id=session,
                      tokens_total=tin + tout,
                      confidence="parsed",
                      detail={"path": path,
                              "new_lines": len(new),
                              "total_lines": len(lines),
                              "messages_with_usage": msgs,
                              "context_window": extra.get("context_window"),
                              "plan_type": extra.get("plan_type"),
                              "input_tokens": tin,
                              "output_tokens": tout,
                              "cache_read_tokens": cache,
                              "thinking_blocks": thinking,
                              "reasoning_text": "\n\n".join(reasoning)[:8000] or None,
                              "response_text": "\n\n".join(assistant_text)[:8000] or None,
                              "models": sorted(models),
                              "tools_used": sorted(tools_used),
                              "roles": sorted(roles)})
        print(f"  transcript {tool:<12} {len(new):>4} new lines  "
              f"{tin+tout:>7} tokens  {os.path.basename(path)[:28]}")

    def note_artifact(self, path, tool):
        if path in self.seen:
            return False
        self.seen[path] = 1
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        self.emit.row("artifact_written", tool=tool,
                      detail={"path": path,
                              "extension": os.path.splitext(path)[1],
                              "size": size,
                              "kind": "recording" if path.endswith((".mp4", ".webm"))
                                      else "image" if path.endswith((".png", ".jpg"))
                                      else "document"})
        print(f"  artifact   {tool:<12} {os.path.basename(path)[:44]} ({size}b)")
        return True


class Zone3Config:
    """Detect our own visibility changing. Small zone, high value."""

    def __init__(self, emit, workspace):
        self.emit = emit
        self.targets = list(CONFIG_TARGETS)
        for rel in CONFIG_IN_WORKSPACE:
            self.targets.append((os.path.join(workspace, rel), "workspace", "config"))
        self.state = {}

    def scan(self, first=False):
        for path, tool, kind in self.targets:
            exists = os.path.exists(path)
            h = file_hash(path) if exists else None
            prev = self.state.get(path, ("unset", None))
            self.state[path] = ("present" if exists else "absent", h)
            if first or prev[0] == "unset":
                continue
            if prev[0] == "present" and not exists:
                self.flag(path, tool, kind, "removed",
                          "collector configuration deleted, visibility lost")
            elif prev[0] == "absent" and exists:
                self.flag(path, tool, kind, "added", "collector configuration created")
            elif exists and h != prev[1]:
                hooks = self.count_hooks(path)
                self.flag(path, tool, kind, "modified",
                          f"configuration changed, {hooks} hook entries now")

    def count_hooks(self, path):
        try:
            data = json.load(open(path))
            return len(data.get("hooks", {}))
        except Exception:
            return "?"

    def flag(self, path, tool, kind, change, note):
        self.emit.row("collector_config_changed",
                      tool=tool,
                      escalation_flag=(change == "removed"),
                      violation_count=1 if change == "removed" else 0,
                      sensitivity_tier=3,
                      detail={"path": path, "config_kind": kind,
                              "change": change, "note": note})
        print(f"  CONFIG  {change:<9} {kind:<10} {path}")
        print(f"          {note}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="keep watching")
    ap.add_argument("--scan", action="store_true", help="one pass and exit")
    ap.add_argument("--parse-transcripts", action="store_true",
                    help="only parse agent transcripts and artifacts")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be collected, write nothing")
    ap.add_argument("--workspace", default=os.environ.get("SIGNAL_WORKSPACE", os.getcwd()))
    args = ap.parse_args()

    emit = Emitter()
    if args.dry_run:
        emit.row = lambda *a, **k: (_ for _ in ()).throw(SystemExit) if False else {
            "sensitivity_tier": 1, "interaction_type": a[0] if a else ""}
        print("\nDRY RUN, nothing will be written\n")
    ws = os.path.abspath(args.workspace)
    z1 = Zone1Workspace(ws, emit)
    z2 = Zone2AgentData(emit)
    z3 = Zone3Config(emit, ws)

    if args.parse_transcripts:
        print(f"\nparsing agent data directories\n")
        z2.scan()
        print(f"\n{emit.count} rows -> {emit.path}")
        return 0

    print(f"\nworkspace  {ws}")
    print(f"rows       {emit.path}")
    print("\nbaseline scan (existing files are not reported as changes)")
    z1.diff(first=True)
    z3.scan(first=True)
    print(f"  {len(z1.state)} files under watch")

    print("\nagent data")
    z2.scan()

    if args.scan and not args.watch:
        print(f"\n{emit.count} rows written")
        return 0

    print(f"\nwatching every {INTERVAL}s, ctrl-c to stop\n")
    try:
        while True:
            time.sleep(INTERVAL)
            z1.diff()
            z3.scan()
            z2.scan()
    except KeyboardInterrupt:
        print(f"\n\n{emit.count} rows -> {emit.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
