"""SeekDB backend for seekvfs — wraps pyseekdb collection API.

Supports embedded mode (local `.db` directory) and remote seekdb/OceanBase server mode.
Falls back gracefully with a clear ImportError when pyseekdb is not installed.
"""

from __future__ import annotations

import fnmatch
import contextlib
import io
import json
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any

from seekvfs import BackendProtocol
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

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            if self._host:
                admin = pyseekdb.AdminClient(host=self._host, port=self._port)
                self._create_database_if_missing(admin)
                self._client = pyseekdb.Client(
                    host=self._host,
                    port=self._port,
                    database=self._database,
                )
            else:
                pathlib.Path(self._path).mkdir(parents=True, exist_ok=True)
                admin = pyseekdb.AdminClient(path=self._path)
                self._create_database_if_missing(admin)
                self._client = pyseekdb.Client(path=self._path, database=self._database)

            self._collection = self._client.get_or_create_collection(
                "context_items",
                embedding_function=ef,
            )
        self.ensure_sync_table()

    def _create_database_if_missing(self, admin: Any) -> None:
        """Create the seekdb database before opening a Client connection."""
        try:
            admin.create_database(self._database)
        except Exception as exc:
            message = str(exc).lower()
            if "exist" not in message and "duplicate" not in message:
                # Client initialization below will surface a precise connection
                # error if the database is still unavailable.
                pass

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Write / Read / Delete
    # ------------------------------------------------------------------

    @staticmethod
    def _index_text(payload: dict[str, Any]) -> str:
        """Pick the text the vector / FTS index should be built over.

        Prefers the L0 abstract, then the L1 summary, then the raw content —
        so the index reflects what the item *means* rather than the full JSON
        envelope (embedding arrays, provenance, timestamps).
        """
        for key in ("abstract", "summary"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
        content = payload.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for key in ("body", "description", "text", "content"):
                val = content.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            try:
                return json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(content)
        return str(content) if content is not None else ""

    def write(self, path: str, content: bytes | str) -> None:
        doc = content.decode("utf-8") if isinstance(content, bytes) else content
        try:
            payload = json.loads(doc)
        except (json.JSONDecodeError, TypeError):
            payload = None

        _, bare = _split_scheme(path)
        metadata: dict[str, Any] = {"bare_path": bare, "payload": doc}

        if isinstance(payload, dict):
            metadata["scope"] = str(payload.get("scope", ""))
            metadata["stage"] = str(payload.get("stage", ""))
            metadata["searchable"] = 1 if payload.get("searchable", True) else 0
            metadata["created_at"] = str(payload.get("created_at") or "")
            if payload.get("hash"):
                metadata["hash"] = str(payload["hash"])
            index_text = self._index_text(payload)
            embedding = payload.get("embedding")
        else:
            metadata["scope"] = ""
            metadata["searchable"] = 1
            index_text = doc
            embedding = None

        with self._lock:
            kwargs: dict[str, Any] = {
                "ids": [path],
                "documents": [index_text],
                "metadatas": [metadata],
            }
            if isinstance(embedding, list) and embedding:
                kwargs["embeddings"] = [embedding]
            self._collection.upsert(**kwargs)

    # ------------------------------------------------------------------
    # Sync hash table (plain SQL — no vector index overhead)
    # ------------------------------------------------------------------

    def _sql(self, sql: str) -> list:
        """Execute a SQL statement via the underlying seekdb connection."""
        return self._client._server._execute(sql) or []

    def ensure_sync_table(self) -> None:
        """Create the sync bookkeeping tables if they do not exist."""
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_hashes "
            "(scope VARCHAR(512) NOT NULL, hash CHAR(64) NOT NULL, "
            "PRIMARY KEY (scope, hash))"
        )
        # Per-file ingest records for the mtime fast-path (SHA256 authoritative).
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_files "
            "(scope VARCHAR(512) NOT NULL, path_hash CHAR(64) NOT NULL, "
            "path VARCHAR(1024) NOT NULL, mtime DOUBLE NOT NULL, "
            "content_hash CHAR(64) NOT NULL, PRIMARY KEY (scope, path_hash))"
        )
        # Key/value metadata (e.g. the embedding dimensionality in use).
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_meta "
            "(k VARCHAR(128) NOT NULL, v VARCHAR(512) NOT NULL, PRIMARY KEY (k))"
        )

    def meta_get(self, key: str) -> str | None:
        """Return a stored metadata value, or ``None`` when absent."""
        from pyseekdb.client.sql_utils import escape_string

        rows = self._sql(
            f"SELECT v FROM contextseek_meta WHERE k = '{escape_string(key)}'"
        )
        return rows[0][0] if rows else None

    def meta_set(self, key: str, value: str) -> None:
        """Upsert a metadata key/value pair."""
        from pyseekdb.client.sql_utils import escape_string

        self._sql(
            "REPLACE INTO contextseek_meta (k, v) VALUES "
            f"('{escape_string(key)}', '{escape_string(value)}')"
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

    def sync_files_for_scope(self, scope: str) -> dict[str, tuple[float, str]]:
        """Return ``{path: (mtime, content_hash)}`` ingest records for *scope*."""
        from pyseekdb.client.sql_utils import escape_string

        rows = self._sql(
            f"SELECT path, mtime, content_hash FROM contextseek_sync_files "
            f"WHERE scope = '{escape_string(scope)}'"
        )
        return {row[0]: (float(row[1]), row[2]) for row in rows}

    def visible_count_for_scope(self, scope: str) -> int:
        """Return the number of items visible under the current metadata schema."""
        return len(self._list_ids_for_scope(scope))

    def sync_file_record(
        self, scope: str, path: str, mtime: float, content_hash: str
    ) -> None:
        """Upsert one per-file ingest record (mtime + content hash)."""
        import hashlib

        from pyseekdb.client.sql_utils import escape_string

        path_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()
        self._sql(
            "REPLACE INTO contextseek_sync_files "
            "(scope, path_hash, path, mtime, content_hash) VALUES "
            f"('{escape_string(scope)}', '{path_hash}', "
            f"'{escape_string(path)}', {float(mtime)}, '{content_hash}')"
        )

    def read(self, path: str, hint: str | None = None) -> FileData:
        result = self._collection.get(ids=[path], include=["metadatas"])
        metas = result.get("metadatas") or []
        if not metas or metas[0] is None:
            raise NotFoundError(path)
        payload = metas[0].get("payload")
        if payload is None:
            raise NotFoundError(path)
        return FileData(payload.encode("utf-8"), "utf-8")

    def read_full(self, path: str) -> FileData:
        return self.read(path)

    def read_batch(self, paths: list[str]) -> dict[str, FileData]:
        if not paths:
            return {}
        result = self._collection.get(ids=paths, include=["metadatas"])
        ids = result.get("ids") or []
        metas = result.get("metadatas") or []
        out: dict[str, FileData] = {}
        for id_, meta in zip(ids, metas):
            payload = (meta or {}).get("payload")
            if payload is not None:
                out[id_] = FileData(payload.encode("utf-8"), "utf-8")
        return out

    def delete(self, path: str) -> None:
        check = self._collection.get(ids=[path], include=[])
        if not (check.get("ids")):
            raise NotFoundError(path)
        with self._lock:
            self._collection.delete(ids=[path])

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_from_list_path(path: str) -> str | None:
        """Map a list prefix like ``seekvfs://me/work/`` to stored metadata scope."""
        _, bare = _split_scheme(path)
        bare = bare.strip("/")
        return bare or None

    def _list_ids_for_scope(self, scope_key: str) -> list[str]:
        """List collection ids for one scope via metadata index (not a full scan)."""
        all_ids: list[str] = []
        batch_size = 1000
        offset = 0
        while True:
            result = self._collection.get(
                where={"scope": {"$eq": scope_key}},
                include=[],
                limit=batch_size,
                offset=offset,
            )
            ids = result.get("ids") or []
            if not ids:
                break
            all_ids.extend(ids)
            if len(ids) < batch_size:
                break
            offset += batch_size
        return all_ids

    def _list_all_ids(self) -> list[str]:
        """List all ids in the collection, used only for root-level listings."""
        total = self._collection.count()
        all_ids: list[str] = []
        batch_size = 1000
        for offset in range(0, total, batch_size):
            result = self._collection.get(
                include=[],
                limit=batch_size,
                offset=offset,
            )
            all_ids.extend(result.get("ids") or [])
        return all_ids

    @staticmethod
    def _bare_path(path: str) -> str:
        """Return a stored id/path without its URI scheme."""
        _, bare = _split_scheme(path)
        return bare

    def ls(
        self,
        path: str,
        pattern: str | None = None,
        recursive: bool = False,
    ) -> list[FileInfo]:
        prefix = self._bare_path(path)
        prefix = prefix if prefix.endswith("/") else prefix + "/"
        scope_key = self._scope_from_list_path(path)
        if scope_key:
            all_ids = self._list_ids_for_scope(scope_key)
        else:
            all_ids = self._list_all_ids()

        now = datetime.now(tz=timezone.utc)
        out: list[FileInfo] = []
        for id_ in all_ids:
            bare = self._bare_path(id_)
            if not bare.startswith(prefix):
                continue
            rel = bare[len(prefix) :]
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

        def _run_query(where: dict[str, Any] | None) -> Any:
            kwargs: dict[str, Any] = {
                "n_results": n,
                "include": ["metadatas", "distances"],
            }
            if where is not None:
                kwargs["where"] = where
            if query_embedding is not None:
                kwargs["query_embeddings"] = [query_embedding]
            else:
                kwargs["query_texts"] = [query] if query else None
            return self._collection.query(**kwargs)

        # Prefer to drop soft-deleted items at the index level. Fall back to an
        # unfiltered query when the predicate is unsupported or matches nothing
        # (e.g. a pre-existing collection written before `searchable` metadata).
        try:
            result = _run_query({"searchable": {"$eq": 1}})
            if not (result.get("ids") or [[]])[0]:
                result = _run_query(None)
        except Exception:
            result = _run_query(None)

        ids_list: list[str] = (result.get("ids") or [[]])[0]
        metas_list: list[dict] = (result.get("metadatas") or [[]])[0]
        dist_list: list[float] = (result.get("distances") or [[]])[0]

        hits: list[SearchHit] = []
        for id_, meta, dist in zip(ids_list, metas_list, dist_list):
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            score = max(0.0, 1.0 - float(dist))
            if score_threshold is not None and score < score_threshold:
                continue
            snippet = (meta or {}).get("payload") or ""
            hits.append(SearchHit(path=id_, snippet=snippet, score=score))

        return SearchResult(query=query, hits=hits[:limit], searched_paths=ids_list)

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    def edit(self, path: str, old: str, new: str) -> int:
        result = self._collection.get(ids=[path], include=["metadatas"])
        metas = result.get("metadatas") or []
        if not metas or metas[0] is None:
            raise NotFoundError(path)
        payload = metas[0].get("payload")
        if payload is None:
            raise NotFoundError(path)
        count = payload.count(old)
        if count == 0:
            return 0
        # Re-derive the index text and metadata from the edited payload so the
        # stored document and the full JSON stay consistent.
        self.write(path, payload.replace(old, new))
        return count

    def grep(
        self,
        pattern: str,
        path_pattern: str | None = None,
    ) -> list[GrepMatch]:
        result = self._collection.get(include=["metadatas"])
        ids: list[str] = result.get("ids") or []
        metas: list[dict] = result.get("metadatas") or []
        out: list[GrepMatch] = []
        for id_, meta in zip(ids, metas):
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            payload = (meta or {}).get("payload")
            if not payload:
                continue
            for idx, line in enumerate(payload.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=id_, line_number=idx, line=line))
        return out


__all__ = ["SeekDBBackend"]
