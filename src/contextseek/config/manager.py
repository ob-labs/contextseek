"""Versioned, provenance-tracked configuration store for ContextSeek.

The store lives at a fixed path (``${CONTEXTSEEK_HOME:-.contextseek}/config/``)
and is deliberately independent of the VFS storage backend so there is no
bootstrap cycle (config decides the storage backend; the storage backend must
not be needed to read the config history).

History is append-only: rollback creates a *new* version whose payload equals
an old version's payload. No history file is ever deleted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _payload_hash(payload: dict) -> str:
    return (
        "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    )


def _set_path(nested: dict, dotted_key: str, value: Any) -> None:
    """Set a value at a dotted path inside a nested dict (e.g. ``llm.model``)."""
    parts = dotted_key.split(".")
    cur = nested
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _merge(native: dict, projected: dict) -> tuple[dict, dict]:
    """Merge ``projected`` (baseline) with ``native`` (overrides).

    Returns ``(effective, override_sources)`` where ``override_sources`` maps
    dotted leaf-key paths to ``"native"`` or ``"projected:agentseek"``. Dicts
    are always recursed into; a leaf is any non-dict value, so ``native``
    overrides individual leaf keys while preserving projected siblings.
    """
    effective: dict = {}
    sources: dict = {}

    def walk(base: dict, target: dict, source_label: str, prefix: str = "") -> None:
        for k, v in base.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                child = target.setdefault(k, {})
                walk(v, child, source_label, key)
            else:
                target[k] = v
                sources[key] = source_label

    walk(projected, effective, "projected:agentseek")
    walk(native, effective, "native")
    return effective, sources


@dataclass
class ConfigVersion:
    """One snapshot in the append-only config history."""

    version_id: str
    parent_version_id: str | None
    created_at: str
    origin: str
    author: str
    reason: str
    payload_hash: str
    source_ref: str | None = None
    payload: dict = field(default_factory=dict)
    diff: dict | None = None
    override_sources: dict = field(default_factory=dict)


class ConfigManager:
    """Authoritative versioned configuration store."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)
        self.history_dir = self.config_dir / "history"
        self.sources_dir = self.config_dir / "sources"
        self.manifest_path = self.config_dir / "manifest.jsonl"
        self.current_path = self.config_dir / "current.json"

    # ------------------------------------------------------------------ store
    def init_store(self) -> None:
        """Create the store layout if absent. Idempotent."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.touch(exist_ok=True)
        if not self.current_path.exists():
            self.current_path.write_text("{}", encoding="utf-8")

    # ----------------------------------------------------------------- read
    def current(self) -> ConfigVersion | None:
        """Return the latest committed version, or None if the store is empty."""
        hist = self.history()
        return hist[0] if hist else None

    def get_version(self, version_id: str) -> ConfigVersion:
        path = self.history_dir / f"{version_id}.json"
        if not path.exists():
            msg = f"unknown version: {version_id}"
            raise KeyError(msg)
        return self._load_version(path)

    def history(self, n: int | None = None) -> list[ConfigVersion]:
        """Return versions newest-first, optionally limited to ``n``."""
        if not self.manifest_path.exists():
            return []
        records = [
            json.loads(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = list(reversed(records))  # newest first
        if n is not None:
            records = records[:n]
        return [
            self._load_version(self.history_dir / f"{r['version_id']}.json")
            for r in records
        ]

    def _load_version(self, path: Path) -> ConfigVersion:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ConfigVersion(
            version_id=raw["version_id"],
            parent_version_id=raw.get("parent_version_id"),
            created_at=raw["created_at"],
            origin=raw["origin"],
            author=raw["author"],
            reason=raw["reason"],
            payload_hash=raw["payload_hash"],
            source_ref=raw.get("source_ref"),
            payload=raw.get("payload", {}),
            diff=raw.get("diff"),
            override_sources=raw.get("override_sources", {}),
        )

    # ---------------------------------------------------------------- write
    def set_native(
        self, key: str, value: Any, *, author: str, reason: str
    ) -> ConfigVersion:
        cur = self.current()
        native = dict(cur.payload.get("native", {})) if cur else {}
        _set_path(native, key, value)
        return self.commit(native=native, origin="manual", author=author, reason=reason)

    def set_native_many(
        self, updates: dict[str, Any], *, author: str, reason: str
    ) -> ConfigVersion:
        cur = self.current()
        native = dict(cur.payload.get("native", {})) if cur else {}
        for k, v in updates.items():
            _set_path(native, k, v)
        return self.commit(native=native, origin="manual", author=author, reason=reason)

    def commit(
        self,
        *,
        native: dict | None = None,
        projected: dict | None = None,
        origin: str,
        author: str,
        reason: str,
        source_ref: str | None = None,
    ) -> ConfigVersion:
        """Commit a new version. ``native``/``projected`` are full new layer states.

        If a layer is omitted, the current version's layer is carried forward.
        """
        cur = self.current()
        prev_native = dict(cur.payload.get("native", {})) if cur else {}
        prev_projected = dict(cur.payload.get("projected", {})) if cur else {}
        new_native = prev_native if native is None else native
        new_projected = prev_projected if projected is None else projected
        effective, sources = _merge(new_native, new_projected)
        payload = {
            "native": new_native,
            "projected": new_projected,
            "effective": effective,
        }
        version_id = self._next_version_id()
        diff = self._diff_payloads(
            cur.payload.get("effective", {}) if cur else {}, effective
        )
        version = ConfigVersion(
            version_id=version_id,
            parent_version_id=cur.version_id if cur else None,
            created_at=_utc_now_iso(),
            origin=origin,
            author=author,
            reason=reason,
            payload_hash=_payload_hash(payload),
            source_ref=source_ref,
            payload=payload,
            diff=diff,
            override_sources=sources,
        )
        self._write_version(version)
        return version

    def _next_version_id(self) -> str:
        count = 0
        if self.manifest_path.exists():
            count = sum(
                1
                for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        return f"v{count + 1:06d}"

    def _write_version(self, version: ConfigVersion) -> None:
        """Atomic write: tmp → rename → manifest append → current.json update."""
        self.init_store()
        path = self.history_dir / f"{version.version_id}.json"
        tmp = path.with_suffix(".json.tmp")
        body = {
            "version_id": version.version_id,
            "parent_version_id": version.parent_version_id,
            "created_at": version.created_at,
            "origin": version.origin,
            "author": version.author,
            "reason": version.reason,
            "payload_hash": version.payload_hash,
            "source_ref": version.source_ref,
            "payload": version.payload,
            "diff": version.diff,
            "override_sources": version.override_sources,
        }
        tmp.write_text(_canonical_json(body), encoding="utf-8")
        tmp.replace(path)

        manifest_record = {
            "version_id": version.version_id,
            "parent_version_id": version.parent_version_id,
            "created_at": version.created_at,
            "origin": version.origin,
            "author": version.author,
            "reason": version.reason,
            "payload_hash": version.payload_hash,
            "source_ref": version.source_ref,
        }
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")

        self.current_path.write_text(_canonical_json(version.payload), encoding="utf-8")

    # ----------------------------------------------------------------- diff
    def _diff_payloads(self, a: dict, b: dict) -> dict:
        """Compare two effective payloads, return {added, changed, removed}."""
        added, changed, removed = [], [], []

        def flat(d: dict, pre: str = "") -> dict[str, Any]:
            out: dict[str, Any] = {}
            for k, v in d.items():
                key = f"{pre}.{k}" if pre else k
                if isinstance(v, dict):
                    out.update(flat(v, key))
                else:
                    out[key] = v
            return out

        fa, fb = flat(a), flat(b)
        for k in fa:
            if k not in fb:
                removed.append(k)
            elif fa[k] != fb[k]:
                changed.append(k)
        for k in fb:
            if k not in fa:
                added.append(k)
        return {
            "added": sorted(added),
            "changed": sorted(changed),
            "removed": sorted(removed),
        }
