"""PowerMem DataPlug.

PowerMem is a memory system that stores structured memories with embeddings.
This plug streams PowerMem entries as RawEvents, preserving their metadata
for ContextSeek's evolution pipeline to process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from contextseek.plugs.core.protocols import PlugMeta, RawEvent


@dataclass
class PowerMemPlug:
    """DataPlug that streams entries from a PowerMem-compatible memory store.

    Accepts a list of memory entries (dicts with at least a "content" key)
    or a callable that returns such entries.

    Each entry may include:
    - content (str | dict): the memory content
    - tags (list[str]): optional tags
    - metadata (dict): optional extra metadata
    - source (str): optional source identifier
    - embedding (list[float]): optional pre-computed embedding

    Example::

        from contextseek.plugs import PowerMemPlug

        plug = PowerMemPlug.from_memory(memory, user_id="u123", agent_id="bot")
        ctx.plug(plug, scope="tenant/bot/u123")
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    source_name: str = "powermem"
    description: str = "PowerMem memory import"

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]],
        *,
        source_prefix: str = "powermem",
        **kwargs: Any,
    ) -> PowerMemPlug:
        """Build a plug from PowerMem ``get_all`` / ``search`` result dicts.

        Accepts rows where text lives in ``content`` (get_all) or ``memory``
        (search). Preserves ``metadata``, optional ``importance``, and builds a
        stable ``source`` id from the memory primary key when present.
        """
        entries: list[dict[str, Any]] = []
        for rec in records:
            content = rec.get("content") or rec.get("memory") or ""
            if not content:
                continue

            meta = dict(rec.get("metadata") or {})
            if "score" in rec:
                meta.setdefault("powermem_score", rec["score"])
            for key in ("user_id", "agent_id", "run_id", "created_at", "updated_at"):
                if key in rec and rec[key] is not None:
                    meta.setdefault(key, rec[key])

            mem_id = rec.get("id")
            source = (
                f"{source_prefix}://{mem_id}" if mem_id is not None else source_prefix
            )

            tags = list(meta.pop("tags", []) or [])
            entry: dict[str, Any] = {
                "content": content,
                "source": source,
                "metadata": meta,
                "tags": tags,
            }
            if "importance" in rec:
                entry["importance"] = rec["importance"]
            elif "importance" in meta:
                entry["importance"] = meta["importance"]
            entries.append(entry)

        return cls(entries=entries, **kwargs)

    @classmethod
    def from_memory(
        cls,
        memory: Any,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
        **kwargs: Any,
    ) -> PowerMemPlug:
        """Build a plug from a PowerMem-style object with ``get_all(...)``.

        Duck-typed; no dependency on the ``powermem`` package at import time.
        Pass the result to ``ContextSeek.plug(plug, scope=...)``.
        """
        payload = memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )
        return cls.from_records(list(payload.get("results", [])), **kwargs)

    def stream(self) -> Iterator[RawEvent]:
        """Yield each memory entry as a RawEvent."""
        for entry in self.entries:
            content = entry.get("content", "")
            if not content:
                continue

            tags = list(entry.get("tags", []))
            tags = ["powermem"] + tags

            metadata: dict[str, Any] = {}
            if "embedding" in entry:
                metadata["embedding"] = entry["embedding"]
            if "metadata" in entry and isinstance(entry["metadata"], dict):
                metadata.update(entry["metadata"])
            if "importance" in entry:
                metadata["importance"] = entry["importance"]

            yield RawEvent(
                content=content,
                source=entry.get("source", self.source_name),
                tags=tags,
                metadata=metadata,
            )

    def metadata(self) -> PlugMeta:
        """Return plug metadata."""
        return PlugMeta(
            name=self.source_name,
            source_type="document",
            description=self.description,
        )
