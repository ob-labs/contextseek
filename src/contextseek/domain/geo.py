"""GIS domain types for ContextSeek.

Provides coordinate primitives and query descriptors used by
OceanBaseGeoBackend and the geo recall route.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GeoPoint:
    """WGS84 geographic coordinate (SRID 4326)."""

    lat: float
    """Latitude in [-90, 90]."""

    lon: float
    """Longitude in [-180, 180]."""

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"latitude out of range: {self.lat}")
        if not (-180 <= self.lon <= 180):
            raise ValueError(f"longitude out of range: {self.lon}")

    def to_wkt(self) -> str:
        """Return WKT POINT string in lat-lon order required by OceanBase SRID 4326."""
        return f"POINT({self.lat} {self.lon})"

    def bounding_box(self, radius_km: float) -> tuple[float, float, float, float]:
        """Return an approximate (min_lon, min_lat, max_lon, max_lat) bounding box.

        Uses 1 degree latitude ≈ 111 km; longitude shrinks by cos(lat).
        Accurate enough for radius < 500 km.
        """
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * max(math.cos(math.radians(self.lat)), 1e-6))
        return (
            self.lon - lon_delta,
            self.lat - lat_delta,
            self.lon + lon_delta,
            self.lat + lat_delta,
        )

    def distance_km(self, other: GeoPoint) -> float:
        """Haversine great-circle distance in kilometres."""
        r = 6371.0
        d_lat = math.radians(other.lat - self.lat)
        d_lon = math.radians(other.lon - self.lon)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(self.lat))
            * math.cos(math.radians(other.lat))
            * math.sin(d_lon / 2) ** 2
        )
        return r * 2 * math.asin(math.sqrt(a))


@dataclass
class GeoMetadata:
    """Geo metadata attached to a ContextItem's content dict.

    Place this under ``content["geo"]`` (``lat`` and ``lon`` are required).
    ``OceanBaseGeoBackend`` extracts it automatically and writes a row to the
    geo index table.

    Example::

        ContextItem(
            content={
                "name": "Central Airport T3",
                "geo": {"lat": 40.0799, "lon": 116.6031},
                "geo_type": "poi",
                "geo_wkt": None,   # fill for polygon / linestring shapes
            },
            ...
        )
    """

    lat: float
    lon: float
    geo_type: str = "poi"
    """Free-form geometry category label stored in the geo index.

    The framework treats this as an opaque string — any value is valid.
    Common examples: ``poi``, ``zone``, ``route``, ``area``.
    """

    geo_wkt: str | None = None
    """Full geometry as WKT. Fill for polygon / linestring shapes;
    leave ``None`` for point geometries (lat/lon is used directly)."""

    address: str | None = None
    region_code: str | None = None
    """Administrative region code (format depends on the data provider)."""

    altitude_m: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_content(cls, content: dict) -> GeoMetadata | None:
        """Extract GeoMetadata from a ContextItem content dict, or return None."""
        geo = content.get("geo")
        if not isinstance(geo, dict):
            return None
        lat = geo.get("lat")
        lon = geo.get("lon")
        if lat is None or lon is None:
            return None
        try:
            # Accept geo_type / geo_shape nested inside content["geo"] as well as top-level
            geo_type = geo.get("geo_type") or content.get("geo_type", "poi")
            geo_wkt = content.get("geo_wkt") or geo.get("geo_shape")
            return cls(
                lat=float(lat),
                lon=float(lon),
                geo_type=str(geo_type),
                geo_wkt=geo_wkt,
                address=content.get("address"),
                region_code=content.get("region_code"),
                altitude_m=content.get("altitude_m"),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class GeoQuery:
    """Spatial query parameters passed to ``cs.retrieve(geo_query=...)``.

    Three mutually exclusive query modes (use exactly one per call):

    - **Radius**: ``center`` + ``radius_km``
    - **Polygon containment**: ``polygon_wkt``
    - **Route corridor**: ``route_wkt`` + ``buffer_km``
    """

    # Mode 1: radius search
    center: GeoPoint | None = None
    radius_km: float = 5.0

    # Mode 2: polygon containment
    polygon_wkt: str | None = None

    # Mode 3: route corridor
    route_wkt: str | None = None
    buffer_km: float = 1.0

    # Common filter
    geo_type_filter: list[str] | None = None
    """Return only items matching these geo_type values. None = no filter."""

    # Score weight override
    geo_weight: float | None = None
    """Override the global GEO_GEO_WEIGHT for this query."""

    def active_mode(self) -> str:
        """Return the active query mode: ``"radius"``, ``"polygon"``, ``"route"``, or ``"none"``."""
        if self.center is not None:
            return "radius"
        if self.polygon_wkt is not None:
            return "polygon"
        if self.route_wkt is not None:
            return "route"
        return "none"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_linestring_points(wkt: str) -> list[GeoPoint]:
    """Extract all vertices from a WKT LINESTRING.

    Format: ``LINESTRING(lat1 lon1, lat2 lon2, ...)`` (SRID 4326 axis order).
    """
    body = re.search(r"LINESTRING\s*\(([^)]+)\)", wkt, re.I)
    if body is None:
        return []
    points: list[GeoPoint] = []
    for pair in body.group(1).split(","):
        parts = pair.strip().split()
        if len(parts) >= 2:
            try:
                points.append(GeoPoint(lat=float(parts[0]), lon=float(parts[1])))
            except (ValueError, IndexError):
                continue
    return points


def sample_linestring_points(wkt: str, interval_km: float) -> list[GeoPoint]:
    """Sample key points along a LineString at *interval_km* spacing.

    Adjacent vertices closer than *interval_km* are merged (first point wins).
    The last vertex is always included to ensure full coverage.

    Used as a workaround when ``ST_Buffer`` is unavailable on SRID 4326.
    """
    vertices = _parse_linestring_points(wkt)
    if not vertices:
        return []

    sampled: list[GeoPoint] = [vertices[0]]
    accumulated = 0.0

    for i in range(1, len(vertices)):
        seg_km = vertices[i - 1].distance_km(vertices[i])
        accumulated += seg_km
        if accumulated >= interval_km:
            sampled.append(vertices[i])
            accumulated = 0.0

    # Always include the terminal vertex
    if sampled[-1] is not vertices[-1]:
        sampled.append(vertices[-1])

    return sampled


def dist_to_geo_sim(dist_m: float, decay_km: float = 1.0) -> float:
    """Convert a distance in metres to a [0, 1] similarity score for RRF fusion.

    Score is 1.0 at distance 0 and halves every *decay_km* kilometres.
    """
    return 1.0 / (1.0 + dist_m / (decay_km * 1000.0))


__all__ = [
    "GeoPoint",
    "GeoMetadata",
    "GeoQuery",
    "dist_to_geo_sim",
    "sample_linestring_points",
]

# Register GIS-domain source types when this module is imported.
# Any module that needs these types should import from contextseek.domain.geo
# (or contextseek.storage.ob_geo_backend, which already does so).
from contextseek.domain.provenance import register_source_type as _reg  # noqa: E402

_reg("sensor_fusion", confidence=0.88)  # multi-sensor fusion (LIDAR / camera / radar)
_reg("v2x_message", confidence=0.80)  # vehicle-to-everything broadcast
_reg("hd_map_provider", confidence=0.95)  # HD map vendor data
_reg("iot_telemetry", confidence=0.92)  # IoT sensor readings
_reg("fleet_telemetry", confidence=0.90)  # fleet management system reports

del _reg
