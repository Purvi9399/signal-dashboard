"""
Update Engine: keeps an already-deployed package (Collector Framework +
Integration Packages + Discovery/Policy/Activation Engines) from silently
rotting as agent vendors ship changes, without requiring a real update
server -- there isn't one in this project, and fabricating a fake HTTP API
to talk to would violate this project's own "no fabricated shapes/evidence"
rule the same way a made-up telemetry payload would.

SCOPE, honestly stated:
This module does NOT know how to reach out to the internet and check
"is there a newer collector_framework available." There is no real release
registry/CDN for this project to query. What it DOES do, for real, using
only the local filesystem:

  1. SNAPSHOT -- hash every tracked file in a deployed
     `collector_framework/` tree into a `Manifest` (sha256 + size per file,
     grouped by component: core / collectors.<agent> / discovery / policy /
     activation / demos). This manifest IS the package's "version" --
     concrete and verifiable, not a made-up semver string nobody's
     enforcing.
  2. DIFF -- compare a currently-installed manifest against a candidate
     "release" tree's manifest (e.g. a newer checkout, a staged directory
     a real deployment pipeline would drop files into) to determine
     exactly which files are added / changed / removed, grouped by
     component -- this is the "staleness/drift detection" a Continuous Ops
     health check would run periodically.
  3. APPLY -- copy added/changed files from the release tree into the
     installed tree, but ONLY after backing up whatever they're about to
     overwrite into a timestamped backup directory. Removed files are
     reported, never auto-deleted -- deleting collector code unattended is
     a strictly higher-risk operation than adding/changing it, and this
     module deliberately refuses to do it without a separate, explicit
     confirmation step this file does not implement.
  4. VERIFY -- after applying, re-import the updated package's core
     modules and run a real, side-effect-free `DiscoveryEngine().scan()`
     against them. If that raises, the update did not produce a healthy
     package.
  5. ROLLBACK -- if VERIFY fails (or an operator just changes their mind),
     restore every backed-up file from step 3, byte-for-byte, reversing
     the apply.

SCOPE NOTE (the same find/decide/activate-style boundary as every other
component in this package): Update Engine only manages the package's OWN
code/config files on disk. It has nothing to do with WHICH agents/
techniques get activated (that's Discovery/Policy/Activation Engine's job,
entirely unaffected by this module) -- an update here changes what code
exists on disk, not what's currently running.
"""

from __future__ import annotations

import hashlib
import importlib
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Only these extensions are tracked -- this is a Python package plus a
# handful of JSON config files (sample_policy.json); binary/log/data files
# (reference_tables/*.csv, __pycache__, etc.) are deliberately out of scope
# for version tracking, matching this framework's own "code + JSON config
# only" footprint described throughout the rest of the README.
TRACKED_EXTENSIONS = {".py", ".json"}

# Directories never tracked, regardless of extension match above.
EXCLUDED_DIR_NAMES = {"__pycache__", ".git", "reference_tables"}

# Top-level directories that constitute this package's own components, per
# the Repository layout in README.md.
COMPONENT_ROOTS = ["core", "collectors", "discovery", "policy", "activation", "demos", "update"]


def _component_for(relative_path: Path) -> str:
    """Maps a file's path (relative to the collector_framework root) to a
    component name for grouping in diffs -- e.g.
    'collectors/claude_code/claude_code_hooks.py' -> 'collectors.claude_code',
    'core/registry.py' -> 'core', 'policy/sample_policy.json' -> 'policy'.
    """
    parts = relative_path.parts
    if not parts:
        return "unknown"
    top = parts[0]
    if top == "collectors" and len(parts) > 1:
        return f"collectors.{parts[1]}"
    if top in COMPONENT_ROOTS:
        return top
    return "root"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@dataclass
class FileRecord:
    sha256: str
    size: int
    component: str


@dataclass
class Manifest:
    """A content-addressed snapshot of every tracked file in a
    collector_framework tree. This IS the package's version -- two trees
    with identical manifests are, by definition, identical deployments."""

    generated_at: str
    root: str
    files: Dict[str, FileRecord] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "root": self.root,
            "files": {k: asdict(v) for k, v in self.files.items()},
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Manifest":
        files = {k: FileRecord(**v) for k, v in data.get("files", {}).items()}
        return Manifest(generated_at=data["generated_at"], root=data["root"], files=files)

    def digest(self) -> str:
        """A single hash summarizing the whole manifest (sorted file hashes
        concatenated) -- a convenient one-line "version fingerprint" for
        logging, distinct from any individual file's own hash."""
        h = hashlib.sha256()
        for path in sorted(self.files):
            h.update(path.encode())
            h.update(self.files[path].sha256.encode())
        return h.hexdigest()[:16]


class UpdateEngine:
    """Snapshots, diffs, applies, verifies, and rolls back updates to a
    deployed collector_framework tree. Every operation is explicit and
    logged; nothing here runs on a timer or reaches the network -- a
    Continuous Ops scheduler (out of scope here, same as everywhere else in
    this package) would be what calls `snapshot()`/`diff()` periodically
    and `apply_update()` only on operator/pipeline decision.
    """

    def snapshot(self, root: Path) -> Manifest:
        root = Path(root).resolve()
        files: Dict[str, FileRecord] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in TRACKED_EXTENSIONS:
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            rel = path.relative_to(root)
            files[str(rel)] = FileRecord(
                sha256=_sha256_of(path),
                size=path.stat().st_size,
                component=_component_for(rel),
            )
        return Manifest(
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            root=str(root),
            files=files,
        )

    def diff(self, installed: Manifest, candidate: Manifest) -> "UpdateDiff":
        installed_paths = set(installed.files)
        candidate_paths = set(candidate.files)

        added = sorted(candidate_paths - installed_paths)
        removed = sorted(installed_paths - candidate_paths)
        changed = sorted(
            p for p in installed_paths & candidate_paths
            if installed.files[p].sha256 != candidate.files[p].sha256
        )
        unchanged_count = len(installed_paths & candidate_paths) - len(changed)

        by_component: Dict[str, Dict[str, List[str]]] = {}
        for bucket_name, paths in (("added", added), ("changed", changed), ("removed", removed)):
            for p in paths:
                comp = (candidate.files.get(p) or installed.files.get(p)).component
                by_component.setdefault(comp, {"added": [], "changed": [], "removed": []})
                by_component[comp][bucket_name].append(p)

        return UpdateDiff(
            added=added,
            changed=changed,
            removed=removed,
            unchanged_count=unchanged_count,
            by_component=by_component,
            installed_digest=installed.digest(),
            candidate_digest=candidate.digest(),
        )

    def apply_update(
        self,
        installed_root: Path,
        candidate_root: Path,
        backup_root: Path,
        update_diff: "UpdateDiff",
    ) -> "ApplyResult":
        """Copies every added/changed file from candidate_root into
        installed_root, backing up whatever it overwrites into backup_root
        first (preserving relative path, so rollback is a straight
        reverse-copy). Removed files are reported, never deleted -- see
        module docstring."""
        installed_root = Path(installed_root).resolve()
        candidate_root = Path(candidate_root).resolve()
        backup_root = Path(backup_root).resolve()
        backup_root.mkdir(parents=True, exist_ok=True)

        backed_up: List[str] = []
        applied: List[str] = []
        errors: List[str] = []

        for rel in update_diff.added + update_diff.changed:
            src = candidate_root / rel
            dst = installed_root / rel
            try:
                if dst.is_file():
                    backup_dst = backup_root / rel
                    backup_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, backup_dst)
                    backed_up.append(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                applied.append(rel)
            except OSError as exc:
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")

        return ApplyResult(
            applied=applied,
            backed_up=backed_up,
            skipped_removed=list(update_diff.removed),
            errors=errors,
            backup_root=str(backup_root),
        )

    def rollback(self, installed_root: Path, backup_root: Path, apply_result: "ApplyResult") -> "RollbackResult":
        """Reverses a specific apply_update() call: restores every file it
        backed up, and removes any file it applied that did NOT previously
        exist (i.e. was newly added, not changed) -- since those have no
        backup to restore from, `apply_result.applied` minus
        `apply_result.backed_up` tells us exactly which ones those are."""
        installed_root = Path(installed_root).resolve()
        backup_root = Path(backup_root).resolve()

        restored: List[str] = []
        removed_new_files: List[str] = []
        errors: List[str] = []

        backed_up_set = set(apply_result.backed_up)
        for rel in apply_result.applied:
            dst = installed_root / rel
            try:
                if rel in backed_up_set:
                    src = backup_root / rel
                    shutil.copy2(src, dst)
                    restored.append(rel)
                else:
                    if dst.is_file():
                        dst.unlink()
                    removed_new_files.append(rel)
            except OSError as exc:
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")

        return RollbackResult(restored=restored, removed_new_files=removed_new_files, errors=errors)

    def verify(self, root: Path) -> "VerifyResult":
        """Post-update health check: import the updated tree's own core
        modules and run a real, side-effect-free DiscoveryEngine().scan()
        against them. This is deliberately the SAME kind of check
        Continuous Ops would run periodically anyway (see
        discovery_engine.py's own "safe to run repeatedly" design) -- an
        update that breaks basic importability or makes discovery raise is
        exactly the failure mode this exists to catch before anything
        downstream (Policy/Activation Engine) touches the updated tree.
        """
        root = Path(root).resolve()
        # Ensure the updated tree's modules are (re-)imported fresh, not
        # whatever was cached from a previously-imported different root.
        for mod_name in list(sys.modules):
            if mod_name.split(".")[0] in ("core", "discovery", "policy", "activation", "collectors"):
                del sys.modules[mod_name]

        original_sys_path = list(sys.path)
        sys.path.insert(0, str(root))
        try:
            discovery_module = importlib.import_module("discovery.discovery_engine")
            report = discovery_module.scan_this_machine()
            agent_count = len(report.agents)
            return VerifyResult(ok=True, agents_scanned=agent_count, error=None)
        except Exception as exc:  # noqa: BLE001 - this IS the failure we're checking for
            return VerifyResult(ok=False, agents_scanned=0, error=f"{type(exc).__name__}: {exc}")
        finally:
            sys.path[:] = original_sys_path
            for mod_name in list(sys.modules):
                if mod_name.split(".")[0] in ("core", "discovery", "policy", "activation", "collectors"):
                    del sys.modules[mod_name]


@dataclass
class UpdateDiff:
    added: List[str]
    changed: List[str]
    removed: List[str]
    unchanged_count: int
    by_component: Dict[str, Dict[str, List[str]]]
    installed_digest: str
    candidate_digest: str

    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApplyResult:
    applied: List[str]
    backed_up: List[str]
    skipped_removed: List[str]
    errors: List[str]
    backup_root: str

    def ok(self) -> bool:
        return not self.errors


@dataclass
class RollbackResult:
    restored: List[str]
    removed_new_files: List[str]
    errors: List[str]

    def ok(self) -> bool:
        return not self.errors


@dataclass
class VerifyResult:
    ok: bool
    agents_scanned: int
    error: Optional[str]
