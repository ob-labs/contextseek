"""Connector abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from contextseek.ingestion.models import ConnectorConfig, SyncCheckpoint


@dataclass(slots=True)
class PullResult:
    payloads: list[dict[str, Any]]
    next_cursor: str = ""
    has_more: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    config: ConnectorConfig

    def discover(self) -> list[str]:
        """Return partitions for this connector."""

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        """Pull payloads incrementally for one partition."""


class BaseConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def discover(self) -> list[str]:
        return ["default"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")


def cursor_as_epoch(cursor: str) -> float:
    if not cursor:
        return 0.0
    if cursor.startswith("mtime:"):
        try:
            return float(cursor.split(":", 1)[1])
        except ValueError:
            return 0.0
    return 0.0

