"""Adapter wrapping `seekvfs.VFS` for the `SeekVFSAdapter` protocol."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime
from typing import Any
from typing import TYPE_CHECKING

from contextseek.storage.protocol import GeoSearchMixin
from contextseek.storage.protocol import HashIndexMixin
from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.storage.protocol import VectorSearchMixin
from contextseek.domain.levels import ContentLevel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from seekvfs import VFS


_EXT_SCHEME = "contextseek://"


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


class SeekVFSStorageAdapter(
    HashIndexMixin, GeoSearchMixin, VectorSearchMixin, SeekVFSAdapter
):
    """Bridge `seekvfs.VFS` to contextseek's storage protocol.

    Translates contextseek's `contextseek://` refs to the VFS's own scheme
    (default `seekvfs://`) on the way in, and back on the way out.
    """

    def __init__(self, vfs: VFS, *, inner_scheme: str | None = None) -> None:
        from seekvfs import SCHEME

        self._vfs = vfs
        self._inner_scheme = inner_scheme or getattr(vfs, "_scheme", SCHEME)

    def _to_inner(self, ref: str) -> str:
        return ref.removeprefix(_EXT_SCHEME)

    def _to_outer(self, inner_path: str) -> str:
        return _EXT_SCHEME + inner_path.removeprefix(self._inner_scheme)

    def write(self, ref: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
            "utf-8"
        )
        self._vfs.write(self._to_inner(ref), data)

    def read(self, ref: str) -> dict[str, Any] | None:
        from seekvfs import NotFoundError

        try:
            fd = self._vfs.read(self._to_inner(ref))
        except NotFoundError:
            return None
        return json.loads(fd.content)

    def search(
        self,
        prefix: str,
        query: str,
        *,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        inner_prefix = self._to_inner(prefix)
        path_pattern = f"{self._inner_scheme}{inner_prefix}*"
        result = self._backend_search(
            query,
            path_pattern=path_pattern,
            limit=k,
            query_embedding=query_embedding,
        )
        if not result.hits:
            return []

        fetch_paths = [hit.path for hit in result.hits if not hit.snippet]
        batch = self._vfs.read_batch(fetch_paths) if fetch_paths else {}

        out: list[dict[str, Any]] = []
        for hit in result.hits:
            if hit.snippet:
                payload = json.loads(hit.snippet)
            else:
                fd = batch.get(hit.path)
                if fd is None:
                    continue
                payload = json.loads(fd.content)
            payload["ref"] = self._to_outer(hit.path)
            payload["score"] = hit.score
            out.append(payload)
        return out

    def _backend_search(
        self,
        query: str,
        *,
        path_pattern: str,
        limit: int,
        query_embedding: list[float] | None,
    ) -> Any:
        """Call backend.search, passing query_embedding when supported.

        The bundled ``seekvfs.VFS.search`` does not accept ``query_embedding``,
        so when one is provided we route directly to the backend resolved from
        the path pattern. When not provided, we fall through to the regular
        VFS fan-out so the cross-route reranker still applies.
        """
        if query_embedding is None:
            return self._vfs.search(query, path_pattern=path_pattern, limit=limit)

        backend = self._resolve_backend(path_pattern)
        if backend is None:
            return self._vfs.search(query, path_pattern=path_pattern, limit=limit)

        # Use inspect to see whether the backend accepts query_embedding; when it
        # does not, explicitly fall back to FTS with a warning instead of masking
        # unrelated TypeErrors.
        try:
            sig = inspect.signature(backend.search)
        except (TypeError, ValueError):
            sig = None

        if sig is None or "query_embedding" not in sig.parameters:
            logger.warning(
                "backend %s does not support query_embedding; falling back to FTS",
                type(backend).__name__,
            )
            return backend.search(query, path_pattern=path_pattern, limit=limit)

        return backend.search(
            query,
            path_pattern=path_pattern,
            limit=limit,
            query_embedding=query_embedding,
        )

    def _resolve_backend(self, path_pattern: str) -> Any | None:
        """Resolve the backend that owns *path_pattern* via the underlying VFS router."""
        router = getattr(self._vfs, "_router", None)
        if router is None:
            return None
        try:
            _, route = router.resolve(path_pattern.rstrip("*"))
        except Exception:
            return None
        if not isinstance(route, dict):
            return None
        return route.get("backend")

    def ls(self, prefix: str) -> list[str]:
        inner_prefix = self._to_inner(prefix)
        infos = self._vfs.ls(inner_prefix, recursive=True)
        return [self._to_outer(fi.path) for fi in infos]

    def delete(self, ref: str) -> bool:
        from seekvfs import NotFoundError

        try:
            self._vfs.delete(self._to_inner(ref))
        except NotFoundError:
            return False
        return True

    def find_by_hash(self, prefix: str, hash_value: str) -> str | None:
        """Delegate hash lookup to the underlying backend when available.

        Returns ``None`` when the backend cannot answer cheaply, signalling
        callers to fall back to a full scan or skip exact-duplicate detection.
        """
        if not hash_value:
            return None
        inner_prefix = self._to_inner(prefix)
        path_pattern = f"{self._inner_scheme}{inner_prefix}*"
        backend = self._resolve_backend(path_pattern)
        if backend is None:
            return None
        backend_fn = getattr(backend, "find_by_hash", None)
        if backend_fn is None:
            return None
        try:
            inner_ref = backend_fn(path_pattern, hash_value)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "find_by_hash failed on backend %s: %s",
                type(backend).__name__,
                exc,
            )
            return None
        if not inner_ref:
            return None
        return self._to_outer(str(inner_ref))

    def read_with_level(self, ref: str, level: ContentLevel) -> str | None:
        """Read the content field for the requested tier."""
        payload = self.read(ref)
        if payload is None:
            return None
        if level == ContentLevel.L0:
            return payload.get("abstract")
        if level == ContentLevel.L1:
            return payload.get("summary")
        # L2
        content = payload.get("content")
        return str(content) if content is not None else None

    def read_batch_with_level(
        self,
        refs: list[str],
        level: ContentLevel,
    ) -> dict[str, str]:
        """Batch-read content strings for *level*; refs with no value are omitted."""
        out: dict[str, str] = {}
        for ref in refs:
            value = self.read_with_level(ref, level)
            if value is not None:
                out[ref] = value
        return out

    def vector_search(
        self,
        prefix: str,
        query_vector: list[float],
        *,
        k: int,
    ) -> list[dict[str, Any]]:
        """Delegate vector search to the underlying VFS if it supports it.

        Deprecated: no longer called by ``VectorRecallRoute`` (which now goes
        through :meth:`search` with ``query_embedding=...``). Retained for
        external callers and ``VectorMemoryAdapter`` compatibility.

        The underlying seekvfs backend (e.g. OceanBase) may expose a
        ``vector_search`` method.  We call it through the VFS object if
        present; otherwise we fall back to the empty-list default from
        ``VectorSearchMixin``.
        """
        backend_vector_search = getattr(self._vfs, "vector_search", None)
        if backend_vector_search is None:
            return []
        inner_prefix = self._to_inner(prefix)
        raw_hits: list[dict[str, Any]] = backend_vector_search(
            inner_prefix, query_vector, k=k
        )
        out: list[dict[str, Any]] = []
        for hit in raw_hits:
            hit = dict(hit)
            if "ref" in hit:
                hit["ref"] = self._to_outer(str(hit["ref"]))
            out.append(hit)
        return out

    def geo_search(
        self,
        geo_query: Any,
        *,
        prefix: str,
        k: int,
    ) -> list[dict[str, Any]]:
        """Delegate geo_search to the underlying OceanBaseGeoBackend if available."""
        inner_prefix = self._to_inner(prefix)
        path_pattern = f"{self._inner_scheme}{inner_prefix}*"
        backend = self._resolve_backend(path_pattern)
        if backend is None or not hasattr(backend, "geo_search"):
            return []
        raw: list[dict[str, Any]] = backend.geo_search(geo_query, prefix=prefix, k=k)
        out: list[dict[str, Any]] = []
        for hit in raw:
            hit = dict(hit)
            if "ref" in hit:
                hit["ref"] = self._to_outer(str(hit["ref"]))
            out.append(hit)
        return out

    def is_point_within_zone(self, point: Any, *, zone_type: str, scope: str) -> bool:
        """Delegate is_point_within_zone to the underlying OceanBaseGeoBackend if available."""
        inner_prefix = self._to_inner(scope)
        path_pattern = f"{self._inner_scheme}{inner_prefix}*"
        backend = self._resolve_backend(path_pattern)
        if backend is None or not hasattr(backend, "is_point_within_zone"):
            return False
        # Namespace column stores full URI — build it from the scope
        full_scope = _EXT_SCHEME + scope.strip("/")
        return backend.is_point_within_zone(
            point, zone_type=zone_type, scope=full_scope
        )


__all__ = ["SeekVFSStorageAdapter"]
