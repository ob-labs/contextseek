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


def _read_config_env(keys: set[str]) -> dict[str, str]:
    """Read simple KEY=value entries from the resolved ContextSeek config file."""
    try:
        from contextseek.config.settings import _get_default_env_file
    except Exception:
        return {}

    env_file = _get_default_env_file()
    if not env_file:
        return {}

    values: dict[str, str] = {}
    try:
        lines = Path(env_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if key in keys:
            values[key] = value.strip().strip('"').strip("'")
    return values


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


def run_desktop_server(args: argparse.Namespace) -> int:
    """Launch uvicorn serving the same-origin (API + SPA) app."""
    data_dir = (
        Path(args.data_dir).expanduser() if args.data_dir else _default_data_dir()
    )
    data_dir.mkdir(parents=True, exist_ok=True)

    backend = _configure_storage(data_dir)

    port = args.port
    if port is None:
        port = int(os.environ.get("CTX_DESKTOP_PORT", "8000"))

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
    uvicorn.run(
        create_app(),
        host=args.host,
        port=port,
        log_level=args.log_level,
    )
    return 0
