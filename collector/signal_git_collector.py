#!/usr/bin/env python3
"""
Signal Collect — git and cloud collector (collector 6 of 6)

The sixth interception point. Every other collector observes the machine
while the agent works. This one observes what became of the work: what was
committed, by whom, whether an agent authored it, and what reached a remote.

Implemented against git and the GitHub API rather than as a listening webhook
endpoint. A webhook needs a public URL and a tunnel, and for the observables
we actually want, commit authorship, agent attribution, pull requests and
branch state, polling after the fact returns the same data with no
infrastructure and no enterprise tenant.

    python3 signal_git_collector.py --repo ~/bench-claude
    python3 signal_git_collector.py --repo . --since "2 hours ago"
    python3 signal_git_collector.py --repo . --remote        # also hit the API
    python3 signal_git_collector.py --repo . --snapshot      # state, not history

Env:
    SIGNAL_SESSION_DIR   where rows go       (default ~/.signal/sessions)
    SIGNAL_TOOL          tool to attribute   (default: unknown)
    SIGNAL_OPERATOR      who is running it   (default $USER)
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR",
                             os.path.join(HOME, ".signal", "sessions"))
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))
TOOL = os.environ.get("SIGNAL_TOOL", "unknown")

# Markers that a commit or PR was produced by an agent rather than a person.
# GitHub sets actor_is_agent on its own; for local commits we look at the
# trailers and the author identity, which is how these tools sign their work.
AGENT_MARKERS = ("claude", "copilot", "cursor", "codex", "antigravity",
                 "co-authored-by: claude", "generated with", "[bot]",
                 "noreply@anthropic.com", "agent")

SENSITIVE = (".env", "id_rsa", ".pem", "credential", "secret", "password",
             ".aws", ".ssh", "private_key", ".npmrc", ".netrc")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def git(repo, *args):
    try:
        out = subprocess.run(["git", "-C", repo, *args],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        return out.stdout.rstrip("\n")
    except Exception:
        return None


def gh(*args):
    try:
        out = subprocess.run(["gh", *args], capture_output=True,
                             text=True, timeout=30)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def looks_agentic(*texts):
    blob = " ".join(t or "" for t in texts).lower()
    return any(m in blob for m in AGENT_MARKERS)


def sensitivity(paths):
    joined = " ".join(paths).lower()
    if any(m in joined for m in SENSITIVE):
        return 3
    if any(m in joined for m in ("/etc/", "config", "prod", "deploy", ".github")):
        return 2
    return 1


class Emitter:
    def __init__(self):
        os.makedirs(SESSION_DIR, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        self.path = os.path.join(SESSION_DIR, f"{day}-git.jsonl")
        self.n = 0

    def row(self, interaction_type, **extra):
        r = {
            "observable_id": uuid.uuid4().hex,
            "timestamp": extra.pop("timestamp", now_iso()),
            "agent_id": extra.pop("session", ""),
            "agent_type": "coding_agent",
            "agent_tool": extra.pop("tool", TOOL),
            "operator": OPERATOR,
            "company": "real_fleet",
            "surface": "cloud",
            "collector": "webhook",          # the cloud/VCS interception point
            "interaction_type": interaction_type,
            "protocol": "git",
            "connector": extra.pop("connector", "git"),
            "permission_status": "not_applicable",
            "latency_ms": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "model": "",
            "sensitivity_tier": extra.pop("sensitivity_tier", 1),
            "cascade_depth": 0,
            "violation_count": 0,
            "escalation_flag": extra.pop("escalation_flag", False),
            "success": True,
            "error_type": "",
            "workspace": extra.pop("workspace", ""),
            # a commit's author is stated by the commit, not inferred by us
            "attribution": extra.pop("attribution", "unknown"),
            "confidence": extra.pop("confidence", "reported"),
            "detail": extra.pop("detail", {}),
        }
        r.update(extra)
        with open(self.path, "a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        self.n += 1
        return r


def collect_commits(repo, emit, since):
    """Commit history, with authorship and agent attribution."""
    fmt = "%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%aI%x1f%s%x1f%b%x1e"
    log = git(repo, "log", f"--since={since}", f"--pretty=format:{fmt}",
              "--no-merges")
    if not log:
        return 0
    n = 0
    for entry in log.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        # Python treats the unit separator as whitespace, so a commit with an
        # empty body loses its trailing field to any strip() upstream. Pad
        # rather than discard: a commit without a body is still a commit.
        parts = entry.split("\x1f")
        if len(parts) < 7:
            continue
        parts = (parts + [""] * 8)[:8]
        sha, an, ae, cn, ce, when, subject, body = parts

        stat = git(repo, "show", "--numstat", "--format=", sha) or ""
        files, added, removed = [], 0, 0
        for line in stat.split("\n"):
            bits = line.split("\t")
            if len(bits) == 3:
                a, d, path = bits
                files.append(path)
                added += int(a) if a.isdigit() else 0
                removed += int(d) if d.isdigit() else 0

        agentic = looks_agentic(an, ae, cn, ce, subject, body)
        emit.row("commit",
                 timestamp=when,
                 workspace=repo,
                 attribution="agent" if agentic else "human",
                 confidence="reported",
                 sensitivity_tier=sensitivity(files),
                 detail={
                     "sha": sha[:12],
                     "author_name": an, "author_email": ae,
                     "committer_name": cn, "committer_email": ce,
                     "subject": subject[:300],
                     "body": (body or "")[:1000],
                     "authored_by_agent": agentic,
                     "files_changed": len(files),
                     "lines_added": added,
                     "lines_removed": removed,
                     "files": files[:60],
                 })
        n += 1
    return n


def collect_state(repo, emit):
    """Branch, remote, and what is changed but not committed."""
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    remote = git(repo, "config", "--get", "remote.origin.url")
    head = git(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain") or ""
    untracked = [l[3:] for l in status.split("\n") if l.startswith("??")]
    modified = [l[3:] for l in status.split("\n")
                if l and not l.startswith("??")]
    ahead = git(repo, "rev-list", "--count", "@{u}..HEAD") if remote else None

    emit.row("repo_state",
             workspace=repo,
             attribution="unknown",
             confidence="observed",
             sensitivity_tier=sensitivity(untracked + modified),
             detail={
                 "branch": branch, "head": (head or "")[:12],
                 "remote": remote,
                 "uncommitted_modified": len(modified),
                 "uncommitted_untracked": len(untracked),
                 "modified_files": modified[:40],
                 "untracked_files": untracked[:40],
                 "commits_ahead_of_remote": int(ahead) if ahead and ahead.isdigit() else None,
             })
    return 1


def collect_remote(repo, emit):
    """Pull requests and commit attribution from the GitHub API."""
    remote = git(repo, "config", "--get", "remote.origin.url") or ""
    if "github.com" not in remote:
        return 0
    n = 0

    prs = gh("pr", "list", "--limit", "30", "--state", "all", "--json",
             "number,title,author,createdAt,state,isDraft,headRefName,"
             "additions,deletions,changedFiles")
    if prs:
        try:
            for pr in json.loads(prs):
                author = (pr.get("author") or {}).get("login", "")
                agentic = looks_agentic(author, pr.get("title"))
                emit.row("pull_request",
                         timestamp=pr.get("createdAt"),
                         workspace=repo,
                         attribution="agent" if agentic else "human",
                         detail={
                             "number": pr.get("number"),
                             "title": (pr.get("title") or "")[:300],
                             "author": author,
                             "authored_by_agent": agentic,
                             "state": pr.get("state"),
                             "draft": pr.get("isDraft"),
                             "branch": pr.get("headRefName"),
                             "additions": pr.get("additions"),
                             "deletions": pr.get("deletions"),
                             "files_changed": pr.get("changedFiles"),
                         })
                n += 1
        except json.JSONDecodeError:
            pass

    # repository visibility matters: an agent touching a public repo is a
    # different risk from the same action on a private one
    info = gh("repo", "view", "--json",
              "name,visibility,defaultBranchRef,isPrivate")
    if info:
        try:
            d = json.loads(info)
            emit.row("repo_metadata",
                     workspace=repo,
                     attribution="unknown",
                     detail={"name": d.get("name"),
                             "visibility": d.get("visibility"),
                             "private": d.get("isPrivate"),
                             "default_branch": (d.get("defaultBranchRef")
                                                or {}).get("name")})
            n += 1
        except json.JSONDecodeError:
            pass
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--since", default="24 hours ago")
    ap.add_argument("--remote", action="store_true",
                    help="also query the GitHub API")
    ap.add_argument("--snapshot", action="store_true",
                    help="repository state only, no history")
    ap.add_argument("--tool", help="attribute rows to this tool")
    a = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(a.repo))
    if a.tool:
        globals()["TOOL"] = a.tool

    if git(repo, "rev-parse", "--git-dir") is None:
        print(f"not a git repository: {repo}")
        print("initialise one so commits can be observed:")
        print(f"   git -C {repo} init && git -C {repo} add -A && "
              f"git -C {repo} commit -m 'baseline'")
        return 1

    emit = Emitter()
    print(f"\nrepo   {repo}")
    print(f"rows   {emit.path}\n")

    n_state = collect_state(repo, emit)
    print(f"  repository state        {n_state}")
    if not a.snapshot:
        n_commits = collect_commits(repo, emit, a.since)
        print(f"  commits since {a.since:<12} {n_commits}")
    if a.remote:
        n_remote = collect_remote(repo, emit)
        print(f"  pull requests and metadata  {n_remote}")

    print(f"\n{emit.n} rows written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
