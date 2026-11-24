"""CI/CD Phase 1 helpers: tag/VERSION checks and version.json manifest.

See package/CICD.md. Keep McCabe ≤ 5 per function.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from package.semver import load_semver_file, normalize_semver

_TAG_PREFIX = "v"


def parse_release_tag(tag: str) -> str:
    """Parse a git tag like v1.2.3 into normalized semver 1.2.3."""
    text = tag.strip()
    if not text.startswith(_TAG_PREFIX):
        raise ValueError(f"release tag must start with 'v', got {tag!r}")
    return normalize_semver(text[len(_TAG_PREFIX) :])


def assert_tag_matches_version(tag: str, version_file_text: str) -> str:
    """Ensure tag vX.Y.Z matches VERSION file contents X.Y.Z. Returns semver."""
    from_tag = parse_release_tag(tag)
    from_file = normalize_semver(version_file_text)
    if from_tag != from_file:
        raise ValueError(
            f"tag semver {from_tag!r} does not match VERSION {from_file!r}"
        )
    return from_tag


def assert_baked_semver(version_json: Mapping[str, Any], expected_semver: str) -> None:
    """Fail if frozen --json version semver != expected."""
    got = version_json.get("semver")
    if got != expected_semver:
        raise ValueError(
            f"baked semver {got!r} does not match expected {expected_semver!r}"
        )


def assert_platforms_compatible(a: Mapping[str, Any], b: Mapping[str, Any]) -> None:
    """Fail if platform version payloads disagree on release identity.

    Digests are allowed to differ across OS: Manifest path keys use the
    native separator, and Windows checkouts may normalize line endings.
    Top-level release digest is always the Windows pin (Intune Win32).
    """
    if a.get("semver") != b.get("semver"):
        raise ValueError(
            f"platform version mismatch on semver: {a.get('semver')!r} vs {b.get('semver')!r}"
        )
    if a.get("tracked_files") != b.get("tracked_files"):
        raise ValueError(
            "platform version mismatch on tracked_files: "
            f"{a.get('tracked_files')!r} vs {b.get('tracked_files')!r}"
        )


# Back-compat alias for older imports/tests.
def assert_digests_agree(a: Mapping[str, Any], b: Mapping[str, Any]) -> None:
    """Deprecated: digests are not required to match across platforms."""
    assert_platforms_compatible(a, b)


def build_version_manifest(
    *,
    semver: str,
    digest: str,
    tracked_files: int,
    git_sha: str,
    built_at: str,
    artifacts: Dict[str, str],
    platforms: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the release version.json object (CICD.md §1.6)."""
    data: Dict[str, Any] = {
        "semver": semver,
        "digest": digest,
        "tracked_files": tracked_files,
        "artifacts": dict(artifacts),
        "git_sha": git_sha,
        "built_at": built_at,
    }
    if platforms is not None:
        data["platforms"] = platforms
    return data


def write_version_manifest(path: Path, data: Mapping[str, Any]) -> None:
    """Write version.json with stable UTF-8 formatting."""
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _cmd_check_tag(args: argparse.Namespace) -> int:
    text = Path(args.version_file).read_text(encoding="utf-8")
    semver = assert_tag_matches_version(args.tag, text)
    print(semver)
    return 0


def _cmd_check_semver_file(args: argparse.Namespace) -> int:
    semver = load_semver_file(Path(args.version_file))
    expected = normalize_semver(args.expected)
    if semver != expected:
        raise ValueError(f"VERSION {semver!r} != expected {expected!r}")
    print(semver)
    return 0


def _cmd_check_baked(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.version_json).read_text(encoding="utf-8"))
    assert_baked_semver(data, normalize_semver(args.expected_semver))
    print(data["digest"])
    return 0


def _cmd_write_manifest(args: argparse.Namespace) -> int:
    win = json.loads(Path(args.windows_json).read_text(encoding="utf-8"))
    mac = json.loads(Path(args.macos_json).read_text(encoding="utf-8"))
    assert_platforms_compatible(win, mac)
    assert_baked_semver(win, normalize_semver(args.expected_semver))
    built_at = args.built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = build_version_manifest(
        semver=win["semver"],
        digest=win["digest"],
        tracked_files=int(win["tracked_files"]),
        git_sha=args.git_sha,
        built_at=built_at,
        artifacts={
            "windows_exe": "signal-agent.exe",
            "macos_bin": "signal-agent-macos",
            "windows_intunewin": "install.intunewin",
            "macos_pkg": "ProjectSignal.pkg",
            "windows_detect_ps1": "detect.ps1",
        },
        platforms={
            "windows": {
                "digest": win["digest"],
                "tracked_files": int(win["tracked_files"]),
            },
            "macos": {
                "digest": mac["digest"],
                "tracked_files": int(mac["tracked_files"]),
            },
        },
    )
    write_version_manifest(Path(args.out), data)
    print(args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_manifest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tag = sub.add_parser("check-tag", help="assert git tag matches VERSION file")
    p_tag.add_argument("--tag", required=True)
    p_tag.add_argument("--version-file", required=True)
    p_tag.set_defaults(func=_cmd_check_tag)

    p_file = sub.add_parser("check-semver-file", help="assert VERSION equals expected")
    p_file.add_argument("--version-file", required=True)
    p_file.add_argument("--expected", required=True)
    p_file.set_defaults(func=_cmd_check_semver_file)

    p_baked = sub.add_parser("check-baked", help="assert --json version semver")
    p_baked.add_argument("--version-json", required=True)
    p_baked.add_argument("--expected-semver", required=True)
    p_baked.set_defaults(func=_cmd_check_baked)

    p_write = sub.add_parser("write-manifest", help="merge platform JSONs → version.json")
    p_write.add_argument("--windows-json", required=True)
    p_write.add_argument("--macos-json", required=True)
    p_write.add_argument("--expected-semver", required=True)
    p_write.add_argument("--git-sha", required=True)
    p_write.add_argument("--out", required=True)
    p_write.add_argument("--built-at", default="")
    p_write.set_defaults(func=_cmd_write_manifest)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
