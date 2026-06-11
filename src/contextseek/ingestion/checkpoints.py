"""Checkpoint store abstractions and implementations."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from contextseek.ingestion._atomic import atomic_write_text
from contextseek.ingestion.models import SyncCheckpoint


class CheckpointStore(Protocol):
    def load(self, connector_id: str, partition: str) -> SyncCheckpoint | None: ...

    def save(self, checkpoint: SyncCheckpoint) -> None: ...

    def list(self, connector_id: str) -> list[SyncCheckpoint]: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], SyncCheckpoint] = {}
        self._lock = RLock()

    def load(self, connector_id: str, partition: str) -> SyncCheckpoint | None:
        with self._lock:
            return self._data.get((connector_id, partition))

    def save(self, checkpoint: SyncCheckpoint) -> None:
        with self._lock:
            self._data[(checkpoint.connector_id, checkpoint.partition)] = checkpoint

    def list(self, connector_id: str) -> list[SyncCheckpoint]:
        with self._lock:
            return [cp for (cid, _), cp in self._data.items() if cid == connector_id]


class JsonFileCheckpointStore:
    """Simple JSON-backed checkpoint store for local deployments."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        atomic_write_text(
            self._path,
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
        )

    def load(self, connector_id: str, partition: str) -> SyncCheckpoint | None:
        with self._lock:
            data = self._read()
            key = f"{connector_id}::{partition}"
            payload = data.get(key)
            if payload is None:
                return None
            return SyncCheckpoint(**payload)

    def save(self, checkpoint: SyncCheckpoint) -> None:
        with self._lock:
            data = self._read()
            key = f"{checkpoint.connector_id}::{checkpoint.partition}"
            data[key] = asdict(checkpoint)
            self._write(data)

    def list(self, connector_id: str) -> list[SyncCheckpoint]:
        with self._lock:
            data = self._read()
            result: list[SyncCheckpoint] = []
            prefix = f"{connector_id}::"
            for key, payload in data.items():
                if key.startswith(prefix):
                    result.append(SyncCheckpoint(**payload))
            return result

