"""Claude Code plugin configuration for PowerMem."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from contextseek.plugs.core.linkers import LinkerResult
from contextseek.plugs.powermem.linkers.download import urlopen_with_certifi
from contextseek.plugs.powermem.linkers.runtime import (
    power_mem_download_progress_callback,
)


_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_PLUGIN_NAME = "memory-powermem"
_DEFAULT_SCOPE = "user"
_DEFAULT_REPO_URL = "https://github.com/oceanbase/powermem.git"
_DEFAULT_PLUGIN_ZIP_URL = (
    "https://obbusiness-private.oss-cn-shanghai.aliyuncs.com/"
    "download-center/opensource/powermem/"
    "powermem-claude-code-plugin-0.1.0.zip"
)
_PLUGIN_DIR_MODE = "plugin_dir"
_MARKETPLACE_MODE = "marketplace"
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_RUNTIME_ENV_MARKER = "# ContextSeek managed PowerMem hook endpoint"
_RUNTIME_ENV_KEYS = ("POWERMEM_BASE_URL", "POWERMEM_AGENT_ID")


@dataclass(frozen=True)
class RuntimeEnvWriteResult:
    """Result of publishing the live PowerMem hook endpoint."""

    paths: tuple[Path, ...]
    changed: bool


@dataclass(frozen=True)
class ClaudeCodePluginRuntimeInstaller:
    """Prepare the PowerMem Claude Code plugin entry.

    The default ``plugin_dir`` mode downloads the official PowerMem Claude Code
    plugin zip from the managed OSS mirror, validates its hook runtime files,
    registers the package as a local Claude Code marketplace, and installs the
    plugin. Source zip preparation remains available as an explicit
    configuration-level debug path. The legacy marketplace install remains
    available through an explicit install mode.
    """

    plugin_name: str = _DEFAULT_PLUGIN_NAME
    scope: str = _DEFAULT_SCOPE
    command_env_var: str = "CONTEXTSEEK_CLAUDE_CODE_COMMAND"
    install_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL"
    install_mode_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL_MODE"
    marketplace_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MARKETPLACE"
    repo_url_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_REPO_URL"
    source_zip_url_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SOURCE_ZIP_URL"
    plugin_zip_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP"
    plugin_zip_url_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP_URL"
    plugin_zip_sha256_env_var: str = (
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP_SHA256"
    )
    managed_repo_dir_env_var: str = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MANAGED_REPO_DIR"
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

        mode = _install_mode(self.install_mode_env_var)
        if mode == _MARKETPLACE_MODE:
            return self._install_marketplace(dry_run=dry_run, check=check)
        return self._prepare_plugin_dir(dry_run=dry_run, check=check)

    def prepared_plugin_dir(self) -> Path:
        """Return the plugin directory that install/check will prepare."""
        plugin_zip = _plugin_zip_path(self.plugin_zip_env_var)
        if plugin_zip is not None:
            version = _plugin_zip_version(plugin_zip) or "unknown"
            return _managed_plugin_zip_dir(version)
        if _source_zip_requested(
            source_zip_url_env_var=self.source_zip_url_env_var,
            managed_repo_dir_env_var=self.managed_repo_dir_env_var,
        ):
            return _repo_plugin_dir(_managed_repo_dir(self.managed_repo_dir_env_var))
        return _managed_release_plugin_dir()

    def _prepare_plugin_dir(
        self,
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        command = _claude_command(self.command_env_var)
        actions = [
            f"prepare Claude Code plugin dir: {self.plugin_name}",
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

        plugin_dir, changed, prepare_actions, prepare_warnings = (
            self._resolve_plugin_dir(
                dry_run=dry_run,
                check=check,
            )
        )
        actions.extend(prepare_actions)
        warnings.extend(prepare_warnings)
        if plugin_dir is None:
            return LinkerResult(
                changed=changed,
                dry_run=dry_run or check,
                actions=actions,
                warnings=warnings,
            )
        if (dry_run or check) and changed and not plugin_dir.exists():
            return LinkerResult(
                changed=True,
                dry_run=True,
                actions=actions,
                warnings=warnings,
            )

        if _source_zip_requested(
            source_zip_url_env_var=self.source_zip_url_env_var,
            managed_repo_dir_env_var=self.managed_repo_dir_env_var,
        ):
            validation_warnings = _config_level_plugin_warnings(plugin_dir)
        else:
            validation_warnings = _runtime_level_plugin_warnings(plugin_dir)
        warnings.extend(validation_warnings)
        if not validation_warnings:
            version = _plugin_version(plugin_dir)
            suffix = f" ({version})" if version else ""
            actions.append(f"verified Claude Code plugin dir{suffix}: {plugin_dir}")
            install_result = self._install_local_plugin_dir(
                command,
                plugin_dir,
                dry_run=dry_run,
                check=check,
            )
            changed = changed or install_result.changed
            actions.extend(install_result.actions)
            warnings.extend(install_result.warnings)
        return LinkerResult(
            changed=changed,
            dry_run=dry_run or check,
            actions=actions,
            warnings=warnings,
        )

    def _install_marketplace(
        self,
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        command = _claude_command(self.command_env_var)
        actions = [
            f"detect Claude Code marketplace plugin: {self.plugin_name}",
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
        return LinkerResult(
            changed=True,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )

    def _resolve_plugin_dir(
        self,
        *,
        dry_run: bool,
        check: bool,
    ) -> tuple[Path | None, bool, list[str], list[str]]:
        plugin_zip = _plugin_zip_path(self.plugin_zip_env_var)
        if plugin_zip is not None:
            return _resolve_plugin_zip_dir(
                plugin_zip,
                dry_run=dry_run,
                check=check,
            )
        if not _source_zip_requested(
            source_zip_url_env_var=self.source_zip_url_env_var,
            managed_repo_dir_env_var=self.managed_repo_dir_env_var,
        ):
            return _resolve_release_plugin_zip_dir(
                dry_run=dry_run,
                check=check,
                timeout=self.timeout,
                plugin_zip_url_env_var=self.plugin_zip_url_env_var,
                plugin_zip_sha256_env_var=self.plugin_zip_sha256_env_var,
            )

        repo_dir = _managed_repo_dir(self.managed_repo_dir_env_var)
        plugin_dir = _repo_plugin_dir(repo_dir)
        plan_url = _source_zip_url(
            source_zip_url_env_var=self.source_zip_url_env_var,
            repo_url_env_var=self.repo_url_env_var,
            timeout=self.timeout,
            resolve_latest=False,
        )
        download_action = (
            "download latest PowerMem release source archive for Claude Code plugin: "
            f"{plan_url}"
        )
        if check and plugin_dir.exists():
            return (
                plugin_dir,
                False,
                [f"use plugin dir: {plugin_dir}"],
                [],
            )
        if dry_run or check:
            return (
                plugin_dir,
                True,
                [f"would {download_action}", f"would use plugin dir: {plugin_dir}"],
                [],
            )

        try:
            source_url = _source_zip_url(
                source_zip_url_env_var=self.source_zip_url_env_var,
                repo_url_env_var=self.repo_url_env_var,
                timeout=self.timeout,
                resolve_latest=True,
            )
            _download_source_archive(source_url, repo_dir, timeout=self.timeout)
        except OSError as exc:
            return (
                None,
                True,
                [download_action],
                [
                    "failed to prepare Claude Code plugin dir: " + str(exc),
                ],
            )
        return (
            plugin_dir,
            True,
            [
                "download latest PowerMem release source archive for Claude Code "
                f"plugin: {source_url}",
                f"use plugin dir: {plugin_dir}",
            ],
            [],
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

    def _install_local_plugin_dir(
        self,
        command: list[str],
        plugin_dir: Path,
        *,
        dry_run: bool,
        check: bool,
    ) -> LinkerResult:
        state = self._installed_state(command)
        if state.installed:
            actions = [f"Claude Code plugin already installed: {self.plugin_name}"]
            warnings: list[str] = []
            changed = False
            if state.disabled:
                enable_result = self._enable(command, dry_run=dry_run, check=check)
                actions.extend(enable_result.actions)
                warnings.extend(enable_result.warnings)
                changed = changed or enable_result.changed
            return LinkerResult(
                changed=changed,
                dry_run=dry_run or check,
                actions=actions,
                warnings=warnings,
            )

        add_action = f"add Claude Code plugin marketplace: {plugin_dir}"
        install_action = (
            f"install Claude Code plugin: {self.plugin_name} --scope {self.scope}"
        )
        if dry_run or check:
            return LinkerResult(
                changed=True,
                dry_run=True,
                actions=[f"would {add_action}", f"would {install_action}"],
            )

        actions = [add_action]
        warnings: list[str] = []
        marketplace_result = self._run(
            command,
            ["plugin", "marketplace", "add", str(plugin_dir)],
        )
        if marketplace_result.returncode != 0:
            warnings.append(
                "failed to add Claude Code plugin marketplace: "
                + _short_output(marketplace_result),
            )
            return LinkerResult(
                changed=False,
                dry_run=False,
                actions=actions,
                warnings=warnings,
            )

        install_result = self._run(
            command,
            ["plugin", "install", "--scope", self.scope, self.plugin_name],
        )
        actions.append(install_action)
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
        return LinkerResult(
            changed=True,
            dry_run=False,
            actions=actions,
            warnings=warnings,
        )

    def _installed_state(self, command: list[str]) -> "_PluginState":
        details = self._run(command, ["plugin", "details", self.plugin_name])
        if details.returncode != 0:
            return _PluginState(installed=False, disabled=False)

        listing = self._run(command, ["plugin", "list"])
        disabled = False
        if listing.returncode == 0:
            disabled = _plugin_line_mentions_disabled(listing.output, self.plugin_name)
        return _PluginState(installed=True, disabled=disabled)

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


@dataclass(frozen=True)
class _PluginReleaseAsset:
    name: str
    url: str
    sha256: str | None


def write_claude_code_plugin_runtime_env(
    proxy_base_url: str,
    *,
    agent_id: str = "claude-code",
    plugin_dir: Path | None = None,
    dry_run: bool = False,
    check: bool = False,
) -> tuple[Path, bool]:
    """Publish the live ContextSeek proxy URL for PowerMem's HTTP hook."""
    root = plugin_dir or ClaudeCodePluginRuntimeInstaller().prepared_plugin_dir()
    runtime_env = root / "config" / "runtime.env"
    if not root.exists():
        return runtime_env, False
    values = {
        "POWERMEM_BASE_URL": proxy_base_url.rstrip("/"),
        "POWERMEM_AGENT_ID": agent_id,
    }
    changed = _write_runtime_env_file(
        runtime_env,
        values,
        dry_run=dry_run,
        check=check,
    )
    return runtime_env, changed


def write_claude_code_plugin_runtime_envs(
    proxy_base_url: str,
    *,
    agent_id: str = "claude-code",
    plugin_name: str = _DEFAULT_PLUGIN_NAME,
    plugin_dir: Path | None = None,
    dry_run: bool = False,
    check: bool = False,
) -> RuntimeEnvWriteResult:
    """Publish the live hook endpoint everywhere the official hook can read it."""
    values = {
        "POWERMEM_BASE_URL": proxy_base_url.rstrip("/"),
        "POWERMEM_AGENT_ID": agent_id,
    }
    paths = _runtime_env_publish_paths(
        plugin_name=plugin_name,
        plugin_dir=plugin_dir,
    )
    changed = False
    for path in paths:
        changed = (
            _write_runtime_env_file(
                path,
                values,
                dry_run=dry_run,
                check=check,
            )
            or changed
        )
    return RuntimeEnvWriteResult(paths=paths, changed=changed)


def _runtime_env_publish_paths(
    *,
    plugin_name: str,
    plugin_dir: Path | None,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    paths.append(Path.home() / ".powermem" / "runtime.env")
    data_dir_raw = os.environ.get("POWERMEM_DATA_DIR", "").strip()
    if data_dir_raw:
        paths.append(Path(data_dir_raw).expanduser() / "runtime.env")

    root = (
        plugin_dir
        or ClaudeCodePluginRuntimeInstaller(
            plugin_name=plugin_name,
        ).prepared_plugin_dir()
    )
    if root.exists():
        paths.append(root / "config" / "runtime.env")

    for installed_dir in _installed_claude_plugin_dirs(plugin_name):
        paths.append(installed_dir / "config" / "runtime.env")

    return tuple(_dedupe_paths(paths))


def _write_runtime_env_file(
    runtime_env: Path,
    values: dict[str, str],
    *,
    dry_run: bool,
    check: bool,
) -> bool:
    before = runtime_env.read_text(encoding="utf-8") if runtime_env.exists() else ""
    after = _updated_runtime_env_text(before, values)
    changed = after != before
    if changed and not (dry_run or check):
        runtime_env.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = runtime_env.with_name(f".{runtime_env.name}.tmp")
        tmp_path.write_text(after, encoding="utf-8")
        tmp_path.replace(runtime_env)
    return changed


def _installed_claude_plugin_dirs(plugin_name: str) -> list[Path]:
    roots: list[Path] = []
    installed_plugins = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        payload = json.loads(installed_plugins.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    if isinstance(plugins, dict):
        for key, entries in plugins.items():
            if not isinstance(key, str) or not key.startswith(f"{plugin_name}@"):
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                install_path = str(entry.get("installPath") or "").strip()
                if install_path:
                    roots.append(Path(install_path).expanduser())

    cache_root = Path.home() / ".claude" / "plugins" / "cache"
    if cache_root.is_dir():
        roots.extend(cache_root.glob(f"*/{plugin_name}/*"))
    return [path for path in _dedupe_paths(roots) if path.is_dir()]


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _updated_runtime_env_text(existing: str, values: dict[str, str]) -> str:
    managed_prefixes = tuple(f"{key}=" for key in _RUNTIME_ENV_KEYS)
    lines = [
        line
        for line in existing.splitlines()
        if line != _RUNTIME_ENV_MARKER and not line.startswith(managed_prefixes)
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.append(_RUNTIME_ENV_MARKER)
    for key in _RUNTIME_ENV_KEYS:
        lines.append(f"{key}={shlex.quote(values[key])}")
    return "\n".join(lines) + "\n"


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


def _install_mode(env_var: str) -> str:
    value = os.environ.get(env_var, "").strip().lower().replace("-", "_")
    if value in {_MARKETPLACE_MODE, "claude_marketplace"}:
        return _MARKETPLACE_MODE
    return _PLUGIN_DIR_MODE


def _plugin_line_mentions_disabled(output: str, plugin_name: str) -> bool:
    for line in output.splitlines():
        lowered = line.lower()
        if plugin_name.lower() in lowered and "disabled" in lowered:
            return True
    return False


def _plugin_root(path: Path) -> Path:
    path = path.expanduser()
    if (path / ".claude-plugin" / "plugin.json").is_file():
        return path
    nested = path / "apps" / "claude-code-plugin"
    if (nested / ".claude-plugin" / "plugin.json").is_file():
        return nested
    return path


def _repo_plugin_dir(repo_dir: Path) -> Path:
    plugin_dir = _plugin_root(repo_dir)
    if plugin_dir != repo_dir:
        return plugin_dir
    return repo_dir.expanduser() / "apps" / "claude-code-plugin"


def _managed_repo_dir(env_var: str) -> Path:
    configured = os.environ.get(env_var, "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / ".contextseek"
        / "plugs"
        / "powermem"
        / "claude-code-plugin"
        / "source"
        / "powermem"
    )


def _source_zip_url(
    *,
    source_zip_url_env_var: str,
    repo_url_env_var: str,
    timeout: float,
    resolve_latest: bool,
) -> str:
    configured = os.environ.get(source_zip_url_env_var, "").strip()
    if configured:
        return configured
    repo_url = os.environ.get(repo_url_env_var, "").strip() or _DEFAULT_REPO_URL
    owner_repo = _github_owner_repo(repo_url)
    if owner_repo is None:
        msg = f"cannot infer GitHub repository from {repo_url}"
        raise OSError(msg)
    owner, repo = owner_repo
    latest_api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    if not resolve_latest:
        return latest_api_url
    try:
        request = urllib.request.Request(
            latest_api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "contextseek",
            },
        )
        with urlopen_with_certifi(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"failed to resolve latest release: {exc}"
        raise OSError(msg) from exc
    zipball_url = str(payload.get("zipball_url") or "").strip()
    if not zipball_url:
        msg = "latest release response does not include zipball_url"
        raise OSError(msg)
    return zipball_url


def _github_owner_repo(repo_url: str) -> tuple[str, str] | None:
    raw = repo_url.strip().removesuffix(".git")
    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlparse(raw)
        if parsed.netloc != "github.com":
            return None
        path = parsed.path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _download_source_archive(
    source_url: str, repo_dir: Path, *, timeout: float
) -> None:
    repo_dir = repo_dir.expanduser()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "powermem-source.zip"
        extract_dir = tmp_dir / "extract"
        try:
            _download_to_path(
                source_url,
                archive,
                label=archive.name,
                timeout=timeout,
            )
        except OSError as exc:
            msg = f"download failed: {exc}"
            raise OSError(msg) from exc

        try:
            _safe_extract_zip(archive, extract_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            msg = f"extract failed: {exc}"
            raise OSError(msg) from exc

        source_root = _find_powermem_source_root(extract_dir)
        if source_root is None:
            msg = "archive does not contain apps/claude-code-plugin"
            raise OSError(msg)

        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        shutil.move(str(source_root), str(repo_dir))


def _resolve_release_plugin_zip_dir(
    *,
    dry_run: bool,
    check: bool,
    timeout: float,
    plugin_zip_url_env_var: str,
    plugin_zip_sha256_env_var: str,
) -> tuple[Path | None, bool, list[str], list[str]]:
    plugin_dir = _managed_release_plugin_dir()
    plan_url = (
        os.environ.get(plugin_zip_url_env_var, "").strip() or _DEFAULT_PLUGIN_ZIP_URL
    )
    action = f"download PowerMem Claude Code plugin zip: {plan_url} -> {plugin_dir}"
    if check and plugin_dir.exists():
        return plugin_dir, False, [f"use packaged plugin dir: {plugin_dir}"], []
    if dry_run or check:
        return (
            plugin_dir,
            True,
            [f"would {action}", f"would use packaged plugin dir: {plugin_dir}"],
            [],
        )
    try:
        asset = _plugin_release_asset(
            plugin_zip_url_env_var=plugin_zip_url_env_var,
            plugin_zip_sha256_env_var=plugin_zip_sha256_env_var,
        )
        plugin_dir = _download_release_plugin_zip(asset, plugin_dir, timeout=timeout)
    except OSError as exc:
        return (
            None,
            True,
            [action],
            ["failed to prepare Claude Code plugin dir: " + str(exc)],
        )
    return (
        plugin_dir,
        True,
        [
            f"download PowerMem Claude Code plugin zip: {asset.name} -> {plugin_dir}",
            f"use packaged plugin dir: {plugin_dir}",
        ],
        [],
    )


def _plugin_release_asset(
    *,
    plugin_zip_url_env_var: str,
    plugin_zip_sha256_env_var: str,
) -> _PluginReleaseAsset:
    url = os.environ.get(plugin_zip_url_env_var, "").strip() or _DEFAULT_PLUGIN_ZIP_URL
    name = Path(urllib.parse.urlparse(url).path).name
    return _PluginReleaseAsset(
        name=name or "powermem-claude-code-plugin.zip",
        url=url,
        sha256=os.environ.get(plugin_zip_sha256_env_var, "").strip() or None,
    )


def _download_release_plugin_zip(
    asset: _PluginReleaseAsset,
    target: Path,
    *,
    timeout: float,
) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset.name
        _download_to_path(asset.url, archive, label=asset.name, timeout=timeout)
        _verify_sha256(archive, asset.sha256)
        return _extract_plugin_zip_to(archive, target)


def _download_to_path(
    url: str,
    path: Path,
    *,
    label: str,
    timeout: float,
) -> None:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "contextseek"},
        )
        with urlopen_with_certifi(request, timeout=timeout) as response:
            total = _response_content_length(response)
            received = 0
            callback = power_mem_download_progress_callback()
            if callback is not None:
                callback(label, received, total)
            with path.open("wb") as handle:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if callback is not None:
                        callback(label, received, total)
            if total > 0 and received != total:
                msg = f"download incomplete: expected {total} bytes, got {received}"
                raise OSError(msg)
    except OSError as exc:
        msg = f"download failed: {url}: {exc}"
        raise OSError(msg) from exc


def _response_content_length(response: object) -> int:
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0
    value = headers.get("Content-Length") if hasattr(headers, "get") else None
    try:
        return int(value) if value else 0
    except (TypeError, ValueError):
        return 0


def _download_bytes(url: str, *, timeout: float) -> bytes:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "contextseek"},
        )
        with urlopen_with_certifi(request, timeout=timeout) as response:
            return response.read()
    except OSError as exc:
        msg = f"download failed: {url}: {exc}"
        raise OSError(msg) from exc


def _verify_sha256(path: Path, expected: str | None) -> None:
    if not expected:
        return
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        msg = f"sha256 mismatch for {path.name}: expected {expected}, got {actual}"
        raise OSError(msg)


def _resolve_plugin_zip_dir(
    plugin_zip: Path,
    *,
    dry_run: bool,
    check: bool,
) -> tuple[Path | None, bool, list[str], list[str]]:
    plugin_dir = _managed_plugin_zip_dir(_plugin_zip_version(plugin_zip) or "unknown")
    action = f"extract Claude Code plugin zip: {plugin_zip} -> {plugin_dir}"
    if check and plugin_dir.exists():
        return plugin_dir, False, [f"use packaged plugin dir: {plugin_dir}"], []
    if dry_run or check:
        return (
            plugin_dir,
            True,
            [f"would {action}", f"would use packaged plugin dir: {plugin_dir}"],
            _plugin_zip_path_warnings(plugin_zip),
        )
    try:
        plugin_dir = _extract_plugin_zip(plugin_zip)
    except OSError as exc:
        return (
            None,
            True,
            [action],
            ["failed to prepare Claude Code plugin dir: " + str(exc)],
        )
    return (
        plugin_dir,
        True,
        [action, f"use packaged plugin dir: {plugin_dir}"],
        [],
    )


def _plugin_zip_path(env_var: str) -> Path | None:
    configured = os.environ.get(env_var, "").strip()
    return Path(configured).expanduser() if configured else None


def _plugin_zip_path_warnings(plugin_zip: Path) -> list[str]:
    if plugin_zip.is_file():
        return []
    return [f"Claude Code plugin zip does not exist: {plugin_zip}"]


def _managed_plugin_zip_dir(version: str) -> Path:
    return _managed_plugin_root() / _safe_cache_name(version)


def _managed_plugin_root() -> Path:
    return Path.home() / ".contextseek" / "plugs" / "powermem" / "claude-code-plugin"


def _managed_release_plugin_dir() -> Path:
    return _managed_plugin_zip_dir("release")


def _source_zip_requested(
    *,
    source_zip_url_env_var: str,
    managed_repo_dir_env_var: str,
) -> bool:
    return bool(
        os.environ.get(source_zip_url_env_var, "").strip()
        or os.environ.get(managed_repo_dir_env_var, "").strip()
    )


def _safe_cache_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "-" for ch in value)
    return safe.strip(".-") or "unknown"


def _plugin_zip_version(plugin_zip: Path) -> str | None:
    try:
        payload = _plugin_json_from_zip(plugin_zip)
    except OSError:
        return None
    version = payload.get("version")
    return str(version) if version else None


def _plugin_json_from_zip(plugin_zip: Path) -> dict[str, object]:
    if not plugin_zip.is_file():
        msg = f"plugin zip does not exist: {plugin_zip}"
        raise OSError(msg)
    try:
        with zipfile.ZipFile(plugin_zip) as archive:
            for name in archive.namelist():
                parts = Path(name).parts
                if len(parts) < 2 or parts[-2:] != (".claude-plugin", "plugin.json"):
                    continue
                payload = json.loads(archive.read(name).decode("utf-8"))
                return payload if isinstance(payload, dict) else {}
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        msg = f"failed to read plugin zip manifest: {exc}"
        raise OSError(msg) from exc
    msg = "plugin zip does not contain .claude-plugin/plugin.json"
    raise OSError(msg)


def _extract_plugin_zip(plugin_zip: Path) -> Path:
    plugin_zip = plugin_zip.expanduser()
    if not plugin_zip.is_file():
        msg = f"plugin zip does not exist: {plugin_zip}"
        raise OSError(msg)
    version = _plugin_zip_version(plugin_zip) or "unknown"
    return _extract_plugin_zip_to(plugin_zip, _managed_plugin_zip_dir(version))


def _extract_plugin_zip_to(plugin_zip: Path, target: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        extract_dir = tmp_dir / "extract"
        try:
            _safe_extract_zip(plugin_zip, extract_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            msg = f"extract failed: {exc}"
            raise OSError(msg) from exc

        plugin_root = _find_claude_plugin_root(extract_dir)
        if plugin_root is None:
            msg = "archive does not contain a Claude Code plugin"
            raise OSError(msg)

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(plugin_root), str(target))
    return target


def _safe_extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target_root):
                msg = f"unsafe zip path: {member.filename}"
                raise OSError(msg)
        handle.extractall(target)


def _find_powermem_source_root(extract_dir: Path) -> Path | None:
    for candidate in [extract_dir, *extract_dir.iterdir()]:
        if _repo_plugin_dir(candidate).is_dir():
            return candidate
    return None


def _find_claude_plugin_root(extract_dir: Path) -> Path | None:
    direct = _plugin_root(extract_dir)
    if direct != extract_dir or (direct / ".claude-plugin" / "plugin.json").is_file():
        return direct
    for manifest in extract_dir.rglob("plugin.json"):
        if manifest.parent.name != ".claude-plugin":
            continue
        plugin_root = manifest.parent.parent
        if (plugin_root / "hooks").is_dir():
            return plugin_root
    return None


def _config_level_plugin_warnings(plugin_dir: Path) -> list[str]:
    warnings: list[str] = []
    if not plugin_dir.is_dir():
        return [f"Claude Code plugin dir does not exist: {plugin_dir}"]
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        warnings.append(f"Claude Code plugin manifest is missing: {plugin_json}")
    hook_runner = _hook_runner(plugin_dir)
    if not hook_runner.is_file():
        warnings.append(f"Claude Code hook runner is missing: {hook_runner}")
    return warnings


def _runtime_level_plugin_warnings(plugin_dir: Path) -> list[str]:
    warnings = _config_level_plugin_warnings(plugin_dir)
    mcp_config = plugin_dir / ".mcp.json"
    if not mcp_config.is_file():
        warnings.append(f"Claude Code plugin MCP config is missing: {mcp_config}")
    shell_runner = plugin_dir / "hooks" / "run-hook.sh"
    if not shell_runner.is_file():
        warnings.append(f"Claude Code hook runner is missing: {shell_runner}")
    powershell_runner = plugin_dir / "hooks" / "run-hook.ps1"
    if not powershell_runner.is_file():
        warnings.append(f"Claude Code hook runner is missing: {powershell_runner}")
    hook_binary = _hook_binary(plugin_dir)
    if not hook_binary.is_file():
        warnings.append(f"Claude Code plugin hook binary is missing: {hook_binary}")
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        warnings.append(f"Claude Code plugin skills dir is missing: {skills_dir}")
    return warnings


def _plugin_version(plugin_dir: Path) -> str | None:
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
    try:
        payload = json.loads(plugin_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version")
    return str(version) if version else None


def _hook_runner(plugin_dir: Path) -> Path:
    if platform.system().lower() == "windows":
        return plugin_dir / "hooks" / "run-hook.ps1"
    return plugin_dir / "hooks" / "run-hook.sh"


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
    suffix = ".exe" if goos == "windows" else ""
    return plugin_dir / "hooks" / "bin" / f"powermem-hook-{goos}-{goarch}{suffix}"


def _short_output(result: _CommandResult) -> str:
    text = " ".join(result.output.strip().split())
    return text[:300] if text else f"exit code {result.returncode}"
