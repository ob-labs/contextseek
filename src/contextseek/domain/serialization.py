"""Serialization — convert ContextItem to/from dict for storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contextseek.domain.context_item import ContextItem
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance
from contextseek.domain.stages import Stability, Stage


def serialize_context_item(item: ContextItem) -> dict[str, Any]:
    """Serialize a ContextItem to a flat dict suitable for JSON storage."""
    payload: dict[str, Any] = {
        "id": item.id,
        "scope": item.scope,
        "content": item.content,
        "stage": item.stage.value,
        "stability": item.stability.value if item.stability else "transient",
        "hash": item.hash,
        "searchable": item.searchable,
        "relevance_boost": item.relevance_boost,
        "importance": item.importance,
        "access_count": item.access_count,
        "created_at": _dt_to_str(item.created_at),
        "provenance": _serialize_provenance(item.provenance),
        "tags": item.tags,
    }
    if item.abstract:
        payload["abstract"] = item.abstract
    if item.summary:
        payload["summary"] = item.summary
    if item.embedding:
        payload["embedding"] = item.embedding
    if item.links:
        payload["links"] = [_serialize_link(lnk) for lnk in item.links]
    if item.updated_at:
        payload["updated_at"] = _dt_to_str(item.updated_at)
    if item.last_accessed_at:
        payload["last_accessed_at"] = _dt_to_str(item.last_accessed_at)
    if item.superseded_by:
        payload["superseded_by"] = item.superseded_by
    if item.effective_confidence is not None:
        payload["effective_confidence"] = item.effective_confidence
    if item.deleted_at:
        payload["deleted_at"] = _dt_to_str(item.deleted_at)
        payload["deleted_reason"] = item.deleted_reason
    return payload


def deserialize_context_item(payload: dict[str, Any]) -> ContextItem:
    """Reconstruct a ContextItem from a stored dict."""
    provenance = _deserialize_provenance(payload["provenance"])
    links = [_deserialize_link(lnk) for lnk in payload.get("links", [])]

    item = ContextItem(
        id=payload["id"],
        scope=payload["scope"],
        content=payload["content"],
        provenance=provenance,
        stage=Stage(payload.get("stage", "raw")),
        stability=Stability(payload.get("stability", "transient")),
        hash=payload.get("hash", ""),
        abstract=payload.get("abstract"),
        summary=payload.get("summary"),
        tags=payload.get("tags", []),
        embedding=payload.get("embedding"),
        searchable=payload.get("searchable", True),
        relevance_boost=payload.get("relevance_boost", 1.0),
        importance=payload.get("importance", 1.0),
        access_count=payload.get("access_count", 0),
        links=links,
        effective_confidence=payload.get("effective_confidence"),
        created_at=_str_to_dt(payload["created_at"]),
        updated_at=_str_to_dt(payload["updated_at"])
        if payload.get("updated_at")
        else None,
        last_accessed_at=_str_to_dt(payload["last_accessed_at"])
        if payload.get("last_accessed_at")
        else None,
        superseded_by=payload.get("superseded_by"),
        deleted_at=_str_to_dt(payload["deleted_at"])
        if payload.get("deleted_at")
        else None,
        deleted_reason=payload.get("deleted_reason"),
    )
    return item


def _serialize_provenance(p: Provenance) -> dict[str, Any]:
    d: dict[str, Any] = {
        "source_type": p.source_type,
        "source_id": p.source_id,
        "confidence": p.confidence,
        "verified": p.verified,
    }
    if p.created_by:
        d["created_by"] = p.created_by
    if p.context:
        d["context"] = p.context
    return d


def _deserialize_provenance(d: dict[str, Any]) -> Provenance:
    return Provenance(
        source_type=d["source_type"],
        source_id=d["source_id"],
        confidence=d.get("confidence", 1.0),
        verified=d.get("verified", False),
        created_by=d.get("created_by"),
        context=d.get("context"),
    )


def _serialize_link(lnk: Link) -> dict[str, Any]:
    return {
        "target_id": lnk.target_id,
        "relation": lnk.relation.value,
        "strength": lnk.strength,
        "created_at": _dt_to_str(lnk.created_at),
    }


def _deserialize_link(d: dict[str, Any]) -> Link:
    return Link(
        target_id=d["target_id"],
        relation=LinkType(d["relation"]),
        strength=d.get("strength", 1.0),
        created_at=_str_to_dt(d["created_at"]) if d.get("created_at") else None,
    )


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
