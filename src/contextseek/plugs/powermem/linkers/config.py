"""PowerMem linker implementations that write target configuration."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextseek.plugs.core.linkers import (
    LifecycleLinker,
    LinkerResult,
    merge_linker_results,
)
from contextseek.plugs.powermem.env import (
    ensure_managed_powermem_env,
    managed_powermem_env_path,
)
from contextseek.plugs.powermem.linkers.claude_code_plugin import (
    ClaudeCodePluginRuntimeInstaller,
)
from contextseek.plugs.powermem.linkers.runtime import (
    PowerMemCLIRuntimeInstaller,
    PowerMemHTTPRuntimeInstaller,
    PowerMemMCPRuntimeInstaller,
)


_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class PowerMemMCPConfigLinker(LifecycleLinker):
    """Install a PowerMem MCP proxy server into a target MCP config file."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    server_name: str = "powermem"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemMCPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before = _read_json(path)
        after = dict(before)
        servers = dict(after.get("mcpServers") or {})
        desired = _powermem_mcp_server_config()
        servers[self.server_name] = desired
        after["mcpServers"] = servers
        changed = env_result.changed or after != before
        actions = env_result.actions + [
            f"write {self.target} MCP config: {path}",
            f"set mcpServers.{self.server_name}.command={desired['command']}",
            f"route {plug_name} MCP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=env_result.warnings,
            )
        if changed:
            _write_json(path, after)
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=env_result.warnings,
        )


@dataclass(frozen=True)
class PowerMemVSCodeMCPConfigLinker(LifecycleLinker):
    """Install a PowerMem MCP proxy server into a VS Code MCP config file."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    server_name: str = "powermem"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemMCPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before_text = _read_text(path)
        before = _read_jsonc(path)
        warnings: list[str] = list(env_result.warnings)
        if before is None:
            before = {}
            warnings.append(
                f"skip writing {path}: existing VS Code MCP config is not valid JSON/JSONC",
            )
        after = dict(before)
        servers = dict(after.get("servers") or {})
        desired = _powermem_vscode_mcp_server_config()
        servers[self.server_name] = desired
        after["servers"] = servers
        config_changed = after != before and len(warnings) == len(env_result.warnings)
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} VS Code MCP config: {path}",
            f"set servers.{self.server_name}.command={desired['command']}",
            f"route {plug_name} MCP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )
        if config_changed:
            _write_jsonc_top_level_object_entry(
                path,
                before_text,
                after,
                object_key="servers",
                entry_key=self.server_name,
            )
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )


@dataclass(frozen=True)
class PowerMemOpenCodeConfigLinker(LifecycleLinker):
    """Install a PowerMem MCP proxy server into OpenCode config."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    server_name: str = "powermem"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemMCPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before_text = _read_text(path)
        before = _read_jsonc(path)
        warnings: list[str] = list(env_result.warnings)
        if before is None:
            before = {}
            warnings.append(
                f"skip writing {path}: existing OpenCode config is not valid JSON/JSONC",
            )
        after = dict(before)
        servers = dict(after.get("mcp") or {})
        desired = _powermem_opencode_mcp_server_config()
        servers[self.server_name] = desired
        after["mcp"] = servers
        config_changed = after != before and len(warnings) == len(env_result.warnings)
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} config: {path}",
            f"set mcp.{self.server_name}.command={desired['command'][0]}",
            f"route {plug_name} MCP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )
        if config_changed:
            _write_jsonc_top_level_object_entry(
                path,
                before_text,
                after,
                object_key="mcp",
                entry_key=self.server_name,
            )
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )


@dataclass(frozen=True)
class PowerMemCodexConfigLinker(LifecycleLinker):
    """Install a PowerMem MCP proxy server into legacy Codex config.toml."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    server_name: str = "powermem"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemMCPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before = _read_text(path)
        section = _powermem_codex_toml_section(self.server_name)
        after = _upsert_toml_section(
            before,
            header=f"mcp_servers.{self.server_name}",
            section=section,
        )
        config_changed = after != before
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} config.toml: {path}",
            f"set [mcp_servers.{self.server_name}] command={_powermem_mcp_command()}",
            f"route {plug_name} MCP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=env_result.warnings,
            )
        if config_changed:
            _write_text(path, after)
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=env_result.warnings,
        )


@dataclass(frozen=True)
class PowerMemWindsurfConfigLinker(LifecycleLinker):
    """Install a PowerMem MCP context provider into Windsurf config."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemMCPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before = _read_json(path)
        after = dict(before)
        after["contextProvider"] = "powermem-mcp"
        after["mcp"] = {"configPath": _powermem_mcp_command()}
        config_changed = after != before
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} context config: {path}",
            "set contextProvider=powermem-mcp",
            f"set mcp.configPath={_powermem_mcp_command()}",
            f"route {plug_name} MCP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=env_result.warnings,
            )
        if config_changed:
            _write_json(path, after)
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=env_result.warnings,
        )


@dataclass(frozen=True)
class PowerMemClaudeCodeHTTPConfigLinker(LifecycleLinker):
    """Route PowerMem Claude Code plugin HTTP hooks through ContextSeek."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    mcp_config_env_var: str
    mcp_default_config_path: Path
    server_name: str = "powermem"
    plugin_name_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN"
    plugin_scope_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_SCOPE"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        http_result = PowerMemHTTPRuntimeInstaller().install(
            dry_run=dry_run,
            check=check,
        )
        plugin_installer = ClaudeCodePluginRuntimeInstaller(
            plugin_name=os.environ.get(
                self.plugin_name_env_var,
                "memory-powermem",
            ).strip()
            or "memory-powermem",
            scope=os.environ.get(self.plugin_scope_env_var, "user").strip() or "user",
        )
        plugin_result = plugin_installer.install(dry_run=dry_run, check=check)
        return merge_linker_results(
            http_result,
            plugin_result,
            dry_run=dry_run or check,
        )

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        settings_before_text = _read_text(path)
        settings_before = _read_jsonc(path)
        warnings: list[str] = list(env_result.warnings)
        if settings_before is None:
            settings_before = {}
            warnings.append(
                f"skip writing {path}: existing Claude Code settings are not valid JSON/JSONC",
            )

        proxy_url = _contextseek_powermem_proxy_url()
        settings_after = dict(settings_before)
        env = dict(settings_after.get("env") or {})
        env["POWERMEM_BASE_URL"] = proxy_url
        env.setdefault("POWERMEM_AGENT_ID", "claude-code")
        settings_after["env"] = env

        settings_changed = settings_after != settings_before and len(warnings) == len(
            env_result.warnings
        )
        mcp_path = _path_from_env(self.mcp_config_env_var, self.mcp_default_config_path)
        mcp_changed = False
        mcp_servers: dict[str, Any] = {}
        mcp_before_text = ""
        if mcp_path.exists():
            mcp_before_text = _read_text(mcp_path)
            mcp_before = _read_jsonc(mcp_path)
            if mcp_before is None:
                warnings.append(
                    f"skip cleaning {mcp_path}: existing Claude Code MCP config is not valid JSON/JSONC",
                )
            else:
                mcp_servers = dict(mcp_before.get("mcpServers") or {})
                if self.server_name in mcp_servers:
                    mcp_servers.pop(self.server_name, None)
                    mcp_changed = True

        changed = env_result.changed or settings_changed or mcp_changed
        actions = env_result.actions + [
            f"write {self.target} settings env: {path}",
            f"set env.POWERMEM_BASE_URL={proxy_url}",
            "set default env.POWERMEM_AGENT_ID=claude-code",
            f"remove mcpServers.{self.server_name} from {mcp_path} when present",
            f"route {plug_name} HTTP hooks through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )
        if settings_changed:
            _write_jsonc_top_level_entry(
                path, settings_before_text, key="env", value=env
            )
        if mcp_changed:
            _write_jsonc_top_level_entry(
                mcp_path,
                mcp_before_text,
                key="mcpServers",
                value=mcp_servers,
            )
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )


@dataclass(frozen=True)
class PowerMemEnvFileLinker(LifecycleLinker):
    """Install a PowerMem HTTP proxy endpoint into an env-style config file."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return PowerMemHTTPRuntimeInstaller().install(dry_run=dry_run, check=check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        env_result = ensure_managed_powermem_env(dry_run=dry_run, check=check)
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before = _read_env_file(path)
        proxy_url = _contextseek_powermem_proxy_url()
        after = dict(before)
        after["POWERMEM_BASE_URL"] = proxy_url
        config_changed = after != before
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} env config: {path}",
            f"set POWERMEM_BASE_URL={proxy_url}",
            f"route {plug_name} HTTP calls through ContextSeek PowerMem proxy",
        ]
        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=env_result.warnings,
            )
        if config_changed:
            _write_env_file(path, after)
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=env_result.warnings,
        )


@dataclass(frozen=True)
class PowerMemOpenClawCLIConfigLinker(LifecycleLinker):
    """Install PowerMem CLI proxy settings into OpenClaw config."""

    name: str
    target: str
    config_env_var: str
    default_config_path: Path
    plugin_name: str = "memory-powermem"

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return merge_linker_results(
            PowerMemCLIRuntimeInstaller().install(
                dry_run=dry_run,
                check=check,
            ),
            OpenClawPluginRuntimeInstaller(plugin_name=self.plugin_name).install(
                dry_run=dry_run,
                check=check,
            ),
            dry_run=dry_run or check,
        )

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        real_cli = _real_powermem_cli_command()
        cli_warnings = _powermem_cli_warnings(real_cli)
        env_result = ensure_managed_powermem_env(
            dry_run=dry_run,
            check=check,
            extra_defaults=_powermem_cli_env_defaults(real_cli),
        )
        path = _path_from_env(self.config_env_var, self.default_config_path)
        before_text = _read_text(path)
        before = _read_jsonc(path)
        warnings = list(env_result.warnings) + cli_warnings
        if before is None:
            before = {}
            warnings.append(
                f"skip writing {path}: existing OpenClaw config is not valid JSON/JSONC",
            )

        after = dict(before)
        plugins = dict(after.get("plugins") or {})
        slots = dict(plugins.get("slots") or {})
        entries = dict(plugins.get("entries") or {})
        entry = dict(entries.get(self.plugin_name) or {})
        config = dict(entry.get("config") or {})

        slots["memory"] = self.plugin_name
        config.update(
            {
                "mode": "cli",
                "pmemPath": _powermem_cli_proxy_command(),
                "envFile": str(managed_powermem_env_path()),
            }
        )
        config.pop("baseUrl", None)
        config.setdefault("autoCapture", True)
        config.setdefault("autoRecall", True)
        config.setdefault("inferOnAdd", True)
        entry["enabled"] = True
        entry["config"] = config
        entries[self.plugin_name] = entry
        plugins["slots"] = slots
        plugins["entries"] = entries
        after["plugins"] = plugins

        can_write_config = len(warnings) == len(env_result.warnings) + len(
            cli_warnings,
        )
        config_changed = after != before and can_write_config
        changed = env_result.changed or config_changed
        actions = env_result.actions + [
            f"write {self.target} config: {path}",
            f"set plugins.slots.memory={self.plugin_name}",
            f"set plugins.entries.{self.plugin_name}.config.mode=cli",
            f"set pmemPath={_powermem_cli_proxy_command()}",
            f"set envFile={managed_powermem_env_path()}",
            f"route {plug_name} CLI calls through ContextSeek PowerMem proxy",
        ]
        if real_cli:
            actions.append(f"set CONTEXTSEEK_POWERMEM_CLI={real_cli}")

        if check or dry_run:
            return LinkerResult(
                changed=changed,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )
        if config_changed:
            _write_jsonc_top_level_entry(
                path,
                before_text,
                key="plugins",
                value=after["plugins"],
            )
        return LinkerResult(
            changed=changed,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )


@dataclass(frozen=True)
class OpenClawPluginRuntimeInstaller:
    """Ensure the target OpenClaw plugin is installed before writing config."""

    plugin_name: str = "memory-powermem"
    command_env_var: str = "CONTEXTSEEK_OPENCLAW_COMMAND"
    install_env_var: str = "CONTEXTSEEK_POWERMEM_OPENCLAW_PLUGIN_INSTALL"
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
                    f"skip OpenClaw plugin install: {self.install_env_var}=0",
                ],
            )

        command = _openclaw_command(self.command_env_var)
        actions = [f"detect OpenClaw plugin: {self.plugin_name}"]
        if not _command_available(command):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    "OpenClaw CLI cannot be found; install OpenClaw or set "
                    f"{self.command_env_var}",
                ],
            )

        list_result = self._run(command, ["plugins", "list"])
        if list_result.returncode == 0 and _plugin_list_contains(
            list_result.output,
            self.plugin_name,
        ):
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[
                    *actions,
                    f"OpenClaw plugin already installed: {self.plugin_name}",
                ],
            )

        install_action = f"install OpenClaw plugin: {self.plugin_name}"
        if dry_run or check:
            return LinkerResult(
                changed=True,
                dry_run=True,
                actions=[*actions, f"would {install_action}"],
            )

        install_result = self._run(
            command,
            ["plugins", "install", self.plugin_name],
        )
        actions.append(install_action)
        if install_result.returncode != 0:
            return LinkerResult(
                changed=False,
                dry_run=False,
                actions=actions,
                warnings=[
                    f"failed to install OpenClaw plugin {self.plugin_name}: "
                    + _short_command_output(install_result),
                ],
            )

        verify_result = self._run(command, ["plugins", "list"])
        if verify_result.returncode != 0 or not _plugin_list_contains(
            verify_result.output,
            self.plugin_name,
        ):
            return LinkerResult(
                changed=True,
                dry_run=False,
                actions=actions,
                warnings=[
                    f"failed to verify OpenClaw plugin {self.plugin_name} after install: "
                    + _short_command_output(verify_result),
                ],
            )
        actions.append(f"verified OpenClaw plugin: {self.plugin_name}")
        return LinkerResult(changed=True, dry_run=False, actions=actions)

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
class PowerMemEnvOnlyMCPConfigLinker(LifecycleLinker):
    """Install only when the target MCP config path is explicitly configured."""

    name: str
    target: str
    config_env_var: str
    server_name: str = "powermem"

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        value = os.environ.get(self.config_env_var)
        if not value:
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[
                    f"skip {self.target} MCP config: set {self.config_env_var} to an explicit config path",
                ],
                warnings=[
                    f"{self.target} MCP config path is not verified; no default file was written",
                ],
            )
        return PowerMemMCPConfigLinker(
            name=self.name,
            target=self.target,
            config_env_var=self.config_env_var,
            default_config_path=Path(value),
            server_name=self.server_name,
        ).install(plug_name=plug_name, dry_run=dry_run, check=check)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    output: str


def _openclaw_command(env_var: str) -> list[str]:
    raw = os.environ.get(env_var, "").strip() or "openclaw"
    return shlex.split(raw)


def _command_available(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if os.sep in executable:
        return Path(executable).expanduser().is_file()
    return shutil.which(executable) is not None


def _plugin_list_contains(output: str, plugin_name: str) -> bool:
    return plugin_name.lower() in output.lower()


def _short_command_output(result: _CommandResult) -> str:
    text = " ".join(result.output.strip().split())
    return text[:300] if text else f"exit code {result.returncode}"


def _disabled(value: str) -> bool:
    return value.strip().lower() in _FALSE_VALUES


def _path_from_env(env_var: str, default_path: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value).expanduser() if value else default_path.expanduser()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _read_jsonc(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        payload = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_jsonc_top_level_object_entry(
    path: Path,
    before_text: str,
    after: dict[str, Any],
    *,
    object_key: str,
    entry_key: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if before_text.strip():
        text = _upsert_jsonc_top_level_object_entry(
            before_text,
            object_key=object_key,
            entry_key=entry_key,
            entry_value=after[object_key][entry_key],
        )
    else:
        text = json.dumps(after, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _write_jsonc_top_level_entry(
    path: Path,
    before_text: str,
    *,
    key: str,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if before_text.strip():
        text = _upsert_jsonc_top_level_entry(before_text, key=key, value=value)
    else:
        text = json.dumps({key: value}, ensure_ascii=False, indent=2, sort_keys=True)
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _strip_jsonc(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        result.append(char)
        index += 1
    return _remove_jsonc_trailing_commas("".join(result))


def _remove_jsonc_trailing_commas(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            probe = index + 1
            while probe < len(text) and text[probe].isspace():
                probe += 1
            if probe < len(text) and text[probe] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _upsert_jsonc_top_level_object_entry(
    text: str,
    *,
    object_key: str,
    entry_key: str,
    entry_value: dict[str, Any],
) -> str:
    root = _find_jsonc_object_bounds(text, 0)
    if root is None:
        payload = {object_key: {entry_key: entry_value}}
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    object_member = _find_jsonc_object_member(text, root[0], root[1], object_key)
    if object_member is None:
        object_value = {entry_key: entry_value}
        return _insert_jsonc_object_member(
            text,
            root[0],
            root[1],
            object_key,
            object_value,
        )

    value_start, value_end = object_member.value_start, object_member.value_end
    if _skip_ws_comments(text, value_start) >= len(text) or text[value_start] != "{":
        replacement = json.dumps(
            {entry_key: entry_value},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        replacement = _indent_multiline(replacement, _column_at(text, value_start))
        return text[:value_start] + replacement + text[value_end:]

    servers_bounds = _find_jsonc_object_bounds(text, value_start)
    if servers_bounds is None:
        replacement = json.dumps(
            {entry_key: entry_value},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        replacement = _indent_multiline(replacement, _column_at(text, value_start))
        return text[:value_start] + replacement + text[value_end:]

    entry_member = _find_jsonc_object_member(
        text,
        servers_bounds[0],
        servers_bounds[1],
        entry_key,
    )
    if entry_member is not None:
        replacement = json.dumps(
            entry_value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        replacement = _indent_multiline(
            replacement,
            _column_at(text, entry_member.value_start),
        )
        return (
            text[: entry_member.value_start]
            + replacement
            + text[entry_member.value_end :]
        )

    return _insert_jsonc_object_member(
        text,
        servers_bounds[0],
        servers_bounds[1],
        entry_key,
        entry_value,
    )


def _upsert_jsonc_top_level_entry(
    text: str,
    *,
    key: str,
    value: dict[str, Any],
) -> str:
    root = _find_jsonc_object_bounds(text, 0)
    if root is None:
        return json.dumps({key: value}, ensure_ascii=False, indent=2, sort_keys=True)
    member = _find_jsonc_object_member(text, root[0], root[1], key)
    if member is not None:
        replacement = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        replacement = _indent_multiline(
            replacement, _column_at(text, member.value_start)
        )
        return text[: member.value_start] + replacement + text[member.value_end :]
    return _insert_jsonc_object_member(text, root[0], root[1], key, value)


@dataclass(frozen=True)
class _JSONCMember:
    key_start: int
    key_end: int
    value_start: int
    value_end: int


def _find_jsonc_object_bounds(text: str, start: int) -> tuple[int, int] | None:
    object_start = _skip_ws_comments(text, start)
    if object_start >= len(text) or text[object_start] != "{":
        return None
    object_end = _find_jsonc_matching(text, object_start, "{", "}")
    if object_end is None:
        return None
    return object_start, object_end


def _find_jsonc_object_member(
    text: str,
    object_start: int,
    object_end: int,
    key: str,
) -> _JSONCMember | None:
    for member in _iter_jsonc_object_members(text, object_start, object_end):
        if json.loads(text[member.key_start : member.key_end]) == key:
            return member
    return None


def _iter_jsonc_object_members(
    text: str,
    object_start: int,
    object_end: int,
) -> list[_JSONCMember]:
    members: list[_JSONCMember] = []
    index = object_start + 1
    while index < object_end:
        index = _skip_ws_comments(text, index)
        if index >= object_end:
            break
        if text[index] == ",":
            index += 1
            continue
        if text[index] != '"':
            break
        key_start = index
        key_end = _find_jsonc_string_end(text, key_start)
        if key_end is None:
            break
        index = _skip_ws_comments(text, key_end)
        if index >= object_end or text[index] != ":":
            break
        value_start = _skip_ws_comments(text, index + 1)
        value_end = _find_jsonc_value_end(text, value_start)
        if value_end is None:
            break
        members.append(
            _JSONCMember(
                key_start=key_start,
                key_end=key_end,
                value_start=value_start,
                value_end=value_end,
            ),
        )
        index = value_end
    return members


def _insert_jsonc_object_member(
    text: str,
    object_start: int,
    object_end: int,
    key: str,
    value: dict[str, Any],
) -> str:
    members = _iter_jsonc_object_members(text, object_start, object_end)
    closing_indent = _line_indent_at(text, object_end)
    inner_indent = closing_indent + "  "
    value_text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    value_text = _indent_multiline(value_text, len(inner_indent) + len(key) + 4)
    entry = f"{inner_indent}{json.dumps(key, ensure_ascii=False)}: {value_text}"
    if not members:
        insertion = "\n" + entry + "\n" + closing_indent
        return text[: object_start + 1] + insertion + text[object_end:]

    last = members[-1]
    probe = _skip_ws_comments(text, last.value_end)
    if probe < object_end and text[probe] == ",":
        insert_pos = probe + 1
        insertion = "\n" + entry
    else:
        insert_pos = last.value_end
        insertion = ",\n" + entry
    return text[:insert_pos] + insertion + text[insert_pos:]


def _skip_ws_comments(text: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("//", index):
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if text.startswith("/*", index):
            index += 2
            while index + 1 < len(text) and not text.startswith("*/", index):
                index += 1
            index += 2
            continue
        break
    return index


def _find_jsonc_string_end(text: str, start: int) -> int | None:
    index = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index + 1
        index += 1
    return None


def _find_jsonc_value_end(text: str, start: int) -> int | None:
    start = _skip_ws_comments(text, start)
    if start >= len(text):
        return None
    char = text[start]
    if char == '"':
        return _find_jsonc_string_end(text, start)
    if char == "{":
        end = _find_jsonc_matching(text, start, "{", "}")
        return None if end is None else end + 1
    if char == "[":
        end = _find_jsonc_matching(text, start, "[", "]")
        return None if end is None else end + 1
    index = start
    while index < len(text) and text[index] not in ",}\n\r":
        index += 1
    return index


def _find_jsonc_matching(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> int | None:
    depth = 0
    index = start
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _indent_multiline(text: str, continuation_indent: int) -> str:
    lines = text.splitlines()
    if len(lines) <= 1:
        return text
    indent = " " * continuation_indent
    return lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])


def _column_at(text: str, index: int) -> int:
    line_start = text.rfind("\n", 0, index) + 1
    return index - line_start


def _line_indent_at(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    cursor = line_start
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    return text[line_start:cursor]


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    path.write_text(text, encoding="utf-8")


def _powermem_mcp_server_config() -> dict[str, Any]:
    config: dict[str, Any] = {"command": _powermem_mcp_command(), "args": []}
    env = _powermem_mcp_env()
    if env:
        config["env"] = env
    return config


def _powermem_vscode_mcp_server_config() -> dict[str, Any]:
    config = _powermem_mcp_server_config()
    return {"type": "stdio", **config}


def _powermem_opencode_mcp_server_config() -> dict[str, Any]:
    config: dict[str, Any] = {
        "type": "local",
        "command": [_powermem_mcp_command()],
    }
    env = _powermem_mcp_env()
    if env:
        config["environment"] = env
    return config


def _powermem_codex_toml_section(server_name: str) -> str:
    lines = [
        f"[mcp_servers.{server_name}]",
        f"command = {_toml_string(_powermem_mcp_command())}",
        "args = []",
    ]
    env = _powermem_mcp_env()
    if env:
        lines.append(f"env = {_toml_inline_table(env)}")
    return "\n".join(lines) + "\n"


def _upsert_toml_section(text: str, *, header: str, section: str) -> str:
    normalized_section = section.rstrip() + "\n"
    block = _find_toml_section_block(text, header)
    if block is not None:
        start, end = block
        prefix = text[:start].rstrip()
        suffix = text[end:].lstrip("\n")
        parts = [
            part
            for part in [prefix, normalized_section.rstrip(), suffix.rstrip()]
            if part
        ]
        return "\n\n".join(parts) + "\n"
    prefix = text.rstrip()
    if not prefix:
        return normalized_section
    return prefix + "\n\n" + normalized_section


def _find_toml_section_block(text: str, header: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    start_line: int | None = None
    end_line = len(lines)
    for index, line in enumerate(lines):
        section_header = _toml_section_header(line)
        if section_header is None:
            continue
        is_target = section_header == header or section_header.startswith(header + ".")
        if start_line is None:
            if is_target:
                start_line = index
            continue
        if not is_target:
            end_line = index
            break

    if start_line is None:
        return None
    start = offsets[start_line]
    end = offsets[end_line] if end_line < len(offsets) else len(text)
    return start, end


def _toml_section_header(line: str) -> str | None:
    match = re.match(r"^\s*\[(?!\[)\s*([^\]]+?)\s*\]\s*(?:#.*)?$", line)
    return match.group(1).strip() if match else None


def _powermem_mcp_command() -> str:
    return _contextseek_proxy_command(
        "CONTEXTSEEK_POWERMEM_MCP_COMMAND",
        "contextseek-pmem-mcp-stdio",
    )


def _powermem_cli_proxy_command() -> str:
    return _contextseek_proxy_command(
        "CONTEXTSEEK_POWERMEM_CLI_PROXY_COMMAND",
        "contextseek-pmem-proxy",
    )


def _contextseek_proxy_command(env_var: str, default: str) -> str:
    configured = os.environ.get(env_var, "").strip()
    if configured:
        return configured
    detected = shutil.which(default)
    if detected:
        return detected
    sibling = Path(sys.executable).parent / default
    return str(sibling) if sibling.is_file() else default


def _real_powermem_cli_command() -> str:
    configured = (
        os.environ.get("CONTEXTSEEK_POWERMEM_CLI")
        or os.environ.get("CONTEXTSEEK_REAL_PMEM")
        or os.environ.get("PMEM_PATH")
    )
    if configured and not _would_recurse_cli(configured):
        return configured
    runtime_cli = PowerMemCLIRuntimeInstaller().cli_command()
    if runtime_cli and not _would_recurse_cli(runtime_cli):
        return runtime_cli
    detected = shutil.which("pmem")
    if detected and not _would_recurse_cli(detected):
        return detected
    return ""


def _powermem_cli_env_defaults(real_cli: str) -> dict[str, str]:
    defaults = {
        "CONTEXTSEEK_POWERMEM_ENV_FILE": str(managed_powermem_env_path()),
    }
    if real_cli:
        defaults["CONTEXTSEEK_POWERMEM_CLI"] = real_cli
    contextseek_config = os.environ.get("CONTEXTSEEK_CONFIG")
    if contextseek_config:
        defaults["CONTEXTSEEK_CONFIG"] = contextseek_config
    return defaults


def _powermem_cli_warnings(real_cli: str) -> list[str]:
    if real_cli:
        return []
    return [
        "PowerMem CLI executable cannot be inferred; set CONTEXTSEEK_POWERMEM_CLI to the real pmem path",
    ]


def _would_recurse_cli(command: str) -> bool:
    proxy = shutil.which(_powermem_cli_proxy_command()) or _powermem_cli_proxy_command()
    try:
        return os.path.realpath(command) == os.path.realpath(proxy)
    except OSError:
        return Path(command).name == _powermem_cli_proxy_command()


def _powermem_mcp_env() -> dict[str, str]:
    env: dict[str, str] = {
        "CONTEXTSEEK_POWERMEM_ENV_FILE": str(managed_powermem_env_path()),
    }
    for key in (
        "CONTEXTSEEK_CONFIG",
        "CONTEXTSEEK_POWERMEM_RUNTIME_DIR",
        "CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY",
        "CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    default_scope = os.environ.get("CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE")
    if default_scope:
        env["CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"] = default_scope
    return env


def _toml_inline_table(values: dict[str, str]) -> str:
    items = [
        f"{_toml_string(key)} = {_toml_string(value)}"
        for key, value in sorted(values.items())
    ]
    return "{ " + ", ".join(items) + " }"


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _contextseek_powermem_proxy_url() -> str:
    return os.environ.get(
        "CONTEXTSEEK_POWERMEM_PROXY_BASE_URL",
        "http://127.0.0.1:2882/plugins/powermem/default",
    ).rstrip("/")
