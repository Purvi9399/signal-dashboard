"""Semver helpers for VERSION file + bake/CLI (MAJOR.MINOR.PATCH only)."""

from __future__ import annotations

import re
from pathlib import Path

# Digits only, three components. No pre-release / build metadata (keeps CC low).
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def normalize_semver(raw: str) -> str:
    """Return stripped MAJOR.MINOR.PATCH or raise ValueError."""
    text = raw.strip()
    if _SEMVER_RE.fullmatch(text) is None:
        raise ValueError(
            f"invalid semver {raw!r}; expected MAJOR.MINOR.PATCH (e.g. '1.0.0')"
        )
    return text


def load_semver_file(path: Path) -> str:
    """Read a VERSION file and return a normalized semver string."""
    return normalize_semver(path.read_text(encoding="utf-8"))


def default_version_path(framework_root: Path) -> Path:
    """Path to the framework VERSION file (source of truth)."""
    return framework_root / "VERSION"
