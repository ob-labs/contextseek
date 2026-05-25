"""OceanBase-backed GIS extension for contextseek.

Extends :class:`OceanBaseBackend` with a separate geo index table
(``contextseek_geo``) that stores spatial data alongside the
existing main table.

Architecture
------------
- Main table (``contextseek_items``) – unchanged; all existing operations.
- Geo table (``contextseek_geo``) – new; managed by this class.
  - ``location POINT NOT NULL SRID 4326`` + SPATIAL INDEX (QuadTree).
  - ``geo_shape GEOMETRY NULL`` for polygons / linestrings (no spatial index).
  - Only ContextItems whose ``content["geo"]`` contains ``lat`` + ``lon``
    get a geo table row; all others are invisible to geo queries.

Query patterns
--------------
- Radius: two-step (MBRContains → spatial index, HAVING ST_Distance_Sphere).
- Polygon: ST_Contains on ``geo_shape`` or ``location``.
- Route corridor: keypoint decomposition (ST_Buffer unavailable on SRID 4326).
- Zone gate: ``is_point_within_zone()`` – hard constraint check, not retrieval ranking.

Requirements: OceanBase >= 4.2.2 (``_ST_MakeEnvelope`` introduced there).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from contextseek.domain.geo import (
    GeoMetadata,
    GeoPoint,
    GeoQuery,
    dist_to_geo_sim,
    sample_linestring_points,
)
from contextseek.storage.ob_backend import OceanBaseBackend

try:
    from sqlalchemy import text
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "OceanBaseGeoBackend requires pyobvector and sqlalchemy. "
        "Install with: pip install 'contextseek[oceanbase]'"
    ) from exc


logger = logging.getLogger(__name__)

_GEO_TABLE_COLUMNS = """
    ref        VARCHAR(1024) NOT NULL,
    namespace  VARCHAR(512)  NOT NULL,
    location   POINT         NOT NULL SRID 4326,
    geo_type   VARCHAR(32)   NULL,
    geo_shape  GEOMETRY      NULL
"""

_GEO_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS `{table}` (
    ref        VARCHAR(1024)  NOT NULL,
    namespace  VARCHAR(512)   NOT NULL,
    location   POINT          NOT NULL SRID 4326,
    geo_type   VARCHAR(32)    NULL,
    geo_shape  GEOMETRY       NULL,
    SPATIAL INDEX sidx_location (location),
    INDEX idx_ref       (ref(255)),
    INDEX idx_namespace (namespace(255)),
    INDEX idx_geo_type  (geo_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_MIN_VERSION = (4, 2, 2)


class OceanBaseGeoBackend(OceanBaseBackend):
    """OceanBaseBackend with GIS support via a separate geo index table.

    Pass ``geo_table_name`` to customise the geo table name (defaults to
    ``contextseek_geo``).  All other constructor parameters are
    forwarded to :class:`OceanBaseBackend`.
    """

    def __init__(
        self,
        *args: Any,
        geo_table_name: str = "contextseek_geo",
        distance_decay_km: float = 1.0,
        route_sample_interval_km: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._geo_table_name = geo_table_name
        self._distance_decay_km = distance_decay_km
        self._route_sample_interval_km = route_sample_interval_km

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def initialize(self) -> None:
        super().initialize()
        self._check_ob_version()
        self._create_geo_table()

    def _check_ob_version(self) -> None:
        assert self._obvector is not None
        try:
            with self._obvector.engine.connect() as conn:
                row = conn.execute(text("SELECT VERSION()")).fetchone()
        except Exception as exc:
            logger.warning("GIS version check failed: %s", exc)
            return
        if row is None:
            return
        m = re.search(r"OceanBase[^-]*-v(\d+)\.(\d+)\.(\d+)", str(row[0]), re.I)
        if not m:
            return
        ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if ver < _MIN_VERSION:
            raise RuntimeError(
                f"OceanBase GIS requires version >= {'.'.join(str(v) for v in _MIN_VERSION)}, "
                f"found {'.'.join(str(v) for v in ver)}. "
                "Upgrade OceanBase or set GEO_ENABLED=false."
            )

    def _create_geo_table(self) -> None:
        assert self._obvector is not None
        ddl = _GEO_TABLE_DDL.format(table=self._geo_table_name)
        try:
            with self._obvector.engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(ddl))
        except Exception as exc:
            logger.error("Failed to create geo table %r: %s", self._geo_table_name, exc)
            raise

    # ------------------------------------------------------------------ #
    # Write / Delete overrides                                             #
    # ------------------------------------------------------------------ #

    def write(self, path: str, content: bytes | str) -> None:
        super().write(path, content)
        raw = content if isinstance(content, str) else content.decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        geo_meta = _extract_geo(payload)
        if geo_meta is not None:
            self._upsert_geo_index(path, geo_meta)

    def delete(self, path: str) -> None:
        super().delete(path)
        self._delete_geo_index(path)

    # ------------------------------------------------------------------ #
    # Geo index table management                                           #
    # ------------------------------------------------------------------ #

    def _upsert_geo_index(self, ref: str, geo: GeoMetadata) -> None:
        assert self._obvector is not None
        namespace = _namespace_of(ref)
        point_wkt = f"POINT({geo.lat} {geo.lon})"

        if geo.geo_wkt:
            shape_expr = f"ST_GeomFromText('{_escape_wkt(geo.geo_wkt)}', 4326)"
        else:
            shape_expr = "NULL"

        delete_sql = text(f"DELETE FROM `{self._geo_table_name}` WHERE ref = :ref")
        insert_sql = text(f"""
            INSERT INTO `{self._geo_table_name}`
                (ref, namespace, location, geo_type, geo_shape)
            VALUES (
                :ref,
                :namespace,
                ST_GeomFromText(:point_wkt, 4326),
                :geo_type,
                {shape_expr}
            )
        """)
        try:
            with self._obvector.engine.connect() as conn:
                with conn.begin():
                    conn.execute(delete_sql, {"ref": ref})
                    conn.execute(
                        insert_sql,
                        {
                            "ref": ref,
                            "namespace": namespace,
                            "point_wkt": point_wkt,
                            "geo_type": geo.geo_type,
                        },
                    )
        except Exception as exc:
            logger.warning("geo_index upsert failed for %r: %s", ref, exc)

    def _delete_geo_index(self, ref: str) -> None:
        assert self._obvector is not None
        sql = text(f"DELETE FROM `{self._geo_table_name}` WHERE ref = :ref")
        try:
            with self._obvector.engine.connect() as conn:
                with conn.begin():
                    conn.execute(sql, {"ref": ref})
        except Exception as exc:
            logger.warning("geo_index delete failed for %r: %s", ref, exc)

    # ------------------------------------------------------------------ #
    # Public geo query API                                                 #
    # ------------------------------------------------------------------ #

    def geo_search(
        self,
        geo_query: GeoQuery,
        *,
        prefix: str | None = None,
        k: int = 60,
    ) -> list[dict[str, Any]]:
        """Execute a spatial query and return full payload dicts.

        Dispatches to one of three strategies based on the active query mode:
        - ``radius``  → bounding-box pre-filter + ST_Distance_Sphere refinement
        - ``polygon`` → ST_Contains
        - ``route``   → keypoint decomposition + radius union
        """
        mode = geo_query.active_mode()
        if mode == "radius":
            hits = self._geo_radius_search(geo_query, prefix, k)
        elif mode == "polygon":
            hits = self._geo_polygon_search(geo_query, prefix, k)
        elif mode == "route":
            hits = self._geo_route_search(geo_query, prefix, k)
        else:
            return []
        return self._enrich_with_payloads(hits, decay_km=self._distance_decay_km)

    def is_point_within_zone(
        self,
        point: GeoPoint,
        *,
        zone_type: str,
        scope: str,
    ) -> bool:
        """Return True if *point* lies inside any polygon of *zone_type* within *scope*.

        Uses ``ST_Contains`` on the ``geo_shape`` column (polygon geometry).
        This is a hard-constraint check — not part of the retrieval ranking pipeline.

        Args:
            point: The coordinate to test.
            zone_type: The ``geo_type`` value of the zone polygons to test against
                (e.g. ``"geofence"``, ``"restricted_area"``).
            scope: Namespace prefix to limit which zone items are considered.
        """
        assert self._obvector is not None
        ns_filter = scope if scope.endswith("/") else scope + "/"
        point_wkt = point.to_wkt()
        sql = text(f"""
            SELECT COUNT(*) AS cnt
            FROM `{self._geo_table_name}`
            WHERE geo_type = :zone_type
              AND namespace LIKE :ns_like
              AND geo_shape IS NOT NULL
              AND ST_Contains(geo_shape, ST_GeomFromText(:point_wkt, 4326))
        """)
        try:
            with self._obvector.engine.connect() as conn:
                row = conn.execute(
                    sql,
                    {
                        "zone_type": zone_type,
                        "ns_like": f"{ns_filter}%",
                        "point_wkt": point_wkt,
                    },
                ).fetchone()
            return (row[0] if row else 0) > 0
        except Exception as exc:
            logger.warning("is_point_within_zone failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Internal search strategies                                           #
    # ------------------------------------------------------------------ #

    def _geo_radius_search(
        self,
        geo_query: GeoQuery,
        prefix: str | None,
        k: int,
    ) -> list[dict[str, Any]]:
        """Radius search using ST_Distance_Sphere for exact distance filtering."""
        assert self._obvector is not None
        assert geo_query.center is not None

        center = geo_query.center
        radius_m = geo_query.radius_km * 1000.0
        point_wkt = center.to_wkt()

        type_clause, type_params = _build_type_clause(geo_query.geo_type_filter)
        ns_clause, ns_params = _build_ns_clause(prefix)

        sql = text(f"""
            SELECT
                ref,
                geo_type,
                ST_Distance_Sphere(
                    location,
                    ST_GeomFromText(:point_wkt, 4326)
                ) AS dist_m
            FROM `{self._geo_table_name}`
            WHERE 1=1
            {ns_clause}
            {type_clause}
            HAVING dist_m <= :radius_m
            ORDER BY dist_m
            LIMIT :k
        """)
        params: dict[str, Any] = {
            "point_wkt": point_wkt,
            "radius_m": radius_m,
            "k": k,
            **ns_params,
            **type_params,
        }
        return self._execute_geo_sql(sql, params)

    def _geo_polygon_search(
        self,
        geo_query: GeoQuery,
        prefix: str | None,
        k: int,
    ) -> list[dict[str, Any]]:
        """ST_Contains polygon containment search."""
        assert self._obvector is not None
        assert geo_query.polygon_wkt is not None

        type_clause, type_params = _build_type_clause(geo_query.geo_type_filter)
        ns_clause, ns_params = _build_ns_clause(prefix)

        sql = text(f"""
            SELECT ref, geo_type, 0.0 AS dist_m
            FROM `{self._geo_table_name}`
            WHERE ST_Contains(
                ST_GeomFromText(:polygon_wkt, 4326),
                location
            )
            {ns_clause}
            {type_clause}
            LIMIT :k
        """)
        params: dict[str, Any] = {
            "polygon_wkt": geo_query.polygon_wkt,
            "k": k,
            **ns_params,
            **type_params,
        }
        return self._execute_geo_sql(sql, params)

    def _geo_route_search(
        self,
        geo_query: GeoQuery,
        prefix: str | None,
        k: int,
    ) -> list[dict[str, Any]]:
        """Route corridor search via keypoint decomposition.

        ST_Buffer is unavailable on SRID 4326 in OceanBase/MySQL, so we
        decompose the route into sample points and union radius searches.
        """
        assert geo_query.route_wkt is not None

        interval_km = max(
            self._route_sample_interval_km,
            geo_query.buffer_km / 4.0,
        )
        keypoints = sample_linestring_points(geo_query.route_wkt, interval_km)
        if not keypoints:
            return []

        per_point_k = max(k // max(len(keypoints), 1) + 5, 10)
        seen: dict[str, dict[str, Any]] = {}

        for pt in keypoints:
            sub_query = GeoQuery(
                center=pt,
                radius_km=geo_query.buffer_km,
                geo_type_filter=geo_query.geo_type_filter,
            )
            hits = self._geo_radius_search(sub_query, prefix, per_point_k)
            for h in hits:
                ref = h["ref"]
                if ref not in seen or h["dist_m"] < seen[ref]["dist_m"]:
                    seen[ref] = h

        return sorted(seen.values(), key=lambda x: x["dist_m"])[:k]

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _execute_geo_sql(
        self,
        sql: Any,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute a geo query and return raw (ref, geo_type, dist_m) rows."""
        assert self._obvector is not None
        try:
            with self._obvector.engine.connect() as conn:
                with conn.begin():
                    rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            logger.warning("geo SQL failed: %s", exc)
            return []
        return [
            {
                "ref": str(row[0]),
                "geo_type": str(row[1]) if row[1] else "",
                "dist_m": float(row[2]) if row[2] is not None else 0.0,
            }
            for row in rows
        ]

    def _enrich_with_payloads(
        self,
        geo_hits: list[dict[str, Any]],
        *,
        decay_km: float,
    ) -> list[dict[str, Any]]:
        """Fetch full payloads from main table and merge with geo scores.

        The geo score replaces ``score`` so the retrieval orchestrator can
        use it in RRF fusion alongside phrase / vector scores.
        """
        if not geo_hits:
            return []

        refs = [h["ref"] for h in geo_hits]
        dist_by_ref = {h["ref"]: h["dist_m"] for h in geo_hits}

        try:
            batch = self.read_batch(refs)
        except Exception as exc:
            logger.warning("read_batch failed in geo enrichment: %s", exc)
            return []

        out: list[dict[str, Any]] = []
        for ref in refs:
            fd = batch.get(ref)
            if fd is None:
                continue
            try:
                payload: dict[str, Any] = json.loads(fd.content)
            except Exception:
                continue
            dist_m = dist_by_ref.get(ref, 0.0)
            payload["ref"] = ref
            payload["score"] = dist_to_geo_sim(dist_m, decay_km)
            payload["_geo_dist_m"] = dist_m
            payload["_geo_type"] = dist_by_ref.get(ref, "")
            out.append(payload)
        return out


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_geo(payload: dict[str, Any]) -> GeoMetadata | None:
    """Extract GeoMetadata from a ContextItem payload dict."""
    content = payload.get("content", {})
    if not isinstance(content, dict):
        return None
    return GeoMetadata.from_content(content)


def _namespace_of(ref: str) -> str:
    if "/" not in ref:
        return ref
    return ref.rsplit("/", 1)[0] + "/"


def _escape_wkt(wkt: str) -> str:
    """Minimal WKT escaping for embedding in SQL strings.

    Only single-quotes are escaped; WKT normally contains no other
    SQL-special characters.
    """
    return wkt.replace("'", "''")


def _build_ns_clause(prefix: str | None) -> tuple[str, dict[str, Any]]:
    if not prefix:
        return "", {}
    ns_like = prefix if prefix.endswith("/") else prefix + "/"
    return "AND namespace LIKE :ns_like", {"ns_like": f"{ns_like}%"}


def _build_type_clause(
    geo_type_filter: list[str] | None,
) -> tuple[str, dict[str, Any]]:
    if not geo_type_filter:
        return "", {}
    placeholders = ", ".join(f":gt_{i}" for i in range(len(geo_type_filter)))
    params = {f"gt_{i}": v for i, v in enumerate(geo_type_filter)}
    return f"AND geo_type IN ({placeholders})", params


__all__ = ["OceanBaseGeoBackend"]
