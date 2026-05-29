"""OceanBase-backed seekvfs backend with hybrid vector + full-text search.

Implements :class:`seekvfs.BackendProtocol`, so it plugs into ``seekvfs.VFS``
the same way :class:`FileBackend` does. Each ref maps to one row with HNSW
vector index and FTS index; ``search`` fuses vector ANN + FTS MATCH AGAINST
through application-level Reciprocal Rank Fusion and carries the full
payload JSON in :attr:`SearchHit.snippet` so upstream adapters don't need a
second round-trip.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime
from typing import Any

from seekvfs import BackendProtocol
from seekvfs.exceptions import NotFoundError
from seekvfs.models import FileData
from seekvfs.models import FileInfo
from seekvfs.models import GrepMatch
from seekvfs.models import SearchHit
from seekvfs.models import SearchResult

try:
    from pyobvector import FtsIndexParam
    from pyobvector import FtsParser
    from pyobvector import ObVecClient
    from pyobvector import VECTOR
    from pyobvector import VecIndexType
    from pyobvector import cosine_distance
    from pyobvector import inner_product
    from pyobvector import l2_distance
    from pyobvector.schema import ReplaceStmt
    from sqlalchemy import BigInteger
    from sqlalchemy import Column
    from sqlalchemy import JSON
    from sqlalchemy import String
    from sqlalchemy import Table
    from sqlalchemy import bindparam
    from sqlalchemy import select
    from sqlalchemy import text
    from sqlalchemy.dialects.mysql import LONGTEXT
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "OceanBaseBackend requires pyobvector and sqlalchemy. "
        "Install with: pip install 'contextseek[oceanbase]'"
    ) from exc


logger = logging.getLogger(__name__)


_FTS_PARSER_MAP = {
    "ik": FtsParser.IK,
    "ngram": FtsParser.NGRAM,
    "ngram2": FtsParser.NGRAM2,
    "beng": FtsParser.BASIC_ENGLISH,
    "jieba": FtsParser.JIEBA,
    "space": None,
}


_SNOWFLAKE_LOCK = threading.Lock()
_SNOWFLAKE_SEQ = 0
_SNOWFLAKE_LAST = -1


def _snowflake_id() -> int:
    """Generate a 64-bit monotonic id (ms timestamp << 22 | 12-bit seq)."""
    global _SNOWFLAKE_SEQ, _SNOWFLAKE_LAST
    with _SNOWFLAKE_LOCK:
        ts = int(time.time() * 1000)
        if ts == _SNOWFLAKE_LAST:
            _SNOWFLAKE_SEQ = (_SNOWFLAKE_SEQ + 1) & 0xFFF
        else:
            _SNOWFLAKE_SEQ = 0
            _SNOWFLAKE_LAST = ts
        return (ts << 22) | _SNOWFLAKE_SEQ


def _namespace_of(ref: str) -> str:
    if "/" not in ref:
        return ref
    return ref.rsplit("/", 1)[0] + "/"


def _serialize_dt(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    if v is None:
        return ""
    return str(v)


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip through json to coerce datetimes (and other non-JSON types) safely."""

    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    return json.loads(json.dumps(payload, default=_default))


_HOISTED: frozenset[str] = frozenset({"embedding", "content", "abstract", "summary"})
"""Fields stored in dedicated columns; stripped from payload_json on write."""


def _parse_vector(v: Any) -> list[float] | None:
    """Coerce an OceanBase VECTOR column value to a Python list of floats.

    pyobvector's result_processor returns numpy ndarray; we convert it to a
    plain Python list so downstream code does not depend on numpy.
    """
    if v is None:
        return None
    if hasattr(v, "tolist"):
        try:
            return [float(x) for x in v.tolist()]
        except (TypeError, ValueError):
            return None
    if isinstance(v, list):
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None
    return None


def _merge_hoisted(
    payload_json: Any,
    content: str | None,
    abstract: str | None,
    summary: str | None,
    abstract_embedding: Any,
) -> dict[str, Any]:
    """Reconstruct a full payload dict by merging hoisted column values back in.

    ``payload_json`` is the slim JSON stored in the DB (hoisted fields stripped).
    The remaining args come from their dedicated columns and are merged back so
    callers get a complete payload identical to what was originally written.
    """
    if isinstance(payload_json, str):
        d: dict[str, Any] = json.loads(payload_json) if payload_json else {}
    elif isinstance(payload_json, dict):
        d = dict(payload_json)
    else:
        d = {}
    if content:
        try:
            d["content"] = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            d["content"] = content
    else:
        d["content"] = ""
    d["abstract"] = abstract or ""
    d["summary"] = summary or ""
    emb = _parse_vector(abstract_embedding)
    if emb is not None:
        d["embedding"] = emb
    else:
        d.pop("embedding", None)
    return d


def _distance_func(metric: str):
    metric = metric.lower()
    if metric == "l2":
        return l2_distance
    if metric == "cosine":
        return cosine_distance
    if metric == "inner_product":
        return inner_product
    raise ValueError(f"unsupported vidx_metric_type: {metric}")


def _dist_to_sim(dist: float, metric: str) -> float:
    metric = metric.lower()
    if metric == "l2":
        return 1.0 / (1.0 + dist)
    if metric == "cosine":
        return max(0.0, 1.0 - dist / 2.0)
    if metric == "inner_product":
        return max(0.0, min(1.0, (-dist + 1.0) / 2.0))
    return 0.0


def _safe_fetchall(result):
    if not getattr(result, "returns_rows", True):
        return []
    return result.fetchall()


def _fts_parser_enum(parser_name: str):
    key = parser_name.lower()
    if key not in _FTS_PARSER_MAP:
        raise ValueError(
            f"unsupported fulltext parser: {parser_name}. "
            f"supported: {', '.join(_FTS_PARSER_MAP.keys())}"
        )
    return _FTS_PARSER_MAP[key]


def _prefix_from_pattern(path_pattern: str | None) -> str | None:
    """Strip a single trailing ``*`` from a glob pattern to derive a prefix.

    ``seekvfs://ns/*`` → ``seekvfs://ns/``. ``None`` is passed through.
    """
    if path_pattern is None:
        return None
    return path_pattern.removesuffix("*")


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_updated_at(updated_at: Any) -> datetime:
    if not updated_at:
        return datetime.now(tz=UTC)
    try:
        dt = datetime.fromisoformat(str(updated_at))
    except Exception:
        return datetime.now(tz=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class OceanBaseBackend(BackendProtocol):
    """BackendProtocol implementation backed by OceanBase."""

    def __init__(
        self,
        table_name: str,
        vector_dims: int,
        *,
        host: str,
        port: str = "2881",
        user: str = "root@test",
        password: str = "",
        db_name: str = "test",
        vector_weight: float = 0.7,
        fts_weight: float = 0.3,
        vidx_metric_type: str = "l2",
        fulltext_parser: str = "ngram",
        rrf_k: int = 60,
        importance_alpha: float = 0.5,
        importance_floor: float = 0.1,
    ) -> None:
        self._table_name = table_name
        self._vector_dims = int(vector_dims)
        self._host = host
        self._port = str(port)
        self._user = user
        self._password = password
        self._db_name = db_name
        self._vector_weight = float(vector_weight)
        self._fts_weight = float(fts_weight)
        self._vidx_metric_type = vidx_metric_type.lower()
        self._fulltext_parser = fulltext_parser
        self._rrf_k = int(rrf_k)
        self._importance_alpha = float(importance_alpha)
        self._importance_floor = float(importance_floor)

        self._obvector: ObVecClient | None = None
        self._table: Table | None = None

    def initialize(self) -> None:
        if self._obvector is not None:
            return
        self._create_client()
        self._configure_vector_index_settings()
        self._create_table()
        self._validate_dims()

    def _create_client(self) -> None:
        self._obvector = ObVecClient(
            uri=f"{self._host}:{self._port}",
            user=self._user,
            password=self._password,
            db_name=self._db_name,
        )

    def _configure_vector_index_settings(self) -> None:
        assert self._obvector is not None
        try:
            with self._obvector.engine.connect() as conn:
                version_row = conn.execute(text("SELECT VERSION()")).fetchone()
                if version_row is None:
                    return
                version_str = str(version_row[0])
                m = re.search(r"OceanBase[^-]*-v(\d+)\.(\d+)\.(\d+)", version_str, re.I)
                if not m:
                    return
                major, minor, patch = (
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                )
                if (major, minor, patch) >= (4, 4, 1):
                    return
                result = conn.execute(
                    text("SHOW PARAMETERS LIKE 'ob_vector_memory_limit_percentage'")
                )
                if result.fetchone():
                    return
                conn.execute(
                    text("ALTER SYSTEM SET ob_vector_memory_limit_percentage = 30")
                )
                conn.commit()
        except Exception as exc:
            logger.warning(f"failed to configure vector index settings: {exc}")

    def _create_table(self) -> None:
        assert self._obvector is not None
        if self._obvector.check_table_exists(self._table_name):
            self._table = Table(
                self._table_name,
                self._obvector.metadata_obj,
                autoload_with=self._obvector.engine,
            )
            return

        columns = [
            Column("id", BigInteger, primary_key=True, autoincrement=False),
            Column("ref", String(1024), unique=True, nullable=False),
            Column("namespace", String(512), nullable=False),
            Column("content", LONGTEXT),
            Column("abstract", LONGTEXT),
            Column("summary", LONGTEXT),
            Column("abstract_embedding", VECTOR(self._vector_dims)),
            Column("fulltext_content", LONGTEXT),
            Column("payload_json", JSON),
            Column("created_at", String(64)),
            Column("updated_at", String(64)),
        ]

        vidx_params = self._obvector.prepare_index_params()
        vidx_params.add_index(
            field_name="abstract_embedding",
            index_type=VecIndexType.HNSW,
            index_name="vidx",
            metric_type=self._vidx_metric_type,
            params={"M": 16, "efConstruction": 200},
        )

        fts_idx = FtsIndexParam(
            index_name="ftsidx",
            field_names=["fulltext_content"],
            parser_type=_fts_parser_enum(self._fulltext_parser),
        )

        self._obvector.create_table_with_index_params(
            table_name=self._table_name,
            columns=columns,
            indexes=None,
            vidxs=vidx_params,
            fts_idxs=[fts_idx],
            partitions=None,
        )

        self._table = Table(
            self._table_name,
            self._obvector.metadata_obj,
            autoload_with=self._obvector.engine,
        )

    def _validate_dims(self) -> None:
        """Fail fast if the existing table's VECTOR column dimension differs from ours.

        Checks ``abstract_embedding`` first (new schema); falls back to ``embedding``
        for backward compatibility with tables created before the L0/L1/L2 split.
        """
        assert self._obvector is not None
        try:
            with self._obvector.engine.connect() as conn:
                rows = conn.execute(text(f"DESCRIBE `{self._table_name}`")).fetchall()
        except Exception as exc:
            logger.warning(f"dim validation skipped (DESCRIBE failed): {exc}")
            return
        for col in rows:
            name = col[0]
            col_type = str(col[1])
            if name not in ("abstract_embedding", "embedding"):
                continue
            m = re.match(r"VECTOR\((\d+)\)", col_type, re.I)
            if not m:
                return
            existing = int(m.group(1))
            if existing != self._vector_dims:
                raise ValueError(
                    f"vector dimension mismatch for table '{self._table_name}' "
                    f"column '{name}': existing={existing}, requested={self._vector_dims}. "
                    f"Use a different table_name or match the existing dims."
                )
            return

    def write(self, path: str, content: bytes | str) -> None:
        assert self._obvector is not None and self._table is not None
        raw = content if isinstance(content, str) else content.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(
                f"OceanBaseBackend expects a JSON-object payload, got {type(payload).__name__}"
            )
        abstract = str(payload.get("abstract") or "")
        summary = str(payload.get("summary") or "")
        raw_content = payload.get("content")
        text_content = (
            json.dumps(raw_content, ensure_ascii=False)
            if isinstance(raw_content, (dict, list))
            else str(raw_content or "")
        )
        abstract_emb = payload.get("embedding")
        # FTS surface: prefer abstract+summary (L0/L1 granularity); fall back to
        # full text when both are empty for backward compatibility.
        fulltext_content = (f"{abstract} {summary}").strip() or text_content

        namespace = _namespace_of(path)
        now = datetime.now(tz=UTC).isoformat()
        payload_slim = {k: v for k, v in payload.items() if k not in _HOISTED}
        record: dict[str, Any] = {
            "id": _snowflake_id(),
            "ref": path,
            "namespace": namespace,
            "content": text_content,
            "abstract": abstract,
            "summary": summary,
            "fulltext_content": fulltext_content,
            "payload_json": _json_safe(payload_slim),
            "created_at": _serialize_dt(payload.get("created_at")) or now,
            "updated_at": now,
        }
        if abstract_emb is not None:
            record["abstract_embedding"] = abstract_emb

        table = self._table

        with self._obvector.engine.connect() as conn:
            with conn.begin():
                # REPLACE INTO is DELETE + INSERT, which destroys the existing
                # abstract_embedding when none is provided in the payload (e.g.
                # during touch / access_count updates).  Preserve it by reading
                # the stored vector first whenever the caller omits an embedding.
                if abstract_emb is None:
                    existing = conn.execute(
                        select(table.c["abstract_embedding"])
                        .where(table.c["ref"] == path)
                        .limit(1)
                    ).fetchone()
                    has_existing_emb = existing is not None and existing[0] is not None
                    if has_existing_emb:
                        record["abstract_embedding"] = existing[0]
                conn.execute(ReplaceStmt(self._table).values([record]))

    def read(self, path: str, hint: str | None = None) -> FileData:
        assert self._obvector is not None and self._table is not None
        table = self._table
        stmt = (
            select(
                table.c["payload_json"],
                table.c["content"],
                table.c["abstract"],
                table.c["summary"],
                table.c["abstract_embedding"],
            )
            .where(table.c["ref"] == path)
            .limit(1)
        )
        with self._obvector.engine.connect() as conn:
            row = conn.execute(stmt).fetchone()
        if row is None:
            raise NotFoundError(path)
        payload_json, content, abstract, summary, abstract_embedding = row
        payload_dict = _merge_hoisted(
            payload_json, content, abstract, summary, abstract_embedding
        )
        return FileData(
            content=json.dumps(payload_dict, ensure_ascii=False).encode("utf-8"),
            encoding="utf-8",
        )

    def read_full(self, path: str) -> FileData:
        return self.read(path)

    def read_batch(self, paths: list[str]) -> dict[str, FileData]:
        assert self._obvector is not None and self._table is not None
        if not paths:
            return {}
        table = self._table
        stmt = select(
            table.c["ref"],
            table.c["payload_json"],
            table.c["content"],
            table.c["abstract"],
            table.c["summary"],
            table.c["abstract_embedding"],
        ).where(table.c["ref"].in_(paths))
        with self._obvector.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        out: dict[str, FileData] = {}
        for row in rows:
            ref, payload_json, content, abstract, summary, abstract_embedding = row
            payload_dict = _merge_hoisted(
                payload_json, content, abstract, summary, abstract_embedding
            )
            out[ref] = FileData(
                content=json.dumps(payload_dict, ensure_ascii=False).encode("utf-8"),
                encoding="utf-8",
            )
        return out

    def search(
        self,
        query: str,
        path_pattern: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        *,
        query_embedding: list[float] | None = None,
    ) -> SearchResult:
        assert self._obvector is not None and self._table is not None
        prefix = _prefix_from_pattern(path_pattern)
        candidate_k = max(limit * 3, 1)

        if query_embedding is not None:
            with ThreadPoolExecutor(max_workers=2) as pool:
                vec_fut = pool.submit(
                    self._vector_search, query_embedding, prefix, candidate_k
                )
                fts_fut = pool.submit(self._fulltext_search, query, prefix, candidate_k)
            return self._rrf_fusion(
                vec_fut.result(),
                fts_fut.result(),
                limit=limit,
                query=query,
                prefix=prefix,
                score_threshold=score_threshold,
            )

        # No vector path: FTS-only recall.
        fts_hits = self._fulltext_search(query, prefix, candidate_k)
        return self._rrf_fusion(
            [],
            fts_hits,
            limit=limit,
            query=query,
            prefix=prefix,
            score_threshold=score_threshold,
        )

    def _vector_search(
        self, emb: list[float], prefix: str | None, k: int
    ) -> list[dict[str, Any]]:
        assert self._obvector is not None and self._table is not None
        table = self._table
        where_clause = [table.c["namespace"].like(f"{prefix}%")] if prefix else []

        try:
            results = self._obvector.ann_search(
                table_name=self._table_name,
                vec_data=emb,
                vec_column_name="abstract_embedding",
                distance_func=_distance_func(self._vidx_metric_type),
                with_dist=True,
                topk=k,
                output_column_names=[
                    "id",
                    "ref",
                    "payload_json",
                    "content",
                    "abstract",
                    "summary",
                    "abstract_embedding",
                ],
                where_clause=where_clause,
            )
        except Exception as exc:
            logger.warning(f"vector search failed: {exc}")
            return []

        out: list[dict[str, Any]] = []
        for row in _safe_fetchall(results):
            mapping = row._mapping if hasattr(row, "_mapping") else row
            if "distance" in mapping:
                dist = mapping["distance"]
            elif "anon_1" in mapping:
                dist = mapping["anon_1"]
            else:
                dist = 0.0
            try:
                dist_f = float(dist) if dist is not None else 0.0
            except (TypeError, ValueError):
                dist_f = 0.0
            sim = _dist_to_sim(dist_f, self._vidx_metric_type)
            out.append(
                {
                    "_db_id": mapping["id"],
                    "ref": mapping["ref"],
                    "_vec_score": sim,
                    "payload_json": mapping["payload_json"],
                    "_content": mapping.get("content") or "",
                    "_abstract": mapping.get("abstract") or "",
                    "_summary": mapping.get("summary") or "",
                    "_abstract_embedding": mapping.get("abstract_embedding"),
                }
            )
        return out

    def _fulltext_search(
        self, query: str, prefix: str | None, k: int
    ) -> list[dict[str, Any]]:
        assert self._obvector is not None and self._table is not None
        if not query.strip():
            return []

        table = self._table
        ns_cond = table.c["namespace"].like(f"{prefix}%") if prefix else None

        fts_where_expr = text(
            "MATCH(fulltext_content) AGAINST(:q_where IN NATURAL LANGUAGE MODE)"
        ).bindparams(bindparam("q_where", query))
        fts_score_expr = text(
            "MATCH(fulltext_content) AGAINST(:q_score IN NATURAL LANGUAGE MODE) AS fts_score"
        ).bindparams(bindparam("q_score", query))

        stmt = select(
            table.c["id"],
            table.c["ref"],
            table.c["payload_json"],
            table.c["content"],
            table.c["abstract"],
            table.c["summary"],
            table.c["abstract_embedding"],
            fts_score_expr,
        ).where(fts_where_expr)
        if ns_cond is not None:
            stmt = stmt.where(ns_cond)
        stmt = stmt.order_by(text("fts_score DESC")).limit(k)

        try:
            with self._obvector.engine.connect() as conn:
                with conn.begin():
                    rows = [dict(r._mapping) for r in conn.execute(stmt)]
        except Exception as exc:
            logger.warning(f"FTS failed, fallback to LIKE: {exc}")
            like_stmt = select(
                table.c["id"],
                table.c["ref"],
                table.c["payload_json"],
                table.c["content"],
                table.c["abstract"],
                table.c["summary"],
                table.c["abstract_embedding"],
            ).where(
                table.c["fulltext_content"].like(
                    f"%{_escape_like(query)}%", escape="\\"
                )
            )
            if ns_cond is not None:
                like_stmt = like_stmt.where(ns_cond)
            like_stmt = like_stmt.limit(k)
            try:
                with self._obvector.engine.connect() as conn:
                    with conn.begin():
                        rows = [
                            dict(r._mapping) | {"fts_score": 1.0}
                            for r in conn.execute(like_stmt)
                        ]
            except Exception as fallback_exc:
                logger.error(f"LIKE fallback also failed: {fallback_exc}")
                return []

        return [
            {
                "_db_id": row["id"],
                "ref": row["ref"],
                "_fts_score": float(row["fts_score"]) if "fts_score" in row else 0.0,
                "payload_json": row["payload_json"],
                "_content": row.get("content") or "",
                "_abstract": row.get("abstract") or "",
                "_summary": row.get("summary") or "",
                "_abstract_embedding": row.get("abstract_embedding"),
            }
            for row in rows
        ]

    def _rrf_fusion(
        self,
        vec_hits: list[dict[str, Any]],
        fts_hits: list[dict[str, Any]],
        *,
        limit: int,
        query: str,
        prefix: str | None,
        score_threshold: float | None,
    ) -> SearchResult:
        k = self._rrf_k
        all_docs: dict[Any, dict[str, Any]] = {}

        for rank, hit in enumerate(vec_hits, 1):
            doc_id = hit["_db_id"]
            all_docs[doc_id] = {
                **hit,
                "_vec_rank": rank,
                "_fts_rank": None,
                "_rrf": 0.0,
            }

        for rank, hit in enumerate(fts_hits, 1):
            doc_id = hit["_db_id"]
            if doc_id in all_docs:
                all_docs[doc_id]["_fts_rank"] = rank
            else:
                all_docs[doc_id] = {
                    **hit,
                    "_vec_rank": None,
                    "_fts_rank": rank,
                    "_rrf": 0.0,
                }

        # Adaptive weight normalization (per-document fairness, à la powermem):
        # Re-normalize each document's weights to sum to 1.0 based on how many
        # retrieval paths actually returned it. This ensures vector-only and
        # fts-only items are scored on the same scale as combined items,
        # avoiding structural advantage from accumulating multiple path weights.
        for doc in all_docs.values():
            active: list[tuple[float, int]] = []  # (weight, rank)
            if doc["_vec_rank"] is not None:
                active.append((self._vector_weight, doc["_vec_rank"]))
            if doc["_fts_rank"] is not None:
                active.append((self._fts_weight, doc["_fts_rank"]))
            if not active:
                continue
            total_w = sum(w for w, _ in active)
            if total_w <= 0:
                continue
            doc["_rrf"] = sum((w / total_w) * (1.0 / (k + r)) for w, r in active)

        def _parse_importance(doc: dict[str, Any]) -> float:
            pj = doc.get("payload_json")
            if isinstance(pj, str):
                try:
                    pj = json.loads(pj)
                except (json.JSONDecodeError, TypeError):
                    pj = {}
            elif not isinstance(pj, dict):
                pj = {}
            try:
                imp = float(pj.get("importance") or 1.0)
            except (TypeError, ValueError):
                imp = 1.0
            return max(imp, self._importance_floor)

        # Sort by importance-adjusted _rrf so that lower-importance items are
        # deprioritized at the recall phase (before the :limit cut).  We store the
        # adjusted key separately to avoid corrupting the raw _rrf used for
        # normalization below.
        if self._importance_alpha > 0.0:
            for doc in all_docs.values():
                imp = _parse_importance(doc)
                doc["_sort_key"] = doc["_rrf"] * (imp**self._importance_alpha)
        else:
            for doc in all_docs.values():
                doc["_sort_key"] = doc["_rrf"]

        ranked = sorted(all_docs.values(), key=lambda x: x["_sort_key"], reverse=True)[
            :limit
        ]

        # Batch-level max normalization on raw _rrf (importance-independent) so
        # the top semantically-relevant item anchors the scale at 1.0.
        max_rrf = float(ranked[0]["_rrf"]) if ranked else 1.0
        if max_rrf <= 0.0:
            max_rrf = 1.0

        hits: list[SearchHit] = []
        for doc in ranked:
            norm = float(doc["_rrf"]) / max_rrf
            # For vector-only items (no FTS/phrase match), multiply by the actual
            # cosine similarity to preserve absolute semantic quality signal.
            if doc["_fts_rank"] is None and doc["_vec_rank"] is not None:
                vec_sim = float(doc.get("_vec_score", 1.0))
                norm = norm * vec_sim
            score = round(norm, 6)
            if score_threshold is not None and score < score_threshold:
                continue
            payload_dict = _merge_hoisted(
                doc.get("payload_json"),
                doc.get("_content"),
                doc.get("_abstract"),
                doc.get("_summary"),
                doc.get("_abstract_embedding"),
            )
            snippet = json.dumps(payload_dict, ensure_ascii=False)
            hits.append(SearchHit(path=doc["ref"], snippet=snippet, score=score))
        return SearchResult(query=query, hits=hits, searched_paths=[prefix or ""])

    def ls(
        self,
        path: str,
        pattern: str | None = None,
        recursive: bool = False,
    ) -> list[FileInfo]:
        assert self._obvector is not None and self._table is not None
        prefix = path if path.endswith("/") else path + "/"
        table = self._table
        stmt = select(table.c["ref"], table.c["updated_at"]).where(
            table.c["ref"].like(f"{prefix}%")
        )
        with self._obvector.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()

        out: list[FileInfo] = []
        for row in rows:
            ref = row[0]
            updated_at = row[1]
            rel = ref[len(prefix) :]
            if not recursive and "/" in rel:
                continue
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            out.append(
                FileInfo(
                    path=ref,
                    size=0,
                    mtime=_parse_updated_at(updated_at),
                    is_dir=False,
                )
            )
        out.sort(key=lambda fi: fi.path)
        return out

    def edit(
        self,
        path: str,
        old: str,
        new: str,
        *,
        new_embedding: list[float] | None = None,
    ) -> int:
        assert self._obvector is not None and self._table is not None
        table = self._table
        stmt = (
            select(table.c["content"], table.c["payload_json"])
            .where(table.c["ref"] == path)
            .limit(1)
        )
        with self._obvector.engine.connect() as conn:
            row = conn.execute(stmt).fetchone()
        if row is None:
            raise NotFoundError(path)
        current = row[0] or ""
        count = current.count(old)
        if count == 0:
            return 0
        new_content = current.replace(old, new)
        payload = row[1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload = {k: v for k, v in payload.items() if k not in _HOISTED}
        now = datetime.now(tz=UTC).isoformat()
        update_values: dict[str, Any] = {
            "content": new_content,
            "fulltext_content": new_content,
            "payload_json": _json_safe(payload),
            "updated_at": now,
        }
        if new_embedding is not None:
            update_values["abstract_embedding"] = new_embedding
        update_stmt = (
            table.update().where(table.c["ref"] == path).values(**update_values)
        )
        with self._obvector.engine.connect() as conn:
            with conn.begin():
                conn.execute(update_stmt)
        return count

    def grep(
        self,
        pattern: str,
        path_pattern: str | None = None,
    ) -> list[GrepMatch]:
        assert self._obvector is not None and self._table is not None
        prefix = _prefix_from_pattern(path_pattern)
        table = self._table
        stmt = select(table.c["ref"], table.c["content"])
        if prefix:
            stmt = stmt.where(table.c["ref"].like(f"{prefix}%"))
        stmt = stmt.where(
            table.c["content"].like(f"%{_escape_like(pattern)}%", escape="\\")
        )
        with self._obvector.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        out: list[GrepMatch] = []
        for row in rows:
            ref = row[0]
            content = row[1] or ""
            for idx, line in enumerate(content.splitlines(), start=1):
                if pattern in line:
                    out.append(GrepMatch(path=ref, line_number=idx, line=line))
        return out

    def delete(self, path: str) -> None:
        assert self._obvector is not None and self._table is not None
        stmt = self._table.delete().where(self._table.c["ref"] == path)
        with self._obvector.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(stmt)
        if result.rowcount == 0:
            raise NotFoundError(path)

    def find_by_hash(self, path_pattern: str | None, hash_value: str) -> str | None:
        """Return the ref of an item whose ``payload_json.hash`` equals *hash_value*.

        Reads from ``payload_json`` via ``JSON_EXTRACT`` because ``hash`` is not
        hoisted to a dedicated column. For scopes with a small candidate set
        this is fast even without a dedicated index; for very large scopes a
        functional index on ``JSON_EXTRACT(payload_json, '$.hash')`` can be
        added externally.
        """
        if not hash_value:
            return None
        assert self._obvector is not None and self._table is not None
        prefix = _prefix_from_pattern(path_pattern)
        table = self._table
        hash_expr = text(
            "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.hash')) = :h"
        ).bindparams(bindparam("h", hash_value))
        stmt = select(table.c["ref"]).where(hash_expr)
        if prefix:
            stmt = stmt.where(table.c["namespace"].like(f"{prefix}%"))
        stmt = stmt.limit(1)
        try:
            with self._obvector.engine.connect() as conn:
                row = conn.execute(stmt).fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"find_by_hash failed: {exc}")
            return None
        if row is None:
            return None
        return str(row[0])

    def close(self) -> None:
        """Dispose the underlying SQLAlchemy engine connection pool.

        ``ObVecClient`` has no ``close()`` method; disposing the engine is the
        standard SQLAlchemy way to release pooled connections.
        """
        if self._obvector is not None:
            try:
                self._obvector.engine.dispose()
            except Exception:
                pass
            self._obvector = None


__all__ = ["OceanBaseBackend"]
