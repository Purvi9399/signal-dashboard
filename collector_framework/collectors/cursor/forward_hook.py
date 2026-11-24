"""POST Cursor command-hook stdin JSON to the Signal webhook. Fail open."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request


def forward_stdin(url: str, raw: bytes) -> None:
    if not url:
        return
    req = urllib.request.Request(
        url,
        data=raw or b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def main(argv: list) -> int:
    url = argv[1] if len(argv) > 1 else ""
    forward_stdin(url, sys.stdin.buffer.read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
