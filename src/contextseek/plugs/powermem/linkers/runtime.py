"""PowerMem runtime installers grouped by access mode."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from contextseek.plugs.core.linkers import LinkerResult, merge_linker_results
from contextseek.plugs.core.runtime import PythonPackageRuntimeInstaller
from contextseek.plugs.powermem.sdk import (
    POWERMEM_SDK_MIN_VERSION,
    POWERMEM_SDK_REQUIREMENT,
    powermem_sdk_version_info,
)


_PACKAGE_INSTALL_STRATEGY_ENV = "CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY"
_RUNTIME_DIR_ENV = "CONTEXTSEEK_POWERMEM_RUNTIME_DIR"
_WHEELHOUSE_ENV = "CONTEXTSEEK_POWERMEM_WHEELHOUSE"
_POWERMEM_SERVER_REQUIREMENT = f"powermem[server]>={POWERMEM_SDK_MIN_VERSION}"
_POWERMEM_SERVER_READY_CHECK = "import click, fastapi, uvicorn\n"
_POWERMEM_MCP_PACKAGE = "powermem-mcp"
_POWERMEM_MCP_REQUIREMENT = "powermem-mcp"
_SOCKSIO_PACKAGE = "socksio"
_SOCKSIO_REQUIREMENT = "socksio"
_OPTIONAL_PROVIDER_REQUIREMENTS = {
    "ollama": ("ollama", "ollama"),
}


@dataclass(frozen=True)
class PowerMemHTTPRuntimeInstaller:
    """Runtime requirements for PowerMem HTTP/server mode."""

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return merge_linker_results(
            _powermem_python_package_installer(
                requirement=_POWERMEM_SERVER_REQUIREMENT,
                ready_check_script=_POWERMEM_SERVER_READY_CHECK,
                ready_check_label="server dependencies",
            ).install(
                dry_run=dry_run,
                check=check,
            ),
            _socksio_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            dry_run=dry_run or check,
        )

    def server_command(self) -> list[str]:
        if _runtime_strategy() == "current_env":
            return ["powermem-server"]
        return [
            str(
                _powermem_python_package_installer(
                    requirement=_POWERMEM_SERVER_REQUIREMENT,
                ).managed_bin("powermem-server"),
            ),
        ]


@dataclass(frozen=True)
class PowerMemCLIRuntimeInstaller:
    """Runtime requirements for PowerMem CLI mode."""

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return merge_linker_results(
            _powermem_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            _socksio_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            dry_run=dry_run or check,
        )

    def cli_command(self) -> str:
        if _runtime_strategy() == "current_env":
            detected = shutil.which("pmem")
            return detected or ""
        return str(_powermem_python_package_installer().managed_bin("pmem"))


@dataclass(frozen=True)
class PowerMemSDKRuntimeInstaller:
    """Runtime requirements for PowerMem SDK mode."""

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return merge_linker_results(
            _powermem_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            _socksio_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            dry_run=dry_run or check,
        )


@dataclass(frozen=True)
class PowerMemMCPRuntimeInstaller:
    """Runtime requirements for PowerMem MCP mode."""

    mcp_command: str = "contextseek-pmem-mcp-stdio"
    backend_command_env_var: str = "CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND"
    backend_executable: str = "powermem-mcp"

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        results = [
            LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[f"use PowerMem MCP runtime: {self.mcp_command}"],
            ),
            _powermem_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
            _socksio_python_package_installer().install(
                dry_run=dry_run,
                check=check,
            ),
        ]
        results.extend(
            installer.install(dry_run=dry_run, check=check)
            for installer in _powermem_optional_provider_installers()
        )
        if os.environ.get(self.backend_command_env_var, "").strip():
            results.extend(
                [
                    _powermem_mcp_python_package_installer().install(
                        dry_run=dry_run,
                        check=check,
                    ),
                ]
            )
        return merge_linker_results(*results, dry_run=dry_run or check)

    def python_command(self) -> str:
        if _runtime_strategy() == "current_env":
            return sys.executable
        return str(_venv_python_path(_powermem_managed_venv_path()))

    def backend_command(self) -> list[str]:
        configured = os.environ.get(self.backend_command_env_var, "").strip()
        if configured:
            return shlex.split(configured)
        if _runtime_strategy() == "current_env":
            detected = shutil.which(self.backend_executable)
            if detected:
                return [detected, "stdio"]
            return ["uvx", "--with", "socksio", _POWERMEM_MCP_PACKAGE, "stdio"]
        return [
            str(
                _powermem_mcp_python_package_installer().managed_bin(
                    self.backend_executable,
                )
            ),
            "stdio",
        ]


def _powermem_python_package_installer(
    *,
    requirement: str = POWERMEM_SDK_REQUIREMENT,
    ready_check_script: str | None = None,
    ready_check_label: str | None = None,
) -> PythonPackageRuntimeInstaller:
    return PythonPackageRuntimeInstaller(
        package_name="powermem",
        requirement=requirement,
        display_name="PowerMem",
        min_version=POWERMEM_SDK_MIN_VERSION,
        install_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL",
        install_command_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
        install_strategy_env_var=_PACKAGE_INSTALL_STRATEGY_ENV,
        managed_venv_path=_powermem_managed_venv_path(),
        wheelhouse_env_var=_WHEELHOUSE_ENV,
        ready_check_script=ready_check_script,
        ready_check_label=ready_check_label,
        version_info=powermem_sdk_version_info,
    )


def _powermem_mcp_python_package_installer() -> PythonPackageRuntimeInstaller:
    return PythonPackageRuntimeInstaller(
        package_name=_POWERMEM_MCP_PACKAGE,
        requirement=_POWERMEM_MCP_REQUIREMENT,
        display_name="PowerMem MCP",
        install_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL",
        install_command_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
        install_strategy_env_var=_PACKAGE_INSTALL_STRATEGY_ENV,
        managed_venv_path=_powermem_managed_venv_path(),
        wheelhouse_env_var=_WHEELHOUSE_ENV,
    )


def _socksio_python_package_installer() -> PythonPackageRuntimeInstaller:
    return PythonPackageRuntimeInstaller(
        package_name=_SOCKSIO_PACKAGE,
        requirement=_SOCKSIO_REQUIREMENT,
        display_name="PowerMem MCP socksio",
        install_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL",
        install_command_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
        install_strategy_env_var=_PACKAGE_INSTALL_STRATEGY_ENV,
        managed_venv_path=_powermem_managed_venv_path(),
        wheelhouse_env_var=_WHEELHOUSE_ENV,
    )


def _powermem_optional_provider_installers() -> list[PythonPackageRuntimeInstaller]:
    from contextseek.plugs.powermem.env import build_powermem_env_defaults

    values = build_powermem_env_defaults()
    providers = {
        values.get("LLM_PROVIDER", "").strip().lower(),
        values.get("EMBEDDING_PROVIDER", "").strip().lower(),
    }
    installers: list[PythonPackageRuntimeInstaller] = []
    for provider in sorted(providers):
        if provider not in _OPTIONAL_PROVIDER_REQUIREMENTS:
            continue
        package_name, requirement = _OPTIONAL_PROVIDER_REQUIREMENTS[provider]
        installers.append(
            PythonPackageRuntimeInstaller(
                package_name=package_name,
                requirement=requirement,
                display_name=f"PowerMem {provider} provider",
                install_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL",
                install_command_env_var="CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
                install_strategy_env_var=_PACKAGE_INSTALL_STRATEGY_ENV,
                managed_venv_path=_powermem_managed_venv_path(),
                wheelhouse_env_var=_WHEELHOUSE_ENV,
            )
        )
    return installers


def _runtime_strategy() -> str:
    raw = os.environ.get(_PACKAGE_INSTALL_STRATEGY_ENV, "").strip()
    return (raw or "managed_venv").lower().replace("-", "_")


def _powermem_managed_venv_path() -> Path:
    configured = os.environ.get(_RUNTIME_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser() / "venv"
    return (
        Path.home()
        / ".contextseek"
        / "runtimes"
        / "powermem"
        / POWERMEM_SDK_MIN_VERSION
        / "venv"
    )


def _venv_python_path(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"
