"""Normalizer abstractions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from contextseek.ingestion.models import RawEvent, compute_fingerprint


class EventNormalizer(Protocol):
    def normalize(self, payload: dict[str, Any], *, connector_id: str, partition: str) -> RawEvent: ...


def deterministic_event_id(payload: dict[str, Any], *, connector_id: str, partition: str) -> str:
    body = json.dumps(
        {
            "connector_id": connector_id,
            "partition": partition,
            "source_id": payload.get("source_id"),
            "updated_at": payload.get("updated_at"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def normalize_base(
    payload: dict[str, Any],
    *,
    connector_id: str,
    partition: str,
    source_type: str,
) -> RawEvent:
    content = str(payload.get("content", ""))
    metadata = dict(payload.get("metadata", {}))
    metadata["connector_id"] = connector_id
    metadata["partition"] = partition
    metadata.setdefault("raw_type", source_type)
    return RawEvent(
        event_id=str(
            payload.get("event_id")
            or deterministic_event_id(
                payload,
                connector_id=connector_id,
                partition=partition,
            )
        ),
        source_type=source_type,
        source_id=str(payload.get("source_id", "")),
        scope=str(payload.get("scope", metadata.get("scope", "default"))),
        title=str(payload["title"]) if payload.get("title") else None,
        content=content,
        updated_at=str(payload.get("updated_at", "")),
        fingerprint=str(payload.get("fingerprint") or compute_fingerprint(content, metadata)),
        acl_principals=list(payload.get("acl_principals", [])) or None,
        metadata=metadata,
    )

