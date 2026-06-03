"""File system watcher — triggers incremental sync when watched paths change.

Uses the `watchdog` library when available (install with
`pip install contextseek[daemon]`).  Falls back to a no-op with a one-time
warning when watchdog is not installed.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek


_WATCHDOG_AVAILABLE = False
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    _WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]


class _SyncHandler(FileSystemEventHandler):  # type: ignore[misc]
    """Watchdog handler that calls sync_path on file create/modify events."""

    def __init__(self, ctx: "ContextSeek", scope: str) -> None:
        super().__init__()
        self._ctx = ctx
        self._scope = scope

    def on_created(self, event: Any) -> None:
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_modified(self, event: Any) -> None:
        if not event.is_directory:
            self._trigger(event.src_path)

    def _trigger(self, path: str) -> None:
        from contextseek.daemon.sync_cmd import sync_path

        try:
            sync_path(self._ctx, path, scope=self._scope)
        except Exception:
            pass


class FileWatcher:
    """Watches one or more paths and incrementally syncs changes into ContextSeek.

    Usage::
        watcher = FileWatcher()
        watcher.add_watch("~/notes", "me/work", ctx)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(self) -> None:
        self._watches: list[tuple[str, str, "ContextSeek"]] = []
        self._observer: Any = None
        self._started = False
        self._warned = False

    def add_watch(self, path: str, scope: str, ctx: "ContextSeek") -> None:
        """Register a directory or file path to watch."""
        self._watches.append((str(Path(path).expanduser()), scope, ctx))

    def start(self) -> None:
        if self._started:
            return
        if not _WATCHDOG_AVAILABLE:
            if not self._warned:
                warnings.warn(
                    "watchdog is not installed; file watching disabled.  "
                    "Install with: pip install 'contextseek[daemon]'",
                    RuntimeWarning,
                    stacklevel=2,
                )
                self._warned = True
            return

        self._observer = Observer()
        for path, scope, ctx in self._watches:
            p = Path(path)
            watch_dir = str(p if p.is_dir() else p.parent)
            handler = _SyncHandler(ctx, scope)
            self._observer.schedule(handler, watch_dir, recursive=True)

        self._observer.start()
        self._started = True

    def stop(self) -> None:
        if self._observer is not None and self._started:
            self._observer.stop()
            self._observer.join()
            self._started = False

    @property
    def running(self) -> bool:
        return self._started and self._observer is not None
