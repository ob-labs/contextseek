"""In-memory seekvfs BackendProtocol implementation.

Plugs into `seekvfs.VFS` as a backend for local usage and tests. Stores
one entry per VFS path in a flat dict — scheme-agnostic, so the scheme
that VFS was built with flows through unchanged.
"""

from __future__ import annotations

import fnmatch
import json
from datetime import UTC
from datetime import datetime

from seekvfs import BackendProtocol
from seekvfs.exceptions import NotFoundError
from seekvfs.models import FileData
from seekvfs.models import FileInfo
from seekvfs.models import GrepMatch
from seekvfs.models import SearchHit
from seekvfs.models import SearchResult


def _to_bytes(content: bytes | str) -> bytes:
    return content if isinstance(content, bytes) else content.encode("utf-8")


def _extract_hash(raw: bytes) -> str | None:
    """Best-effort read of payload['hash'] from a JSON-encoded record.

    Returns ``None`` when the payload is not a JSON object or the hash field
    is missing/empty — callers treat that as "no fast-path lookup possible".
    """
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        payload = json.loads(decoded)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("hash")
    return str(value) if value else None


class InMemoryBackend(BackendProtocol):
    """Flat in-memory K/V backend implementing `seekvfs.BackendProtocol`."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._mtime: dict[str, datetime] = {}
        # hash → path; many paths may share a hash (e.g. cross-scope) so we
        # keep the latest writer; callers must always re-verify with read().
        self._hash_index: dict[str, str] = {}

    def write(self, path: str, content: bytes | str) -> None:
        raw = _to_bytes(content)
        # When overwriting, drop the old hash mapping if it pointed here.
        prev = self._data.get(path)
        if prev is not None:
            prev_hash = _extract_hash(prev)
            if prev_hash and self._hash_index.get(prev_hash) == path:
                self._hash_index.pop(prev_hash, None)
        self._data[path] = raw
        self._mtime[path] = datetime.now(tz=UTC)
        new_hash = _extract_hash(raw)
        if new_hash:
            self._hash_index[new_hash] = path

    def read(self, path: str, hint: str | None = None) -> FileData:
        if path not in self._data:
            raise NotFoundError(path)
        return FileData(self._data[path], "utf-8")

    def read_full(self, path: str) -> FileData:
        return self.read(path)

    def read_batch(self, paths: list[str]) -> dict[str, FileData]:
        return {p: self.read(p) for p in paths}

    def search(
        self,
        query: str,
        path_pattern: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        *,
        query_embedding: list[float] | None = None,
    ) -> SearchResult:
        # query_embedding is accepted for protocol compatibility but is not
        # consumed: this backend only does keyword matching.
        del query_embedding
        q_low = query.lower()
        hits: list[SearchHit] = []
        searched: list[str] = []
        for path, data in self._data.items():
            if path_pattern is not None and not fnmatch.fnmatch(path, path_pattern):
                continue
            searched.append(path)
            text = data.decode("utf-8", errors="replace")
            score = 1.0 if q_low and q_low in text.lower() else 0.0
            if score_threshold is not None and score < score_threshold:
                continue
            if score <= 0:
                continue
            hits.append(SearchHit(path=path, snippet="", score=score))
        return SearchResult(query=query, hits=hits[:limit], searched_paths=searched)

    def ls(
        self,
        path: str,
        pattern: str | None = None,
        recursive: bool = False,
    ) -> list[FileInfo]:
        prefix = path if path.endswith("/") else path + "/"
        out: list[FileInfo] = []
        for key, data in self._data.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if not recursive and "/" in rest:
                continue
            if pattern is not None and not fnmatch.fnmatch(rest, pattern):
                continue
            out.append(
                FileInfo(
                    path=key,
                    size=len(data),
                    mtime=self._mtime.get(key, datetime.now(tz=UTC)),
                    is_dir=False,
                )
            )
        out.sort(key=lambda fi: fi.path)
        return out

    def edit(self, path: str, old: str, new: str) -> int:
        if path not in self._data:
            raise NotFoundError(path)
        text = self._data[path].decode("utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            return 0
        self._data[path] = text.replace(old, new).encode("utf-8")
        self._mtime[path] = datetime.now(tz=UTC)
        return count

    def grep(
        self,
        pattern: str,
        path_pattern: str | None = None,
    ) -> list[GrepMatch]:
        out: list[GrepMatch] = []
        for path, data in self._data.items():
            if path_pattern is not None and not fnmatch.fnmatch(path, path_pattern):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=path, line_number=idx, line=line))
        return out

    def delete(self, path: str) -> None:
        if path not in self._data:
            raise NotFoundError(path)
        existing_hash = _extract_hash(self._data[path])
        del self._data[path]
        self._mtime.pop(path, None)
        if existing_hash and self._hash_index.get(existing_hash) == path:
            self._hash_index.pop(existing_hash, None)

    def find_by_hash(self, path_pattern: str, hash_value: str) -> str | None:
        """Return the path of an item whose payload hash matches *hash_value*.

        ``path_pattern`` is the same fnmatch-style glob that ``search`` accepts,
        used here to constrain the lookup to a prefix.
        """
        if not hash_value:
            return None
        path = self._hash_index.get(hash_value)
        if path is None:
            return None
        if path_pattern is not None and not fnmatch.fnmatch(path, path_pattern):
            return None
        return path

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        pass


__all__ = ["InMemoryBackend"]
