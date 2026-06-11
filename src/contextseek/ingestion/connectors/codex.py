"""Codex transcript connector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint


def _offset_cursor(cursor: str) -> int:
    if cursor.startswith("offset:"):
        try:
            return int(cursor.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


class CodexConnector(BaseConnector):
    """Read transcript JSONL incrementally using byte offsets."""

    def discover(self) -> list[str]:
        sessions = self.config.config.get("sessions")
        if isinstance(sessions, list) and sessions:
            return [str(s) for s in sessions]
        session = self.config.config.get("session")
        return [str(session)] if session else ["default"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        transcript = Path(str(self.config.config.get("transcript_path", ""))).expanduser()
        if not transcript.exists():
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        offset = _offset_cursor(checkpoint.cursor if checkpoint else "")
        payloads: list[dict[str, Any]] = []
        with transcript.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = str(row.get("session_id", partition))
                if session_id != partition:
                    continue
                payloads.append(
                    {
                        "source_id": f"codex:{session_id}:{row.get('turn_id', 'unknown')}",
                        "title": str(row.get("title", "Codex turn")),
                        "content": str(row.get("content", "")),
                        "updated_at": float(row.get("updated_at", 0.0)),
                        "acl_principals": list(row.get("acl_principals", [])),
                        "metadata": {
                            "raw_type": "chat_turn",
                            "session_id": session_id,
                            "turn_id": row.get("turn_id"),
                            "byte_offset": fh.tell(),
                            "connector_kind": self.config.kind.value,
                        },
                    }
                )
            next_cursor = f"offset:{fh.tell()}"
        return PullResult(payloads=payloads, next_cursor=next_cursor, has_more=False)

