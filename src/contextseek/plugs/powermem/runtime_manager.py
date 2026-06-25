"""Managed PowerMem HTTP upstream process.

The desktop server already exposes ContextSeek's plug proxy routes. This module
only manages the hidden PowerMem HTTP server behind that proxy.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from contextseek.plugs.powermem.env import (
    ensure_managed_powermem_env,
    managed_powermem_env_path,
    powermem_child_process_cwd,
    powermem_child_process_env,
)
from contextseek.plugs.powermem.linkers.runtime import PowerMemHTTPRuntimeInstaller


_FALSE_VALUES = {"0", "false", "no", "off"}
_AUTOSTART_ENV = "CONTEXTSEEK_POWERMEM_AUTOSTART"
_UPSTREAM_BASE_URL_ENV = "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL"
_UPSTREAM_HOST_ENV = "CONTEXTSEEK_POWERMEM_UPSTREAM_HOST"
_UPSTREAM_PORT_ENV = "CONTEXTSEEK_POWERMEM_UPSTREAM_PORT"
_STARTUP_GRACE_ENV = "CONTEXTSEEK_POWERMEM_STARTUP_GRACE"

_LOCK = threading.Lock()
_PROCESS: subprocess.Popen[str] | None = None
_STATE: "ManagedPowerMemHTTPRuntime | None" = None


@dataclass(frozen=True)
class ManagedPowerMemHTTPRuntime:
    """State for a ContextSeek-managed PowerMem HTTP upstream."""

    pid: int
    upstream_base_url: str
    command: list[str]
    managed_env_path: Path


def start_managed_powermem_http_runtime(
    *,
    host: str | None = None,
    port: int | None = None,
    require_installed: bool = True,
) -> ManagedPowerMemHTTPRuntime | None:
    """Start PowerMem HTTP upstream when a local runtime is available.

    Returns ``None`` when autostart is disabled, an explicit upstream URL is
    already configured, or no PowerMem server executable is available yet.
    """

    global _PROCESS, _STATE
    if _disabled(os.environ.get(_AUTOSTART_ENV, "1")):
        return None
    if os.environ.get(_UPSTREAM_BASE_URL_ENV, "").strip() and _PROCESS is None:
        return None

    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None and _STATE is not None:
            os.environ[_UPSTREAM_BASE_URL_ENV] = _STATE.upstream_base_url
            return _STATE
        _PROCESS = None
        _STATE = None

        command = PowerMemHTTPRuntimeInstaller().server_command()
        if require_installed and not _command_available(command):
            return None

        upstream_host = (
            host or os.environ.get(_UPSTREAM_HOST_ENV, "").strip() or "127.0.0.1"
        )
        upstream_port = port or _configured_port() or _free_tcp_port(upstream_host)
        full_command = [*command, "--host", upstream_host, "--port", str(upstream_port)]
        ensure_managed_powermem_env()
        process = subprocess.Popen(  # noqa: S603
            full_command,
            env=powermem_child_process_env(),
            cwd=powermem_child_process_cwd(),
            text=True,
        )
        grace = _startup_grace_seconds()
        if grace > 0:
            time.sleep(grace)
        if process.poll() is not None:
            return None

        upstream_base_url = _base_url(upstream_host, upstream_port)
        os.environ[_UPSTREAM_BASE_URL_ENV] = upstream_base_url
        _PROCESS = process
        _STATE = ManagedPowerMemHTTPRuntime(
            pid=int(process.pid),
            upstream_base_url=upstream_base_url,
            command=full_command,
            managed_env_path=managed_powermem_env_path(),
        )
        return _STATE


def stop_managed_powermem_http_runtime() -> None:
    """Stop the managed PowerMem HTTP upstream if ContextSeek started it."""

    with _LOCK:
        global _PROCESS, _STATE
        process = _PROCESS
        state = _STATE
        _PROCESS = None
        _STATE = None
        if (
            state is not None
            and os.environ.get(_UPSTREAM_BASE_URL_ENV) == state.upstream_base_url
        ):
            os.environ.pop(_UPSTREAM_BASE_URL_ENV, None)
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def managed_powermem_http_runtime_status() -> ManagedPowerMemHTTPRuntime | None:
    """Return the current managed upstream state when it is alive."""

    with _LOCK:
        if _PROCESS is None or _PROCESS.poll() is not None:
            return None
        return _STATE


def _command_available(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if os.sep in executable:
        return Path(executable).expanduser().is_file()
    return shutil.which(executable) is not None


def _configured_port() -> int | None:
    raw = os.environ.get(_UPSTREAM_PORT_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _free_tcp_port(host: str) -> int:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def _base_url(host: str, port: int) -> str:
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{url_host}:{port}"


def _startup_grace_seconds() -> float:
    raw = os.environ.get(_STARTUP_GRACE_ENV, "0.3").strip()
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return 0.3


def _disabled(value: str) -> bool:
    return value.strip().lower() in _FALSE_VALUES


atexit.register(stop_managed_powermem_http_runtime)


__all__ = [
    "ManagedPowerMemHTTPRuntime",
    "managed_powermem_http_runtime_status",
    "start_managed_powermem_http_runtime",
    "stop_managed_powermem_http_runtime",
]
