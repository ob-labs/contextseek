"""Confluence connector."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint
from contextseek.ingestion.scheduler import RetryableError


def _iso_to_epoch(value: str) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _cursor_to_epoch(cursor: str) -> float:
    if cursor.startswith("updated_at:"):
        try:
            return float(cursor.split(":", 1)[1])
        except ValueError:
            return 0.0
    return 0.0


class ConfluenceConnector(BaseConnector):
    """Incremental pull from Confluence REST API."""

    def discover(self) -> list[str]:
        spaces = self.config.config.get("spaces")
        if isinstance(spaces, list) and spaces:
            return [str(space) for space in spaces]
        default_space = self.config.config.get("space")
        return [str(default_space)] if default_space else ["global"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        base_url = str(self.config.config.get("base_url", "")).rstrip("/")
        token = str(self.config.config.get("token", ""))
        if not base_url or not token:
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        limit = int(self.config.config.get("limit", 50))
        since_epoch = _cursor_to_epoch(checkpoint.cursor if checkpoint else "")
        start = int(self.config.config.get("start", 0))
        payloads: list[dict[str, Any]] = []
        max_updated = since_epoch
        has_more = True
        while has_more:
            params = {
                "limit": str(limit),
                "start": str(start),
                "expand": "version,body.storage,space",
            }
            if partition != "global":
                params["spaceKey"] = partition
            query = parse.urlencode(params)
            url = f"{base_url}/rest/api/content?{query}"
            req = request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            try:
                with request.urlopen(req, timeout=15) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504}:
                    raise RetryableError(f"confluence temporary error: {exc.code}") from exc
                raise
            except URLError as exc:
                raise RetryableError(f"confluence network error: {exc}") from exc

            results = data.get("results") or []
            for row in results:
                version = row.get("version") or {}
                updated_at = str(version.get("when", ""))
                updated_epoch = _iso_to_epoch(updated_at)
                if updated_epoch <= since_epoch:
                    continue
                max_updated = max(max_updated, updated_epoch)
                content = (
                    ((row.get("body") or {}).get("storage") or {}).get("value", "") or ""
                )
                if not content.strip():
                    continue
                page_id = row.get("id")
                space = (row.get("space") or {}).get("key") or partition
                payloads.append(
                    {
                        "source_id": f"confluence:{space}:{page_id}",
                        "title": str(row.get("title", "")),
                        "content": str(content),
                        "updated_at": updated_epoch,
                        "acl_principals": list(row.get("acl_principals", [])),
                        "metadata": {
                            "raw_type": "page",
                            "space": space,
                            "page_id": page_id,
                            "version": version.get("number"),
                            "connector_kind": self.config.kind.value,
                        },
                    }
                )

            size = len(results)
            has_more = size >= limit
            start += size
            if size == 0:
                break
        next_cursor = f"updated_at:{max_updated:.6f}"
        return PullResult(payloads=payloads, next_cursor=next_cursor, has_more=False)

