"""
Signal Agent CLI: the single entry point that gets frozen into the
deployable executable (see package/build_exe.py / package/BUILDING.md).

This is the "Structure it as a deployable exe package" step of the
Deployment phase's "Build the package" box -- it is deliberately just a
thin command-line wrapper around the five components already built and
tested for real (Collector Framework, Discovery Engine, Policy Engine,
Activation Engine, Update Engine). It adds no new detection/decision/
activation logic of its own -- it only sequences the real, already-vetted
components and gives them a stable CLI surface an MDM tool (Intune/Jamf)
can invoke non-interactively, on a schedule, per the Continuous Ops steps
on the roadmap ("Initiate collectors", "Monitor health & coverage",
"Report state and update integration").

Commands:
  scan                    -- Discovery Engine only. Prints/saves a DiscoveryReport.
  decide [--policy PATH]  -- Discovery + Policy Engine. Prints/saves a PolicyDecisionReport.
  run [--live]            -- Discovery -> Policy -> Activation -> capture -> deactivate.
                             Default mode is "replay" (side-effect-free, safe to run anywhere,
                             including in this exe's own smoke test). --live actually registers
                             and activates real collectors against real running agent processes --
                             the real production mode for a deployed device.
  update check --candidate DIR         -- diff the running install against a candidate release dir.
  update apply --candidate DIR --backup DIR   -- apply + auto-verify + auto-rollback-on-failure.
  update rollback --backup DIR         -- reverse a specific prior apply (uses backup/apply_result.json).
  version                 -- print this install's manifest digest (its real, verifiable "version").

Every command exits 0 on success, non-zero on failure, and prints
JSON when --json is passed -- so an MDM/orchestration layer can script
against this reliably instead of parsing human-readable text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller --onefile extracts bundled data (see package/build_exe.py's
    # --add-data flags) into a temp dir at sys._MEIPASS -- there is no real,
    # complete collector_framework source tree on disk to walk in this mode
    # (the Python modules themselves are embedded in the binary, not
    # extracted as files). Only what build_exe.py explicitly added as data
    # (currently: sample_policy.json) actually exists as a real file here.
    MEIPASS = Path(getattr(sys, "_MEIPASS"))
    COLLECTOR_FRAMEWORK_ROOT = MEIPASS
else:
    PACKAGE_DIR = Path(__file__).resolve().parent
    COLLECTOR_FRAMEWORK_ROOT = PACKAGE_DIR.parent
    sys.path.insert(0, str(COLLECTOR_FRAMEWORK_ROOT))

from discovery.discovery_engine import scan_this_machine  # noqa: E402
from policy.policy_engine import PolicyConfig, PolicyEngine  # noqa: E402
from activation.activation_engine import ActivationEngine  # noqa: E402
from update.update_engine import UpdateEngine, ApplyResult, Manifest  # noqa: E402

try:
    from package._baked_version import BAKED_VERSION_DIGEST, BAKED_TRACKED_FILES  # noqa: E402
except ImportError:
    BAKED_VERSION_DIGEST = None
    BAKED_TRACKED_FILES = None

DEFAULT_POLICY_PATH = COLLECTOR_FRAMEWORK_ROOT / "policy" / "sample_policy.json"


def _emit(data: Dict[str, Any], as_json: bool, human_fn) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        human_fn(data)


def cmd_scan(args: argparse.Namespace) -> int:
    report = scan_this_machine()
    data = report.to_dict()

    def human(_d: Dict[str, Any]) -> None:
        for agent in report.agents:
            status = "INSTALLED" if agent.installed else "not found"
            print(f"{agent.agent}: {status}  (confidence={agent.confidence})")
            for tech in agent.technique_results:
                mark = "viable" if tech.viable else "not viable"
                print(f"    {tech.technique:12s} {mark:12s} {tech.note or ''}")
        if report.scan_errors:
            print(f"scan_errors: {report.scan_errors}")

    _emit(data, args.json, human)
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    discovery_report = scan_this_machine()
    policy = PolicyConfig.load(str(args.policy))
    decision_report = PolicyEngine(policy).evaluate(discovery_report)
    data = decision_report.to_dict()

    def human(_d: Dict[str, Any]) -> None:
        for decision in decision_report.decisions:
            status = "APPROVED" if decision.approved else "DENIED"
            print(f"{decision.agent:10s} {decision.technique:12s} {status:10s} ({decision.decided_by}) {decision.reason}")

    _emit(data, args.json, human)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    discovery_report = scan_this_machine()
    policy = PolicyConfig.load(str(args.policy))
    decision_report = PolicyEngine(policy).evaluate(discovery_report)

    mode = "live" if args.live else "replay"
    engine = ActivationEngine(mode=mode, allow_live_hazards=args.allow_live_hazards)
    registry, wiring_result = engine.wire(decision_report)

    events = list(registry.merged_capture()) if mode == "live" else []
    registry.deactivate_all()

    data = {
        "mode": mode,
        "wiring": wiring_result.to_dict(),
        "events_captured": len(events),
    }

    def human(_d: Dict[str, Any]) -> None:
        print(f"mode: {mode}")
        print(f"registered ({len(wiring_result.registered)}): {wiring_result.registered}")
        print(f"activated  ({len(wiring_result.activated)}): {wiring_result.activated}")
        if wiring_result.skipped:
            print("skipped:")
            for agent, technique, reason in wiring_result.skipped:
                print(f"  - {agent}.{technique}: {reason}")
        if wiring_result.construction_errors:
            print(f"construction_errors: {wiring_result.construction_errors}")
        print(f"events captured this run: {len(events)}")

    _emit(data, args.json, human)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    if FROZEN and BAKED_VERSION_DIGEST is not None:
        # A frozen exe's own Python modules are embedded in the binary, not
        # present as real files to hash at runtime -- see build_exe.py,
        # which computes this digest from the real source tree at BUILD
        # time and bakes it in, so `version` still reports something real
        # and verifiable rather than nothing.
        data = {"digest": BAKED_VERSION_DIGEST, "tracked_files": BAKED_TRACKED_FILES, "root": "(baked in at build time; this is a frozen executable)"}

        def human(_d: Dict[str, Any]) -> None:
            print(f"installed version digest (baked in at build time): {BAKED_VERSION_DIGEST}")
            print(f"tracked files at build time: {BAKED_TRACKED_FILES}")

        _emit(data, args.json, human)
        return 0

    manifest = UpdateEngine().snapshot(COLLECTOR_FRAMEWORK_ROOT)
    data = {"digest": manifest.digest(), "tracked_files": len(manifest.files), "root": manifest.root}

    def human(_d: Dict[str, Any]) -> None:
        print(f"installed version digest: {manifest.digest()}")
        print(f"tracked files: {len(manifest.files)}")
        print(f"root: {manifest.root}")

    _emit(data, args.json, human)
    return 0


def cmd_update_check(args: argparse.Namespace) -> int:
    engine = UpdateEngine()
    installed_manifest = engine.snapshot(args.installed)
    candidate_manifest = engine.snapshot(args.candidate)
    diff = engine.diff(installed_manifest, candidate_manifest)
    data = diff.to_dict()

    def human(_d: Dict[str, Any]) -> None:
        print(f"installed digest: {diff.installed_digest}")
        print(f"candidate digest: {diff.candidate_digest}")
        print(f"added   ({len(diff.added)}): {diff.added}")
        print(f"changed ({len(diff.changed)}): {diff.changed}")
        print(f"removed ({len(diff.removed)}) [reported only, never auto-applied]: {diff.removed}")
        print(f"unchanged: {diff.unchanged_count}")

    _emit(data, args.json, human)
    return 0 if not diff.has_changes() else 1


def cmd_update_apply(args: argparse.Namespace) -> int:
    engine = UpdateEngine()
    installed_root = args.installed
    installed_manifest = engine.snapshot(installed_root)
    candidate_manifest = engine.snapshot(args.candidate)
    diff = engine.diff(installed_manifest, candidate_manifest)

    if not diff.has_changes():
        print("No changes to apply; installed tree already matches candidate.")
        return 0

    apply_result = engine.apply_update(installed_root, args.candidate, args.backup, diff)
    Path(args.backup, "apply_result.json").write_text(json.dumps({
        "applied": apply_result.applied,
        "backed_up": apply_result.backed_up,
        "skipped_removed": apply_result.skipped_removed,
        "errors": apply_result.errors,
        "backup_root": apply_result.backup_root,
    }, indent=2))

    verify_result = engine.verify(installed_root)

    rolled_back = False
    if not apply_result.ok() or not verify_result.ok:
        engine.rollback(installed_root, args.backup, apply_result)
        rolled_back = True

    data = {
        "applied": apply_result.applied,
        "backed_up": apply_result.backed_up,
        "skipped_removed": apply_result.skipped_removed,
        "apply_errors": apply_result.errors,
        "verify_ok": verify_result.ok,
        "verify_error": verify_result.error,
        "rolled_back": rolled_back,
    }

    def human(_d: Dict[str, Any]) -> None:
        print(f"applied: {apply_result.applied}")
        print(f"backed_up: {apply_result.backed_up}")
        print(f"skipped_removed (not deleted): {apply_result.skipped_removed}")
        print(f"verify ok: {verify_result.ok}  (agents_scanned={verify_result.agents_scanned}, error={verify_result.error})")
        print(f"rolled_back: {rolled_back}")

    _emit(data, args.json, human)
    return 1 if rolled_back or not apply_result.ok() else 0


def cmd_update_rollback(args: argparse.Namespace) -> int:
    apply_result_path = Path(args.backup, "apply_result.json")
    if not apply_result_path.exists():
        print(f"No apply_result.json found in {args.backup} -- nothing recorded to roll back.", file=sys.stderr)
        return 1

    recorded = json.loads(apply_result_path.read_text())
    apply_result = ApplyResult(
        applied=recorded["applied"],
        backed_up=recorded["backed_up"],
        skipped_removed=recorded["skipped_removed"],
        errors=recorded["errors"],
        backup_root=recorded["backup_root"],
    )

    engine = UpdateEngine()
    installed_root = args.installed
    rollback_result = engine.rollback(installed_root, args.backup, apply_result)
    data = {
        "restored": rollback_result.restored,
        "removed_new_files": rollback_result.removed_new_files,
        "errors": rollback_result.errors,
    }

    def human(_d: Dict[str, Any]) -> None:
        print(f"restored: {rollback_result.restored}")
        print(f"removed_new_files: {rollback_result.removed_new_files}")
        print(f"errors: {rollback_result.errors}")

    _emit(data, args.json, human)
    return 1 if rollback_result.errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signal-agent", description="Project Signal collector agent CLI")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of human text")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Discovery Engine only")
    p_scan.set_defaults(func=cmd_scan)

    p_decide = sub.add_parser("decide", help="Discovery + Policy Engine")
    p_decide.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    p_decide.set_defaults(func=cmd_decide)

    p_run = sub.add_parser("run", help="Full pipeline: discover -> decide -> activate -> capture -> deactivate")
    p_run.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    p_run.add_argument("--live", action="store_true", help="actually register/activate real collectors (default: safe replay mode)")
    p_run.add_argument("--allow-live-hazards", action="store_true", help="permit constructing collectors documented as live-process-spawning hazards")
    p_run.set_defaults(func=cmd_run)

    p_version = sub.add_parser("version", help="print this install's manifest digest")
    p_version.set_defaults(func=cmd_version)

    p_update = sub.add_parser("update", help="Update Engine operations")
    update_sub = p_update.add_subparsers(dest="update_command", required=True)

    # --installed is required (no default self-path): a frozen exe's own
    # Python modules are embedded in the binary, not present as a real
    # directory tree to snapshot/patch at runtime (see cli.py module
    # docstring / FROZEN handling above) -- an on-disk install directory
    # must always be named explicitly, by whatever deployed this exe.
    p_update_check = update_sub.add_parser("check", help="diff an installed directory vs a candidate release directory")
    p_update_check.add_argument("--candidate", type=Path, required=True)
    p_update_check.add_argument("--installed", type=Path, required=True)
    p_update_check.set_defaults(func=cmd_update_check)

    p_update_apply = update_sub.add_parser("apply", help="apply a candidate release, with auto-verify and auto-rollback-on-failure")
    p_update_apply.add_argument("--candidate", type=Path, required=True)
    p_update_apply.add_argument("--backup", type=Path, required=True)
    p_update_apply.add_argument("--installed", type=Path, required=True)
    p_update_apply.set_defaults(func=cmd_update_apply)

    p_update_rollback = update_sub.add_parser("rollback", help="reverse a specific prior apply, using its recorded backup")
    p_update_rollback.add_argument("--backup", type=Path, required=True)
    p_update_rollback.add_argument("--installed", type=Path, required=True)
    p_update_rollback.set_defaults(func=cmd_update_rollback)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
