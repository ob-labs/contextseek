"""Scheduling primitives for ingestion runtime."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import time


RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5, 30, 120, 600)


class RetryableError(Exception):
    """A transient error that should be retried with backoff."""


@dataclass(slots=True, order=True)
class ScheduledTask:
    run_after: float
    connector_id: str
    partition: str
    reason: str = ""


class IngestionScheduler:
    def __init__(self, retry_backoff_seconds: tuple[int, ...] | None = None) -> None:
        self._queue: list[ScheduledTask] = []
        self._retry_backoff_seconds = retry_backoff_seconds or RETRY_BACKOFF_SECONDS

    def enqueue_now(self, connector_id: str, partition: str, *, reason: str = "") -> None:
        self._upsert_task(
            ScheduledTask(
                run_after=time.time(),
                connector_id=connector_id,
                partition=partition,
                reason=reason,
            )
        )

    def requeue(
        self,
        connector_id: str,
        partition: str,
        retry_count: int,
        *,
        reason: str = "",
    ) -> float:
        idx = min(retry_count, len(self._retry_backoff_seconds) - 1)
        delay = float(self._retry_backoff_seconds[idx])
        run_after = time.time() + delay
        self._upsert_task(
            ScheduledTask(
                run_after=run_after,
                connector_id=connector_id,
                partition=partition,
                reason=reason,
            )
        )
        return delay

    def pop_ready(self) -> ScheduledTask | None:
        if not self._queue:
            return None
        if self._queue[0].run_after > time.time():
            return None
        return heapq.heappop(self._queue)

    def has_pending(self) -> bool:
        return bool(self._queue)

    def next_delay(self) -> float | None:
        if not self._queue:
            return None
        return max(0.0, self._queue[0].run_after - time.time())

    def _upsert_task(self, task: ScheduledTask) -> None:
        # Keep at most one pending task per connector+partition.
        replaced = False
        filtered: list[ScheduledTask] = []
        for existing in self._queue:
            if (
                existing.connector_id == task.connector_id
                and existing.partition == task.partition
            ):
                replaced = True
                continue
            filtered.append(existing)
        if replaced:
            self._queue = filtered
            heapq.heapify(self._queue)
        heapq.heappush(self._queue, task)

