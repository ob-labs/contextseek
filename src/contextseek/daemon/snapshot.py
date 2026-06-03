"""Lightweight pre-evolution snapshots.

``compact()`` and ``dream()`` archive items irreversibly. Before a lifecycle
cycle mutates a scope, a snapshot of its durable items (``stage=knowledge`` and
``stage=skill`` only — raw/extracted are cheap to regenerate) is written to
``~/.contextseek/backups/`` so a mis-evolution can be recovered. The newest
``keep`` snapshots are retained; older ones are pruned automatically.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek

_SNAPSHOT_SUFFIX = ".snapshot"
_DURABLE_STAGES = ("knowledge", "skill")


def write_snapshot(
    client: "ContextSeek",
    scopes: list[str],
    snapshot_dir: str | pathlib.Path,
    *,
    keep: int = 7,
) -> pathlib.Path | None:
    """Write a snapshot of durable items across *scopes* and prune old ones.

    Returns the snapshot path, or ``None`` when there was nothing to snapshot.
    """
    from contextseek.domain.serialization import serialize_context_item

    directory = pathlib.Path(snapshot_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for scope in scopes:
        try:
            items = client.items(scope=scope)
        except Exception:
            continue
        for item in items:
            if item.is_deleted:
                continue
            if item.stage.value not in _DURABLE_STAGES:
                continue
            records.append(serialize_context_item(item))

    if not records:
        return None

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    path = directory / f"{ts}{_SNAPSHOT_SUFFIX}"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    _prune(directory, keep=keep)
    return path


def _prune(directory: pathlib.Path, *, keep: int) -> None:
    snapshots = sorted(
        directory.glob(f"*{_SNAPSHOT_SUFFIX}"),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in snapshots[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


__all__ = ["write_snapshot"]
