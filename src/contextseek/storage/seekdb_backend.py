"""SeekDB backend for seekvfs — wraps pyseekdb collection API.

Supports embedded mode (local `.db` directory) and remote seekdb/OceanBase server mode.
Falls back gracefully with a clear ImportError when pyseekdb is not installed.
"""

from __future__ import annotations

import fnmatch
import json
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any

from seekvfs import BackendProtocol, SCHEME
from seekvfs.exceptions import NotFoundError
from seekvfs.models import FileData, FileInfo, GrepMatch, SearchHit, SearchResult


def _split_scheme(path: str) -> tuple[str, str]:
    i = path.find("://")
    if i == -1:
        return "", path
    return path[: i + 3], path[i + 3 :]


class SeekDBBackend(BackendProtocol):
    """seekvfs backend backed by a pyseekdb collection.

    Args:
        path: Local directory for embedded mode (e.g. ``~/.contextseek/seekdb.db``).
            Ignored when *host* is set.
        database: seekdb database name.
        host: Remote host for server mode. Empty string (default) = embedded mode.
        port: Remote port for server mode. Default ``2881``.
        embedding_function: Optional pyseekdb-compatible embedding function.
            When ``None``, ``pyseekdb.get_default_embedding_function()`` is used
            (built-in all-MiniLM-L6-v2 via ONNX, no external API key required).
    """

    def __init__(
        self,
        path: str = "~/.contextseek/seekdb.db",
        database: str = "contextseek",
        host: str = "",
        port: int = 2881,
        embedding_function: Any = None,
    ) -> None:
        self._path = str(pathlib.Path(path).expanduser())
        self._database = database
        self._host = host
        self._port = port
        self._ef = embedding_function
        self._collection: Any = None
        self._client: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        try:
            import pyseekdb
        except ImportError as exc:
            raise ImportError(
                "pyseekdb is required for STORAGE_BACKEND=seekdb. "
                "Install with: pip install pyseekdb"
            ) from exc

        ef = self._ef or pyseekdb.get_default_embedding_function()

        if self._host:
            self._client = pyseekdb.Client(
                host=self._host,
                port=self._port,
                database=self._database,
            )
        else:
            pathlib.Path(self._path).mkdir(parents=True, exist_ok=True)
            self._client = pyseekdb.Client(path=self._path, database=self._database)

        self._collection = self._client.get_or_create_collection(
            "context_items",
            embedding_function=ef,
        )
        self.ensure_sync_table()

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Write / Read / Delete
    # ------------------------------------------------------------------

    def write(self, path: str, content: bytes | str) -> None:
        doc = content.decode("utf-8") if isinstance(content, bytes) else content
        try:
            payload = json.loads(doc)
            scope = str(payload.get("scope", ""))
            item_hash = str(payload["hash"]) if payload.get("hash") else ""
        except (json.JSONDecodeError, AttributeError):
            scope = ""
            item_hash = ""

        _, bare = _split_scheme(path)
        metadata: dict[str, Any] = {"scope": scope, "bare_path": bare}
        if item_hash:
            metadata["hash"] = item_hash

        with self._lock:
            self._collection.upsert(
                ids=[path],
                documents=[doc],
                metadatas=[metadata],
            )

    # ------------------------------------------------------------------
    # Sync hash table (plain SQL — no vector index overhead)
    # ------------------------------------------------------------------

    def _sql(self, sql: str) -> list:
        """Execute a SQL statement via the underlying seekdb connection."""
        return self._client._server._execute(sql) or []

    def ensure_sync_table(self) -> None:
        """Create the sync_hashes table if it does not exist."""
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_hashes "
            "(scope VARCHAR(512) NOT NULL, hash CHAR(64) NOT NULL, "
            "PRIMARY KEY (scope, hash))"
        )

    def sync_hashes_for_scope(self, scope: str) -> set[str]:
        """Return all known content hashes for *scope* (single indexed lookup)."""
        from pyseekdb.client.sql_utils import escape_string
        rows = self._sql(
            f"SELECT hash FROM contextseek_sync_hashes "
            f"WHERE scope = '{escape_string(scope)}'"
        )
        return {row[0] for row in rows}

    def sync_hash_add(self, scope: str, hash_val: str) -> None:
        """Record a content hash as synced (idempotent)."""
        from pyseekdb.client.sql_utils import escape_string
        self._sql(
            f"INSERT IGNORE INTO contextseek_sync_hashes (scope, hash) "
            f"VALUES ('{escape_string(scope)}', '{hash_val}')"
        )

    def sync_hashes_add_batch(self, scope: str, hashes: set[str]) -> None:
        """Bulk-insert a set of hashes for initial bootstrap."""
        if not hashes:
            return
        from pyseekdb.client.sql_utils import escape_string
        esc_scope = escape_string(scope)
        values = ", ".join(f"('{esc_scope}', '{h}')" for h in hashes)
        self._sql(
            f"INSERT IGNORE INTO contextseek_sync_hashes (scope, hash) VALUES {values}"
        )

    def read(self, path: str, hint: str | None = None) -> FileData:
        result = self._collection.get(ids=[path], include=["documents"])
        docs = result.get("documents") or []
        if not docs or docs[0] is None:
            raise NotFoundError(path)
        return FileData(docs[0].encode("utf-8"), "utf-8")

    def read_full(self, path: str) -> FileData:
        return self.read(path)

    def read_batch(self, paths: list[str]) -> dict[str, FileData]:
        if not paths:
            return {}
        result = self._collection.get(ids=paths, include=["documents"])
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        return {
            id_: FileData(doc.encode("utf-8"), "utf-8")
            for id_, doc in zip(ids, docs)
            if doc is not None
        }

    def delete(self, path: str) -> None:
        check = self._collection.get(ids=[path], include=[])
        if not (check.get("ids")):
            raise NotFoundError(path)
        with self._lock:
            self._collection.delete(ids=[path])

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def ls(
        self,
        path: str,
        pattern: str | None = None,
        recursive: bool = False,
    ) -> list[FileInfo]:
        prefix = path if path.endswith("/") else path + "/"
        result = self._collection.get(include=[])
        all_ids: list[str] = result.get("ids") or []

        now = datetime.now(tz=timezone.utc)
        out: list[FileInfo] = []
        for id_ in all_ids:
            if not id_.startswith(prefix):
                continue
            rel = id_[len(prefix):]
            if not recursive and "/" in rel:
                continue
            if pattern is not None and not fnmatch.fnmatch(rel, pattern):
                continue
            out.append(FileInfo(path=id_, size=0, mtime=now, is_dir=False))
        out.sort(key=lambda fi: fi.path)
        return out

    def find_by_hash(self, path_pattern: str, hash_value: str) -> str | None:
        """Return the path of an item whose payload hash matches *hash_value*.

        Uses metadata filtering (O(1) index lookup) instead of a full document
        scan.  Returns ``None`` when no match exists or the collection is empty.
        """
        if not hash_value:
            return None
        try:
            result = self._collection.get(
                where={"hash": {"$eq": hash_value}},
                include=[],
            )
            ids: list[str] = result.get("ids") or []
            for id_ in ids:
                if path_pattern is None or fnmatch.fnmatch(id_, path_pattern):
                    return id_
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        path_pattern: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        *,
        query_embedding: list[float] | None = None,
    ) -> SearchResult:
        total = self._collection.count()
        if total == 0:
            return SearchResult(query=query, hits=[], searched_paths=[])

        n = max(1, min(limit * 3, total))  # over-fetch to allow path filtering

        if query_embedding is not None:
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
                include=["documents", "distances"],
            )
        else:
            result = self._collection.query(
                query_texts=[query] if query else None,
                n_results=n,
                include=["documents", "distances"],
            )

        ids_list: list[str] = (result.get("ids") or [[]])[0]
        docs_list: list[str] = (result.get("documents") or [[]])[0]
        dist_list: list[float] = (result.get("distances") or [[]])[0]

        hits: list[SearchHit] = []
        for id_, doc, dist in zip(ids_list, docs_list, dist_list):
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            score = max(0.0, 1.0 - float(dist))
            if score_threshold is not None and score < score_threshold:
                continue
            hits.append(SearchHit(path=id_, snippet=doc or "", score=score))

        return SearchResult(query=query, hits=hits[:limit], searched_paths=ids_list)

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    def edit(self, path: str, old: str, new: str) -> int:
        result = self._collection.get(ids=[path], include=["documents"])
        docs = result.get("documents") or []
        if not docs or docs[0] is None:
            raise NotFoundError(path)
        text = docs[0]
        count = text.count(old)
        if count == 0:
            return 0
        with self._lock:
            self._collection.upsert(ids=[path], documents=[text.replace(old, new)])
        return count

    def grep(
        self,
        pattern: str,
        path_pattern: str | None = None,
    ) -> list[GrepMatch]:
        result = self._collection.get(include=["documents"])
        ids: list[str] = result.get("ids") or []
        docs: list[str] = result.get("documents") or []
        out: list[GrepMatch] = []
        for id_, doc in zip(ids, docs):
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            if not doc:
                continue
            for idx, line in enumerate(doc.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=id_, line_number=idx, line=line))
        return out


__all__ = ["SeekDBBackend"]
