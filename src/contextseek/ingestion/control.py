"""Control-plane façade for ingestion runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from contextseek.ingestion.connector_store import (
    ConnectorConfigStore,
    InMemoryConnectorConfigStore,
)
from contextseek.ingestion.models import ConnectorConfig, IngestionStatus, SyncCheckpoint
from contextseek.ingestion.registry import build_connector, build_normalizer
from contextseek.ingestion.runtime import ConnectorRuntime


class IngestionControlPlane:
    def __init__(
        self,
        runtime: ConnectorRuntime,
        *,
        event_buffer_size: int = 200,
        config_store: ConnectorConfigStore | None = None,
        restore_on_startup: bool = True,
        throughput_window_seconds: int = 60,
    ) -> None:
        self.runtime = runtime
        self._configs: dict[str, ConnectorConfig] = {}
        self._events: dict[str, deque[dict[str, Any]]] = {}
        self._config_store = config_store or InMemoryConnectorConfigStore()
        self._event_buffer_size = event_buffer_size
        self._throughput_window_seconds = max(1, throughput_window_seconds)
        self.runtime.event_callback = self.record_event
        if restore_on_startup:
            self.restore()

    def create_connector(self, config: ConnectorConfig) -> ConnectorConfig:
        self._configs[config.connector_id] = config
        self._config_store.save(config)
        connector = build_connector(config)
        normalizer = build_normalizer(config)
        self.runtime.register(config.connector_id, connector, normalizer)
        self._events.setdefault(config.connector_id, deque(maxlen=self._event_buffer_size))
        return config

    def list_connectors(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        metrics = self.runtime.metrics_snapshot()
        for cfg in self._configs.values():
            checkpoints = self.runtime.checkpoint_snapshot(cfg.connector_id)
            last_checkpoint = checkpoints[-1] if checkpoints else {}
            runtime_metrics = metrics.get(
                cfg.connector_id,
                {
                    "events_total": 0,
                    "events_written": 0,
                    "events_skipped": 0,
                    "events_rejected": 0,
                    "failed_total": 0,
                },
            )
            runtime_metrics = {
                **runtime_metrics,
                "throughput_per_min": self._throughput_per_min(cfg.connector_id),
            }
            rows.append(
                {
                    **asdict(cfg),
                    "checkpoint_count": len(checkpoints),
                    "last_status": last_checkpoint.get("status", "queued"),
                    "last_success_at": last_checkpoint.get("last_success_at"),
                    "retry_count": last_checkpoint.get("retry_count", 0),
                    "runtime_metrics": runtime_metrics,
                }
            )
        return rows

    def trigger_sync(self, connector_id: str) -> int:
        cfg = self._configs.get(connector_id)
        if cfg is None:
            msg = f"connector not found: {connector_id}"
            raise KeyError(msg)
        if not cfg.enabled:
            return 0
        self.runtime.enqueue_discovery(connector_id)
        return self.runtime.run_until_idle()

    def pause(self, connector_id: str) -> None:
        cfg = self._configs[connector_id]
        cfg.enabled = False
        self._config_store.save(cfg)

    def resume(self, connector_id: str) -> None:
        cfg = self._configs[connector_id]
        cfg.enabled = True
        self._config_store.save(cfg)

    def checkpoints(self, connector_id: str) -> list[dict[str, Any]]:
        return self.runtime.checkpoint_snapshot(connector_id)

    def events(self, connector_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(connector_id, []))

    def dead_letters(self, connector_id: str) -> list[dict[str, Any]]:
        records = self.runtime.dead_letter_store.list(connector_id=connector_id)
        return [asdict(record) for record in records]

    def delete_dead_letter(self, connector_id: str, record_id: str) -> bool:
        cfg = self._configs.get(connector_id)
        if cfg is None:
            msg = f"connector not found: {connector_id}"
            raise KeyError(msg)
        records = self.runtime.dead_letter_store.list(connector_id=connector_id)
        if not any(record.id == record_id for record in records):
            return False
        return self.runtime.dead_letter_store.delete(record_id)

    def replay_dead_letter(
        self,
        connector_id: str,
        record_id: str,
        *,
        run_now: bool = True,
    ) -> dict[str, Any]:
        cfg = self._configs.get(connector_id)
        if cfg is None:
            msg = f"connector not found: {connector_id}"
            raise KeyError(msg)
        records = self.runtime.dead_letter_store.list(connector_id=connector_id)
        record = next((item for item in records if item.id == record_id), None)
        if record is None:
            msg = f"dead-letter record not found: {record_id}"
            raise ValueError(msg)

        checkpoint = self.runtime.checkpoint_store.load(connector_id, record.partition)
        if checkpoint is None:
            checkpoint = SyncCheckpoint(
                connector_id=connector_id,
                partition=record.partition,
            )
        checkpoint.status = IngestionStatus.queued.value
        checkpoint.cursor = ""
        checkpoint.last_event_count = 0
        checkpoint.retry_count = 0
        checkpoint.last_error = None
        self.runtime.checkpoint_store.save(checkpoint)
        self.runtime.scheduler.enqueue_now(
            connector_id,
            record.partition,
            reason=f"replay:{record.id}",
        )
        scheduled_steps = self.runtime.run_until_idle() if run_now else 0
        return {
            "record_id": record.id,
            "connector_id": connector_id,
            "partition": record.partition,
            "scheduled_steps": scheduled_steps,
        }

    def replay_all_dead_letters(
        self,
        connector_id: str,
        *,
        run_now: bool = True,
        remove_after_replay: bool = False,
    ) -> dict[str, Any]:
        cfg = self._configs.get(connector_id)
        if cfg is None:
            msg = f"connector not found: {connector_id}"
            raise KeyError(msg)
        records = self.runtime.dead_letter_store.list(connector_id=connector_id)
        replayed: list[dict[str, Any]] = []
        for record in records:
            result = self.replay_dead_letter(connector_id, record.id, run_now=False)
            replayed.append(result)
            if remove_after_replay:
                self.runtime.dead_letter_store.delete(record.id)
        scheduled_steps = self.runtime.run_until_idle() if run_now else 0
        return {
            "connector_id": connector_id,
            "replayed_count": len(replayed),
            "scheduled_steps": scheduled_steps,
            "remove_after_replay": remove_after_replay,
            "records": replayed,
        }

    def record_event(self, connector_id: str, event: dict[str, Any]) -> None:
        queue = self._events.setdefault(
            connector_id, deque(maxlen=self._event_buffer_size)
        )
        queue.append(event)

    def restore(self) -> int:
        restored = 0
        for config in self._config_store.list():
            self._configs[config.connector_id] = config
            connector = build_connector(config)
            normalizer = build_normalizer(config)
            self.runtime.register(config.connector_id, connector, normalizer)
            self._events.setdefault(
                config.connector_id,
                deque(maxlen=self._event_buffer_size),
            )
            restored += 1
        return restored

    def _throughput_per_min(self, connector_id: str) -> float:
        queue = self._events.get(connector_id)
        if not queue:
            return 0.0
        now = datetime.now(timezone.utc)
        window = float(self._throughput_window_seconds)
        count = 0
        for event in queue:
            ts_raw = event.get("ts")
            if not isinstance(ts_raw, str) or not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            age = (now - ts).total_seconds()
            if 0 <= age <= window:
                count += 1
        return round((count * 60.0) / window, 2)

