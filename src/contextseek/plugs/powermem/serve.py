"""Serve PowerMem through a ContextSeek-managed HTTP proxy."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from contextseek.plugs.core.linkers import LinkerResult
from contextseek.plugs.powermem.adapter import DEFAULT_CONTEXTSEEK_SCOPE
from contextseek.plugs.powermem.env import (
    ensure_managed_powermem_env,
    managed_powermem_env_path,
    powermem_child_process_cwd,
    powermem_child_process_env,
)
from contextseek.plugs.powermem.linkers.runtime import PowerMemHTTPRuntimeInstaller


@dataclass(frozen=True)
class PowerMemServePlan:
    """Resolved process and URL plan for ContextSeek-managed PowerMem serving."""

    contextseek_host: str
    contextseek_port: int
    powermem_host: str
    powermem_port: int
    proxy_base_url: str
    upstream_base_url: str
    powermem_command: list[str]
    managed_env_path: Path
    default_scope: str | None
    linker: str | None
    install_linker: bool


def build_powermem_serve_plan(args: Any) -> PowerMemServePlan:
    """Build a deterministic serve plan from parsed CLI args."""
    contextseek_host = str(getattr(args, "host", None) or "127.0.0.1")
    contextseek_port = int(getattr(args, "port", None) or 2882)
    powermem_host = str(getattr(args, "powermem_host", None) or "127.0.0.1")
    powermem_port = int(getattr(args, "powermem_port", None) or 8000)
    proxy_base_url = str(
        getattr(args, "proxy_base_url", None)
        or _base_url_for_host(
            contextseek_host,
            contextseek_port,
            path="/plugins/powermem/default",
        )
    ).rstrip("/")
    upstream_base_url = str(
        getattr(args, "powermem_upstream_base_url", None)
        or _base_url_for_host(powermem_host, powermem_port)
    ).rstrip("/")
    command = _powermem_server_command(
        getattr(args, "powermem_command", None),
        host=powermem_host,
        port=powermem_port,
    )
    return PowerMemServePlan(
        contextseek_host=contextseek_host,
        contextseek_port=contextseek_port,
        powermem_host=powermem_host,
        powermem_port=powermem_port,
        proxy_base_url=proxy_base_url,
        upstream_base_url=upstream_base_url,
        powermem_command=command,
        managed_env_path=managed_powermem_env_path(),
        default_scope=_default_scope(
            getattr(args, "scope", None),
            linker=getattr(args, "linker", None),
        ),
        linker=getattr(args, "linker", None),
        install_linker=not bool(getattr(args, "no_install", False)),
    )


def run_powermem_serve(args: Any) -> int:
    """Run PowerMem HTTP upstream and ContextSeek HTTP proxy as one command."""
    plan = build_powermem_serve_plan(args)
    if plan.linker:
        from contextseek.plugs.powermem.linkers import (
            disabled_linker_message,
            is_linker_disabled,
        )

        if is_linker_disabled(plan.linker):
            warning = disabled_linker_message(plan.linker)
            if bool(getattr(args, "dry_run", False)):
                _print_dry_run(
                    plan,
                    env_result=None,
                    install_result=LinkerResult(
                        changed=False,
                        dry_run=True,
                        warnings=[warning],
                    ),
                )
            else:
                print(f"[contextseek] {warning}", file=sys.stderr, flush=True)
            return 1
    env_updates = _contextseek_proxy_env(plan)

    with _temporary_environ(env_updates):
        install_result = None
        http_result = PowerMemHTTPRuntimeInstaller().install(
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        if _status_from_warnings(list(http_result.warnings)) != 0:
            _print_dry_run(
                plan,
                env_result=None,
                install_result=None,
                runtime_result=http_result,
            )
            return 1
        if plan.install_linker and plan.linker:
            from contextseek.plugs.powermem import PowerMemAdapter

            install_result = PowerMemAdapter().install(
                linker=plan.linker,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
            if _status_from_warnings(list(install_result.warnings)) != 0:
                _print_dry_run(
                    plan,
                    env_result=None,
                    install_result=install_result,
                    runtime_result=http_result,
                )
                return 1
        env_result = ensure_managed_powermem_env(
            dry_run=bool(getattr(args, "dry_run", False)),
        )

    if bool(getattr(args, "dry_run", False)):
        _print_dry_run(plan, env_result, install_result, runtime_result=http_result)
        return _status_from_warnings(
            list(env_result.warnings)
            + list(getattr(http_result, "warnings", []) or [])
            + list(getattr(install_result, "warnings", []) or []),
        )

    process = _start_powermem_server(plan)
    try:
        time.sleep(float(getattr(args, "powermem_startup_grace", 0.5)))
        if process.poll() is not None:
            print(
                f"[contextseek] PowerMem server exited early: {process.returncode}",
                file=sys.stderr,
                flush=True,
            )
            return int(process.returncode or 1)
        return _run_contextseek_http(
            plan, log_level=str(getattr(args, "log_level", "info"))
        )
    finally:
        _terminate_process(process)


def _powermem_server_command(
    configured: str | None,
    *,
    host: str,
    port: int,
) -> list[str]:
    raw = configured or os.environ.get("CONTEXTSEEK_POWERMEM_SERVER_COMMAND")
    command = (
        shlex.split(raw) if raw else PowerMemHTTPRuntimeInstaller().server_command()
    )
    if not command:
        command = PowerMemHTTPRuntimeInstaller().server_command()
    return [*command, "--host", host, "--port", str(port)]


def _base_url_for_host(host: str, port: int, *, path: str = "") -> str:
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    suffix = "/" + path.lstrip("/") if path else ""
    return f"http://{url_host}:{port}{suffix}"


def _default_scope(scope: str | None, *, linker: str | None) -> str:
    if scope:
        return scope
    return DEFAULT_CONTEXTSEEK_SCOPE


def _contextseek_proxy_env(plan: PowerMemServePlan) -> dict[str, str]:
    values = {
        "CONTEXTSEEK_POWERMEM_PROXY_BASE_URL": plan.proxy_base_url,
        "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL": plan.upstream_base_url,
    }
    if plan.default_scope:
        values["CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"] = plan.default_scope
    return values


def _start_powermem_server(plan: PowerMemServePlan) -> subprocess.Popen[str]:
    env = powermem_child_process_env()
    return subprocess.Popen(  # noqa: S603
        plan.powermem_command,
        env=env,
        cwd=powermem_child_process_cwd(),
        text=True,
    )


def _run_contextseek_http(plan: PowerMemServePlan, *, log_level: str) -> int:
    os.environ.update(_contextseek_proxy_env(plan))
    _publish_claude_code_hook_env(plan)
    try:
        import uvicorn

        from contextseek.http.server import create_app
    except ImportError as exc:
        print(
            "[contextseek] missing HTTP dependencies. "
            "Install with: pip install contextseek[http]\n"
            f"  ({exc})",
            file=sys.stderr,
            flush=True,
        )
        return 1
    uvicorn.run(
        create_app(),
        host=plan.contextseek_host,
        port=plan.contextseek_port,
        log_level=log_level,
    )
    return 0


def _publish_claude_code_hook_env(plan: PowerMemServePlan) -> None:
    if plan.linker not in {"claude-code", "claude-code-http"}:
        return
    try:
        from contextseek.plugs.powermem.linkers.claude_code_plugin import (
            write_claude_code_plugin_runtime_envs,
        )

        write_claude_code_plugin_runtime_envs(plan.proxy_base_url)
    except Exception as exc:  # pragma: no cover - defensive serve bootstrap
        print(
            f"[contextseek] PowerMem hook env update skipped: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _print_dry_run(
    plan: PowerMemServePlan,
    env_result: Any,
    install_result: Any,
    *,
    runtime_result: Any = None,
) -> None:
    payload = {
        "proxy_base_url": plan.proxy_base_url,
        "upstream_base_url": plan.upstream_base_url,
        "powermem_command": plan.powermem_command,
        "managed_env": str(plan.managed_env_path),
        "default_scope": plan.default_scope,
        "contextseek": {
            "host": plan.contextseek_host,
            "port": plan.contextseek_port,
        },
        "powermem": {
            "host": plan.powermem_host,
            "port": plan.powermem_port,
        },
        "actions": list(getattr(runtime_result, "actions", []) or [])
        + list(getattr(env_result, "actions", []) or [])
        + list(getattr(install_result, "actions", []) or []),
        "warnings": list(getattr(runtime_result, "warnings", []) or [])
        + list(getattr(env_result, "warnings", []) or [])
        + list(getattr(install_result, "warnings", []) or []),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _status_from_warnings(warnings: list[str]) -> int:
    fatal_prefixes = (
        "disabled linker",
        "unknown linker",
        "Claude Code CLI cannot be found",
        "failed to install Claude Code plugin",
        "failed to enable Claude Code plugin",
        "failed to prepare Claude Code plugin dir",
        "Claude Code plugin",
        "OpenClaw CLI cannot be found",
        "failed to install OpenClaw plugin",
        "failed to verify OpenClaw plugin",
        "failed to install Python package",
        "Python package install finished",
        "PowerMem LLM_PROVIDER cannot be inferred",
    )
    return 1 if any(w.startswith(fatal_prefixes) for w in warnings) else 0


@contextmanager
def _temporary_environ(updates: dict[str, str]) -> Iterator[None]:
    old_values: dict[str, str | None] = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


__all__ = [
    "PowerMemServePlan",
    "build_powermem_serve_plan",
    "run_powermem_serve",
]
