"""Shared live OTLP/HTTP receiver for OTel collectors (LIVE_OTEL.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple

from collectors.copilot.hook_settings import apply_required_env, restore_env
from collectors.live_http import LiveEventFeed, attach_hook_route, detach_hook_route

SIGNALS: Tuple[str, ...] = ("/v1/traces", "/v1/logs", "/v1/metrics")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4318

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LiveOTLPServer",
    "SIGNALS",
    "apply_required_env",
    "install_codex_otel",
    "merge_codex_otel",
    "otlp_base",
    "restore_codex_otel",
    "restore_env",
]


def otlp_base(host: str, port: int, agent_key: str) -> str:
    return f"http://{host}:{port}/otel/{agent_key}"


def otlp_paths(prefix: str) -> Tuple[str, ...]:
    return tuple(prefix + suffix for suffix in SIGNALS)


def _otel_present(text: str) -> bool:
    return "[otel]" in text or "[otel." in text


def merge_codex_otel(text: str, base_url: str) -> str:
    if _otel_present(text):
        return text
    section = (
        "[otel]\n"
        'environment = "signal"\n'
        "log_user_prompt = true\n\n"
        "[otel.exporter.otlp-http]\n"
        f'endpoint = "{base_url}/v1/logs"\n'
        'protocol = "json"\n\n'
        "[otel.trace_exporter.otlp-http]\n"
        f'endpoint = "{base_url}/v1/traces"\n'
        'protocol = "json"\n\n'
        "[otel.metrics_exporter.otlp-http]\n"
        f'endpoint = "{base_url}/v1/metrics"\n'
        'protocol = "json"\n'
    )
    if not text.strip():
        return section
    return text.rstrip() + "\n\n" + section


def install_codex_otel(path: Path, base_url: str) -> Optional[bytes]:
    backup = path.read_bytes() if path.is_file() else None
    current = backup.decode("utf-8") if backup else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merge_codex_otel(current, base_url), encoding="utf-8")
    return backup


def restore_codex_otel(path: Path, backup: Optional[bytes]) -> None:
    if backup is None:
        if path.is_file():
            path.unlink()
        return
    path.write_bytes(backup)


class LiveOTLPServer:
    """Attach three OTLP signal paths that share one LiveEventFeed."""

    def __init__(self, host: str, port: int, prefix: str, feed: LiveEventFeed) -> None:
        self._host = host
        self._port = port
        self._prefix = prefix
        self._feed = feed
        self._bound: Optional[int] = None
        self._paths: Sequence[str] = ()

    @property
    def port(self) -> int:
        if self._bound is None:
            return self._port
        return self._bound

    def start(self) -> int:
        paths = otlp_paths(self._prefix)
        bound = attach_hook_route(self._host, self._port, paths[0], self._feed)
        for path in paths[1:]:
            attach_hook_route(self._host, bound, path, self._feed)
        self._bound = bound
        self._paths = paths
        return bound

    def stop(self) -> None:
        bound = self._bound
        self._bound = None
        if bound is None:
            return
        for path in self._paths:
            detach_hook_route(self._host, bound, path)
        self._feed.stop()
