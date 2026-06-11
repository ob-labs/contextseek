"""GitHub connector."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from contextseek.ingestion.connectors.base import BaseConnector, PullResult
from contextseek.ingestion.models import SyncCheckpoint
from contextseek.ingestion.scheduler import RetryableError


def _cursor_to_iso(cursor: str) -> str:
    if cursor.startswith("updated_at:"):
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


def _epoch_to_iso(epoch: float) -> str:
    if epoch <= 0:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class GitHubConnector(BaseConnector):
    """Incremental pull from GitHub issues API."""

    def discover(self) -> list[str]:
        repos = self.config.config.get("repos")
        if isinstance(repos, list) and repos:
            return [str(repo) for repo in repos]
        repo = self.config.config.get("repo")
        return [str(repo)] if repo else []

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        token = str(self.config.config.get("token", ""))
        if not partition:
            return PullResult(payloads=[], next_cursor=checkpoint.cursor if checkpoint else "")

        per_page = int(self.config.config.get("per_page", 100))
        page = int(self.config.config.get("page", 1))
        since_iso = _cursor_to_iso(checkpoint.cursor if checkpoint else "")
        payloads: list[dict[str, Any]] = []
        max_updated = _iso_to_epoch(since_iso)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        while True:
            params = {
                "state": "all",
                "per_page": str(per_page),
                "page": str(page),
            }
            if since_iso:
                params["since"] = since_iso
            query = parse.urlencode(params)
            url = f"https://api.github.com/repos/{partition}/issues?{query}"
            req = request.Request(url, headers=headers)
            try:
                with request.urlopen(req, timeout=15) as resp:  # noqa: S310
                    rows = json.loads(resp.read().decode("utf-8", errors="replace"))
            except HTTPError as exc:
                if exc.code in {403, 429, 500, 502, 503, 504}:
                    raise RetryableError(f"github temporary error: {exc.code}") from exc
                raise
            except URLError as exc:
                raise RetryableError(f"github network error: {exc}") from exc

            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                updated_at = str(row.get("updated_at", ""))
                updated_epoch = _iso_to_epoch(updated_at)
                if updated_epoch <= max_updated:
                    pass
                max_updated = max(max_updated, updated_epoch)
                body = str(row.get("body") or "")
                if not body.strip():
                    continue
                is_pr = "pull_request" in row
                source_type = "pull_request" if is_pr else "issue"
                payloads.append(
                    {
                        "source_id": f"github:{partition}:{source_type}:{row.get('number')}",
                        "title": str(row.get("title", "")),
                        "content": body,
                        "updated_at": updated_epoch,
                        "acl_principals": self.config.config.get("acl_principals", []),
                        "metadata": {
                            "raw_type": source_type,
                            "repo": partition,
                            "number": row.get("number"),
                            "url": row.get("html_url"),
                            "state": row.get("state"),
                            "connector_kind": self.config.kind.value,
                        },
                    }
                )

            if len(rows) < per_page:
                break
            page += 1

        next_cursor = (
            f"updated_at:{_epoch_to_iso(max_updated)}"
            if max_updated > 0
            else (checkpoint.cursor if checkpoint else "")
        )
        return PullResult(payloads=payloads, next_cursor=next_cursor, has_more=False)

