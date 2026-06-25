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
import os
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


def _flat_leaf_keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _flat_leaf_keys(v, key)
        else:
            out.add(key)
    return out


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
    rollback_target_version_id: str | None = None
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
        self._repair_current_from_manifest()

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
            rollback_target_version_id=raw.get("rollback_target_version_id"),
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
        rollback_target_version_id: str | None = None,
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
            rollback_target_version_id=rollback_target_version_id,
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
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"cfg-{ts}-{count + 1:04d}"

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
            "rollback_target_version_id": version.rollback_target_version_id,
            "payload": version.payload,
            "diff": version.diff,
            "override_sources": version.override_sources,
        }
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(_canonical_json(body))
            fh.flush()
            os.fsync(fh.fileno())
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
            "rollback_target_version_id": version.rollback_target_version_id,
        }
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        with self.current_path.open("w", encoding="utf-8") as fh:
            fh.write(_canonical_json(version.payload))
            fh.flush()
            os.fsync(fh.fileno())

    # ----------------------------------------------------------------- diff
    def _diff_payloads(self, a: dict, b: dict) -> dict:
        """Compare two effective payloads, return {added, changed, removed}."""
        added, changed, removed = [], [], []
        added_values: dict[str, Any] = {}
        changed_values: dict[str, dict[str, Any]] = {}
        removed_values: dict[str, Any] = {}

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
                removed_values[k] = fa[k]
            elif fa[k] != fb[k]:
                changed.append(k)
                changed_values[k] = {"before": fa[k], "after": fb[k]}
        for k in fb:
            if k not in fa:
                added.append(k)
                added_values[k] = fb[k]
        return {
            "added": sorted(added),
            "changed": sorted(changed),
            "removed": sorted(removed),
            "added_values": {k: added_values[k] for k in sorted(added_values)},
            "changed_values": {k: changed_values[k] for k in sorted(changed_values)},
            "removed_values": {k: removed_values[k] for k in sorted(removed_values)},
        }

    # ------------------------------------------------------- rollback/redo
    def rollback(self, target_version_id: str, *, author: str, reason: str) -> ConfigVersion:
        """Create a new version whose payload equals ``target_version_id``'s.

        Append-only: the target and any versions after it remain in history.
        """
        target = self.get_version(target_version_id)
        return self.commit(
            native=dict(target.payload.get("native", {})),
            projected=dict(target.payload.get("projected", {})),
            origin="rollback",
            author=author,
            reason=reason,
            rollback_target_version_id=target_version_id,
        )

    def redo(self, *, author: str, reason: str) -> ConfigVersion | None:
        """Undo the most recent rollback by re-applying the version it reverted.

        Returns None if the latest version is not a rollback.
        """
        cur = self.current()
        if cur is None or cur.origin != "rollback":
            return None
        # The version immediately before the rollback is what was reverted.
        prev = self.get_version(cur.parent_version_id)
        return self.commit(
            native=dict(prev.payload.get("native", {})),
            projected=dict(prev.payload.get("projected", {})),
            origin="manual",
            author=author,
            reason=reason,
        )

    # --------------------------------------------------------------- diff
    def diff(self, a: str, b: str) -> dict:
        va = self.get_version(a)
        vb = self.get_version(b)
        return self._diff_payloads(
            va.payload.get("effective", {}), vb.payload.get("effective", {})
        )

    # -------------------------------------------------------------- blame
    def blame(self, key: str) -> dict | None:
        """Find the most recent version where ``key``'s effective value was set."""
        hist = self.history()  # newest first
        if not hist:
            return None
        current_val = self._flat_get(hist[0].payload.get("effective", {}), key)
        prev_val = None
        prev_eff = hist[1].payload.get("effective", {}) if len(hist) > 1 else {}
        prev_val = self._flat_get(prev_eff, key)
        if current_val != prev_val or len(hist) == 1:
            v = hist[0]
            return {
                "version_id": v.version_id,
                "origin": v.origin,
                "author": v.author,
                "reason": v.reason,
                "source_ref": v.source_ref,
                "value": current_val,
            }
        # walk backwards to the introducing version
        for i, v in enumerate(hist):
            val = self._flat_get(v.payload.get("effective", {}), key)
            older = hist[i + 1] if i + 1 < len(hist) else None
            older_val = (
                self._flat_get(older.payload.get("effective", {}), key) if older else None
            )
            if val != older_val:
                return {
                    "version_id": v.version_id,
                    "origin": v.origin,
                    "author": v.author,
                    "reason": v.reason,
                    "source_ref": v.source_ref,
                    "value": val,
                }
        return None

    @staticmethod
    def _flat_get(d: dict, dotted_key: str) -> Any:
        cur: Any = d
        for part in dotted_key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    # ------------------------------------------------------------- verify
    def verify(self) -> list[str]:
        """Return a list of problems with the store (empty == OK)."""
        problems: list[str] = []
        if not self.manifest_path.exists():
            return problems
        records = [
            json.loads(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected_parent: str | None = None
        for rec in records:
            path = self.history_dir / f"{rec['version_id']}.json"
            if not path.exists():
                problems.append(f"missing version file: {rec['version_id']}")
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            actual_hash = _payload_hash(raw["payload"])
            if actual_hash != rec["payload_hash"]:
                problems.append(
                    f"payload hash mismatch in {rec['version_id']} "
                    f"(manifest={rec['payload_hash']}, file={actual_hash})"
                )
            if rec["parent_version_id"] != expected_parent:
                problems.append(
                    f"parent chain broken at {rec['version_id']}: "
                    f"expected parent {expected_parent}, got {rec['parent_version_id']}"
                )
            expected_parent = rec["version_id"]
        # current.json must match newest version's payload hash
        if records and self.current_path.exists():
            cur_payload = json.loads(self.current_path.read_text(encoding="utf-8"))
            newest = self._load_version(
                self.history_dir / f"{records[-1]['version_id']}.json"
            )
            if _payload_hash(cur_payload) != newest.payload_hash:
                problems.append("current.json does not match newest version payload")
        return problems

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        cur = self.current()
        count = 0
        if self.manifest_path.exists():
            count = sum(
                1 for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        agentseek_ref: str | None = None
        for v in self.history():
            if v.origin == "agentseek-projection":
                agentseek_ref = v.source_ref
                break
        override_conflicts: list[str] = []
        if cur is not None:
            native_keys = _flat_leaf_keys(cur.payload.get("native", {}))
            projected_keys = _flat_leaf_keys(cur.payload.get("projected", {}))
            override_conflicts = sorted(native_keys & projected_keys)
        return {
            "current_version": cur.version_id if cur else None,
            "version_count": count,
            "store_dir": str(self.config_dir),
            "agentseek_source_ref": agentseek_ref,
            "agentseek_stale": agentseek_ref is None,
            "override_conflicts": override_conflicts,
        }

    def _repair_current_from_manifest(self) -> None:
        """Recover ``current.json`` from manifest tail after interrupted writes."""
        if not self.manifest_path.exists():
            return
        records = [
            json.loads(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not records:
            return
        newest = records[-1]
        newest_path = self.history_dir / f"{newest['version_id']}.json"
        if not newest_path.exists():
            return
        newest_payload = json.loads(newest_path.read_text(encoding="utf-8")).get("payload", {})
        if not self.current_path.exists():
            self.current_path.write_text(_canonical_json(newest_payload), encoding="utf-8")
            return
        try:
            current_payload = json.loads(self.current_path.read_text(encoding="utf-8"))
        except Exception:
            current_payload = {}
        if _payload_hash(current_payload) != newest.get("payload_hash"):
            self.current_path.write_text(_canonical_json(newest_payload), encoding="utf-8")

    # --------------------------------------------------------------- apply
    def apply(self, materializer) -> None:  # type: ignore[no-untyped-def]
        """Materialize the current effective config via ``materializer``.

        ``materializer.materialize`` already dry-run-validates and raises
        ``ValueError`` on invalid config without writing files, so a failed
        apply leaves the previously materialized files intact.
        """
        cur = self.current()
        if cur is None:
            msg = "no current config version to apply"
            raise ValueError(msg)
        materializer.materialize(cur.payload.get("effective", {}))
