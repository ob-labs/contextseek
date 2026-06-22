"""PlugGateway orchestration."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.gateway.materializer import PlugMaterializer
from contextseek.plugs.core.gateway.state import resolve_plug_state_store
from contextseek.plugs.core.protocols import MaterializationReceipt, PlugChangeEvent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PlugGateway:
    """Apply standard plug change events into ContextSeek."""

    # Bounded in-process source locks keep memory usage stable. Hash collisions
    # only serialize unrelated external_ids inside the current process.
    _LOCK_STRIPES: tuple[threading.RLock, ...] = tuple(
        threading.RLock() for _ in range(1024)
    )

    def __init__(self, seek: ContextSeek, *, max_retry: int = 3) -> None:
        self._seek = seek
        self._store = resolve_plug_state_store(seek.adapter)
        self._materializer = PlugMaterializer(seek)
        self._max_retry = int(max_retry)

    def apply(
        self,
        event: PlugChangeEvent,
        *,
        max_retry: int | None = None,
    ) -> MaterializationReceipt:
        """Apply one event with durable outbox and source-record updates."""
        lock = self._source_lock(event)
        with lock:
            return self._apply_locked(event, max_retry=max_retry)

    def replay_dead(
        self,
        event_id: str,
        *,
        max_retry: int | None = None,
    ) -> MaterializationReceipt:
        """Explicitly requeue and replay one dead outbox event."""
        existing = self._store.plug_outbox_get(event_id)
        if existing is None:
            raise KeyError(f"plug event not found: {event_id}")
        if existing.get("status") != "dead":
            msg = f"plug event is not dead: {event_id}"
            raise ValueError(msg)
        if not self._store.plug_outbox_requeue_dead(event_id):
            msg = f"plug event could not be requeued from dead: {event_id}"
            raise ValueError(msg)
        row = self._store.plug_outbox_get(event_id)
        try:
            event = PlugChangeEvent.from_payload((row or {}).get("event_payload"))
        except Exception as exc:
            self._mark_failed_and_maybe_dead(
                event_id,
                f"invalid event payload: {exc}",
                max_retry=max_retry,
            )
            raise
        return self.apply(event, max_retry=max_retry)

    def _apply_locked(
        self,
        event: PlugChangeEvent,
        *,
        max_retry: int | None = None,
    ) -> MaterializationReceipt:
        """Apply one event while holding the in-process source lock.

        The storage writes are intentionally at-least-once: outbox, ContextItem,
        and source_record are not one cross-table transaction yet. Deterministic
        ContextItem ids and outbox replay make the flow recoverable after a
        crash, while the source lock reduces same-process races for one external
        record. Cross-process row locking remains a storage-level enhancement.
        """
        existing_outbox = self._store.plug_outbox_get(event.event_id)
        if existing_outbox and existing_outbox.get("status") == "applied":
            return MaterializationReceipt(
                event_id=event.event_id,
                materialization_key=event.materialization_key,
                context_item_id=existing_outbox.get("materialized_context_item_id"),
                operation=event.operation,
                status="applied",
            )
        if existing_outbox and existing_outbox.get("status") == "dead":
            msg = f"plug event is dead and requires explicit replay: {event.event_id}"
            raise ValueError(msg)

        event_payload = event.to_payload()
        outbox_written = self._store.plug_outbox_upsert(
            {
                "event_id": event.event_id,
                "plug_name": event.plug_name,
                "plug_instance_id": event.plug_instance_id,
                "external_id": event.external_id,
                "materialization_key": event.materialization_key,
                "event_payload": event_payload,
                "status": "pending",
                "retry_count": self._retry_count_for_upsert(
                    existing_outbox,
                    event_payload,
                ),
                "created_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
        )
        if not outbox_written:
            return self._receipt_from_terminal_outbox(event)

        try:
            old_record = self._store.plug_source_get(
                event.plug_name,
                event.plug_instance_id,
                event.external_id,
            )
            receipt = self._materializer.apply(event, old_record)
            self._store.plug_source_upsert(
                self._source_record_from_receipt(event, receipt, old_record)
            )
            self._store.plug_outbox_update_status(
                event.event_id,
                status="applied",
                materialized_context_item_id=receipt.context_item_id,
                last_error=None,
            )
            return receipt
        except Exception as exc:
            self._mark_failed_and_maybe_dead(
                event.event_id,
                str(exc),
                max_retry=max_retry,
            )
            raise

    def _mark_failed_and_maybe_dead(
        self,
        event_id: str,
        error: str,
        *,
        max_retry: int | None = None,
    ) -> None:
        self._store.plug_outbox_update_status(
            event_id,
            status="failed",
            last_error=error,
            increment_retry=True,
        )
        latest = self._store.plug_outbox_get(event_id)
        retry_count = int((latest or {}).get("retry_count") or 0)
        retry_limit = self._max_retry if max_retry is None else int(max_retry)
        if retry_count >= retry_limit:
            self._store.plug_outbox_update_status(
                event_id,
                status="dead",
                last_error=error,
            )

    def _receipt_from_terminal_outbox(
        self,
        event: PlugChangeEvent,
    ) -> MaterializationReceipt:
        latest = self._store.plug_outbox_get(event.event_id)
        if latest and latest.get("status") == "applied":
            return MaterializationReceipt(
                event_id=event.event_id,
                materialization_key=event.materialization_key,
                context_item_id=latest.get("materialized_context_item_id"),
                operation=event.operation,
                status="applied",
            )
        if latest and latest.get("status") == "dead":
            msg = f"plug event is dead and requires explicit replay: {event.event_id}"
            raise ValueError(msg)
        msg = f"plug outbox upsert was ignored for non-terminal event: {event.event_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _retry_count_for_upsert(
        existing_outbox: dict[str, Any] | None,
        event_payload: dict[str, Any],
    ) -> int:
        if not existing_outbox:
            return 0
        if existing_outbox.get("status") not in {"pending", "failed"}:
            return 0
        old_payload = existing_outbox.get("event_payload") or {}
        if _stable_digest(old_payload) == _stable_digest(event_payload):
            return int(existing_outbox.get("retry_count") or 0)
        return 0

    @classmethod
    def _source_lock(cls, event: PlugChangeEvent) -> threading.RLock:
        raw = "\0".join((event.plug_name, event.plug_instance_id, event.external_id))
        idx = int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16) % len(
            cls._LOCK_STRIPES
        )
        return cls._LOCK_STRIPES[idx]

    def _source_record_from_receipt(
        self,
        event: PlugChangeEvent,
        receipt: MaterializationReceipt,
        old_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        current_id = receipt.context_item_id
        if receipt.status == "skipped" and old_record is not None:
            current_id = old_record.get("current_context_item_id")
        # plug_source_records is the current pointer for one external_id. On
        # updates it stays active; superseded history lives on old ContextItems.
        status = "deleted" if event.operation == "delete" else "active"
        if receipt.status == "skipped" and old_record is not None:
            status = str(old_record.get("status") or status)
        return {
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "current_context_item_id": current_id,
            "content_version_hash": event.content_version_hash,
            "write_projection_hash": event.write_projection_hash,
            "last_materialization_key": event.materialization_key,
            "last_materialized_context_item_id": receipt.context_item_id,
            "status": status,
            "last_operation": event.operation,
            "last_seen_at": _utc_now_iso(),
            "last_event_id": event.event_id,
            "raw_payload_digest": _stable_digest(event.raw_payload),
        }
