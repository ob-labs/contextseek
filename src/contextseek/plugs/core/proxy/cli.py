"""Generic helpers for CLI plug proxies."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.gateway import PlugGateway
from contextseek.plugs.core.protocols import MaterializationReceipt, PlugChangeEvent


@dataclass(frozen=True)
class CLIRunResult:
    """Captured result from a proxied CLI command."""

    stdout: str
    stderr: str
    returncode: int


def run_cli(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> CLIRunResult:
    completed = subprocess.run(  # noqa: S603
        argv,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return CLIRunResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=int(completed.returncode),
    )


def emit_cli_result(result: CLIRunResult) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def resolve_executable(
    *,
    env_names: list[str],
    fallback: str,
) -> str | None:
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    return shutil.which(fallback)


def materialize_cli_events(
    events: list[PlugChangeEvent],
    *,
    ctx: ContextSeek | None = None,
    max_retry: int | None = None,
) -> list[MaterializationReceipt]:
    if not events:
        return []
    client = ctx or ContextSeek.from_settings()
    gateway = PlugGateway(client, max_retry=max_retry or 3)
    return [gateway.apply(event) for event in events]


def warning(message: str, *, detail: Any = None) -> None:
    suffix = f": {detail}" if detail else ""
    sys.stderr.write(f"[contextseek] {message}{suffix}\n")
