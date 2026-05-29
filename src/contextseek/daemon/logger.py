"""Persistent lifecycle event logger — appends JSONL to ~/.contextseek/logs/lifecycle.jsonl."""

from __future__ import annotations

import json
import pathlib
import threading
from datetime import timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextseek.policies.lifecycle import LifecycleEvent


class LifecycleLogger:
    """Lifecycle event handler that persists events to a JSONL log file.

    Attach to LifecycleScheduler via the on_event callback so every compact/
    dream cycle is durably recorded.  The overview command reads this file to
    display "last evolved N ago".
    """

    def __init__(self, log_path: str | pathlib.Path) -> None:
        self._path = pathlib.Path(log_path)
        self._lock = threading.Lock()

    def __call__(self, event: "LifecycleEvent") -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": event.timestamp.astimezone(timezone.utc).isoformat(),
            "scope": event.scope,
            "action": event.action,
            **event.result,
        }
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_lifecycle_log(path: str | pathlib.Path) -> list[dict]:
    """Read all entries from a lifecycle JSONL log file.

    Returns an empty list if the file does not exist or cannot be parsed.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return []
    entries: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries
