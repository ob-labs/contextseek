"""Connector runtime for ingestion layer v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from contextseek.ingestion.checkpoints import CheckpointStore, InMemoryCheckpointStore
from contextseek.ingestion.connectors.base import SourceConnector
from contextseek.ingestion.dead_letter import DeadLetterStore, InMemoryDeadLetterStore
from contextseek.ingestion.models import IngestionStatus, SyncCheckpoint
from contextseek.ingestion.normalizers.base import EventNormalizer
from contextseek.ingestion.policy.gate import DefaultPolicyGate
from contextseek.ingestion.scheduler import IngestionScheduler, RetryableError
from contextseek.ingestion.writer import IngestionWriter


@dataclass(slots=True)
class RuntimeStats:
    events_total: int = 0
    events_written: int = 0
    events_skipped: int = 0
    events_rejected: int = 0
    failed_total: int = 0


@dataclass(slots=True)
class ConnectorRuntimeStats:
    events_total: int = 0
    events_written: int = 0
    events_skipped: int = 0
    events_rejected: int = 0
    failed_total: int = 0


class ConnectorRuntime:
    def __init__(
        self,
        *,
        writer: IngestionWriter,
        checkpoint_store: CheckpointStore | None = None,
        dead_letter_store: DeadLetterStore | None = None,
        scheduler: IngestionScheduler | None = None,
        policy_gate: DefaultPolicyGate | None = None,
        dead_letter_retry_threshold: int = 4,
        event_callback: Any | None = None,
    ) -> None:
        self.writer = writer
        self.checkpoint_store = checkpoint_store or InMemoryCheckpointStore()
        self.dead_letter_store = dead_letter_store or InMemoryDeadLetterStore()
        self.scheduler = scheduler or IngestionScheduler()
        self.policy_gate = policy_gate or DefaultPolicyGate()
        self.dead_letter_retry_threshold = dead_letter_retry_threshold
        self.event_callback = event_callback
        self.stats = RuntimeStats()
        self._stats_by_connector: dict[str, ConnectorRuntimeStats] = {}
        self._connectors: dict[str, SourceConnector] = {}
        self._normalizers: dict[str, EventNormalizer] = {}

    def register(
        self,
        connector_id: str,
        connector: SourceConnector,
        normalizer: EventNormalizer,
    ) -> None:
        self._connectors[connector_id] = connector
        self._normalizers[connector_id] = normalizer
        self._stats_by_connector.setdefault(connector_id, ConnectorRuntimeStats())

    def enqueue_discovery(self, connector_id: str) -> None:
        connector = self._connectors[connector_id]
        for partition in connector.discover():
            self.scheduler.enqueue_now(connector_id, partition, reason="discover")

    def run_once(self) -> bool:
        task = self.scheduler.pop_ready()
        if task is None:
            return False
        self.run_partition(task.connector_id, task.partition)
        return True

    def run_until_idle(self, *, max_steps: int = 1000) -> int:
        steps = 0
        while steps < max_steps and self.run_once():
            steps += 1
        return steps

    def run_partition(self, connector_id: str, partition: str) -> None:
        connector = self._connectors[connector_id]
        normalizer = self._normalizers[connector_id]
        checkpoint = self.checkpoint_store.load(connector_id, partition)
        if checkpoint is None:
            checkpoint = SyncCheckpoint(
                connector_id=connector_id,
                partition=partition,
                status=IngestionStatus.queued.value,
            )

        try:
            checkpoint.status = IngestionStatus.fetching.value
            result = connector.pull(partition, checkpoint)
            checkpoint.status = IngestionStatus.normalizing.value
            processed = 0
            for payload in result.payloads:
                self.stats.events_total += 1
                self._stats_by_connector[connector_id].events_total += 1
                try:
                    event = normalizer.normalize(
                        payload,
                        connector_id=connector_id,
                        partition=partition,
                    )
                except Exception as exc:
                    self.stats.failed_total += 1
                    self._stats_by_connector[connector_id].failed_total += 1
                    self.dead_letter_store.put(
                        connector_id=connector_id,
                        partition=partition,
                        stage="normalize",
                        payload=payload,
                        exc=exc,
                    )
                    continue

                checkpoint.status = IngestionStatus.persisting.value
                event = self.policy_gate.apply(event)
                if event is None:
                    self.stats.events_rejected += 1
                    self._stats_by_connector[connector_id].events_rejected += 1
                    self._emit_event(
                        connector_id=connector_id,
                        partition=partition,
                        status="rejected",
                        payload=payload,
                    )
                    continue
                write_result = self.writer.write(event)
                if write_result.status == "written":
                    self.stats.events_written += 1
                    self._stats_by_connector[connector_id].events_written += 1
                elif write_result.status == "skipped":
                    self.stats.events_skipped += 1
                    self._stats_by_connector[connector_id].events_skipped += 1
                else:
                    self.stats.failed_total += 1
                    self._stats_by_connector[connector_id].failed_total += 1
                self._emit_event(
                    connector_id=connector_id,
                    partition=partition,
                    status=write_result.status,
                    payload={
                        "event_id": event.event_id,
                        "source_id": event.source_id,
                        "scope": event.scope,
                    },
                )
                processed += 1

            checkpoint.status = IngestionStatus.checkpointing.value
            checkpoint.cursor = result.next_cursor
            checkpoint.last_event_count = processed
            checkpoint.last_success_at = datetime.now(timezone.utc).isoformat()
            checkpoint.retry_count = 0
            checkpoint.last_error = None
            checkpoint.status = IngestionStatus.synced.value
            self.checkpoint_store.save(checkpoint)
        except RetryableError as exc:
            checkpoint.retry_count += 1
            checkpoint.last_error = str(exc)
            checkpoint.status = IngestionStatus.failed.value
            self.checkpoint_store.save(checkpoint)
            if checkpoint.retry_count >= self.dead_letter_retry_threshold:
                self.dead_letter_store.put(
                    connector_id=connector_id,
                    partition=partition,
                    stage="fetch",
                    payload={"partition": partition},
                    exc=exc,
                )
                checkpoint.status = IngestionStatus.dead_letter.value
                self.checkpoint_store.save(checkpoint)
                return
            self.scheduler.requeue(
                connector_id,
                partition,
                checkpoint.retry_count,
                reason=str(exc),
            )
        except Exception as exc:
            checkpoint.retry_count += 1
            checkpoint.last_error = str(exc)
            checkpoint.status = IngestionStatus.failed.value
            self.checkpoint_store.save(checkpoint)
            self.dead_letter_store.put(
                connector_id=connector_id,
                partition=partition,
                stage="fetch",
                payload={"partition": partition},
                exc=exc,
            )
            self.stats.failed_total += 1
            self._stats_by_connector[connector_id].failed_total += 1

    def _emit_event(
        self,
        *,
        connector_id: str,
        partition: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(
                connector_id,
                {
                    "partition": partition,
                    "status": status,
                    "payload": payload,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            return

    def checkpoint_snapshot(self, connector_id: str) -> list[dict[str, Any]]:
        rows = self.checkpoint_store.list(connector_id)
        return [
            {
                "connector_id": cp.connector_id,
                "partition": cp.partition,
                "cursor": cp.cursor,
                "status": cp.status,
                "last_success_at": cp.last_success_at,
                "last_event_count": cp.last_event_count,
                "retry_count": cp.retry_count,
                "last_error": cp.last_error,
            }
            for cp in rows
        ]

    def metrics_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            connector_id: {
                "events_total": stats.events_total,
                "events_written": stats.events_written,
                "events_skipped": stats.events_skipped,
                "events_rejected": stats.events_rejected,
                "failed_total": stats.failed_total,
            }
            for connector_id, stats in self._stats_by_connector.items()
        }

    def export_prometheus_metrics(self) -> str:
        lines: list[str] = []
        for connector_id, stats in self._stats_by_connector.items():
            labels = f'connector_id="{connector_id}"'
            lines.append(
                f"ingestion_events_total{{{labels},status=\"received\"}} {stats.events_total}"
            )
            lines.append(
                f"ingestion_events_total{{{labels},status=\"written\"}} {stats.events_written}"
            )
            lines.append(
                f"ingestion_events_total{{{labels},status=\"skipped\"}} {stats.events_skipped}"
            )
            lines.append(
                f"ingestion_events_total{{{labels},status=\"rejected\"}} {stats.events_rejected}"
            )
            lines.append(
                f"ingestion_failed_total{{{labels}}} {stats.failed_total}"
            )
            checkpoints = self.checkpoint_snapshot(connector_id)
            if checkpoints:
                lines.append(
                    f"checkpoint_partitions_total{{{labels}}} {len(checkpoints)}"
                )
        return "\n".join(lines)

