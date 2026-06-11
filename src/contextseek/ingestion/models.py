"""Core models for ingestion layer v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso8601(ts: datetime | str) -> str:
    if isinstance(ts, str):
        return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_fingerprint(content: str, metadata: dict[str, Any] | None = None) -> str:
    payload = {"content": content, "metadata": metadata or {}}
    digest = hashlib.sha256(stable_json_dumps(payload).encode("utf-8"))
    return digest.hexdigest()


class ConnectorKind(StrEnum):
    codex = "codex"
    claude_code = "claude_code"
    wiki = "wiki"
    notes = "notes"
    url = "url"
    confluence = "confluence"
    notion = "notion"
    github = "github"


class ConnectorMode(StrEnum):
    synced = "synced"
    direct = "direct"
    hybrid = "hybrid"


class IngestionStatus(StrEnum):
    discovered = "discovered"
    queued = "queued"
    fetching = "fetching"
    normalizing = "normalizing"
    persisting = "persisting"
    checkpointing = "checkpointing"
    synced = "synced"
    failed = "failed"
    dead_letter = "dead_letter"


@dataclass(slots=True)
class RawEvent:
    event_id: str
    source_type: str
    source_id: str
    scope: str
    content: str
    updated_at: str
    fingerprint: str
    metadata: dict[str, Any] = field(default_factory=dict)
    acl_principals: list[str] | None = None
    title: str | None = None


@dataclass(slots=True)
class SyncCheckpoint:
    connector_id: str
    partition: str
    cursor: str = ""
    last_success_at: str | None = None
    last_event_count: int = 0
    status: str = IngestionStatus.queued.value
    retry_count: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class ConnectorConfig:
    connector_id: str
    kind: ConnectorKind
    mode: ConnectorMode
    enabled: bool = True
    owner: str = "system"
    config: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: to_iso8601(utc_now()))
    updated_at: str = field(default_factory=lambda: to_iso8601(utc_now()))


@dataclass(slots=True)
class PolicyDecision:
    decision: str
    policy_version: str
    reason: str = ""
    redacted: bool = False

