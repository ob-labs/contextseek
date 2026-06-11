"""Idempotent ingestion writer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import SourceType
from contextseek.ingestion.models import RawEvent


@dataclass(slots=True)
class WriteResult:
    status: str
    item_id: str | None = None
    reason: str = ""


class IngestionWriter:
    """Write RawEvent to ContextSeek with ingestion-level dedupe semantics."""

    def __init__(self, ctx: ContextSeek) -> None:
        self._ctx = ctx
        self._event_index: set[str] = set()
        self._fingerprint_index: set[tuple[str, str]] = set()
        self._latest_by_source: dict[tuple[str, str], str] = {}

    def write(self, event: RawEvent) -> WriteResult:
        # Primary idempotency key: event_id
        if event.event_id in self._event_index:
            return WriteResult(status="skipped", reason="duplicate_event_id")

        # Secondary idempotency key: (source_id, fingerprint)
        secondary_key = (event.source_id, event.fingerprint)
        if secondary_key in self._fingerprint_index:
            self._event_index.add(event.event_id)
            return WriteResult(status="skipped", reason="duplicate_fingerprint")

        links: list[Link] = []
        prev_key = (event.scope, event.source_id)
        previous_item_id = self._latest_by_source.get(prev_key)
        if previous_item_id is not None:
            links.append(Link(target_id=previous_item_id, relation=LinkType.supersedes))

        metadata = dict(event.metadata)
        metadata["ingested_at"] = datetime.now(timezone.utc).isoformat()
        metadata["source_id"] = event.source_id
        metadata["event_id"] = event.event_id
        metadata["source_type"] = event.source_type
        metadata["fingerprint"] = event.fingerprint
        if event.acl_principals:
            metadata["acl"] = {"read_subjects": list(event.acl_principals)}

        item = self._ctx.add(
            content=event.content,
            scope=event.scope,
            source=event.source_id,
            source_type=SourceType.document,
            tags=[f"from:{event.metadata.get('connector_kind', 'unknown')}"],
            links=links,
            check_conflicts=False,
        )
        ref = self._ctx.resolver.ref_for(event.scope, item.id)
        payload = self._ctx.adapter.read(ref)
        if payload is None:
            return WriteResult(status="failed", reason="write_missing")
        source_meta = dict(payload.get("source_meta", {}))
        source_meta.update(metadata)
        payload["source_meta"] = source_meta
        self._ctx.adapter.write(ref, payload)

        self._event_index.add(event.event_id)
        self._fingerprint_index.add(secondary_key)
        self._latest_by_source[prev_key] = item.id
        return WriteResult(status="written", item_id=item.id)

