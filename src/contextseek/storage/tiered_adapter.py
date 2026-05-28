"""Tiered storage adapter routing cold data to a separate backend."""

from __future__ import annotations

from typing import Any

from contextseek.storage.protocol import HashIndexMixin
from contextseek.storage.protocol import SeekVFSAdapter


class TieredSeekVFSAdapter(HashIndexMixin, SeekVFSAdapter):
    """Route cold payloads to a separate backend with hot stubs."""

    def __init__(
        self,
        hot: SeekVFSAdapter,
        cold: SeekVFSAdapter,
        *,
        cleanup_on_write: bool = True,
    ) -> None:
        self._hot = hot
        self._cold = cold
        self._cleanup_on_write = cleanup_on_write

    @property
    def hot(self) -> SeekVFSAdapter:
        return self._hot

    @property
    def cold(self) -> SeekVFSAdapter:
        return self._cold

    def write(self, ref: str, payload: dict[str, Any]) -> None:
        tier = str(payload.get("tier", "")).lower()
        if tier == "cold":
            self._cold.write(ref, payload)
            self._hot.write(ref, _cold_stub(payload))
            return
        self._hot.write(ref, payload)
        if self._cleanup_on_write:
            self._cold.delete(ref)

    def read(self, ref: str) -> dict[str, Any] | None:
        payload = self._hot.read(ref)
        if payload is not None:
            if payload.get("cold_stub"):
                cold_payload = self._cold.read(ref)
                if cold_payload is not None:
                    return cold_payload
            self._hot.delete(ref)
            return payload
        return self._cold.read(ref)

    def search(
        self,
        prefix: str,
        query: str,
        *,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        hot_hits = [
            item
            for item in self._hot.search(
                prefix, query, k=k, query_embedding=query_embedding
            )
            if not item.get("cold_stub")
        ]
        hot_refs = {str(item.get("ref", "")) for item in hot_hits}
        cold_hits = [
            item
            for item in self._cold.search(
                prefix, query, k=k, query_embedding=query_embedding
            )
            if str(item.get("ref", "")) not in hot_refs
        ]
        combined = hot_hits + cold_hits
        combined.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return combined[:k]

    def ls(self, prefix: str) -> list[str]:
        hot_refs = list(self._hot.ls(prefix))
        cold_refs = self._cold.ls(prefix)
        seen = set(hot_refs)
        for ref in cold_refs:
            if ref in seen:
                continue
            hot_refs.append(ref)
            seen.add(ref)
        return sorted(hot_refs)

    def delete(self, ref: str) -> bool:
        removed = self._hot.delete(ref)
        removed = self._cold.delete(ref) or removed
        return removed

    def find_by_hash(self, prefix: str, hash_value: str) -> str | None:
        for adapter in (self._hot, self._cold):
            fn = getattr(adapter, "find_by_hash", None)
            if fn is None:
                continue
            try:
                ref = fn(prefix, hash_value)
            except Exception:  # noqa: BLE001
                ref = None
            if ref:
                return ref
        return None

    def promote(self, ref: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.pop("cold_stub", None)
        self._hot.write(ref, payload)
        self._cold.delete(ref)

    def demote(self, ref: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["tier"] = "cold"
        self._cold.write(ref, payload)
        self._hot.write(ref, _cold_stub(payload))


def _cold_stub(payload: dict[str, Any]) -> dict[str, Any]:
    stub = dict(payload)
    stub["cold_stub"] = True
    stub["content"] = ""
    stub["source_meta"] = dict(payload.get("source_meta", {}))
    return stub


__all__ = ["TieredSeekVFSAdapter"]
