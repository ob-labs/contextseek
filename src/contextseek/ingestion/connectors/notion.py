"""Notion connector."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint
from contextseek.ingestion.scheduler import RetryableError


def _cursor_value(cursor: str) -> str:
    if cursor.startswith("cursor:"):
        return cursor.split(":", 1)[1]
    return ""


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


def _extract_title(row: dict[str, Any]) -> str:
    props = row.get("properties")
    if not isinstance(props, dict):
        return str(row.get("id", "untitled"))
    for prop in props.values():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") == "title":
            title_chunks = prop.get("title") or []
            texts = [str(chunk.get("plain_text", "")) for chunk in title_chunks if chunk]
            title = "".join(texts).strip()
            if title:
                return title
    return str(row.get("id", "untitled"))


def _extract_content(row: dict[str, Any]) -> str:
    parts: list[str] = []
    props = row.get("properties")
    if not isinstance(props, dict):
        return ""
    for prop in props.values():
        if not isinstance(prop, dict):
            continue
        ptype = prop.get("type")
        if ptype == "rich_text":
            chunks = prop.get("rich_text") or []
            parts.extend(str(chunk.get("plain_text", "")) for chunk in chunks if chunk)
        elif ptype == "title":
            chunks = prop.get("title") or []
            parts.extend(str(chunk.get("plain_text", "")) for chunk in chunks if chunk)
    return "\n".join(part for part in parts if part.strip()).strip()


class NotionConnector(BaseConnector):
    """Incremental search-based pull from Notion API."""

    def discover(self) -> list[str]:
        spaces = self.config.config.get("workspaces")
        if isinstance(spaces, list) and spaces:
            return [str(space) for space in spaces]
        return ["workspace:default"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        token = str(self.config.config.get("token", ""))
        notion_version = str(self.config.config.get("notion_version", "2022-06-28"))
        if not token:
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        page_size = int(self.config.config.get("page_size", 50))
        start_cursor = _cursor_value(checkpoint.cursor if checkpoint else "")
        payloads: list[dict[str, Any]] = []

        body: dict[str, Any] = {
            "page_size": page_size,
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "filter": {"property": "object", "value": "page"},
        }
        if start_cursor:
            body["start_cursor"] = start_cursor
        req = request.Request(
            "https://api.notion.com/v1/search",
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": notion_version,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504}:
                raise RetryableError(f"notion temporary error: {exc.code}") from exc
            raise
        except URLError as exc:
            raise RetryableError(f"notion network error: {exc}") from exc

        for row in data.get("results", []):
            if row.get("object") != "page":
                continue
            last_edited = str(row.get("last_edited_time", ""))
            content = _extract_content(row)
            if not content:
                continue
            payloads.append(
                {
                    "source_id": f"notion:{row.get('id')}",
                    "title": _extract_title(row),
                    "content": content,
                    "updated_at": _iso_to_epoch(last_edited),
                    "acl_principals": list(row.get("acl_principals", [])),
                    "metadata": {
                        "raw_type": "page",
                        "workspace": partition,
                        "notion_id": row.get("id"),
                        "url": row.get("url"),
                        "last_edited_time": last_edited,
                        "connector_kind": self.config.kind.value,
                    },
                }
            )

        next_cursor = (
            f"cursor:{data.get('next_cursor')}"
            if data.get("has_more") and data.get("next_cursor")
            else ""
        )
        return PullResult(
            payloads=payloads,
            next_cursor=next_cursor,
            has_more=bool(data.get("has_more", False)),
        )

