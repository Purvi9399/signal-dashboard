"""
Real, end-to-end Update Engine demo: snapshot -> diff -> apply (with
automatic backup) -> verify -> rollback.

SAFETY: this demo NEVER touches the real collector_framework/ directory
this file lives in. It copies the real, working tree into two temporary
directories under the OS temp dir -- an "installed" copy and a "candidate"
(new release) copy -- makes a few realistic, clearly-labeled changes to the
candidate copy only, and runs the full update lifecycle entirely between
those two temp copies. Every temp directory is removed at the end,
regardless of success or failure (see the `finally` block), so this is
safe to run repeatedly with no side effects on the real package.

The changes made to the candidate copy are deliberately realistic, not
arbitrary:
    - CHANGED: policy/sample_policy.json's codex.otel rule is flipped from
    denied to approved, simulating exactly the scenario
    sample_policy.json's own real comment describes as the unblock
    condition ("Denied until the upstream Codex OTel exporter itself is
    fixed") -- i.e. a believable future policy update, not a random edit.
  - ADDED: a new update/CHANGELOG.json file, representing what a real
    release would ship alongside code changes.
  - REMOVED (relative to candidate): a synthetic scratch file created only
    in the "installed" copy for this demo, so the REMOVED code path (which
    Update Engine deliberately never auto-applies) has something real to
    report on without deleting anything that matters.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update.update_engine import UpdateEngine

REAL_ROOT = Path(__file__).resolve().parents[1]


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def main() -> None:
    tmp_base = Path(tempfile.mkdtemp(prefix="signal_update_demo_"))
    installed_root = tmp_base / "installed"
    candidate_root = tmp_base / "candidate"
    backup_root = tmp_base / "backup"

    try:
        banner("SETUP: copying the real package into two temp working copies")
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REAL_ROOT, installed_root, ignore=ignore)
        shutil.copytree(REAL_ROOT, candidate_root, ignore=ignore)
        print(f"installed_root: {installed_root}")
        print(f"candidate_root: {candidate_root}")

        # Realistic candidate changes (see module docstring).
        policy_path = candidate_root / "policy" / "sample_policy.json"
        policy = json.loads(policy_path.read_text())
        for rule in policy["technique_rules"]:
            if rule.get("agent") == "codex" and rule.get("technique") == "otel":
                rule["allowed"] = True
                rule["reason"] = (
                    "UPDATE DEMO: simulating the real unblock condition sample_policy.json's "
                    "own comment describes -- upstream Codex OTel PII leak fixed, re-approved."
                )
        policy_path.write_text(json.dumps(policy, indent=2))

        changelog_path = candidate_root / "update" / "CHANGELOG.json"
        changelog_path.write_text(json.dumps({
            "version_digest": "demo-only",
            "changes": ["Re-approved codex.otel after upstream PII fix (simulated for this demo)"],
        }, indent=2))

        scratch_path = installed_root / "update" / "_demo_stale_file.json"
        scratch_path.write_text(json.dumps({"note": "synthetic file present only in the old install, to demo REMOVED detection"}))

        engine = UpdateEngine()

        banner("STAGE 1: snapshot() both trees")
        installed_manifest = engine.snapshot(installed_root)
        candidate_manifest = engine.snapshot(candidate_root)
        print(f"installed digest: {installed_manifest.digest()}  ({len(installed_manifest.files)} tracked files)")
        print(f"candidate digest: {candidate_manifest.digest()}  ({len(candidate_manifest.files)} tracked files)")

        banner("STAGE 2: diff() -- real drift detection")
        update_diff = engine.diff(installed_manifest, candidate_manifest)
        print(f"added   ({len(update_diff.added)}): {update_diff.added}")
        print(f"changed ({len(update_diff.changed)}): {update_diff.changed}")
        print(f"removed ({len(update_diff.removed)}): {update_diff.removed}")
        print(f"unchanged: {update_diff.unchanged_count} files")
        print("by component:")
        for comp, buckets in sorted(update_diff.by_component.items()):
            print(f"  {comp}: {buckets}")

        banner("STAGE 3: apply_update() -- backs up before overwriting, never auto-deletes")
        apply_result = engine.apply_update(installed_root, candidate_root, backup_root, update_diff)
        print(f"applied   ({len(apply_result.applied)}): {apply_result.applied}")
        print(f"backed_up ({len(apply_result.backed_up)}): {apply_result.backed_up}")
        print(f"skipped_removed (reported, NOT deleted): {apply_result.skipped_removed}")
        print(f"errors: {apply_result.errors}")
        print(f"backup_root: {apply_result.backup_root}")

        banner("STAGE 4: verify() -- real DiscoveryEngine().scan() against the UPDATED tree")
        verify_result = engine.verify(installed_root)
        print(f"ok: {verify_result.ok}, agents_scanned: {verify_result.agents_scanned}, error: {verify_result.error}")

        # Confirm the policy change actually took effect in the updated tree.
        updated_policy_path = installed_root / "policy" / "sample_policy.json"
        updated_policy = json.loads(updated_policy_path.read_text())
        codex_otel_rule = next(
            r for r in updated_policy["technique_rules"]
            if r.get("agent") == "codex" and r.get("technique") == "otel"
        )
        print(f"\npost-update codex.otel rule 'allowed': {codex_otel_rule['allowed']} (was False before update)")

        banner("STAGE 5: rollback() -- reverse the exact apply_update() call above")
        rollback_result = engine.rollback(installed_root, backup_root, apply_result)
        print(f"restored           ({len(rollback_result.restored)}): {rollback_result.restored}")
        print(f"removed_new_files  ({len(rollback_result.removed_new_files)}): {rollback_result.removed_new_files}")
        print(f"errors: {rollback_result.errors}")

        banner("STAGE 6: verify rollback was exact -- re-snapshot and diff against original")
        post_rollback_manifest = engine.snapshot(installed_root)
        rollback_diff = engine.diff(installed_manifest, post_rollback_manifest)
        print(f"post-rollback diff vs original: has_changes={rollback_diff.has_changes()}")
        print(f"  added={rollback_diff.added}, changed={rollback_diff.changed}, removed={rollback_diff.removed}")
        print(f"original digest:      {installed_manifest.digest()}")
        print(f"post-rollback digest: {post_rollback_manifest.digest()}")
        print(f"digests match: {installed_manifest.digest() == post_rollback_manifest.digest()}")

        banner("DONE")

    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)
        print(f"\ncleaned up temp dir: {tmp_base}")


if __name__ == "__main__":
    main()
