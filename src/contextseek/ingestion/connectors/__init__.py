"""Built-in ingestion connectors."""

from contextseek.ingestion.connectors.base import BaseConnector, PullResult, SourceConnector
from contextseek.ingestion.connectors.confluence import ConfluenceConnector
from contextseek.ingestion.connectors.claude_code import ClaudeCodeConnector
from contextseek.ingestion.connectors.codex import CodexConnector
from contextseek.ingestion.connectors.github import GitHubConnector
from contextseek.ingestion.connectors.notes import NotesConnector
from contextseek.ingestion.connectors.notion import NotionConnector
from contextseek.ingestion.connectors.url import UrlConnector
from contextseek.ingestion.connectors.wiki import WikiConnector

__all__ = [
    "BaseConnector",
    "PullResult",
    "SourceConnector",
    "CodexConnector",
    "ClaudeCodeConnector",
    "WikiConnector",
    "NotesConnector",
    "UrlConnector",
    "ConfluenceConnector",
    "NotionConnector",
    "GitHubConnector",
]

