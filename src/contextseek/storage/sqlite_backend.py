"""SQLite backend for seekvfs — a dependency-light, cross-platform store.

Aligns with :class:`~contextseek.storage.seekdb_backend.SeekDBBackend` in field
layout and search semantics, but uses stdlib ``sqlite3`` for storage:

* full-text recall via SQLite FTS5 (with a ``LIKE`` fallback),
* vector recall via brute-force cosine over stored embeddings,
* hybrid recall fused with Reciprocal Rank Fusion (mirrors seekdb's RRF).

Unlike seekdb it needs no native engine, so it works on every platform
(notably Windows, where seekdb's ``pylibseekdb`` is unavailable). Embeddings are
optional: when no embedding function is available the backend degrades to
FTS-only recall.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import math
import pathlib
import sqlite3
import struct
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


def _pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    n = len(blob) // 4
    if n == 0:
        return None
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


class SQLiteBackend(SyncCapableMixin, BackendProtocol):
    """seekvfs backend backed by a single SQLite database file.

    Args:
        path: Path to the SQLite database file (parent dirs are created).
        embedding_function: Optional callable ``list[str] -> list[list[float]]``
            used to vectorize items on write. When ``None``, the backend stores
            only precomputed embeddings already present on the payload (the
            ContextSeek client supplies these when an embedder is configured) and
            otherwise runs FTS-only — it pulls in no embedding dependency itself.
        rrf_k: Reciprocal Rank Fusion window/constant for hybrid search.
    """

    def __init__(
        self,
        path: str = "~/.contextseek/contextseek.sqlite3",
        embedding_function: Any = None,
        rrf_k: int = 60,
    ) -> None:
        self._path = str(pathlib.Path(path).expanduser())
        self._ef = embedding_function
        self._rrf_k = int(rrf_k)
        self._conn: sqlite3.Connection | None = None
        self._fts = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        pathlib.Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS context_items ("
                "id TEXT PRIMARY KEY, namespace TEXT, updated_at TEXT, "
                "created_at TEXT, document TEXT, content TEXT, abstract TEXT, "
                "summary TEXT, payload_json TEXT, scope TEXT, stage TEXT, "
                "searchable INTEGER, hash TEXT, embedding BLOB)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_scope ON context_items(scope)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_hash ON context_items(hash)"
            )
            self._init_fts()
            self.ensure_sync_table()
            self.ensure_plug_tables()
            self._conn.commit()

    def _init_fts(self) -> None:
        assert self._conn is not None
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS context_items_fts "
                "USING fts5(id UNINDEXED, text)"
            )
            self._fts = True
        except sqlite3.OperationalError:
            self._fts = False  # FTS5 not compiled in; fall back to LIKE.

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.commit()
                self._conn.close()
                self._conn = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteBackend.initialize() was not called")
        return self._conn

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float] | None:
        if not text or self._ef is None:
            return None
        with contextlib.suppress(Exception):
            vecs = self._ef([text])
            if vecs and len(vecs) > 0:
                return [float(x) for x in vecs[0]]
        return None

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
            fulltext_content = f"{abstract} {summary}".strip() or text_content
            payload_slim = {k: v for k, v in payload.items() if k not in _HOISTED}
            embedding = payload.get("embedding")
            row = {
                "namespace": namespace,
                "updated_at": now,
                "created_at": _serialize_dt(payload.get("created_at")) or now,
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
            }
        else:
            fulltext_content = doc
            embedding = None
            row = {
                "namespace": namespace,
                "updated_at": now,
                "created_at": now,
                "content": doc,
                "abstract": "",
                "summary": "",
                "payload_json": "{}",
                "scope": "",
                "stage": "",
                "searchable": 1,
                "hash": "",
            }

        if isinstance(embedding, list) and embedding:
            vec: list[float] | None = [float(x) for x in embedding]
        else:
            vec = self._embed(row["abstract"] or row["summary"] or fulltext_content)
        blob = _pack_vector(vec) if vec else None

        with self._lock:
            self._db.execute(
                "INSERT INTO context_items "
                "(id, namespace, updated_at, created_at, document, content, "
                "abstract, summary, payload_json, scope, stage, searchable, "
                "hash, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "namespace=excluded.namespace, updated_at=excluded.updated_at, "
                "created_at=excluded.created_at, document=excluded.document, "
                "content=excluded.content, abstract=excluded.abstract, "
                "summary=excluded.summary, payload_json=excluded.payload_json, "
                "scope=excluded.scope, stage=excluded.stage, "
                "searchable=excluded.searchable, hash=excluded.hash, "
                "embedding=COALESCE(excluded.embedding, context_items.embedding)",
                (
                    path,
                    row["namespace"],
                    row["updated_at"],
                    row["created_at"],
                    fulltext_content,
                    row["content"],
                    row["abstract"],
                    row["summary"],
                    row["payload_json"],
                    row["scope"],
                    row["stage"],
                    row["searchable"],
                    row["hash"],
                    blob,
                ),
            )
            if self._fts:
                self._db.execute("DELETE FROM context_items_fts WHERE id = ?", (path,))
                self._db.execute(
                    "INSERT INTO context_items_fts (id, text) VALUES (?, ?)",
                    (path, fulltext_content),
                )
            self._db.commit()

    # ------------------------------------------------------------------
    # Read / Delete
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
        return _merge_hoisted(
            row["payload_json"] or "{}",
            row["content"],
            row["abstract"],
            row["summary"],
            None,
            scope=row["scope"],
            stage=row["stage"],
            searchable=row["searchable"],
            hash_val=row["hash"],
        )

    def _get_row(self, path: str) -> sqlite3.Row | None:
        cur = self._db.execute("SELECT * FROM context_items WHERE id = ?", (path,))
        cur.row_factory = sqlite3.Row
        return cur.fetchone()

    def read(self, path: str, hint: str | None = None) -> FileData:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            row = self._db.execute(
                "SELECT * FROM context_items WHERE id = ?", (path,)
            ).fetchone()
        if row is None:
            raise NotFoundError(path)
        full = self._row_to_payload(row)
        return FileData(json.dumps(full, ensure_ascii=False).encode("utf-8"), "utf-8")

    def read_full(self, path: str) -> FileData:
        return self.read(path)

    def read_batch(self, paths: list[str]) -> dict[str, FileData]:
        if not paths:
            return {}
        out: dict[str, FileData] = {}
        with self._lock:
            self._db.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in paths)
            rows = self._db.execute(
                f"SELECT * FROM context_items WHERE id IN ({placeholders})", paths
            ).fetchall()
        for row in rows:
            full = self._row_to_payload(row)
            out[row["id"]] = FileData(
                json.dumps(full, ensure_ascii=False).encode("utf-8"), "utf-8"
            )
        return out

    def delete(self, path: str) -> None:
        with self._lock:
            cur = self._db.execute("DELETE FROM context_items WHERE id = ?", (path,))
            if cur.rowcount == 0:
                raise NotFoundError(path)
            if self._fts:
                self._db.execute("DELETE FROM context_items_fts WHERE id = ?", (path,))
            self._db.commit()

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_from_list_path(path: str) -> str | None:
        _, bare = _split_scheme(path)
        bare = bare.strip("/")
        return bare or None

    @staticmethod
    def _bare_path(path: str) -> str:
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

        with self._lock:
            self._db.row_factory = sqlite3.Row
            if scope_key:
                rows = self._db.execute(
                    "SELECT id, updated_at FROM context_items WHERE scope = ?",
                    (scope_key,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, updated_at FROM context_items"
                ).fetchall()

        out: list[FileInfo] = []
        for row in rows:
            id_ = row["id"]
            bare = self._bare_path(id_)
            if not bare.startswith(prefix):
                continue
            rel = bare[len(prefix) :]
            if not recursive and "/" in rel:
                continue
            if pattern is not None and not fnmatch.fnmatch(rel, pattern):
                continue
            out.append(
                FileInfo(
                    path=id_,
                    size=0,
                    mtime=_parse_updated_at(row["updated_at"]),
                    is_dir=False,
                )
            )
        out.sort(key=lambda fi: fi.path)
        return out

    def find_by_hash(self, path_pattern: str, hash_value: str) -> str | None:
        if not hash_value:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM context_items WHERE hash = ?", (hash_value,)
            ).fetchall()
        for (id_,) in rows:
            if path_pattern is None or fnmatch.fnmatch(id_, path_pattern):
                return id_
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
        prefix = _prefix_from_pattern(path_pattern)
        scope_key = self._scope_from_list_path(prefix) if prefix else None
        n = max(1, limit * 3)

        if query_embedding is not None and query.strip():
            ranked = self._hybrid_search(query_embedding, query, n, scope_key)
        elif query_embedding is not None:
            ranked = self._vector_search(query_embedding, n, scope_key)
        else:
            ranked = self._fts_search(query, n, scope_key)

        hits: list[SearchHit] = []
        searched: list[str] = []
        for id_, score in ranked:
            searched.append(id_)
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            if score_threshold is not None and score < score_threshold:
                continue
            row = self._get_row(id_)
            if row is None:
                continue
            full = self._row_to_payload(row)
            snippet = json.dumps(full, ensure_ascii=False)
            hits.append(SearchHit(path=id_, snippet=snippet, score=score))
            if len(hits) >= limit:
                break
        return SearchResult(query=query, hits=hits, searched_paths=searched)

    def _candidate_rows(self, scope_key: str | None) -> list[sqlite3.Row]:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            if scope_key:
                return self._db.execute(
                    "SELECT id, embedding FROM context_items "
                    "WHERE searchable = 1 AND scope = ?",
                    (scope_key,),
                ).fetchall()
            return self._db.execute(
                "SELECT id, embedding FROM context_items WHERE searchable = 1"
            ).fetchall()

    def _vector_search(
        self, query_embedding: list[float], n: int, scope_key: str | None
    ) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []
        for row in self._candidate_rows(scope_key):
            vec = _unpack_vector(row["embedding"])
            if vec is None:
                continue
            sim = _cosine(query_embedding, vec)
            if sim > 0:
                scored.append((row["id"], sim))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:n]

    @staticmethod
    def _fts_query(query: str) -> str:
        """Build a tolerant FTS5 MATCH expression: OR of quoted tokens."""
        tokens = [t for t in query.replace('"', " ").split() if t]
        if not tokens:
            return '""'
        return " OR ".join(f'"{t}"' for t in tokens)

    def _fts_search(
        self, query: str, n: int, scope_key: str | None
    ) -> list[tuple[str, float]]:
        if not query.strip():
            return []
        ids: list[str] = []
        if self._fts:
            with self._lock, contextlib.suppress(sqlite3.OperationalError):
                rows = self._db.execute(
                    "SELECT id FROM context_items_fts "
                    "WHERE context_items_fts MATCH ? ORDER BY rank LIMIT ?",
                    (self._fts_query(query), n * 3),
                ).fetchall()
                ids = [r[0] for r in rows]
        if not ids:  # FTS unavailable or no hits → LIKE substring fallback.
            with self._lock:
                rows = self._db.execute(
                    "SELECT id FROM context_items "
                    "WHERE searchable = 1 AND document LIKE ? LIMIT ?",
                    (f"%{query}%", n * 3),
                ).fetchall()
                ids = [r[0] for r in rows]

        scoped = self._filter_ids_by_scope(ids, scope_key)
        # Rank-based score in (0, 1]; earlier hits score higher.
        return [(id_, 1.0 / (1 + i)) for i, id_ in enumerate(scoped[:n])]

    def _filter_ids_by_scope(self, ids: list[str], scope_key: str | None) -> list[str]:
        if not ids:
            return []
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            if scope_key:
                rows = self._db.execute(
                    f"SELECT id FROM context_items WHERE id IN ({placeholders}) "
                    "AND searchable = 1 AND scope = ?",
                    [*ids, scope_key],
                ).fetchall()
            else:
                rows = self._db.execute(
                    f"SELECT id FROM context_items WHERE id IN ({placeholders}) "
                    "AND searchable = 1",
                    ids,
                ).fetchall()
        keep = {r[0] for r in rows}
        return [i for i in ids if i in keep]  # preserve original ranking order

    def _hybrid_search(
        self,
        query_embedding: list[float],
        query: str,
        n: int,
        scope_key: str | None,
    ) -> list[tuple[str, float]]:
        vec_ranked = self._vector_search(query_embedding, n, scope_key)
        fts_ranked = self._fts_search(query, n, scope_key)
        sim_by_id = dict(vec_ranked)

        # Reciprocal Rank Fusion over the two ranked lists.
        rrf: dict[str, float] = {}
        for rank, (id_, _) in enumerate(vec_ranked):
            rrf[id_] = rrf.get(id_, 0.0) + 1.0 / (self._rrf_k + rank)
        for rank, (id_, _) in enumerate(fts_ranked):
            rrf[id_] = rrf.get(id_, 0.0) + 1.0 / (self._rrf_k + rank)

        fused = sorted(rrf.items(), key=lambda t: t[1], reverse=True)
        # Surface cosine similarity as the user-facing score when available
        # (meaningful in [0,1]); fall back to the normalized RRF weight.
        max_rrf = fused[0][1] if fused else 1.0
        out: list[tuple[str, float]] = []
        for id_, weight in fused[:n]:
            score = sim_by_id.get(id_)
            if score is None:
                score = weight / max_rrf if max_rrf > 0 else 0.0
            out.append((id_, float(score)))
        return out

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    def edit(self, path: str, old: str, new: str) -> int:
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
        with self._lock:
            self._db.row_factory = sqlite3.Row
            rows = self._db.execute("SELECT id, content FROM context_items").fetchall()
        out: list[GrepMatch] = []
        for row in rows:
            id_ = row["id"]
            if path_pattern and not fnmatch.fnmatch(id_, path_pattern):
                continue
            text = row["content"] or ""
            if not text:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=id_, line_number=idx, line=line))
        return out

    # ------------------------------------------------------------------
    # Sync bookkeeping tables (parameterized SQL)
    # ------------------------------------------------------------------

    def ensure_sync_table(self) -> None:
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_hashes "
            "(scope TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (scope, hash))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS contextseek_sync_files "
            "(scope TEXT NOT NULL, path_hash TEXT NOT NULL, path TEXT NOT NULL, "
            "mtime REAL NOT NULL, content_hash TEXT NOT NULL, "
            "PRIMARY KEY (scope, path_hash))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS contextseek_meta "
            "(k TEXT NOT NULL, v TEXT NOT NULL, PRIMARY KEY (k))"
        )

    def meta_get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT v FROM contextseek_meta WHERE k = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO contextseek_meta (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, value),
            )
            self._db.commit()

    def sync_hashes_for_scope(self, scope: str) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT hash FROM contextseek_sync_hashes WHERE scope = ?", (scope,)
            ).fetchall()
        return {r[0] for r in rows}

    def sync_hash_add(self, scope: str, hash_val: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO contextseek_sync_hashes (scope, hash) "
                "VALUES (?, ?)",
                (scope, hash_val),
            )
            self._db.commit()

    def sync_hashes_add_batch(self, scope: str, hashes: set[str]) -> None:
        if not hashes:
            return
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO contextseek_sync_hashes (scope, hash) "
                "VALUES (?, ?)",
                [(scope, h) for h in hashes],
            )
            self._db.commit()

    def sync_files_for_scope(self, scope: str) -> dict[str, tuple[float, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT path, mtime, content_hash FROM contextseek_sync_files "
                "WHERE scope = ?",
                (scope,),
            ).fetchall()
        return {r[0]: (float(r[1]), r[2]) for r in rows}

    def sync_file_record(
        self, scope: str, path: str, mtime: float, content_hash: str
    ) -> None:
        import hashlib

        path_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()
        with self._lock:
            self._db.execute(
                "INSERT INTO contextseek_sync_files "
                "(scope, path_hash, path, mtime, content_hash) VALUES (?,?,?,?,?) "
                "ON CONFLICT(scope, path_hash) DO UPDATE SET "
                "path=excluded.path, mtime=excluded.mtime, "
                "content_hash=excluded.content_hash",
                (scope, path_hash, path, float(mtime), content_hash),
            )
            self._db.commit()

    def visible_count_for_scope(self, scope: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM context_items WHERE scope = ? AND searchable = 1",
                (scope,),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # PlugGateway bookkeeping tables
    # ------------------------------------------------------------------

    def ensure_plug_tables(self) -> None:
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS plug_source_records ("
            "plug_name TEXT NOT NULL, "
            "plug_instance_id TEXT NOT NULL, "
            "external_id TEXT NOT NULL, "
            "current_context_item_id TEXT, "
            "content_version_hash TEXT, "
            "write_projection_hash TEXT, "
            "last_materialization_key TEXT, "
            "last_materialized_context_item_id TEXT, "
            "status TEXT NOT NULL, "
            "last_operation TEXT, "
            "last_seen_at TEXT, "
            "last_event_id TEXT, "
            "raw_payload_digest TEXT, "
            "PRIMARY KEY (plug_name, plug_instance_id, external_id))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS plug_event_outbox ("
            "event_id TEXT PRIMARY KEY, "
            "plug_name TEXT NOT NULL, "
            "plug_instance_id TEXT NOT NULL, "
            "external_id TEXT NOT NULL, "
            "materialization_key TEXT NOT NULL, "
            "materialized_context_item_id TEXT, "
            "event_payload TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "retry_count INTEGER NOT NULL DEFAULT 0, "
            "last_error TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_plug_outbox_status "
            "ON plug_event_outbox(status, retry_count, updated_at)"
        )

    def plug_source_get(
        self, plug_name: str, plug_instance_id: str, external_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            row = self._db.execute(
                "SELECT * FROM plug_source_records "
                "WHERE plug_name = ? AND plug_instance_id = ? AND external_id = ?",
                (plug_name, plug_instance_id, external_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def plug_source_upsert(self, record: dict[str, Any]) -> None:
        now = datetime.now(tz=UTC).isoformat()
        values = {
            "plug_name": record["plug_name"],
            "plug_instance_id": record["plug_instance_id"],
            "external_id": record["external_id"],
            "current_context_item_id": record.get("current_context_item_id"),
            "content_version_hash": record.get("content_version_hash"),
            "write_projection_hash": record.get("write_projection_hash"),
            "last_materialization_key": record.get("last_materialization_key"),
            "last_materialized_context_item_id": record.get(
                "last_materialized_context_item_id"
            ),
            "status": record.get("status") or "active",
            "last_operation": record.get("last_operation"),
            "last_seen_at": record.get("last_seen_at") or now,
            "last_event_id": record.get("last_event_id"),
            "raw_payload_digest": record.get("raw_payload_digest"),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO plug_source_records ("
                "plug_name, plug_instance_id, external_id, "
                "current_context_item_id, content_version_hash, "
                "write_projection_hash, last_materialization_key, "
                "last_materialized_context_item_id, status, last_operation, "
                "last_seen_at, last_event_id, raw_payload_digest) "
                "VALUES (:plug_name, :plug_instance_id, :external_id, "
                ":current_context_item_id, :content_version_hash, "
                ":write_projection_hash, :last_materialization_key, "
                ":last_materialized_context_item_id, :status, :last_operation, "
                ":last_seen_at, :last_event_id, :raw_payload_digest) "
                "ON CONFLICT(plug_name, plug_instance_id, external_id) DO UPDATE SET "
                "current_context_item_id=excluded.current_context_item_id, "
                "content_version_hash=excluded.content_version_hash, "
                "write_projection_hash=excluded.write_projection_hash, "
                "last_materialization_key=excluded.last_materialization_key, "
                "last_materialized_context_item_id=excluded.last_materialized_context_item_id, "
                "status=excluded.status, last_operation=excluded.last_operation, "
                "last_seen_at=excluded.last_seen_at, last_event_id=excluded.last_event_id, "
                "raw_payload_digest=excluded.raw_payload_digest",
                values,
            )
            self._db.commit()

    def plug_outbox_get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            row = self._db.execute(
                "SELECT * FROM plug_event_outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["event_payload"] = json.loads(result.get("event_payload") or "{}")
        except json.JSONDecodeError:
            result["event_payload"] = {}
        return result

    def plug_outbox_upsert(self, record: dict[str, Any]) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        event_payload = record.get("event_payload") or {}
        if not isinstance(event_payload, str):
            event_payload = json.dumps(event_payload, ensure_ascii=False, default=str)
        values = {
            "event_id": record["event_id"],
            "plug_name": record["plug_name"],
            "plug_instance_id": record["plug_instance_id"],
            "external_id": record["external_id"],
            "materialization_key": record["materialization_key"],
            "materialized_context_item_id": record.get("materialized_context_item_id"),
            "event_payload": event_payload,
            "status": record.get("status") or "pending",
            "retry_count": int(record.get("retry_count") or 0),
            "last_error": record.get("last_error"),
            "created_at": record.get("created_at") or now,
            "updated_at": record.get("updated_at") or now,
        }
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO plug_event_outbox ("
                "event_id, plug_name, plug_instance_id, external_id, "
                "materialization_key, materialized_context_item_id, event_payload, "
                "status, retry_count, last_error, created_at, updated_at) "
                "VALUES (:event_id, :plug_name, :plug_instance_id, :external_id, "
                ":materialization_key, :materialized_context_item_id, :event_payload, "
                ":status, :retry_count, :last_error, :created_at, :updated_at) "
                "ON CONFLICT(event_id) DO UPDATE SET "
                "event_payload=excluded.event_payload, "
                "status=excluded.status, "
                "retry_count=excluded.retry_count, "
                "last_error=excluded.last_error, "
                "updated_at=excluded.updated_at "
                "WHERE plug_event_outbox.status NOT IN ('applied', 'dead')",
                values,
            )
            self._db.commit()
            return cursor.rowcount > 0

    def plug_outbox_update_status(
        self,
        event_id: str,
        *,
        status: str,
        materialized_context_item_id: str | None = None,
        last_error: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            self._db.execute(
                "UPDATE plug_event_outbox SET "
                "status = ?, "
                "materialized_context_item_id = COALESCE(?, materialized_context_item_id), "
                "last_error = ?, "
                "retry_count = retry_count + ?, "
                "updated_at = ? "
                "WHERE event_id = ?",
                (
                    status,
                    materialized_context_item_id,
                    last_error,
                    1 if increment_retry else 0,
                    now,
                    event_id,
                ),
            )
            self._db.commit()

    def plug_outbox_requeue_dead(self, event_id: str) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            cursor = self._db.execute(
                "UPDATE plug_event_outbox SET "
                "status = 'pending', "
                "retry_count = 0, "
                "last_error = NULL, "
                "updated_at = ? "
                "WHERE event_id = ? AND status = 'dead'",
                (now, event_id),
            )
            self._db.commit()
            return cursor.rowcount > 0

    def plug_outbox_list_retryable(
        self, *, limit: int = 100, max_retry: int = 3
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            rows = self._db.execute(
                "SELECT * FROM plug_event_outbox "
                "WHERE status IN ('pending', 'failed') AND retry_count < ? "
                "ORDER BY updated_at ASC LIMIT ?",
                (max_retry, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["event_payload"] = json.loads(item.get("event_payload") or "{}")
            except json.JSONDecodeError:
                item["event_payload"] = {}
            result.append(item)
        return result


__all__ = ["SQLiteBackend"]
