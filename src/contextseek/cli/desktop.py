"""``contextseek desktop-server`` — same-origin backend for the desktop app.

Serves the FastAPI API and the built dashboard SPA from a single origin
(``http://<host>:<port>``). Picks sensible desktop defaults (persistent storage
in a platform app-data directory) before the app is built, so the desktop shell
needs no extra configuration.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


_DESKTOP_CONFIG_SEED_PREFIXES = (
    "EMBEDDING_",
    "LLM_",
    "OB_",
    "OCEANBASE_",
    "SEEKDB_",
    "SQLITE_",
)
_DESKTOP_CONFIG_SEED_KEYS = {
    "ANTHROPIC_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEFAULT_SCOPE",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "SILICONFLOW_API_KEY",
    "STORAGE_BACKEND",
    "STORAGE_PATH",
    "TIMEZONE",
    "VLLM_API_KEY",
}


def _read_config_env(keys: set[str]) -> dict[str, str]:
    """Read simple KEY=value entries from the resolved ContextSeek config file."""
    try:
        from contextseek.config.settings import _get_default_env_file
    except Exception:
        return {}

    env_file = _get_default_env_file()
    if not env_file:
        return {}

    return {
        key: value
        for key, value in _read_env_file(Path(env_file)).items()
        if key in keys
    }


def _read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=value entries from an env file."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _is_desktop_config_seed_key(key: str) -> bool:
    return key in _DESKTOP_CONFIG_SEED_KEYS or key.startswith(
        _DESKTOP_CONFIG_SEED_PREFIXES,
    )


def _desktop_config_seed_candidates(config_path: Path) -> list[Path]:
    """Return config files that can seed the first desktop config."""
    candidates: list[Path] = []
    explicit = os.environ.get("CONTEXTSEEK_CONFIG", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    project_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            Path.cwd() / ".env",
            project_root / ".env",
            Path.home() / ".contextseek" / "config.env",
        ],
    )

    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.expanduser()
        try:
            resolved = path.resolve()
            target = config_path.resolve()
        except OSError:
            resolved = path
            target = config_path
        if resolved == target or resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _seed_desktop_config_values(config_path: Path) -> dict[str, str]:
    for candidate in _desktop_config_seed_candidates(config_path):
        values = {
            key: value
            for key, value in _read_env_file(candidate).items()
            if _is_desktop_config_seed_key(key)
        }
        if values:
            return values
    return {}


def _default_data_dir() -> Path:
    """Platform-standard application data directory for ContextSeek."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "contextseek"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "contextseek"
    # Linux / other: XDG data home.
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / "contextseek"


def _configure_storage(data_dir: Path) -> str:
    """Set storage env defaults (without overriding user-set values).

    Defaults to the SQLite backend: cross-platform, no native dependency, and
    vector recall via pyseekdb's ONNX embedder when available. Returns the
    effective backend name for logging.
    """
    config_env = _read_config_env(
        {"STORAGE_BACKEND", "SQLITE_PATH", "SEEKDB_PATH", "STORAGE_PATH"}
    )
    backend = os.environ.get("STORAGE_BACKEND", "").strip()
    if not backend:
        backend = config_env.get("STORAGE_BACKEND", "").strip()
    if not backend:
        backend = "sqlite"
        os.environ["STORAGE_BACKEND"] = backend

    if backend == "sqlite" and not (
        os.environ.get("SQLITE_PATH") or config_env.get("SQLITE_PATH")
    ):
        os.environ.setdefault("SQLITE_PATH", str(data_dir / "contextseek.sqlite3"))
    elif backend == "seekdb" and not (
        os.environ.get("SEEKDB_PATH") or config_env.get("SEEKDB_PATH")
    ):
        os.environ.setdefault("SEEKDB_PATH", str(data_dir / "seekdb.db"))
    elif backend == "file" and not (
        os.environ.get("STORAGE_PATH") or config_env.get("STORAGE_PATH")
    ):
        os.environ.setdefault("STORAGE_PATH", str(data_dir / "store"))
    return backend


def _configure_desktop_path() -> None:
    """Add common user CLI install directories to PATH for packaged desktop apps.

    GUI apps and Tauri sidecars are often launched without the user's login-shell
    PATH. Agent CLIs installed by npm/nvm/Homebrew can be present on the machine
    but invisible to ``shutil.which`` unless we add these directories here.
    """
    current = os.environ.get("PATH", "")
    existing = [part for part in current.split(os.pathsep) if part]
    seen = {str(Path(part).expanduser()) for part in existing}
    additions: list[str] = []

    for path in _desktop_cli_search_paths():
        text = str(path.expanduser())
        if text in seen or not path.is_dir():
            continue
        seen.add(text)
        additions.append(text)

    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, *existing])


def _configure_desktop_runtime() -> None:
    """Mark the process as desktop so plug runtimes can avoid Python venv setup."""
    os.environ.setdefault("CONTEXTSEEK_DESKTOP", "1")
    os.environ.setdefault("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", "auto")


def _configure_desktop_powermem_proxy_url(host: str, port: int) -> str:
    """Point PowerMem-capable agents at this desktop server's proxy route."""
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    proxy_url = f"http://{url_host}:{port}/plugins/powermem/default"
    os.environ["CONTEXTSEEK_POWERMEM_PROXY_BASE_URL"] = proxy_url
    return proxy_url


def _publish_desktop_powermem_hook_env(proxy_url: str) -> None:
    """Publish the live desktop proxy URL for Claude Code HTTP hooks."""
    try:
        from contextseek.plugs.powermem.linkers.claude_code_plugin import (
            write_claude_code_plugin_runtime_envs,
        )

        write_claude_code_plugin_runtime_envs(proxy_url)
    except Exception as exc:  # pragma: no cover - defensive desktop bootstrap
        print(
            f"[desktop-server] PowerMem hook env update skipped: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _desktop_cli_search_paths() -> list[Path]:
    home = Path.home()
    paths = [
        home / ".local" / "bin",
        home / "bin",
        home / ".npm-global" / "bin",
        home / ".npm" / "bin",
        home / ".volta" / "bin",
        home / ".bun" / "bin",
        home / "Library" / "pnpm",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
    ]
    paths.extend(sorted((home / ".nvm" / "versions" / "node").glob("*/bin")))
    paths.extend(
        sorted((home / ".fnm" / "node-versions").glob("*/installation/bin")),
    )

    appdata = os.environ.get("APPDATA", "").strip()
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    userprofile = os.environ.get("USERPROFILE", "").strip()
    if appdata:
        paths.append(Path(appdata) / "npm")
    if localappdata:
        paths.append(Path(localappdata) / "pnpm")
    if userprofile:
        paths.append(Path(userprofile) / "AppData" / "Roaming" / "npm")
    return paths


def _ensure_desktop_config(data_dir: Path) -> None:
    """Ensure the desktop app has a writable config.env file."""
    config_path = Path(
        os.environ.get("CONTEXTSEEK_CONFIG", data_dir / "config.env")
    ).expanduser()
    os.environ.setdefault("CONTEXTSEEK_CONFIG", str(config_path))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        return

    default_sqlite = data_dir / "contextseek.sqlite3"
    values = _seed_desktop_config_values(config_path)
    values.setdefault("STORAGE_BACKEND", "sqlite")
    if values["STORAGE_BACKEND"].strip().lower() == "sqlite":
        values.setdefault("SQLITE_PATH", str(default_sqlite))
    values.setdefault("LLM_PROVIDER", "none")
    if values["LLM_PROVIDER"].strip().lower() == "none":
        values.setdefault("LLM_MODEL", "none")
    values.setdefault("EMBEDDING_PROVIDER", "none")
    if values["EMBEDDING_PROVIDER"].strip().lower() == "none":
        values.setdefault("EMBEDDING_MODEL", "none")

    config_path.write_text(
        "\n".join(
            [
                "# ContextSeek desktop configuration",
                *(f"{key}={value}" for key, value in values.items()),
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_desktop_server(args: argparse.Namespace) -> int:
    """Launch uvicorn serving the same-origin (API + SPA) app."""
    data_dir = (
        Path(args.data_dir).expanduser() if args.data_dir else _default_data_dir()
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_desktop_config(data_dir)
    _configure_desktop_runtime()
    _configure_desktop_path()

    backend = _configure_storage(data_dir)

    port = args.port
    if port is None:
        port = int(os.environ.get("CTX_DESKTOP_PORT", "8000"))
    proxy_url = _configure_desktop_powermem_proxy_url(args.host, port)
    _publish_desktop_powermem_hook_env(proxy_url)
    try:
        from contextseek.plugs.powermem.runtime_manager import (
            start_managed_powermem_http_runtime,
            stop_managed_powermem_http_runtime,
        )

        start_managed_powermem_http_runtime()
    except Exception as exc:  # pragma: no cover - defensive desktop bootstrap
        print(
            f"[desktop-server] PowerMem runtime autostart skipped: {exc}",
            file=sys.stderr,
            flush=True,
        )
        stop_managed_powermem_http_runtime = None  # type: ignore[assignment]

    try:
        import uvicorn

        from contextseek.http.server import create_app
    except ImportError as exc:
        print(
            "[desktop-server] missing HTTP dependencies. "
            "Install with: pip install contextseek[http]\n"
            f"  ({exc})",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        f"[desktop-server] storage={backend} data_dir={data_dir} "
        f"listening on http://{args.host}:{port}",
        flush=True,
    )
    try:
        uvicorn.run(
            create_app(),
            host=args.host,
            port=port,
            log_level=args.log_level,
        )
    finally:
        if stop_managed_powermem_http_runtime is not None:
            stop_managed_powermem_http_runtime()
    return 0
