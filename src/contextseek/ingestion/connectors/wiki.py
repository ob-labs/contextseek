"""Wiki connector (JSONL feed with reconciliation-friendly cursor)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint


def _cursor_to_ts(cursor: str) -> float:
    if cursor.startswith("updated_at:"):
        try:
            return float(cursor.split(":", 1)[1])
        except ValueError:
            return 0.0
    return 0.0


class WikiConnector(BaseConnector):
    """Consume wiki updates from a local JSONL export.

    Each line should be a JSON object with fields:
    - space (partition key)
    - page_id
    - title
    - content
    - updated_at (epoch seconds)
    - acl_principals (optional list)
    """

    def discover(self) -> list[str]:
        spaces = self.config.config.get("spaces")
        if isinstance(spaces, list) and spaces:
            return [str(item) for item in spaces]
        return [str(self.config.config.get("default_space", "space:default"))]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        feed_path = Path(str(self.config.config.get("feed_path", ""))).expanduser()
        if not feed_path.exists():
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        since = _cursor_to_ts(checkpoint.cursor if checkpoint else "")
        payloads: list[dict[str, Any]] = []
        max_ts = since
        for line in feed_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("space", "")) != partition:
                continue
            updated_at = float(row.get("updated_at", 0.0))
            if updated_at <= since:
                continue
            max_ts = max(max_ts, updated_at)
            payloads.append(
                {
                    "source_id": f"wiki:{row.get('space')}:{row.get('page_id')}",
                    "title": str(row.get("title", "")),
                    "content": str(row.get("content", "")),
                    "updated_at": updated_at,
                    "acl_principals": list(row.get("acl_principals", [])),
                    "metadata": {
                        "raw_type": "page",
                        "space": row.get("space"),
                        "page_id": row.get("page_id"),
                        "version": row.get("version"),
                        "connector_kind": self.config.kind.value,
                    },
                }
            )
        next_cursor = f"updated_at:{max_ts:.6f}"
        return PullResult(payloads=payloads, next_cursor=next_cursor, has_more=False)

