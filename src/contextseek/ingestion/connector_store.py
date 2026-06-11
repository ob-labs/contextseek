"""Persistent store for connector configs."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from threading import RLock

from contextseek.ingestion._atomic import atomic_write_text
from contextseek.ingestion.models import ConnectorConfig, ConnectorKind, ConnectorMode


class ConnectorConfigStore:
    def list(self) -> list[ConnectorConfig]:
        raise NotImplementedError

    def save(self, config: ConnectorConfig) -> None:
        raise NotImplementedError

    def delete(self, connector_id: str) -> None:
        raise NotImplementedError


class InMemoryConnectorConfigStore(ConnectorConfigStore):
    def __init__(self) -> None:
        self._configs: dict[str, ConnectorConfig] = {}
        self._lock = RLock()

    def list(self) -> list[ConnectorConfig]:
        with self._lock:
            return list(self._configs.values())

    def save(self, config: ConnectorConfig) -> None:
        with self._lock:
            self._configs[config.connector_id] = config

    def delete(self, connector_id: str) -> None:
        with self._lock:
            self._configs.pop(connector_id, None)


class JsonFileConnectorConfigStore(ConnectorConfigStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        atomic_write_text(
            self._path,
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def list(self) -> list[ConnectorConfig]:
        with self._lock:
            rows = self._read()
            configs: list[ConnectorConfig] = []
            for payload in rows.values():
                try:
                    configs.append(
                        ConnectorConfig(
                            connector_id=str(payload["connector_id"]),
                            kind=ConnectorKind(str(payload["kind"])),
                            mode=ConnectorMode(str(payload["mode"])),
                            enabled=bool(payload.get("enabled", True)),
                            owner=str(payload.get("owner", "system")),
                            config=dict(payload.get("config", {})),
                            created_at=str(payload.get("created_at", "")),
                            updated_at=str(payload.get("updated_at", "")),
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue
            return configs

    def save(self, config: ConnectorConfig) -> None:
        with self._lock:
            rows = self._read()
            payload = asdict(config)
            payload["kind"] = config.kind.value
            payload["mode"] = config.mode.value
            rows[config.connector_id] = payload
            self._write(rows)

    def delete(self, connector_id: str) -> None:
        with self._lock:
            rows = self._read()
            rows.pop(connector_id, None)
            self._write(rows)

