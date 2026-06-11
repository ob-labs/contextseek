"""URL connector (manual pull + conditional refresh)."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib import request

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint


class _SimpleHTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._inside_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._inside_title and not self.title:
            self.title = text
        self.parts.append(text)


class UrlConnector(BaseConnector):
    def discover(self) -> list[str]:
        urls = self.config.config.get("urls", [])
        if isinstance(urls, list) and urls:
            return [str(u) for u in urls]
        url = self.config.config.get("url")
        return [str(url)] if url else []

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        headers: dict[str, str] = {}
        if checkpoint and checkpoint.cursor:
            for piece in checkpoint.cursor.split("|"):
                if piece.startswith("etag:"):
                    headers["If-None-Match"] = piece.split(":", 1)[1]
                elif piece.startswith("last_modified:"):
                    headers["If-Modified-Since"] = piece.split(":", 1)[1]

        req = request.Request(partition, headers=headers)
        try:
            with request.urlopen(req, timeout=10) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="replace")
                etag = str(resp.headers.get("ETag", "")).strip()
                last_modified = str(resp.headers.get("Last-Modified", "")).strip()
        except Exception:
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        parser = _SimpleHTMLText()
        parser.feed(body)
        content = "\n".join(parser.parts).strip()
        if not content:
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        updated_at = datetime.now(timezone.utc).timestamp()
        cursor_parts = []
        if etag:
            cursor_parts.append(f"etag:{etag}")
        if last_modified:
            cursor_parts.append(f"last_modified:{last_modified}")
        next_cursor = "|".join(cursor_parts)
        payload: dict[str, Any] = {
            "source_id": partition,
            "title": parser.title or partition,
            "content": content,
            "updated_at": updated_at,
            "acl_principals": self.config.config.get("acl_principals", []),
            "metadata": {
                "raw_type": "url_doc",
                "canonical_url": partition,
                "etag": etag,
                "last_modified": last_modified,
                "connector_kind": self.config.kind.value,
            },
        }
        return PullResult(payloads=[payload], next_cursor=next_cursor, has_more=False)

