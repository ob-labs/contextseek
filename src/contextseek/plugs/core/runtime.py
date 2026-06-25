"""Shared runtime installers for plug linkers."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable

from contextseek.plugs.core.linkers import LinkerResult


_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_INSTALL_STRATEGY = "managed_venv"
_SUPPORTED_INSTALL_STRATEGIES = {"managed_venv", "current_env"}


@dataclass(frozen=True)
class PythonPackageVersionInfo:
    """Installed Python package version and compatibility bound."""

    package_name: str
    installed_version: str | None
    min_version: str | None = None


@dataclass(frozen=True)
class PythonPackageRuntimeInstaller:
    """Install or upgrade a Python package for a plug runtime.

    The default strategy is ``managed_venv`` so plug dependencies do not pollute
    the ContextSeek process environment. ``current_env`` remains available as an
    explicit development override.
    """

    package_name: str
    requirement: str
    display_name: str | None = None
    min_version: str | None = None
    install_env_var: str | None = None
    install_command_env_var: str | None = None
    install_strategy_env_var: str | None = None
    install_strategy_override: str | None = None
    managed_venv_env_var: str | None = None
    managed_venv_path: Path | None = None
    wheelhouse_env_var: str | None = None
    wheelhouse_path: Path | None = None
    ready_check_script: str | None = None
    ready_check_label: str | None = None
    version_info: Callable[[], PythonPackageVersionInfo] | None = None
    timeout: float = 300.0

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        if self.install_env_var and _disabled(
            os.environ.get(self.install_env_var, "1")
        ):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[
                    f"skip {self.label} package install: {self.install_env_var}=0"
                ],
            )

        strategy = self.install_strategy()
        if strategy not in _SUPPORTED_INSTALL_STRATEGIES:
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                warnings=[f"unsupported Python package install strategy: {strategy}"],
            )
        if strategy == "current_env":
            return self._install_current_env(dry_run=dry_run, check=check)
        return self._install_managed_venv(dry_run=dry_run, check=check)

    @property
    def label(self) -> str:
        return self.display_name or self.package_name

    def install_strategy(self) -> str:
        if self.install_strategy_override:
            return self.install_strategy_override.lower().replace("-", "_")
        raw = (
            os.environ.get(self.install_strategy_env_var, "").strip()
            if self.install_strategy_env_var
            else ""
        )
        return (raw or _DEFAULT_INSTALL_STRATEGY).lower().replace("-", "_")

    def managed_venv(self) -> Path:
        if self.managed_venv_env_var:
            configured = os.environ.get(self.managed_venv_env_var, "").strip()
            if configured:
                return Path(configured).expanduser()
        if self.managed_venv_path is not None:
            return self.managed_venv_path.expanduser()
        runtime_version = self.min_version or "default"
        return (
            Path.home()
            / ".contextseek"
            / "runtimes"
            / self.package_name
            / runtime_version
            / "venv"
        )

    def managed_bin(self, executable: str) -> Path:
        return _venv_bin(self.managed_venv(), executable)

    def _install_current_env(
        self,
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        info = self._current_env_version_info()
        runtime_ready = self._runtime_ready(Path(sys.executable))
        if (
            info.installed_version
            and not self._installed_version_too_old(info)
            and runtime_ready
        ):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[
                    f"{self.label} Python package already installed in current env: "
                    f"{info.package_name}=={info.installed_version}",
                ],
            )
        reason = self._install_reason(info, runtime_ready=runtime_ready)
        action = f"install Python package: {self.requirement} ({reason})"
        if dry_run or check:
            return LinkerResult(changed=True, dry_run=True, actions=[f"would {action}"])

        result = self._install_with_python(Path(sys.executable))
        if result.returncode != 0:
            return self._install_failure(action, result)
        return self._verify_after_install(
            action,
            version_info=self._current_env_version_info(),
            python=Path(sys.executable),
        )

    def _install_managed_venv(
        self,
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        venv = self.managed_venv()
        python = _venv_python(venv)
        actions = [f"use managed Python runtime: {venv}"]
        venv_ready = python.is_file()
        info = (
            self._python_version_info(python)
            if venv_ready
            else PythonPackageVersionInfo(self.package_name, None, self.min_version)
        )
        runtime_ready = venv_ready and self._runtime_ready(python)
        if (
            venv_ready
            and info.installed_version
            and not self._installed_version_too_old(info)
            and runtime_ready
        ):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions
                + [
                    f"{self.label} Python package already installed in managed runtime: "
                    f"{info.package_name}=={info.installed_version}",
                ],
            )

        reason = (
            "managed runtime missing"
            if not venv_ready
            else self._install_reason(info, runtime_ready=runtime_ready)
        )
        action = f"install Python package: {self.requirement} ({reason})"
        if dry_run or check:
            if not venv_ready:
                actions.append(f"would create managed Python runtime venv: {venv}")
            actions.append(f"would {action}")
            return LinkerResult(changed=True, dry_run=True, actions=actions)

        if not venv_ready:
            create_result = _run(
                [sys.executable, "-m", "venv", str(venv)],
                timeout=self.timeout,
            )
            actions.append(f"create managed Python runtime venv: {venv}")
            if create_result.returncode != 0:
                return LinkerResult(
                    changed=False,
                    dry_run=False,
                    actions=actions,
                    warnings=[
                        f"failed to create managed Python runtime venv {venv}: "
                        + _short_output(create_result),
                    ],
                )

        result = self._install_with_python(python, venv=venv)
        if result.returncode != 0:
            return self._install_failure(action, result, actions=actions)
        verify_info = self._python_version_info(python)
        return self._verify_after_install(
            action,
            version_info=verify_info,
            python=python,
            actions=actions,
        )

    def _current_env_version_info(self) -> PythonPackageVersionInfo:
        if self.version_info is not None:
            return self.version_info()
        try:
            installed = importlib_metadata.version(self.package_name)
        except importlib_metadata.PackageNotFoundError:
            installed = None
        return PythonPackageVersionInfo(
            package_name=self.package_name,
            installed_version=installed,
            min_version=self.min_version,
        )

    def _python_version_info(self, python: Path) -> PythonPackageVersionInfo:
        if not python.is_file():
            return PythonPackageVersionInfo(self.package_name, None, self.min_version)
        script = (
            "from importlib import metadata\n"
            "import sys\n"
            "try:\n"
            "    print(metadata.version(sys.argv[1]))\n"
            "except metadata.PackageNotFoundError:\n"
            "    raise SystemExit(3)\n"
        )
        result = _run([str(python), "-c", script, self.package_name], timeout=30.0)
        installed = (
            result.output.strip().splitlines()[-1] if result.returncode == 0 else None
        )
        return PythonPackageVersionInfo(
            package_name=self.package_name,
            installed_version=installed,
            min_version=self.min_version,
        )

    def _install_with_python(
        self,
        python: Path,
        *,
        venv: Path | None = None,
    ) -> "_CommandResult":
        configured = (
            os.environ.get(self.install_command_env_var, "").strip()
            if self.install_command_env_var
            else ""
        )
        wheelhouse = self._wheelhouse()
        if configured:
            command = _install_command_from_template(
                configured,
                requirement=self.requirement,
                python=python,
                venv=venv,
                wheelhouse=wheelhouse,
            )
            return _run(command, timeout=self.timeout)

        pip_command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            *self._wheelhouse_pip_args(wheelhouse),
            self.requirement,
        ]
        result = _run(pip_command, timeout=self.timeout)
        if result.returncode == 0:
            return result
        uv_result = _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                *self._wheelhouse_pip_args(wheelhouse),
                self.requirement,
            ],
            timeout=self.timeout,
        )
        return uv_result if uv_result.returncode == 0 else result

    def _wheelhouse(self) -> Path | None:
        if self.wheelhouse_env_var:
            configured = os.environ.get(self.wheelhouse_env_var, "").strip()
            if configured:
                return Path(configured).expanduser()
        return self.wheelhouse_path.expanduser() if self.wheelhouse_path else None

    def _wheelhouse_pip_args(self, wheelhouse: Path | None) -> list[str]:
        if wheelhouse is None:
            return []
        return ["--no-index", "--find-links", str(wheelhouse)]

    def _install_reason(
        self,
        info: PythonPackageVersionInfo,
        *,
        runtime_ready: bool,
    ) -> str:
        if not info.installed_version:
            return "not installed"
        if not runtime_ready:
            return f"{self.ready_label} missing"
        return f"installed {info.installed_version} < {info.min_version}"

    def _installed_version_too_old(self, info: PythonPackageVersionInfo) -> bool:
        return bool(
            info.installed_version
            and info.min_version
            and _version_lt(info.installed_version, info.min_version),
        )

    @property
    def ready_label(self) -> str:
        return self.ready_check_label or "runtime dependencies"

    def _runtime_ready(self, python: Path) -> bool:
        if not self.ready_check_script:
            return True
        if not python.is_file():
            return False
        result = _run([str(python), "-c", self.ready_check_script], timeout=30.0)
        return result.returncode == 0

    def _install_failure(
        self,
        action: str,
        result: "_CommandResult",
        *,
        actions: list[str] | None = None,
    ) -> LinkerResult:
        return LinkerResult(
            changed=False,
            dry_run=False,
            actions=list(actions or []) + [action],
            warnings=[
                f"failed to install Python package {self.requirement}: "
                + _short_output(result),
            ],
        )

    def _verify_after_install(
        self,
        action: str,
        *,
        version_info: PythonPackageVersionInfo,
        python: Path,
        actions: list[str] | None = None,
    ) -> LinkerResult:
        merged_actions = list(actions or []) + [action]
        warnings: list[str] = []
        if not version_info.installed_version:
            warnings.append(
                f"Python package install finished but {self.label} version cannot be verified",
            )
        elif self._installed_version_too_old(version_info):
            warnings.append(
                f"Python package install finished but {self.label} version is still too old: "
                f"{version_info.installed_version}",
            )
        elif not self._runtime_ready(python):
            warnings.append(
                f"Python package install finished but {self.label} "
                f"{self.ready_label} cannot be verified",
            )
        else:
            merged_actions.append(
                f"verified {self.label} Python package: "
                f"{version_info.package_name}=={version_info.installed_version}",
            )
        return LinkerResult(
            changed=not warnings,
            dry_run=False,
            actions=merged_actions,
            warnings=warnings,
        )


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    output: str


def _install_command_from_template(
    template: str,
    *,
    requirement: str,
    python: Path,
    venv: Path | None,
    wheelhouse: Path | None,
) -> list[str]:
    replacements = {
        "{requirement}": requirement,
        "{python}": str(python),
        "{venv}": str(venv or ""),
        "{wheelhouse}": str(wheelhouse or ""),
    }
    command = []
    for part in shlex.split(template):
        for key, value in replacements.items():
            part = part.replace(key, value)
        command.append(part)
    if "{requirement}" not in template:
        command.append(requirement)
    return command


def _run(command: list[str], *, timeout: float) -> _CommandResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _CommandResult(returncode=1, output=str(exc))
    return _CommandResult(
        returncode=completed.returncode,
        output=(completed.stdout or "") + (completed.stderr or ""),
    )


def _disabled(value: str) -> bool:
    return value.strip().lower() in _FALSE_VALUES


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _venv_bin(venv: Path, executable: str) -> Path:
    suffix = ".exe" if os.name == "nt" and not executable.endswith(".exe") else ""
    if os.name == "nt":
        return venv / "Scripts" / f"{executable}{suffix}"
    return venv / "bin" / executable


def _version_lt(left: str, right: str) -> bool:
    try:
        from packaging.version import Version

        return Version(left) < Version(right)
    except Exception:
        return _numeric_version_parts(left) < _numeric_version_parts(right)


def _numeric_version_parts(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in value.replace("-", ".").split(".") if part.isdigit()]
    return tuple(parts or [0])


def _short_output(result: _CommandResult) -> str:
    text = " ".join(result.output.strip().split())
    return text[:300] if text else f"exit code {result.returncode}"
