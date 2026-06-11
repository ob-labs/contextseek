"""Dead-letter storage for ingestion failures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from contextseek.ingestion._atomic import atomic_write_text


@dataclass(slots=True)
class DeadLetterRecord:
    id: str
    connector_id: str
    partition: str
    stage: str
    payload: dict[str, Any]
    error_type: str
    error_message: str
    created_at: str


class DeadLetterStore:
    def put(
        self,
        connector_id: str,
        partition: str,
        stage: str,
        payload: dict[str, Any],
        exc: Exception,
    ) -> DeadLetterRecord:
        raise NotImplementedError

    def list(self, connector_id: str | None = None) -> list[DeadLetterRecord]:
        raise NotImplementedError

    def delete(self, record_id: str) -> bool:
        raise NotImplementedError


class InMemoryDeadLetterStore(DeadLetterStore):
    def __init__(self) -> None:
        self._records: list[DeadLetterRecord] = []
        self._lock = RLock()

    def put(
        self,
        connector_id: str,
        partition: str,
        stage: str,
        payload: dict[str, Any],
        exc: Exception,
    ) -> DeadLetterRecord:
        record = DeadLetterRecord(
            id=uuid4().hex,
            connector_id=connector_id,
            partition=partition,
            stage=stage,
            payload=payload,
            error_type=type(exc).__name__,
            error_message=str(exc),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._records.append(record)
        return record

    def list(self, connector_id: str | None = None) -> list[DeadLetterRecord]:
        with self._lock:
            if connector_id is None:
                return list(self._records)
            return [record for record in self._records if record.connector_id == connector_id]

    def delete(self, record_id: str) -> bool:
        with self._lock:
            before = len(self._records)
            self._records = [record for record in self._records if record.id != record_id]
            return len(self._records) < before


class JsonlDeadLetterStore(DeadLetterStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def put(
        self,
        connector_id: str,
        partition: str,
        stage: str,
        payload: dict[str, Any],
        exc: Exception,
    ) -> DeadLetterRecord:
        record = DeadLetterRecord(
            id=uuid4().hex,
            connector_id=connector_id,
            partition=partition,
            stage=stage,
            payload=payload,
            error_type=type(exc).__name__,
            error_message=str(exc),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def list(self, connector_id: str | None = None) -> list[DeadLetterRecord]:
        if not self._path.exists():
            return []
        with self._lock:
            records: list[DeadLetterRecord] = []
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    record = DeadLetterRecord(
                        id=str(payload["id"]),
                        connector_id=str(payload["connector_id"]),
                        partition=str(payload["partition"]),
                        stage=str(payload["stage"]),
                        payload=dict(payload.get("payload", {})),
                        error_type=str(payload.get("error_type", "UnknownError")),
                        error_message=str(payload.get("error_message", "")),
                        created_at=str(payload.get("created_at", "")),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if connector_id is None or record.connector_id == connector_id:
                    records.append(record)
            return records

    def delete(self, record_id: str) -> bool:
        with self._lock:
            records = self.list(connector_id=None)
            kept = [record for record in records if record.id != record_id]
            if len(kept) == len(records):
                return False
            lines = [json.dumps(asdict(record), ensure_ascii=False) for record in kept]
            content = "\n".join(lines)
            if content:
                content += "\n"
            atomic_write_text(self._path, content)
            return True

