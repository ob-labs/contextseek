"""SeekDB backend for seekvfs — wraps pyseekdb collection API.

Supports embedded mode (local `.db` directory) and remote seekdb/OceanBase server mode.
Falls back gracefully with a clear ImportError when pyseekdb is not installed.
"""

from __future__ import annotations

import contextlib
import fnmatch
import io
import json
import pathlib
import threading
from datetime import UTC, datetime
from typing import Any

from seekvfs import BackendProtocol
from seekvfs.exceptions import NotFoundError
from seekvfs.models import FileData, FileInfo, GrepMatch, SearchHit, SearchResult

from contextseek.storage._backend_utils import (
    _HOISTED,
    _json_safe,
    _merge_hoisted,
    _namespace_of,
    _parse_updated_at,
    _prefix_from_pattern,
    _serialize_dt,
)
from contextseek.storage.protocol import SyncCapableMixin


def _split_scheme(path: str) -> tuple[str, str]:
    i = path.find("://")
    if i == -1:
        return "", path
    return path[: i + 3], path[i + 3 :]


class SeekDBBackend(SyncCapableMixin, BackendProtocol):
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
        vector_weight: Weight for vector similarity in hybrid search (interface
            alignment; seekdb uses native RRF, so this value is not directly applied).
        fts_weight: Weight for FTS in hybrid search (interface alignment only).
        rrf_k: Reciprocal Rank Fusion window size, mapped to
            ``rank_window_size`` / ``rank_constant`` in ``hybrid_search``.
        importance_alpha: Importance score exponent (interface alignment; not
            supported by seekdb's hybrid_search API).
        importance_floor: Minimum importance floor (interface alignment only).
    """

    def __init__(
        self,
        path: str = "~/.contextseek/seekdb.db",
        database: str = "contextseek",
        host: str = "",
        port: int = 2881,
        embedding_function: Any = None,
        vector_weight: float = 0.7,
        fts_weight: float = 0.3,
        rrf_k: int = 60,
        importance_alpha: float = 0.5,
        importance_floor: float = 0.1,
    ) -> None:
        self._path = str(pathlib.Path(path).expanduser())
        self._database = database
        self._host = host
        self._port = port
        self._ef = embedding_function
        self._vector_weight = float(vector_weight)
        self._fts_weight = float(fts_weight)
        self._rrf_k = int(rrf_k)
        self._importance_alpha = float(importance_alpha)
        self._importance_floor = float(importance_floor)
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

    def write(self, path: str, content: bytes | str) -> None:
        doc = content.decode("utf-8") if isinstance(content, bytes) else content
        try:
            payload = json.loads(doc)
        except (json.JSONDecodeError, TypeError):
            payload = None

        now = datetime.now(tz=UTC).isoformat()
        namespace = _namespace_of(path)

        if isinstance(payload, dict):
            abstract = str(payload.get("abstract") or "")
            summary = str(payload.get("summary") or "")
            raw_content = payload.get("content")
            text_content = (
                json.dumps(raw_content, ensure_ascii=False)
                if isinstance(raw_content, (dict, list))
                else str(raw_content or "")
            )
            # FTS surface: prefer abstract+summary; fall back to full text.
            fulltext_content = f"{abstract} {summary}".strip() or text_content
            payload_slim = {k: v for k, v in payload.items() if k not in _HOISTED}
            embedding = payload.get("embedding")

            metadata: dict[str, Any] = {
                "namespace": namespace,
                "updated_at": now,
                "content": text_content,
                "abstract": abstract,
                "summary": summary,
                "payload_json": json.dumps(
                    _json_safe(payload_slim), ensure_ascii=False
                ),
                "scope": str(payload.get("scope") or ""),
                "stage": str(payload.get("stage") or ""),
                "searchable": 1 if payload.get("searchable", True) else 0,
                "hash": str(payload.get("hash") or ""),
                "created_at": _serialize_dt(payload.get("created_at")) or now,
            }
        else:
            fulltext_content = doc
            embedding = None
            metadata = {
                "namespace": namespace,
                "updated_at": now,
                "content": doc,
                "abstract": "",
                "summary": "",
                "payload_json": "{}",
                "scope": "",
                "stage": "",
                "searchable": 1,
                "hash": "",
                "created_at": now,
            }

        with self._lock:
            kwargs: dict[str, Any] = {
                "ids": [path],
                "documents": [fulltext_content],
                "metadatas": [metadata],
            }
            if isinstance(embedding, list) and embedding:
                kwargs["embeddings"] = [embedding]
            elif self._collection.embedding_function is not None:
                # No pre-computed embedding: vectorize abstract (falling back to
                # summary → fulltext_content) to mirror OceanBase's abstract_embedding
                # column, which is always the embedding of the abstract-level text.
                embed_text = abstract or summary or fulltext_content
                if embed_text:
                    try:
                        vecs = self._collection.embedding_function([embed_text])
                        if vecs and len(vecs) > 0:
                            kwargs["embeddings"] = [vecs[0]]
                    except Exception:
                        pass  # fall through: let pyseekdb vectorize documents as fallback
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
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_files "
            "(scope VARCHAR(512) NOT NULL, path_hash CHAR(64) NOT NULL, "
            "path VARCHAR(1024) NOT NULL, mtime DOUBLE NOT NULL, "
            "content_hash CHAR(64) NOT NULL, PRIMARY KEY (scope, path_hash))"
        )
        self._sql(
            "CREATE TABLE IF NOT EXISTS contextseek_meta "
            "(k VARCHAR(128) NOT NULL, v VARCHAR(512) NOT NULL, PRIMARY KEY (k))"
        )
        self.ensure_plug_tables()

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
        """Return the number of searchable items in *scope*."""
        batch_size = 1000
        offset = 0
        count = 0
        while True:
            result = self._collection.get(
                where={
                    "$and": [
                        {"scope": {"$eq": scope}},
                        {"searchable": {"$eq": 1}},
                    ]
                },
                include=[],
                limit=batch_size,
                offset=offset,
            )
            ids = result.get("ids") or []
            count += len(ids)
            if len(ids) < batch_size:
                break
            offset += batch_size
        return count

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

    # ------------------------------------------------------------------
    # PlugGateway bookkeeping tables
    # ------------------------------------------------------------------

    def ensure_plug_tables(self) -> None:
        try:
            self._sql(
                "CREATE TABLE IF NOT EXISTS plug_source_records "
                "(plug_name VARCHAR(128) NOT NULL, "
                "plug_instance_id VARCHAR(256) NOT NULL, "
                "external_id VARCHAR(512) NOT NULL, "
                "current_context_item_id VARCHAR(128), "
                "content_version_hash VARCHAR(128), "
                "write_projection_hash VARCHAR(128), "
                "last_materialization_key VARCHAR(128), "
                "last_materialized_context_item_id VARCHAR(128), "
                "status VARCHAR(32) NOT NULL, "
                "last_operation VARCHAR(32), "
                "last_seen_at VARCHAR(64), "
                "last_event_id VARCHAR(128), "
                "raw_payload_digest VARCHAR(128), "
                "PRIMARY KEY (plug_name, plug_instance_id, external_id))"
            )
            self._sql(
                "CREATE TABLE IF NOT EXISTS plug_event_outbox "
                "(event_id VARCHAR(128) NOT NULL, "
                "plug_name VARCHAR(128) NOT NULL, "
                "plug_instance_id VARCHAR(256) NOT NULL, "
                "external_id VARCHAR(512) NOT NULL, "
                "materialization_key VARCHAR(128) NOT NULL, "
                "materialized_context_item_id VARCHAR(128), "
                "event_payload LONGTEXT NOT NULL, "
                "status VARCHAR(32) NOT NULL, "
                "retry_count INT NOT NULL, "
                "last_error LONGTEXT, "
                "created_at VARCHAR(64) NOT NULL, "
                "updated_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (event_id))"
            )
        except Exception as exc:
            raise RuntimeError("ensure_plug_tables failed") from exc

    def _esc(self, value: Any) -> str:
        from pyseekdb.client.sql_utils import escape_string

        return escape_string("" if value is None else str(value))

    @staticmethod
    def _sql_literal(value: Any) -> str:
        if value is None:
            return "NULL"
        from pyseekdb.client.sql_utils import escape_string

        return f"'{escape_string(str(value))}'"

    def plug_source_get(
        self, plug_name: str, plug_instance_id: str, external_id: str
    ) -> dict[str, Any] | None:
        try:
            rows = self._sql(
                "SELECT plug_name, plug_instance_id, external_id, "
                "current_context_item_id, content_version_hash, write_projection_hash, "
                "last_materialization_key, last_materialized_context_item_id, status, "
                "last_operation, last_seen_at, last_event_id, raw_payload_digest "
                "FROM plug_source_records "
                f"WHERE plug_name = '{self._esc(plug_name)}' "
                f"AND plug_instance_id = '{self._esc(plug_instance_id)}' "
                f"AND external_id = '{self._esc(external_id)}'"
            )
        except Exception as exc:
            raise RuntimeError("plug_source_get failed") from exc
        if not rows:
            return None
        keys = [
            "plug_name",
            "plug_instance_id",
            "external_id",
            "current_context_item_id",
            "content_version_hash",
            "write_projection_hash",
            "last_materialization_key",
            "last_materialized_context_item_id",
            "status",
            "last_operation",
            "last_seen_at",
            "last_event_id",
            "raw_payload_digest",
        ]
        return dict(zip(keys, rows[0]))

    def plug_source_upsert(self, record: dict[str, Any]) -> None:
        try:
            now = datetime.now(tz=UTC).isoformat()
            values = [
                record["plug_name"],
                record["plug_instance_id"],
                record["external_id"],
                record.get("current_context_item_id"),
                record.get("content_version_hash"),
                record.get("write_projection_hash"),
                record.get("last_materialization_key"),
                record.get("last_materialized_context_item_id"),
                record.get("status") or "active",
                record.get("last_operation"),
                record.get("last_seen_at") or now,
                record.get("last_event_id"),
                record.get("raw_payload_digest"),
            ]
            self._sql(
                "REPLACE INTO plug_source_records "
                "(plug_name, plug_instance_id, external_id, current_context_item_id, "
                "content_version_hash, write_projection_hash, last_materialization_key, "
                "last_materialized_context_item_id, status, last_operation, "
                "last_seen_at, last_event_id, raw_payload_digest) VALUES "
                f"({', '.join(self._sql_literal(v) for v in values)})"
            )
        except Exception as exc:
            raise RuntimeError("plug_source_upsert failed") from exc

    def plug_outbox_get(self, event_id: str) -> dict[str, Any] | None:
        try:
            rows = self._sql(
                "SELECT event_id, plug_name, plug_instance_id, external_id, "
                "materialization_key, materialized_context_item_id, event_payload, "
                "status, retry_count, last_error, created_at, updated_at "
                "FROM plug_event_outbox "
                f"WHERE event_id = '{self._esc(event_id)}'"
            )
        except Exception as exc:
            raise RuntimeError("plug_outbox_get failed") from exc
        if not rows:
            return None
        keys = [
            "event_id",
            "plug_name",
            "plug_instance_id",
            "external_id",
            "materialization_key",
            "materialized_context_item_id",
            "event_payload",
            "status",
            "retry_count",
            "last_error",
            "created_at",
            "updated_at",
        ]
        result = dict(zip(keys, rows[0]))
        try:
            result["event_payload"] = json.loads(result.get("event_payload") or "{}")
        except json.JSONDecodeError:
            result["event_payload"] = {}
        result["retry_count"] = int(result.get("retry_count") or 0)
        return result

    def plug_outbox_upsert(self, record: dict[str, Any]) -> bool:
        try:
            existing = self.plug_outbox_get(record["event_id"])
            now = datetime.now(tz=UTC).isoformat()
            payload = record.get("event_payload") or {}
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False, default=str)
            if existing is not None:
                if existing.get("status") in {"applied", "dead"}:
                    return False
                self._sql(
                    "UPDATE plug_event_outbox SET "
                    f"event_payload = {self._sql_literal(payload)}, "
                    f"status = {self._sql_literal(record.get('status') or 'pending')}, "
                    f"retry_count = {int(record.get('retry_count') or 0)}, "
                    f"last_error = {self._sql_literal(record.get('last_error'))}, "
                    f"updated_at = {self._sql_literal(record.get('updated_at') or now)} "
                    f"WHERE event_id = '{self._esc(record['event_id'])}' "
                    "AND status NOT IN ('applied', 'dead')"
                )
                row = self.plug_outbox_get(record["event_id"])
                return bool(row and row.get("status") not in {"applied", "dead"})
            values = [
                record["event_id"],
                record["plug_name"],
                record["plug_instance_id"],
                record["external_id"],
                record["materialization_key"],
                record.get("materialized_context_item_id"),
                payload,
                record.get("status") or "pending",
                int(record.get("retry_count") or 0),
                record.get("last_error"),
                record.get("created_at") or now,
                record.get("updated_at") or now,
            ]
            self._sql(
                "INSERT INTO plug_event_outbox "
                "(event_id, plug_name, plug_instance_id, external_id, "
                "materialization_key, materialized_context_item_id, event_payload, "
                "status, retry_count, last_error, created_at, updated_at) VALUES "
                f"({', '.join(self._sql_literal(v) for v in values)})"
            )
            return True
        except Exception as exc:
            raise RuntimeError("plug_outbox_upsert failed") from exc

    def plug_outbox_update_status(
        self,
        event_id: str,
        *,
        status: str,
        materialized_context_item_id: str | None = None,
        last_error: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        try:
            now = datetime.now(tz=UTC).isoformat()
            retry_expr = "retry_count + 1" if increment_retry else "retry_count"
            materialized_expr = (
                self._sql_literal(materialized_context_item_id)
                if materialized_context_item_id is not None
                else "materialized_context_item_id"
            )
            self._sql(
                "UPDATE plug_event_outbox SET "
                f"status = {self._sql_literal(status)}, "
                f"materialized_context_item_id = {materialized_expr}, "
                f"last_error = {self._sql_literal(last_error)}, "
                f"retry_count = {retry_expr}, "
                f"updated_at = {self._sql_literal(now)} "
                f"WHERE event_id = '{self._esc(event_id)}'"
            )
        except Exception as exc:
            raise RuntimeError("plug_outbox_update_status failed") from exc

    def plug_outbox_requeue_dead(self, event_id: str) -> bool:
        try:
            now = datetime.now(tz=UTC).isoformat()
            self._sql(
                "UPDATE plug_event_outbox SET "
                "status = 'pending', "
                "retry_count = 0, "
                "last_error = NULL, "
                f"updated_at = {self._sql_literal(now)} "
                f"WHERE event_id = '{self._esc(event_id)}' AND status = 'dead'"
            )
            row = self.plug_outbox_get(event_id)
            return (
                row is not None
                and row.get("status") == "pending"
                and int(row.get("retry_count") or 0) == 0
                and row.get("updated_at") == now
            )
        except Exception as exc:
            raise RuntimeError("plug_outbox_requeue_dead failed") from exc

    def plug_outbox_list_retryable(
        self, *, limit: int = 100, max_retry: int = 3
    ) -> list[dict[str, Any]]:
        try:
            rows = self._sql(
                "SELECT event_id FROM plug_event_outbox "
                "WHERE status IN ('pending', 'failed') "
                f"AND retry_count < {int(max_retry)} "
                "ORDER BY updated_at ASC "
                f"LIMIT {int(limit)}"
            )
            result: list[dict[str, Any]] = []
            for row in rows:
                item = self.plug_outbox_get(row[0])
                if item is not None:
                    result.append(item)
            return result
        except Exception as exc:
            raise RuntimeError("plug_outbox_list_retryable failed") from exc

    def read(self, path: str, hint: str | None = None) -> FileData:
        result = self._collection.get(ids=[path], include=["metadatas"])
        metas = result.get("metadatas") or []
        if not metas or metas[0] is None:
            raise NotFoundError(path)
        meta = metas[0]
        # Backward compat: fall back to old "payload" field when new fields absent.
        payload_json = meta.get("payload_json") or meta.get("payload") or "{}"
        full = _merge_hoisted(
            payload_json,
            meta.get("content"),
            meta.get("abstract"),
            meta.get("summary"),
            None,
            scope=meta.get("scope"),
            stage=meta.get("stage"),
            searchable=meta.get("searchable"),
            hash_val=meta.get("hash"),
        )
        return FileData(json.dumps(full, ensure_ascii=False).encode("utf-8"), "utf-8")

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
            if meta is None:
                continue
            payload_json = meta.get("payload_json") or meta.get("payload") or "{}"
            full = _merge_hoisted(
                payload_json,
                meta.get("content"),
                meta.get("abstract"),
                meta.get("summary"),
                None,
                scope=meta.get("scope"),
                stage=meta.get("stage"),
                searchable=meta.get("searchable"),
                hash_val=meta.get("hash"),
            )
            out[id_] = FileData(
                json.dumps(full, ensure_ascii=False).encode("utf-8"), "utf-8"
            )
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

        all_items: list[tuple[str, dict]] = []
        batch_size = 1000
        offset = 0
        while True:
            result = self._collection.get(
                where=({"scope": {"$eq": scope_key}} if scope_key else None),
                include=["metadatas"],
                limit=batch_size,
                offset=offset,
            )
            ids = result.get("ids") or []
            metas = result.get("metadatas") or []
            for id_, meta in zip(ids, metas):
                all_items.append((id_, meta or {}))
            if len(ids) < batch_size:
                break
            offset += batch_size

        out: list[FileInfo] = []
        for id_, meta in all_items:
            bare = self._bare_path(id_)
            if not bare.startswith(prefix):
                continue
            rel = bare[len(prefix) :]
            if not recursive and "/" in rel:
                continue
            if pattern is not None and not fnmatch.fnmatch(rel, pattern):
                continue
            mtime = _parse_updated_at(meta.get("updated_at"))
            out.append(FileInfo(path=id_, size=0, mtime=mtime, is_dir=False))
        out.sort(key=lambda fi: fi.path)
        return out

    def find_by_hash(self, path_pattern: str, hash_value: str) -> str | None:
        """Return the path of an item whose payload hash matches *hash_value*.

        Uses metadata filtering (O(1) index lookup) instead of a full document
        scan. Returns ``None`` when no match exists or the collection is empty.
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

        n = max(1, min(limit * 3, total))

        # Derive scope filter from path_pattern so ANN/FTS recall is scoped
        # at the index level rather than relying on post-result fnmatch alone.
        prefix = _prefix_from_pattern(path_pattern)
        scope_key = self._scope_from_list_path(prefix) if prefix else None

        if query_embedding is not None and query.strip():
            result = self._hybrid_search(query_embedding, query, n, scope_key)
        elif query_embedding is not None:
            result = self._vector_only_search(query_embedding, n, scope_key)
        else:
            result = self._fts_only_search(query, n, scope_key)

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
            meta = meta or {}
            payload_json = meta.get("payload_json") or meta.get("payload") or "{}"
            full = _merge_hoisted(
                payload_json,
                meta.get("content"),
                meta.get("abstract"),
                meta.get("summary"),
                None,
                scope=meta.get("scope"),
                stage=meta.get("stage"),
                searchable=meta.get("searchable"),
                hash_val=meta.get("hash"),
            )
            snippet = json.dumps(full, ensure_ascii=False)
            hits.append(SearchHit(path=id_, snippet=snippet, score=score))

        return SearchResult(query=query, hits=hits[:limit], searched_paths=ids_list)

    @staticmethod
    def _build_where(scope_key: str | None) -> dict:
        """Build a pyseekdb `where` dict combining searchable=1 and optional scope."""
        base = {"searchable": {"$eq": 1}}
        if scope_key:
            return {"$and": [base, {"scope": {"$eq": scope_key}}]}
        return base

    def _hybrid_search(
        self,
        query_embedding: list[float],
        query: str,
        n: int,
        scope_key: str | None = None,
    ) -> Any:
        """Run hybrid vector+FTS search using pyseekdb's native hybrid_search."""
        where = self._build_where(scope_key)
        try:
            return self._collection.hybrid_search(
                knn={
                    "query_embeddings": [query_embedding],
                    "n_results": n,
                    "where": where,
                },
                query={
                    "where_document": {"$contains": query},
                    "n_results": n,
                    "where": where,
                },
                rank={
                    "rrf": {
                        "rank_window_size": self._rrf_k,
                        "rank_constant": self._rrf_k,
                    }
                },
                n_results=n,
                include=["metadatas", "distances"],
            )
        except Exception:
            # Fall back to vector-only search if hybrid_search is unavailable.
            return self._vector_only_search(query_embedding, n, scope_key)

    def _vector_only_search(
        self, query_embedding: list[float], n: int, scope_key: str | None = None
    ) -> Any:
        where = self._build_where(scope_key)
        try:
            return self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
                include=["metadatas", "distances"],
                where=where,
            )
        except Exception:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    def _fts_only_search(self, query: str, n: int, scope_key: str | None = None) -> Any:
        where = self._build_where(scope_key)
        try:
            return self._collection.query(
                query_texts=[query],
                n_results=n,
                include=["metadatas", "distances"],
                where=where,
                where_document={"$contains": query},
            )
        except Exception:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    def edit(self, path: str, old: str, new: str) -> int:
        """Edit a stored item by replacing *old* with *new* in its content."""
        # Use read() to get the fully reconstructed payload, then write back.
        try:
            file_data = self.read(path)
        except NotFoundError:
            raise
        current_json = file_data.content.decode("utf-8")
        count = current_json.count(old)
        if count == 0:
            return 0
        self.write(path, current_json.replace(old, new))
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
            meta = meta or {}
            # Match on the hoisted `content` field; fall back to old `payload` for
            # backward compatibility with data written before the schema migration.
            text = meta.get("content") or meta.get("payload") or ""
            if not text:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=id_, line_number=idx, line=line))
        return out


__all__ = ["SeekDBBackend"]
