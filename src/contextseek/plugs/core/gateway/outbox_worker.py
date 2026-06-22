"""Outbox retry worker for PlugGateway."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from contextseek.plugs.core.gateway.gateway import PlugGateway
from contextseek.plugs.core.protocols import MaterializationReceipt, PlugChangeEvent

logger = logging.getLogger(__name__)


@dataclass
class OutboxRunResult:
    """Summary of one outbox worker run."""

    applied: list[MaterializationReceipt] = field(default_factory=list)
    failed_event_ids: list[str] = field(default_factory=list)


class OutboxWorker:
    """Retry pending/failed plug outbox events."""

    def __init__(self, gateway: PlugGateway, *, max_retry: int = 3) -> None:
        self._max_retry = int(max_retry)
        self._gateway = gateway

    def run_once(self, *, limit: int = 100) -> OutboxRunResult:
        """Replay retryable events once; failures are isolated per event."""
        rows = self._gateway._store.plug_outbox_list_retryable(  # noqa: SLF001
            limit=limit,
            max_retry=self._max_retry,
        )
        result = OutboxRunResult()
        for row in rows:
            event_id = str(row.get("event_id") or "")
            try:
                event = PlugChangeEvent.from_payload(row["event_payload"])
            except Exception:
                logger.exception(
                    "Plug outbox payload is invalid for event %s", event_id
                )
                if event_id:
                    result.failed_event_ids.append(event_id)
                    self._mark_invalid_payload(event_id)
                continue

            try:
                result.applied.append(
                    self._gateway.apply(event, max_retry=self._max_retry)
                )
            except Exception:
                logger.exception("Plug outbox replay failed for event %s", event_id)
                if event_id:
                    result.failed_event_ids.append(event_id)
        return result

    def _mark_invalid_payload(self, event_id: str) -> None:
        self._gateway._mark_failed_and_maybe_dead(  # noqa: SLF001
            event_id,
            "invalid event payload",
            max_retry=self._max_retry,
        )
