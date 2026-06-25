"""Run a target agent through ContextSeek-managed PowerMem capabilities."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextseek.plugs.core.linkers import LinkerResult
from contextseek.plugs.powermem.env import ensure_managed_powermem_env
from contextseek.plugs.powermem.linkers import normalize_linker_name
from contextseek.plugs.powermem.linkers.claude_code_plugin import (
    ClaudeCodePluginRuntimeInstaller,
)
from contextseek.plugs.powermem.linkers.runtime import PowerMemHTTPRuntimeInstaller
from contextseek.plugs.powermem.serve import (
    PowerMemServePlan,
    _contextseek_proxy_env,
    _status_from_warnings,
    _terminate_process,
    _temporary_environ,
    build_powermem_serve_plan,
)


@dataclass(frozen=True)
class PowerMemRunPlan:
    """Resolved process plan for running a target agent with PowerMem hooks."""

    linker: str
    serve_plan: PowerMemServePlan
    serve_command: list[str]
    target_command: list[str]
    target_env: dict[str, str]
    plugin_dir: Path


def build_powermem_run_plan(args: Any) -> PowerMemRunPlan:
    """Build a deterministic run plan from parsed CLI args."""
    linker = _run_linker(getattr(args, "linker", None))
    serve_args = _serve_args(args, linker=linker)
    serve_plan = build_powermem_serve_plan(serve_args)
    plugin_installer = ClaudeCodePluginRuntimeInstaller()
    plugin_dir = plugin_installer.prepared_plugin_dir()
    target_command = [
        *_claude_command(),
        "--plugin-dir",
        str(plugin_dir),
        *_target_args(getattr(args, "claude_args", "")),
    ]
    target_env = {
        "POWERMEM_BASE_URL": serve_plan.proxy_base_url,
        "POWERMEM_AGENT_ID": "claude-code",
    }
    return PowerMemRunPlan(
        linker=linker,
        serve_plan=serve_plan,
        serve_command=_plug_serve_command(serve_args),
        target_command=target_command,
        target_env=target_env,
        plugin_dir=plugin_dir,
    )


def run_powermem_run(args: Any) -> int:
    """Run a target agent with a ContextSeek-managed PowerMem proxy."""
    plan = build_powermem_run_plan(args)
    if plan.linker != "claude-code":
        print(
            "[contextseek] plug-run currently supports Claude Code HTTP hook mode only",
            file=sys.stderr,
            flush=True,
        )
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    env_updates = _contextseek_proxy_env(plan.serve_plan)
    with _temporary_environ(env_updates):
        runtime_result = PowerMemHTTPRuntimeInstaller().install(dry_run=dry_run)
        env_result = ensure_managed_powermem_env(dry_run=dry_run)
        plugin_result = ClaudeCodePluginRuntimeInstaller().install(dry_run=dry_run)
        warnings = (
            list(runtime_result.warnings)
            + list(env_result.warnings)
            + list(plugin_result.warnings)
        )

        if dry_run:
            _print_dry_run(plan, runtime_result, env_result, plugin_result)
            return _status_from_warnings(warnings)
        if _status_from_warnings(warnings) != 0:
            _print_dry_run(plan, runtime_result, env_result, plugin_result)
            return 1

        serve_process = subprocess.Popen(plan.serve_command, text=True)  # noqa: S603
        try:
            time.sleep(float(getattr(args, "startup_grace", 1.0)))
            if serve_process.poll() is not None:
                print(
                    "[contextseek] plug-serve exited early: "
                    f"{serve_process.returncode}",
                    file=sys.stderr,
                    flush=True,
                )
                return int(serve_process.returncode or 1)
            child_env = os.environ.copy()
            child_env.update(plan.target_env)
            completed = subprocess.run(  # noqa: S603
                plan.target_command,
                env=child_env,
                check=False,
                text=True,
            )
            return int(completed.returncode)
        finally:
            _terminate_process(serve_process)


def _run_linker(linker: str | None) -> str:
    normalized = normalize_linker_name(linker or "claude-code")
    if normalized == "claude-code-http":
        return "claude-code"
    return normalized


def _serve_args(args: Any, *, linker: str) -> Any:
    return type(
        "_PowerMemRunServeArgs",
        (),
        {
            "plug": "powermem",
            "linker": linker,
            "host": getattr(args, "host", None),
            "port": getattr(args, "port", None),
            "scope": getattr(args, "scope", None),
            "powermem_host": getattr(args, "powermem_host", None),
            "powermem_port": getattr(args, "powermem_port", None),
            "powermem_command": getattr(args, "powermem_command", None),
            "powermem_upstream_base_url": getattr(
                args,
                "powermem_upstream_base_url",
                None,
            ),
            "proxy_base_url": getattr(args, "proxy_base_url", None),
            "no_install": True,
            "dry_run": getattr(args, "dry_run", False),
            "log_level": getattr(args, "log_level", "info"),
            "powermem_startup_grace": getattr(args, "powermem_startup_grace", 0.5),
        },
    )()


def _plug_serve_command(args: Any) -> list[str]:
    command = [
        *_contextseek_command(),
        "plug-serve",
        "powermem",
        "--linker",
        str(args.linker),
        "--no-install",
        "--host",
        str(args.host or "127.0.0.1"),
        "--port",
        str(args.port or 2882),
        "--powermem-host",
        str(args.powermem_host or "127.0.0.1"),
        "--powermem-port",
        str(args.powermem_port or 8000),
        "--log-level",
        str(args.log_level or "info"),
        "--powermem-startup-grace",
        str(args.powermem_startup_grace or 0.5),
    ]
    if args.scope:
        command.extend(["--scope", str(args.scope)])
    if args.powermem_command:
        command.extend(["--powermem-command", str(args.powermem_command)])
    if args.powermem_upstream_base_url:
        command.extend(
            ["--powermem-upstream-base-url", str(args.powermem_upstream_base_url)]
        )
    if args.proxy_base_url:
        command.extend(["--proxy-base-url", str(args.proxy_base_url)])
    return command


def _contextseek_command() -> list[str]:
    configured = os.environ.get("CONTEXTSEEK_COMMAND", "").strip()
    if configured:
        return shlex.split(configured)
    detected = shutil.which("contextseek")
    if detected:
        return [detected]
    sibling = Path(sys.executable).parent / "contextseek"
    if sibling.is_file():
        return [str(sibling)]
    return [sys.executable, "-m", "contextseek.cli.main"]


def _claude_command() -> list[str]:
    raw = os.environ.get("CONTEXTSEEK_CLAUDE_CODE_COMMAND", "").strip() or "claude"
    return shlex.split(raw)


def _target_args(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    return list(value)


def _print_dry_run(
    plan: PowerMemRunPlan,
    runtime_result: LinkerResult,
    env_result: LinkerResult,
    plugin_result: LinkerResult,
) -> None:
    payload = {
        "linker": plan.linker,
        "proxy_base_url": plan.serve_plan.proxy_base_url,
        "upstream_base_url": plan.serve_plan.upstream_base_url,
        "plugin_dir": str(plan.plugin_dir),
        "serve_command": plan.serve_command,
        "target_command": plan.target_command,
        "target_env": plan.target_env,
        "actions": list(runtime_result.actions)
        + list(env_result.actions)
        + list(plugin_result.actions),
        "warnings": list(runtime_result.warnings)
        + list(env_result.warnings)
        + list(plugin_result.warnings),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


__all__ = [
    "PowerMemRunPlan",
    "build_powermem_run_plan",
    "run_powermem_run",
]
