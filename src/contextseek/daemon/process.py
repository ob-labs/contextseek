"""Background daemon process — combines LifecycleScheduler, FileWatcher, and MCP HTTP server."""

from __future__ import annotations

import os
import pathlib
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek

_DEFAULT_MCP_PORT = 2882
_DEFAULT_MCP_HOST = "127.0.0.1"


class DaemonProcess:
    """Manages the contextseek background daemon.

    The daemon combines three long-running components in one process:
      - LifecycleScheduler: periodic compact + dream for registered scopes
      - FileWatcher: incremental sync when watched paths change
      - HTTP MCP server: exposes contextseek tools over the network

    Usage (foreground, called by systemd/launchd)::

        daemon = DaemonProcess(config_dir=Path("~/.contextseek"))
        daemon.start_foreground(ctx)
    """

    def __init__(self, config_dir: pathlib.Path | None = None) -> None:
        self._config_dir = pathlib.Path(
            config_dir or pathlib.Path.home() / ".contextseek"
        ).expanduser()
        self._pid_file = self._config_dir / "daemon.pid"
        self._status_file = self._config_dir / "daemon.status.json"
        self._start_time: datetime | None = None
        self._scheduler: Any = None
        self._watcher: Any = None
        self._mcp_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_foreground(self, ctx: "ContextSeek") -> None:
        """Start all daemon components and block until SIGTERM/SIGINT."""
        self._write_pid()
        self._start_time = datetime.now(timezone.utc)

        log_path = self._config_dir / "logs" / "lifecycle.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        from contextseek.daemon.logger import LifecycleLogger
        from contextseek.policies.lifecycle import LifecycleScheduler

        logger = LifecycleLogger(log_path)

        # Optional: materialize distilled prompt skills as SKILL.md after each cycle.
        export_dir: pathlib.Path | None = None
        if self._load_config_value("SKILL_EXPORT_ENABLED", "false").lower() == "true":
            raw_dir = self._load_config_value(
                "SKILL_EXPORT_DIR", "~/.contextseek/skills"
            )
            export_dir = pathlib.Path(raw_dir).expanduser()
        try:
            export_min_confidence = float(
                self._load_config_value("SKILL_EXPORT_MIN_CONFIDENCE", "0.8")
            )
        except ValueError:
            export_min_confidence = 0.8

        self._scheduler = LifecycleScheduler(
            client=ctx,
            on_event=logger,
            snapshot_dir=self._config_dir / "backups",
            export_dir=export_dir,
            export_min_confidence=export_min_confidence,
        )

        # Register scopes from config (WATCH_PATHS doubles as scope list)
        watch_entries = self._load_watch_paths()
        scopes: set[str] = set()
        for _path, scope in watch_entries:
            if scope:
                scopes.add(scope)
                self._scheduler.register_scope(scope)

        # Start file watcher
        from contextseek.daemon.watcher import FileWatcher

        self._watcher = FileWatcher()
        for watch_path, scope in watch_entries:
            self._watcher.add_watch(watch_path, scope, ctx)
        self._watcher.start()

        # Start lifecycle scheduler
        self._scheduler.start()

        # Start MCP HTTP server in a background thread (optional dep)
        self._start_mcp_server(ctx)

        # Persist component states so cross-process status() can read them
        self._write_status()

        print(
            f"contextseek daemon started (PID {os.getpid()})",
            flush=True,
        )
        if scopes:
            print(f"  scopes: {', '.join(sorted(scopes))}", flush=True)
        print(f"  log: {log_path}", flush=True)

        # Block until signal
        stop_event = threading.Event()

        def _handle_signal(signum: int, frame: Any) -> None:
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        stop_event.wait()
        self._shutdown()

    def stop(self) -> bool:
        """Send SIGTERM to a running daemon.  Returns True if signal was sent."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except (ProcessLookupError, PermissionError):
            self._pid_file.unlink(missing_ok=True)
            return False

    def is_running(self) -> bool:
        """Return True if a daemon process with the recorded PID is alive."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def status(self) -> dict:
        """Return a status dict for display by `contextseek daemon status`."""
        pid = self._read_pid()
        running = self.is_running()

        # Derive uptime from PID file mtime (works cross-process)
        uptime_str = ""
        if running and self._pid_file.exists():
            try:
                mtime = self._pid_file.stat().st_mtime
                delta = datetime.now(timezone.utc).timestamp() - mtime
                h, rem = divmod(int(delta), 3600)
                m = rem // 60
                uptime_str = f"{h}h {m}m"
            except OSError:
                pass

        # Read component states written by the daemon process
        components = self._read_status()
        return {
            "running": running,
            "pid": pid,
            "uptime": uptime_str,
            "components": components,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_status(self) -> None:
        import json as _json
        data = {
            "started_at": self._start_time.isoformat() if self._start_time else None,
            "LifecycleScheduler": self._scheduler is not None and bool(getattr(self._scheduler, "running", False)),
            "FileWatcher": self._watcher is not None and bool(getattr(self._watcher, "running", False)),
            "MCP HTTP server": self._mcp_thread is not None and self._mcp_thread.is_alive(),
        }
        self._status_file.write_text(_json.dumps(data), encoding="utf-8")

    def _read_status(self) -> dict:
        import json as _json
        default = {"LifecycleScheduler": False, "FileWatcher": False, "MCP HTTP server": False}
        if not self._status_file.exists():
            return default
        try:
            data = _json.loads(self._status_file.read_text(encoding="utf-8"))
            return {
                "LifecycleScheduler": bool(data.get("LifecycleScheduler")),
                "FileWatcher": bool(data.get("FileWatcher")),
                "MCP HTTP server": bool(data.get("MCP HTTP server")),
            }
        except (OSError, ValueError):
            return default

    def _write_pid(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def _read_pid(self) -> int | None:
        if not self._pid_file.exists():
            return None
        try:
            return int(self._pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _load_config_value(self, key: str, default: str) -> str:
        """Read a single ``KEY=value`` line from config.env (last wins)."""
        config_env = self._config_dir / "config.env"
        if not config_env.exists():
            return default
        prefix = f"{key}="
        value = default
        for line in config_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or not line.startswith(prefix):
                continue
            value = line[len(prefix):].strip().strip('"').strip("'")
        return value

    def _load_watch_paths(self) -> list[tuple[str, str]]:
        """Parse WATCH_PATHS from config.env.

        Format: ``WATCH_PATHS=~/notes:me/work,~/docs:me/research``
        Returns list of (path, scope) tuples.
        """
        config_env = self._config_dir / "config.env"
        if not config_env.exists():
            return []
        results: list[tuple[str, str]] = []
        for line in config_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("WATCH_PATHS="):
                continue
            value = line[len("WATCH_PATHS="):].strip().strip('"').strip("'")
            for entry in value.split(","):
                entry = entry.strip()
                if ":" in entry:
                    path_part, scope_part = entry.split(":", 1)
                    expanded = str(pathlib.Path(path_part.strip()).expanduser())
                    results.append((expanded, scope_part.strip()))
        return results

    def _start_mcp_server(self, ctx: "ContextSeek") -> None:
        """Start the MCP HTTP server in a daemon thread if FastAPI is available."""
        try:
            import uvicorn  # noqa: F401
            from fastapi import FastAPI  # noqa: F401
            from contextseek.mcp.server import ContextSeekMCPServer
            from contextseek.http.server import create_app  # noqa: F401
        except ImportError:
            return

        from contextseek.mcp.server import ContextSeekMCPServer

        mcp_server = ContextSeekMCPServer(client=ctx)

        def _run() -> None:
            import uvicorn
            from contextseek.mcp.runtime import create_sse_app, MCPRuntime

            app = create_sse_app(runtime=MCPRuntime(server=mcp_server))
            uvicorn.run(
                app,
                host=_DEFAULT_MCP_HOST,
                port=_DEFAULT_MCP_PORT,
                log_level="warning",
            )

        self._mcp_thread = threading.Thread(target=_run, daemon=True, name="mcp-server")
        self._mcp_thread.start()
        print(
            f"  MCP HTTP server listening on {_DEFAULT_MCP_HOST}:{_DEFAULT_MCP_PORT}",
            flush=True,
        )

    def _shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._watcher is not None:
            self._watcher.stop()
        self._pid_file.unlink(missing_ok=True)
        self._status_file.unlink(missing_ok=True)
        print("contextseek daemon stopped", flush=True)
