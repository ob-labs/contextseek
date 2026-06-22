"""DataPlug protocol — streaming ingestion adapters for ContextSeek.

A DataPlug is any source that can stream structured events into the
ContextSeek graph.  Implementations wrap specific data sources (git logs,
Slack channels, document crawlers, etc.) behind a uniform iterator
interface so that `ContextSeek.plug()` can consume them generically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol, runtime_checkable


PlugOperation = Literal["add", "update", "delete", "noop"]
MaterializationStatus = Literal["applied", "skipped", "failed"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


@dataclass
class PlugMeta:
    """Descriptor for a DataPlug — its identity and default source type."""

    name: str
    """Unique plug identifier (e.g. 'github_commits', 'slack_channel')."""

    source_type: str
    """Maps to a SourceType enum value (e.g. 'document', 'trace_extraction')."""

    description: str = ""
    """Human-readable description of what this plug provides."""


@dataclass
class RawEvent:
    """A single event emitted by a DataPlug's stream.

    RawEvents are the normalised unit of ingestion. They carry content
    and minimal metadata; the ContextSeek client is responsible for
    constructing full ContextItems from them.
    """

    content: str | dict
    """The event payload — plain text or a structured dict."""

    source: str
    """Source identifier (e.g. commit SHA, message URL, file path)."""

    tags: list[str] | None = None
    """Optional tags to attach to the resulting ContextItem."""

    metadata: dict = field(default_factory=dict)
    """Extra key-value pairs the plug can supply (passed to provenance context)."""


@runtime_checkable
class DataPlug(Protocol):
    """Protocol for streaming data sources.

    Any object implementing `stream()` and `metadata()` can be registered
    via `ContextSeek.plug()`.

    Example::

        class GitCommitPlug:
            def __init__(self, repo_path: str):
                self._repo_path = repo_path

            def metadata(self) -> PlugMeta:
                return PlugMeta(
                    name="git_commits",
                    source_type="document",
                    description="Git commit messages",
                )

            def stream(self) -> Iterator[RawEvent]:
                # ... yield RawEvent for each commit
                ...
    """

    def stream(self) -> Iterator[RawEvent]:
        """Yield raw events from the underlying data source.

        Implementations should be lazy — events are consumed on demand by
        the ContextSeek client. If the source is unbounded, the plug should
        document its own stopping criteria.
        """
        ...

    def metadata(self) -> PlugMeta:
        """Return plug metadata (name, source_type, description)."""
        ...


@dataclass
class PlugChangeEvent:
    """Standard write-change event emitted by a Proxy DataPlug."""

    plug_name: str
    plug_instance_id: str
    external_id: str
    operation: PlugOperation
    content: str | dict[str, Any] | None
    scope: str
    source_type: str = "external_api"
    event_id: str = ""
    materialization_key: str = ""
    content_version_hash: str = ""
    write_projection_hash: str = ""
    tenant_id: str | None = None
    subject_id: str | None = None
    stage_hint: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 1.0
    raw_payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = _sha256(
                {
                    "plug_name": self.plug_name,
                    "plug_instance_id": self.plug_instance_id,
                    "external_id": self.external_id,
                    "operation": self.operation,
                    "occurred_at": self.occurred_at.isoformat(),
                    "raw_payload": self.raw_payload,
                }
            )
        if not self.content_version_hash:
            self.content_version_hash = _sha256(self.content)
        if not self.write_projection_hash:
            self.write_projection_hash = _sha256(
                {
                    "content": self.content,
                    "tags": self.tags,
                    "source_type": self.source_type,
                    "tenant_id": self.tenant_id,
                    "subject_id": self.subject_id,
                    "metadata": self.metadata,
                    "stage_hint": self.stage_hint,
                    "importance": self.importance,
                }
            )
        if not self.materialization_key:
            self.materialization_key = _sha256(
                {
                    "plug_name": self.plug_name,
                    "plug_instance_id": self.plug_instance_id,
                    "external_id": self.external_id,
                    "write_projection_hash": self.write_projection_hash,
                }
            )

    def to_payload(self) -> dict[str, Any]:
        """Serialize the event to a JSON-friendly payload."""
        return {
            "plug_name": self.plug_name,
            "plug_instance_id": self.plug_instance_id,
            "external_id": self.external_id,
            "event_id": self.event_id,
            "materialization_key": self.materialization_key,
            "operation": self.operation,
            "content": self.content,
            "content_version_hash": self.content_version_hash,
            "write_projection_hash": self.write_projection_hash,
            "scope": self.scope,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "source_type": self.source_type,
            "stage_hint": self.stage_hint,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "importance": self.importance,
            "raw_payload": dict(self.raw_payload),
            "occurred_at": self.occurred_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PlugChangeEvent":
        data = dict(payload)
        required = {
            "plug_name",
            "plug_instance_id",
            "external_id",
            "operation",
            "content",
            "scope",
        }
        missing = sorted(key for key in required if key not in data)
        if missing:
            msg = f"invalid PlugChangeEvent payload, missing: {', '.join(missing)}"
            raise ValueError(msg)
        if data["operation"] not in {"add", "update", "delete", "noop"}:
            msg = f"invalid PlugChangeEvent operation: {data['operation']}"
            raise ValueError(msg)
        occurred_at = data.get("occurred_at")
        if isinstance(occurred_at, str):
            parsed = datetime.fromisoformat(occurred_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            data["occurred_at"] = parsed
        return cls(**data)


@dataclass
class MaterializationReceipt:
    """Result of applying one PlugChangeEvent."""

    event_id: str
    materialization_key: str
    context_item_id: str | None
    operation: PlugOperation
    status: MaterializationStatus
    message: str = ""


@dataclass
class PlugProxyRequest:
    """Generic request passed from PlugGateway proxy endpoints to a DataPlug."""

    method: str
    path: str
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlugProxyResponse:
    """Generic response returned by a Proxy DataPlug."""

    body: Any = None
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class PlugProxyResult:
    """Write proxy result: external response plus standard change events."""

    response: PlugProxyResponse
    events: list[PlugChangeEvent] = field(default_factory=list)


@dataclass
class InstallResult:
    """Result of a plug installation or dry-run."""

    changed: bool
    dry_run: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class ProxyDataPlug(Protocol):
    """Protocol for online proxy plugs that capture external writes."""

    def metadata(self) -> PlugMeta:
        """Return plug metadata."""
        ...

    def handle_write(self, request: PlugProxyRequest) -> PlugProxyResult:
        """Proxy a write call and return standard change events."""
        ...

    def handle_search(self, request: PlugProxyRequest) -> PlugProxyResponse:
        """Proxy a read/search call without forcing ContextSeek materialization."""
        ...

    def install(
        self,
        *,
        linker: str | None = None,
        dry_run: bool = False,
        check: bool = False,
    ) -> InstallResult:
        """Install or describe how to install this proxy plug."""
        ...

    def snapshot(self) -> DataPlug | None:
        """Optionally expose a snapshot DataPlug for bootstrap import."""
        ...
