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
  run [--live] [--export-jsonl PATH] [--upload]
                             -- Discovery -> Policy -> Activation -> capture -> deactivate.
                             Default mode is "replay" (side-effect-free, safe to run anywhere,
                             including in this exe's own smoke test). --live actually registers
                             and activates real collectors against real running agent processes --
                             the real production mode for a deployed device.
                             --export-jsonl / --upload are post-capture opt-in (see LIVE_EXPORT.md).
  upload-jsonl PATH       -- Map RawEvent JSONL -> public.source_events (local service-role
                             sink; see package/UPLOAD.md). Does not alter Find/Decide/Activate.
  curate --from-jsonl|--from-batch     -- Adapt source events -> colleague curate() ->
                             observables (opt-in; see package/CURATE_SPEC.md).
  update check --candidate DIR         -- diff the running install against a candidate release dir.
  update apply --candidate DIR --backup DIR   -- apply + auto-verify + auto-rollback-on-failure.
  update rollback --backup DIR         -- reverse a specific prior apply (uses backup/apply_result.json).
  version                 -- print semver + manifest digest (see package/SEMVER.md).

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

try:
    from package._baked_version import BAKED_SEMVER  # noqa: E402
except ImportError:
    BAKED_SEMVER = None

from package.semver import default_version_path, load_semver_file  # noqa: E402

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


def _guard_upload_flags(args: argparse.Namespace) -> Optional[int]:
    from export.upload_events import check_upload_requires_live

    try:
        check_upload_requires_live(live=bool(args.live), upload=bool(args.upload))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return None


def _capture_live_events(args: argparse.Namespace):
    discovery_report = scan_this_machine()
    policy = PolicyConfig.load(str(args.policy))
    decision_report = PolicyEngine(policy).evaluate(discovery_report)
    mode = "live" if args.live else "replay"
    engine = ActivationEngine(mode=mode, allow_live_hazards=args.allow_live_hazards)
    registry, wiring_result = engine.wire(decision_report)
    events = list(registry.merged_capture()) if mode == "live" else []
    registry.deactivate_all()
    failures = [
        {"collector_id": f.collector_id, "stage": f.stage, "error": f.error}
        for f in registry.failures
    ]
    return mode, wiring_result, events, failures


def _print_skipped(wiring_result: Any) -> None:
    if not wiring_result.skipped:
        return
    print("skipped:")
    for agent, technique, reason in wiring_result.skipped:
        print(f"  - {agent}.{technique}: {reason}")


def _print_failures(failures: list) -> None:
    if not failures:
        return
    print(f"failures ({len(failures)}):")
    for f in failures:
        print(f"  - {f['collector_id']} failed at {f['stage']}: {f['error']}")


def _print_run_human(
    data: Dict[str, Any],
    *,
    mode: str,
    wiring_result: Any,
    failures: list,
    events_len: int,
) -> None:
    print(f"mode: {mode}")
    print(f"registered ({len(wiring_result.registered)}): {wiring_result.registered}")
    print(f"activated  ({len(wiring_result.activated)}): {wiring_result.activated}")
    _print_skipped(wiring_result)
    if wiring_result.construction_errors:
        print(f"construction_errors: {wiring_result.construction_errors}")
    _print_failures(failures)
    print(f"events captured this run: {events_len}")
    if "export" in data:
        print(f"export: {data['export']}")
    if "upload" in data:
        print(f"upload: {data['upload']}")


def cmd_run(args: argparse.Namespace) -> int:
    from export.upload_events import raw_events_to_dicts

    guard = _guard_upload_flags(args)
    if guard is not None:
        return guard

    mode, wiring_result, events, failures = _capture_live_events(args)
    data: Dict[str, Any] = {
        "mode": mode,
        "wiring": wiring_result.to_dict(),
        "events_captured": len(events),
        "failures": failures,
    }
    event_dicts = raw_events_to_dicts(events)
    export_error = _maybe_export_jsonl(args, event_dicts, data)
    if export_error is not None:
        return export_error
    upload_error = _maybe_upload_events(args, event_dicts, data)
    if upload_error is not None:
        return upload_error

    def human(_d: Dict[str, Any]) -> None:
        _print_run_human(
            _d,
            mode=mode,
            wiring_result=wiring_result,
            failures=failures,
            events_len=len(events),
        )

    _emit(data, args.json, human)
    return 0


def _maybe_export_jsonl(
    args: argparse.Namespace,
    event_dicts: list,
    data: Dict[str, Any],
) -> Optional[int]:
    from export.upload_events import write_jsonl

    path = getattr(args, "export_jsonl", None)
    if path is None:
        return None
    try:
        count = write_jsonl(Path(path), event_dicts)
    except OSError as exc:
        print(f"export-jsonl failed: {exc}", file=sys.stderr)
        return 1
    data["export"] = {"path": str(Path(path).resolve()), "events_written": count}
    return None


def _maybe_upload_events(
    args: argparse.Namespace,
    event_dicts: list,
    data: Dict[str, Any],
) -> Optional[int]:
    from export.upload_events import upload_event_dicts

    if not getattr(args, "upload", False):
        return None
    try:
        result = upload_event_dicts(event_dicts, origin="live-capture")
    except (EnvironmentError, ValueError, RuntimeError) as exc:
        print(f"upload failed: {exc}", file=sys.stderr)
        return 1
    data["upload"] = {
        "mode": result.get("mode"),
        "inserted": result.get("inserted"),
        "observables_curated": result.get("observables_curated"),
        "observables_upserted": result.get("observables_upserted"),
        "ingest_batch_id": result.get("ingest_batch_id"),
        "events_read": result.get("events_read"),
        "tenant_id": result.get("tenant_id"),
        "device_id": result.get("device_id"),
    }
    return None


def _print_version_human(data: Dict[str, Any]) -> None:
    print(f"semver: {data['semver']}")
    print(f"installed version digest: {data['digest']}")
    print(f"tracked files: {data['tracked_files']}")
    if data.get("root"):
        print(f"root: {data['root']}")


def _baked_version_payload() -> Optional[Dict[str, Any]]:
    if not FROZEN or BAKED_VERSION_DIGEST is None or BAKED_SEMVER is None:
        return None
    return {
        "semver": BAKED_SEMVER,
        "digest": BAKED_VERSION_DIGEST,
        "tracked_files": BAKED_TRACKED_FILES,
        "root": "(baked in at build time; this is a frozen executable)",
    }


def _live_version_payload() -> Dict[str, Any]:
    semver = load_semver_file(default_version_path(COLLECTOR_FRAMEWORK_ROOT))
    manifest = UpdateEngine().snapshot(COLLECTOR_FRAMEWORK_ROOT)
    return {
        "semver": semver,
        "digest": manifest.digest(),
        "tracked_files": len(manifest.files),
        "root": manifest.root,
    }


def cmd_upload_jsonl(args: argparse.Namespace) -> int:
    """Post-Activate export: JSONL -> Supabase source_events (see package/UPLOAD.md)."""
    from export.upload_jsonl import upload_jsonl

    if not args.path.is_file():
        print(f"file not found: {args.path}", file=sys.stderr)
        return 1
    try:
        result = upload_jsonl(args.path, dry_run=args.dry_run, limit=args.limit)
    except (EnvironmentError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    def human(d: Dict[str, Any]) -> None:
        print(f"file: {d['file']}")
        print(f"events_read: {d['events_read']}")
        print(f"mode: {d.get('mode')}")
        print(f"ingest_batch_id: {d['ingest_batch_id']}")
        print(f"tenant_id: {d['tenant_id']}")
        print(f"device_id: {d['device_id']}")
        print(f"dry_run: {d['dry_run']}")
        print(f"inserted: {d['inserted']}")
        print(f"observables_curated: {d.get('observables_curated')}")
        print(f"observables_upserted: {d.get('observables_upserted')}")
        if d.get("sample_row") is not None:
            print("sample_row:")
            print(json.dumps(d["sample_row"], indent=2, default=str))
        if d.get("sample_observable") is not None:
            print("sample_observable:")
            print(json.dumps(d["sample_observable"], indent=2, default=str))

    _emit(result, args.json, human)
    return 0


def _curate_load(args: argparse.Namespace):
    from curate.run_curate import curate_from_jsonl, curate_from_source_rows
    from curate.observables_sink import fetch_source_events_by_batch

    if args.from_jsonl is not None:
        if not args.from_jsonl.is_file():
            raise ValueError(f"file not found: {args.from_jsonl}")
        return curate_from_jsonl(args.from_jsonl)
    rows = fetch_source_events_by_batch(args.from_batch)
    return curate_from_source_rows(rows)


def _curate_write(args: argparse.Namespace, records: list) -> Dict[str, Any]:
    from curate.run_curate import write_jsonl
    from curate.observables_sink import upsert_observables

    out: Dict[str, Any] = {"written_out": 0, "upserted": 0}
    if args.out is not None:
        out["written_out"] = write_jsonl(args.out, records)
    if args.supabase and not args.dry_run:
        out["upserted"] = upsert_observables(records)
    return out


def cmd_curate(args: argparse.Namespace) -> int:
    """Adapt RawEvent/source_events -> colleague curate() -> observables."""
    from curate.run_curate import summarize

    if (args.from_jsonl is None) == (args.from_batch is None):
        print("provide exactly one of --from-jsonl or --from-batch", file=sys.stderr)
        return 2
    try:
        records, conflicts, _field_status = _curate_load(args)
        writes = _curate_write(args, records)
    except (EnvironmentError, ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    result: Dict[str, Any] = {
        "dry_run": bool(args.dry_run),
        "supabase": bool(args.supabase) and not args.dry_run,
        "conflicts": len(conflicts),
        **summarize(records),
        **writes,
    }
    if args.stats:
        result["stats"] = True

    def human(d: Dict[str, Any]) -> None:
        print(f"records: {d['records']}")
        print(f"conflicts: {d['conflicts']}")
        print(f"dry_run: {d['dry_run']}")
        print(f"supabase: {d['supabase']}")
        print(f"upserted: {d['upserted']}")
        print(f"written_out: {d['written_out']}")
        print("tools:", json.dumps(d["tools"], sort_keys=True))
        print("types:", json.dumps(d["types"], sort_keys=True))

    _emit(result, args.json, human)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    # Frozen builds report bake-time identity; unfrozen builds read VERSION
    # and snapshot the live source tree (see package/SEMVER.md).
    data = _baked_version_payload() or _live_version_payload()
    _emit(data, args.json, _print_version_human)
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
    p_run.add_argument(
        "--export-jsonl",
        type=Path,
        default=None,
        help="after live capture, write RawEvents to this JSONL path (see LIVE_EXPORT.md)",
    )
    p_run.add_argument(
        "--upload",
        action="store_true",
        help="after live capture, upload events via SIGNAL_UPLOAD_MODE (requires --live)",
    )
    p_run.set_defaults(func=cmd_run)

    p_upload = sub.add_parser(
        "upload-jsonl",
        help="upload RawEvent JSONL to Supabase source_events (local service-role; see UPLOAD.md)",
    )
    p_upload.add_argument("path", type=Path, help="JSONL file of RawEvent envelopes")
    p_upload.add_argument("--dry-run", action="store_true", help="map only; do not call Supabase")
    p_upload.add_argument("--limit", type=int, default=None, help="upload at most N events")
    p_upload.set_defaults(func=cmd_upload_jsonl)

    p_curate = sub.add_parser(
        "curate",
        help="adapt RawEvent/source_events -> observables via vendored curator (see CURATE_SPEC.md)",
    )
    p_curate.add_argument("--from-jsonl", type=Path, default=None, help="RawEvent JSONL path")
    p_curate.add_argument(
        "--from-batch",
        type=str,
        default=None,
        help="ingest_batch_id to fetch from public.source_events",
    )
    p_curate.add_argument("--out", type=Path, default=None, help="write curated JSONL")
    p_curate.add_argument(
        "--supabase",
        action="store_true",
        help="upsert curated rows into public.observables only",
    )
    p_curate.add_argument(
        "--dry-run",
        action="store_true",
        help="adapt+curate only; skip --supabase write",
    )
    p_curate.add_argument("--stats", action="store_true", help="include type/tool counts (always printed)")
    p_curate.set_defaults(func=cmd_curate)

    p_version = sub.add_parser("version", help="print semver + this install's manifest digest")
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
