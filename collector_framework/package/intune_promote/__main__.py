"""CLI: python3 -m package.intune_promote …"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from package.intune_promote.graph import GraphClient, GraphError, fetch_client_credentials_token
from package.intune_promote.payloads import build_post_promote_issue_body
from package.intune_promote.promote import promote_win32


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing required env var {name}")
    return value


def _emit_promote_result(result: dict, *, dry_run: bool, issue_body_out: str) -> None:
    printable = {k: v for k, v in result.items() if k != "issue_body"}
    print(json.dumps(printable, indent=2))
    if dry_run:
        print("--- issue body ---")
        print(result["issue_body"])
    if issue_body_out:
        Path(issue_body_out).write_text(result["issue_body"], encoding="utf-8")
        print(f"wrote {issue_body_out}")


def _cmd_promote(args: argparse.Namespace) -> int:
    version = json.loads(Path(args.version_json).read_text(encoding="utf-8"))
    semver = str(version["semver"])
    digest = str(version["digest"])
    client = None if args.dry_run else GraphClient(_token_from_env())
    result = promote_win32(
        client,
        intunewin_path=Path(args.intunewin),
        detect_ps1_path=Path(args.detect_ps1),
        semver=semver,
        group_name=args.group,
        dry_run=args.dry_run,
    )
    result["issue_body"] = build_post_promote_issue_body(semver, digest)
    _emit_promote_result(
        result, dry_run=args.dry_run, issue_body_out=args.issue_body_out
    )
    return 0


def _token_from_env() -> str:
    return fetch_client_credentials_token(
        tenant_id=_env("AZURE_TENANT_ID"),
        client_id=_env("AZURE_CLIENT_ID"),
        client_secret=_env("AZURE_CLIENT_SECRET"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intune_promote")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote-win32", help="in-place Win32 content promote")
    p.add_argument("--intunewin", required=True)
    p.add_argument("--detect-ps1", required=True)
    p.add_argument("--version-json", required=True)
    p.add_argument("--group", default="signal-pilot-windows")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--issue-body-out", default="")
    p.set_defaults(func=_cmd_promote)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError, GraphError, RuntimeError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
