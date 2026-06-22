"""Claude Code plugin runtime installation for PowerMem."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from contextseek.plugs.core.linkers import LinkerResult


_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_PLUGIN_NAME = "memory-powermem"
_DEFAULT_SCOPE = "user"


@dataclass(frozen=True)
class ClaudeCodePluginRuntimeInstaller:
    """Install or enable the PowerMem Claude Code plugin."""

    plugin_name: str = _DEFAULT_PLUGIN_NAME
    scope: str = _DEFAULT_SCOPE
    command_env_var: str = "CONTEXTSEEK_CLAUDE_CODE_COMMAND"
    install_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL"
    marketplace_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MARKETPLACE"
    plugin_dir_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_DIR"
    timeout: float = 30.0

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        if _disabled(os.environ.get(self.install_env_var, "1")):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[
                    f"skip Claude Code plugin runtime install: {self.install_env_var}=0",
                ],
            )

        command = _claude_command(self.command_env_var)
        actions = [
            f"detect Claude Code plugin: {self.plugin_name}",
        ]
        warnings: list[str] = []
        if not _command_available(command):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    "Claude Code CLI cannot be found; install/enable "
                    f"{self.plugin_name} manually or set {self.command_env_var}",
                ],
            )

        state = self._installed_state(command)
        if state.installed:
            actions.append(f"Claude Code plugin already installed: {self.plugin_name}")
            changed = False
            if state.disabled:
                enable_result = self._enable(command, dry_run=dry_run, check=check)
                actions.extend(enable_result.actions)
                warnings.extend(enable_result.warnings)
                changed = changed or enable_result.changed
            warnings.extend(self._local_plugin_warnings())
            return LinkerResult(
                changed=changed,
                dry_run=dry_run or check,
                actions=actions,
                warnings=warnings,
            )

        marketplace = os.environ.get(self.marketplace_env_var, "").strip()
        if dry_run or check:
            if marketplace:
                actions.append(f"would add Claude Code marketplace: {marketplace}")
            actions.append(
                f"would install Claude Code plugin: {self.plugin_name} --scope {self.scope}",
            )
            warnings.extend(self._local_plugin_warnings())
            return LinkerResult(
                changed=True,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )

        if marketplace:
            marketplace_result = self._run(
                command,
                ["plugin", "marketplace", "add", marketplace],
            )
            actions.append(f"add Claude Code marketplace: {marketplace}")
            if marketplace_result.returncode != 0:
                warnings.append(
                    "failed to add Claude Code marketplace: "
                    + _short_output(marketplace_result),
                )

        install_result = self._run(
            command,
            ["plugin", "install", "--scope", self.scope, self.plugin_name],
        )
        actions.append(
            f"install Claude Code plugin: {self.plugin_name} --scope {self.scope}",
        )
        if install_result.returncode != 0:
            warnings.append(
                f"failed to install Claude Code plugin {self.plugin_name}: "
                + _short_output(install_result),
            )
            return LinkerResult(
                changed=False,
                dry_run=False,
                actions=actions,
                warnings=warnings,
            )

        state = self._installed_state(command)
        if not state.installed:
            warnings.append(
                f"Claude Code plugin {self.plugin_name} was installed but cannot be verified",
            )
        elif state.disabled:
            enable_result = self._enable(command, dry_run=False, check=False)
            actions.extend(enable_result.actions)
            warnings.extend(enable_result.warnings)
        warnings.extend(self._local_plugin_warnings())
        return LinkerResult(
            changed=True,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )

    def _enable(
        self,
        command: list[str],
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        action = f"enable Claude Code plugin: {self.plugin_name} --scope {self.scope}"
        if dry_run or check:
            return LinkerResult(changed=True, dry_run=True, actions=[f"would {action}"])
        result = self._run(
            command,
            ["plugin", "enable", "--scope", self.scope, self.plugin_name],
        )
        if result.returncode != 0:
            return LinkerResult(
                changed=False,
                actions=[action],
                warnings=[
                    f"failed to enable Claude Code plugin {self.plugin_name}: "
                    + _short_output(result),
                ],
            )
        return LinkerResult(changed=True, actions=[action])

    def _installed_state(self, command: list[str]) -> "_PluginState":
        details = self._run(command, ["plugin", "details", self.plugin_name])
        if details.returncode != 0:
            return _PluginState(installed=False, disabled=False)

        listing = self._run(command, ["plugin", "list"])
        disabled = False
        if listing.returncode == 0:
            disabled = _plugin_line_mentions_disabled(listing.output, self.plugin_name)
        return _PluginState(installed=True, disabled=disabled)

    def _local_plugin_warnings(self) -> list[str]:
        plugin_dir = os.environ.get(self.plugin_dir_env_var, "").strip()
        if not plugin_dir:
            return []
        hook = _hook_binary(Path(plugin_dir).expanduser())
        if hook.is_file():
            return []
        return [f"Claude Code PowerMem hook binary is missing: {hook}"]

    def _run(self, command: list[str], args: list[str]) -> "_CommandResult":
        try:
            completed = subprocess.run(
                [*command, *args],
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _CommandResult(returncode=1, output=str(exc))
        return _CommandResult(
            returncode=completed.returncode,
            output=(completed.stdout or "") + (completed.stderr or ""),
        )


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    output: str


@dataclass(frozen=True)
class _PluginState:
    installed: bool
    disabled: bool


def _claude_command(env_var: str) -> list[str]:
    raw = os.environ.get(env_var, "").strip() or "claude"
    return shlex.split(raw)


def _command_available(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if os.sep in executable:
        return Path(executable).expanduser().is_file()
    return shutil.which(executable) is not None


def _disabled(value: str) -> bool:
    return value.strip().lower() in _FALSE_VALUES


def _plugin_line_mentions_disabled(output: str, plugin_name: str) -> bool:
    for line in output.splitlines():
        lowered = line.lower()
        if plugin_name.lower() in lowered and "disabled" in lowered:
            return True
    return False


def _hook_binary(plugin_dir: Path) -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        goos = "darwin"
    elif system == "linux":
        goos = "linux"
    else:
        goos = system or "unknown"
    goarch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    return plugin_dir / "hooks" / "bin" / f"powermem-hook-{goos}-{goarch}"


def _short_output(result: _CommandResult) -> str:
    text = " ".join(result.output.strip().split())
    return text[:300] if text else f"exit code {result.returncode}"
