"""PowerMem runtime installers grouped by access mode."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Iterator

from contextseek.plugs.core.linkers import LinkerResult, merge_linker_results
from contextseek.plugs.core.runtime import PythonPackageRuntimeInstaller
from contextseek.plugs.powermem.linkers.download import urlopen_with_certifi
from contextseek.plugs.powermem.sdk import (
    POWERMEM_SDK_MIN_VERSION,
    POWERMEM_SDK_REQUIREMENT,
    powermem_sdk_version_info,
)


_PACKAGE_INSTALL_STRATEGY_ENV = "CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY"
_RUNTIME_MODE_ENV = "CONTEXTSEEK_POWERMEM_RUNTIME_MODE"
_RUNTIME_DIR_ENV = "CONTEXTSEEK_POWERMEM_RUNTIME_DIR"
_RELEASE_BINARY_DIR_ENV = "CONTEXTSEEK_POWERMEM_RELEASE_BINARY_DIR"
_RELEASE_BINARY_URL_ENV = "CONTEXTSEEK_POWERMEM_RELEASE_BINARY_URL"
_RELEASE_BINARY_SHA256_ENV = "CONTEXTSEEK_POWERMEM_RELEASE_BINARY_SHA256"
_DEFAULT_RELEASE_VERSION = "1.1.5"
_DEFAULT_RELEASE_BINARY_BASE_URL = (
    "https://obbusiness-private.oss-cn-shanghai.aliyuncs.com/"
    "download-center/opensource/powermem"
)
_DEFAULT_RELEASE_BINARY_URLS = {
    "linux-amd64": (
        f"{_DEFAULT_RELEASE_BINARY_BASE_URL}/powermem-1.1.5-linux-amd64-binaries.tar.gz"
    ),
    "macos-aarch64": (
        f"{_DEFAULT_RELEASE_BINARY_BASE_URL}/"
        "powermem-1.1.5-macos-aarch64-binaries.tar.gz"
    ),
    "macos-amd64": (
        f"{_DEFAULT_RELEASE_BINARY_BASE_URL}/powermem-1.1.5-macos-amd64-binaries.tar.gz"
    ),
}
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
_SUPPORTED_RUNTIME_MODES = {"auto", "release_binary", "managed_venv", "current_env"}
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_DOWNLOAD_IDLE_TIMEOUT_SECONDS_ENV = "CONTEXTSEEK_POWERMEM_DOWNLOAD_IDLE_TIMEOUT"
_DEFAULT_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 60.0

DownloadProgressCallback = Callable[[str, int, int], None]
_DOWNLOAD_PROGRESS_CALLBACK: ContextVar[DownloadProgressCallback | None] = ContextVar(
    "powermem_download_progress_callback",
    default=None,
)


@contextmanager
def power_mem_download_progress(
    callback: DownloadProgressCallback | None,
) -> Iterator[None]:
    """Attach a per-thread download progress callback for runtime installation."""

    token = _DOWNLOAD_PROGRESS_CALLBACK.set(callback)
    try:
        yield
    finally:
        _DOWNLOAD_PROGRESS_CALLBACK.reset(token)


def power_mem_download_progress_callback() -> DownloadProgressCallback | None:
    return _DOWNLOAD_PROGRESS_CALLBACK.get()


@dataclass(frozen=True)
class _ReleaseAsset:
    version: str
    tag: str
    platform: str
    name: str
    url: str
    sha256: str | None


@dataclass(frozen=True)
class PowerMemReleaseBinaryRuntimeInstaller:
    """Resolve a PowerMem executable from a prepared release binary runtime.

    PowerMem publishes platform binary packages in GitHub releases. This
    installer selects the current platform asset, verifies its SHA256 digest,
    extracts it into ContextSeek's managed runtime directory, and records a
    local ``.installed.json`` manifest.
    """

    executable: str

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        platform_id = _power_mem_platform_id()
        actions = [f"use PowerMem release binary runtime: {platform_id}"]
        if platform_id is None:
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    "PowerMem release binary platform is not supported: "
                    f"{platform.system()} {platform.machine()}",
                ],
            )

        changed = False
        installed = _installed_release_binary_runtime()
        if installed is None:
            try:
                asset = _release_binary_asset(platform_id)
            except OSError as exc:
                return LinkerResult(
                    changed=False,
                    dry_run=dry_run or check,
                    actions=actions,
                    warnings=[f"failed to resolve PowerMem release binary: {exc}"],
                )
            install_root = _release_binary_install_root(asset.version)
            install_action = (
                f"install PowerMem release binary: {asset.name} -> {install_root}"
            )
            if dry_run or check:
                return LinkerResult(
                    changed=True,
                    dry_run=True,
                    actions=actions + [f"would {install_action}"],
                )
            try:
                _install_release_binary_asset(asset, install_root)
            except OSError as exc:
                return LinkerResult(
                    changed=False,
                    dry_run=False,
                    actions=actions + [install_action],
                    warnings=[f"failed to install PowerMem release binary: {exc}"],
                )
            installed = _read_release_binary_runtime(install_root)
            if installed is None:
                return LinkerResult(
                    changed=False,
                    dry_run=False,
                    actions=actions + [install_action],
                    warnings=[
                        "PowerMem release binary install finished but cannot be verified"
                    ],
                )
            actions.append(install_action)
            changed = True

        if installed.platform and installed.platform != platform_id:
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    "PowerMem release binary platform mismatch: "
                    f"installed {installed.platform}, current {platform_id}",
                ],
            )

        executable_path = installed.executable_path(self.executable)
        if executable_path is None:
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    "PowerMem release binary package does not include "
                    f"{self.executable} for {platform_id}",
                ],
            )
        if not executable_path.is_file():
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                actions=actions,
                warnings=[
                    f"PowerMem release binary executable is missing: {executable_path}",
                ],
            )

        return LinkerResult(
            changed=changed,
            dry_run=dry_run or check,
            actions=actions
            + [
                "PowerMem release binary already installed: "
                f"{self.executable}={executable_path}",
            ],
        )

    def command(self, *args: str) -> list[str]:
        installed = _installed_release_binary_runtime()
        executable_path = (
            installed.executable_path(self.executable) if installed else None
        )
        if executable_path is None:
            executable_path = (
                _release_binary_runtime_root() / "bin" / _exe_name(self.executable)
            )
        return [str(executable_path), *args]


@dataclass(frozen=True)
class PowerMemHTTPRuntimeInstaller:
    """Runtime requirements for PowerMem HTTP/server mode."""

    def install(
        self,
        *,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        if _runtime_mode() == "release_binary":
            return PowerMemReleaseBinaryRuntimeInstaller("powermem-server").install(
                dry_run=dry_run,
                check=check,
            )
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
        mode = _runtime_mode()
        if mode == "release_binary":
            return PowerMemReleaseBinaryRuntimeInstaller("powermem-server").command()
        if mode == "current_env":
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
        if _runtime_mode() == "release_binary":
            return PowerMemReleaseBinaryRuntimeInstaller("powermem").install(
                dry_run=dry_run,
                check=check,
            )
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
        mode = _runtime_mode()
        if mode == "release_binary":
            return PowerMemReleaseBinaryRuntimeInstaller("powermem").command()[0]
        if mode == "current_env":
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
        if _runtime_mode() == "release_binary":
            return LinkerResult(
                changed=False,
                dry_run=dry_run or check,
                warnings=[
                    "PowerMem SDK mode is not supported by release binary runtime; "
                    "set CONTEXTSEEK_POWERMEM_RUNTIME_MODE=managed_venv or current_env",
                ],
            )
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
        ]
        if os.environ.get(self.backend_command_env_var, "").strip():
            return merge_linker_results(*results, dry_run=dry_run or check)
        mode = _runtime_mode()
        if mode == "release_binary":
            results.append(
                PowerMemReleaseBinaryRuntimeInstaller(self.backend_executable).install(
                    dry_run=dry_run,
                    check=check,
                )
            )
        else:
            results.extend(
                [
                    _powermem_python_package_installer().install(
                        dry_run=dry_run,
                        check=check,
                    ),
                    _powermem_mcp_python_package_installer().install(
                        dry_run=dry_run,
                        check=check,
                    ),
                    _socksio_python_package_installer().install(
                        dry_run=dry_run,
                        check=check,
                    ),
                ]
            )
        results.extend(
            installer.install(dry_run=dry_run, check=check)
            for installer in _powermem_optional_provider_installers()
        )
        return merge_linker_results(*results, dry_run=dry_run or check)

    def python_command(self) -> str:
        if _runtime_mode() == "current_env":
            return sys.executable
        return str(_venv_python_path(_powermem_managed_venv_path()))

    def backend_command(self) -> list[str]:
        configured = os.environ.get(self.backend_command_env_var, "").strip()
        if configured:
            return shlex.split(configured)
        mode = _runtime_mode()
        if mode == "release_binary":
            return PowerMemReleaseBinaryRuntimeInstaller(
                self.backend_executable
            ).command("stdio")
        if mode == "current_env":
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
        install_strategy_override=_python_package_install_strategy(),
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
        install_strategy_override=_python_package_install_strategy(),
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
        install_strategy_override=_python_package_install_strategy(),
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
                install_strategy_override=_python_package_install_strategy(),
                managed_venv_path=_powermem_managed_venv_path(),
                wheelhouse_env_var=_WHEELHOUSE_ENV,
            )
        )
    return installers


def _release_binary_asset(platform_id: str) -> _ReleaseAsset:
    url = _release_binary_url(platform_id)
    if not url:
        msg = f"no default PowerMem release binary URL found for {platform_id}"
        raise OSError(msg)
    name = _download_name_from_url(url)
    if not name:
        msg = f"PowerMem release binary URL has no file name: {url}"
        raise OSError(msg)
    version = _release_version_from_name(name) or _DEFAULT_RELEASE_VERSION
    return _ReleaseAsset(
        version=version,
        tag=f"v{version}",
        platform=platform_id,
        name=name,
        url=url,
        sha256=_release_binary_sha256(platform_id),
    )


def _release_binary_url(platform_id: str) -> str:
    specific = _platform_env_name(_RELEASE_BINARY_URL_ENV, platform_id)
    return (
        os.environ.get(specific, "").strip()
        or os.environ.get(_RELEASE_BINARY_URL_ENV, "").strip()
        or _DEFAULT_RELEASE_BINARY_URLS.get(platform_id, "")
    )


def _release_binary_sha256(platform_id: str) -> str | None:
    specific = _platform_env_name(_RELEASE_BINARY_SHA256_ENV, platform_id)
    value = (
        os.environ.get(specific, "").strip()
        or os.environ.get(_RELEASE_BINARY_SHA256_ENV, "").strip()
    )
    return value.lower() or None


def _platform_env_name(prefix: str, platform_id: str) -> str:
    suffix = platform_id.upper().replace("-", "_")
    return f"{prefix}_{suffix}"


def _download_name_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return Path(urllib.parse.unquote(parsed.path)).name


def _release_version_from_name(name: str) -> str | None:
    prefix = "powermem-"
    if not name.startswith(prefix):
        return None
    remainder = name[len(prefix) :]
    version, _, _tail = remainder.partition("-")
    return version or None


def _install_release_binary_asset(asset: _ReleaseAsset, install_root: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / asset.name
        _download_to_path(asset.url, archive, label=asset.name, timeout=600.0)
        _verify_sha256(archive, asset.sha256)
        extract_dir = tmp_dir / "extract"
        _extract_release_archive(archive, extract_dir)
        package_root = _find_release_package_root(extract_dir, asset.name)
        if package_root is None:
            msg = f"archive does not contain PowerMem binary package: {asset.name}"
            raise OSError(msg)
        _verify_release_package(package_root, asset.platform)
        _chmod_release_binaries(package_root)

        install_root = install_root.expanduser()
        install_root.parent.mkdir(parents=True, exist_ok=True)
        if install_root.exists():
            shutil.rmtree(install_root)
        shutil.move(str(package_root), str(install_root))
        _write_release_manifest(install_root, asset)


def _download_to_path(
    url: str,
    path: Path,
    *,
    label: str,
    timeout: float,
) -> None:
    try:
        headers = {"User-Agent": "contextseek"}
        request = urllib.request.Request(url, headers=headers)
        idle_timeout = _download_idle_timeout_seconds()
        socket_timeout = min(timeout, idle_timeout)
        with urlopen_with_certifi(request, timeout=socket_timeout) as response:
            total = _response_content_length(response)
            received = 0
            callback = _DOWNLOAD_PROGRESS_CALLBACK.get()
            if callback is not None:
                callback(label, received, total)
            with path.open("wb") as handle:
                while True:
                    before_read = monotonic()
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if callback is not None:
                        callback(label, received, total)
                    if monotonic() - before_read > idle_timeout:
                        msg = f"download idle timeout after {idle_timeout:.0f}s: {url}"
                        raise OSError(msg)
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


def _download_idle_timeout_seconds() -> float:
    raw = os.environ.get(_DOWNLOAD_IDLE_TIMEOUT_SECONDS_ENV, "").strip()
    if not raw:
        return _DEFAULT_DOWNLOAD_IDLE_TIMEOUT_SECONDS
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return _DEFAULT_DOWNLOAD_IDLE_TIMEOUT_SECONDS


def _verify_sha256(path: Path, expected: str | None) -> None:
    if not expected:
        return
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        msg = f"sha256 mismatch for {path.name}: expected {expected}, got {actual}"
        raise OSError(msg)


def _extract_release_archive(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as handle:
            try:
                handle.extractall(target, filter="data")
            except TypeError:
                _verify_tar_members(handle, target)
                handle.extractall(target)
        return
    if archive.name.endswith(".zip"):
        _safe_extract_zip(archive, target)
        return
    msg = f"unsupported PowerMem release archive: {archive.name}"
    raise OSError(msg)


def _verify_tar_members(handle: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in handle.getmembers():
        destination = (target / member.name).resolve()
        if destination != root and root not in destination.parents:
            msg = f"unsafe tar path: {member.name}"
            raise OSError(msg)
        if member.issym() or member.islnk():
            msg = f"unsafe tar link: {member.name}"
            raise OSError(msg)


def _safe_extract_zip(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            destination = (target / member.filename).resolve()
            if destination != root and root not in destination.parents:
                msg = f"unsafe zip path: {member.filename}"
                raise OSError(msg)
        handle.extractall(target)


def _find_release_package_root(extract_dir: Path, archive_name: str) -> Path | None:
    expected = _release_package_name(archive_name)
    candidates = [extract_dir / expected] if expected else []
    candidates.extend(path for path in extract_dir.iterdir() if path.is_dir())
    for candidate in candidates:
        if (candidate / "bin").is_dir():
            return candidate
    return None


def _release_package_name(archive_name: str) -> str:
    for suffix in ("-binaries.tar.gz", "-binaries.zip"):
        if archive_name.endswith(suffix):
            return archive_name[: -len(suffix)]
    return ""


def _verify_release_package(package_root: Path, platform_id: str) -> None:
    bin_dir = package_root / "bin"
    if not bin_dir.is_dir():
        msg = f"PowerMem release binary package is missing bin directory: {bin_dir}"
        raise OSError(msg)
    suffix = ".exe" if platform_id.startswith("windows-") else ""
    expected = {
        f"powermem{suffix}",
        f"powermem-server{suffix}",
        f"powermem-mcp{suffix}",
    }
    actual = {path.name for path in bin_dir.iterdir() if path.is_file()}
    missing = sorted(expected - actual)
    if missing:
        msg = "PowerMem release binary package is missing: " + ", ".join(missing)
        raise OSError(msg)


def _chmod_release_binaries(package_root: Path) -> None:
    if os.name == "nt":
        return
    bin_dir = package_root / "bin"
    for path in bin_dir.iterdir():
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o755)


def _write_release_manifest(root: Path, asset: _ReleaseAsset) -> None:
    suffix = ".exe" if asset.platform.startswith("windows-") else ""
    payload = {
        "version": asset.version,
        "tag": asset.tag,
        "platform": asset.platform,
        "asset_name": asset.name,
        "asset_url": asset.url,
        "sha256": asset.sha256,
        "executables": {
            "powermem": f"bin/powermem{suffix}",
            "powermem-server": f"bin/powermem-server{suffix}",
            "powermem-mcp": f"bin/powermem-mcp{suffix}",
        },
    }
    (root / ".installed.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _release_binary_install_root(version: str) -> Path:
    configured = os.environ.get(_RELEASE_BINARY_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime_dir = os.environ.get(_RUNTIME_DIR_ENV, "").strip()
    if runtime_dir:
        return Path(runtime_dir).expanduser()
    return Path.home() / ".contextseek" / "runtimes" / "powermem" / version


def _runtime_mode() -> str:
    raw = os.environ.get(_RUNTIME_MODE_ENV, "").strip().lower().replace("-", "_")
    if raw and raw != "auto":
        return raw if raw in _SUPPORTED_RUNTIME_MODES else raw
    legacy = os.environ.get(_PACKAGE_INSTALL_STRATEGY_ENV, "").strip()
    if legacy:
        return legacy.lower().replace("-", "_")
    if _is_desktop_runtime():
        return "release_binary"
    return "managed_venv"


def _python_package_install_strategy() -> str:
    mode = _runtime_mode()
    return "current_env" if mode == "current_env" else "managed_venv"


def _is_desktop_runtime() -> bool:
    return os.environ.get("CONTEXTSEEK_DESKTOP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class _ReleaseBinaryRuntime:
    root: Path
    version: str | None
    platform: str | None
    executables: dict[str, Path]

    def executable_path(self, executable: str) -> Path | None:
        path = self.executables.get(executable)
        if path is None and executable == "pmem":
            path = self.executables.get("powermem")
        if path is not None:
            return path
        candidate = self.root / "bin" / _exe_name(executable)
        return candidate if candidate.is_file() else None


def _installed_release_binary_runtime() -> _ReleaseBinaryRuntime | None:
    for root in _release_binary_runtime_candidates():
        installed = _read_release_binary_runtime(root)
        if installed is not None:
            return installed
    return None


def _release_binary_runtime_candidates() -> list[Path]:
    configured = os.environ.get(_RELEASE_BINARY_DIR_ENV, "").strip()
    if configured:
        return [Path(configured).expanduser()]
    runtime_dir = os.environ.get(_RUNTIME_DIR_ENV, "").strip()
    if runtime_dir:
        return [Path(runtime_dir).expanduser()]
    root = Path.home() / ".contextseek" / "runtimes" / "powermem"
    candidates = []
    if root.is_dir():
        candidates.extend(
            sorted(
                (
                    path
                    for path in root.iterdir()
                    if (path / ".installed.json").is_file()
                ),
                reverse=True,
            )
        )
    candidates.append(_release_binary_runtime_root())
    return candidates


def _read_release_binary_runtime(root: Path) -> _ReleaseBinaryRuntime | None:
    root = root.expanduser()
    manifest_path = root / ".installed.json"
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        executables = _release_executables_from_manifest(root, payload)
        return _ReleaseBinaryRuntime(
            root=root,
            version=_optional_str(payload.get("version")),
            platform=_optional_str(payload.get("platform")),
            executables=executables,
        )
    bin_dir = root / "bin"
    if not bin_dir.is_dir():
        return None
    executables = {
        name: path
        for name in ("powermem", "powermem-mcp", "powermem-server", "pmem")
        if (path := bin_dir / _exe_name(name)).is_file()
    }
    if not executables:
        return None
    return _ReleaseBinaryRuntime(
        root=root,
        version=None,
        platform=_power_mem_platform_id(),
        executables=executables,
    )


def _release_executables_from_manifest(
    root: Path,
    payload: dict[str, object],
) -> dict[str, Path]:
    raw = payload.get("executables")
    executables: dict[str, Path] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            path = Path(value).expanduser()
            executables[name] = path if path.is_absolute() else root / path
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, str):
                continue
            executables[value] = root / "bin" / _exe_name(value)
    for name in ("powermem", "powermem-mcp", "powermem-server"):
        executables.setdefault(name, root / "bin" / _exe_name(name))
    if "pmem" not in executables and "powermem" in executables:
        executables["pmem"] = executables["powermem"]
    return executables


def _release_binary_runtime_root() -> Path:
    configured = os.environ.get(_RELEASE_BINARY_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime_dir = os.environ.get(_RUNTIME_DIR_ENV, "").strip()
    if runtime_dir:
        return Path(runtime_dir).expanduser()
    return Path.home() / ".contextseek" / "runtimes" / "powermem" / "release_binary"


def _power_mem_platform_id() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "aarch64" if machine in {"arm64", "aarch64"} else "amd64"
    if machine not in {"arm64", "aarch64", "x86_64", "amd64"}:
        return None
    if system == "darwin":
        return f"macos-{arch}"
    if system == "linux":
        return f"linux-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    return None


def _exe_name(executable: str) -> str:
    if os.name == "nt" and not executable.endswith(".exe"):
        return executable + ".exe"
    return executable


def _optional_str(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None


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
