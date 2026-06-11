"""Notes connector (filesystem incremental scan)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contextseek.ingestion.connectors.base import BaseConnector, PullResult, cursor_as_epoch
from contextseek.ingestion.models import SyncCheckpoint


class NotesConnector(BaseConnector):
    def discover(self) -> list[str]:
        roots = self.config.config.get("roots")
        if isinstance(roots, list) and roots:
            return [str(item) for item in roots]
        root = str(self.config.config.get("root", "."))
        return [root]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        root = Path(partition).expanduser()
        if not root.exists():
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")
        since_mtime = cursor_as_epoch(checkpoint.cursor if checkpoint else "")
        payloads: list[dict[str, Any]] = []
        max_mtime = since_mtime
        for fp in sorted(root.rglob("*")):
            if not fp.is_file() or fp.suffix.lower() not in {".md", ".txt"}:
                continue
            try:
                stat = fp.stat()
                mtime = float(stat.st_mtime)
                if mtime <= since_mtime:
                    continue
                content = fp.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not content:
                continue
            max_mtime = max(max_mtime, mtime)
            payloads.append(
                {
                    "source_id": str(fp),
                    "title": fp.name,
                    "content": content,
                    "updated_at": mtime,
                    "acl_principals": self.config.config.get("acl_principals", []),
                    "metadata": {
                        "raw_type": "markdown_file",
                        "file_path": str(fp),
                        "connector_kind": self.config.kind.value,
                    },
                }
            )
        next_cursor = f"mtime:{max_mtime:.6f}"
        return PullResult(payloads=payloads, next_cursor=next_cursor, has_more=False)

