"""Factory helpers for built-in connectors/normalizers."""

from __future__ import annotations

from contextseek.ingestion.connectors import (
    ClaudeCodeConnector,
    ConfluenceConnector,
    CodexConnector,
    GitHubConnector,
    NotesConnector,
    NotionConnector,
    SourceConnector,
    UrlConnector,
    WikiConnector,
)
from contextseek.ingestion.models import ConnectorConfig, ConnectorKind
from contextseek.ingestion.normalizers import (
    ClaudeCodeNormalizer,
    ConfluenceNormalizer,
    CodexNormalizer,
    EventNormalizer,
    GitHubNormalizer,
    NotesNormalizer,
    NotionNormalizer,
    UrlNormalizer,
    WikiNormalizer,
)


def build_connector(config: ConnectorConfig) -> SourceConnector:
    if config.kind == ConnectorKind.codex:
        return CodexConnector(config)
    if config.kind == ConnectorKind.claude_code:
        return ClaudeCodeConnector(config)
    if config.kind == ConnectorKind.wiki:
        return WikiConnector(config)
    if config.kind == ConnectorKind.notes:
        return NotesConnector(config)
    if config.kind == ConnectorKind.url:
        return UrlConnector(config)
    if config.kind == ConnectorKind.confluence:
        return ConfluenceConnector(config)
    if config.kind == ConnectorKind.notion:
        return NotionConnector(config)
    if config.kind == ConnectorKind.github:
        return GitHubConnector(config)
    msg = f"unsupported connector kind: {config.kind}"
    raise ValueError(msg)


def build_normalizer(config: ConnectorConfig) -> EventNormalizer:
    if config.kind == ConnectorKind.codex:
        return CodexNormalizer()
    if config.kind == ConnectorKind.claude_code:
        return ClaudeCodeNormalizer()
    if config.kind == ConnectorKind.wiki:
        return WikiNormalizer()
    if config.kind == ConnectorKind.notes:
        return NotesNormalizer()
    if config.kind == ConnectorKind.url:
        return UrlNormalizer()
    if config.kind == ConnectorKind.confluence:
        return ConfluenceNormalizer()
    if config.kind == ConnectorKind.notion:
        return NotionNormalizer()
    if config.kind == ConnectorKind.github:
        return GitHubNormalizer()
    msg = f"unsupported connector kind: {config.kind}"
    raise ValueError(msg)

