"""Decay and archival policies for ContextItem lifecycle management.

Items decay based on their Stability level:
- ephemeral: fast decay, archived after TTL
- transient: normal decay (half-life based)
- stable: very slow decay
- permanent: no decay

Decay manifests as reduced `importance` score which affects retrieval ranking.
Items below the archive threshold become non-searchable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


from contextseek.domain.context_item import ContextItem
from contextseek.domain.stages import Stability


@dataclass(frozen=True)
class DecayConfig:
    """Configuration for the decay engine."""

    half_life_days: float = 7.0
    """Half-life in days for transient items."""

    ephemeral_ttl_seconds: float = 3600.0
    """TTL in seconds for ephemeral items (archived after this)."""

    stable_half_life_multiplier: float = 10.0
    """Stable items decay this many times slower than transient."""

    archive_threshold: float = 0.1
    """Items below this importance are archived (searchable=False)."""

    access_boost_factor: float = 0.02
    """Each access adds this much anti-decay bonus."""

    dream_decay_multiplier: float = 3.0
    """Dream items decay this many times faster (unless accessed)."""


@dataclass(frozen=True)
class DecayResult:
    """Result of applying decay to a set of items."""

    decayed_count: int
    archived_count: int
    details: dict[str, Any]


def compute_decay(
    item: ContextItem,
    *,
    now: datetime | None = None,
    config: DecayConfig | None = None,
) -> float:
    """Compute the decayed importance for a single item.

    Returns the new importance value (0.0–1.0) without modifying the item.
    """
    if config is None:
        config = DecayConfig()
    if now is None:
        now = datetime.now(timezone.utc)

    stability = item.stability or Stability.transient

    # Permanent items never decay
    if stability == Stability.permanent:
        return item.importance

    # Ephemeral: binary — alive or dead
    if stability == Stability.ephemeral:
        age_seconds = (now - item.created_at).total_seconds()
        if age_seconds > config.ephemeral_ttl_seconds:
            return 0.0
        return item.importance

    # Time-based exponential decay for transient/stable
    age_days = max(0.0, (now - item.created_at).total_seconds() / 86400.0)

    if stability == Stability.stable:
        half_life = config.half_life_days * config.stable_half_life_multiplier
    else:  # transient
        half_life = config.half_life_days

    # Dream items decay faster unless they've been accessed ("use it or lose it")
    if "dreamed" in item.tags and item.access_count == 0:
        half_life = half_life / config.dream_decay_multiplier

    # Exponential decay: importance * 2^(-age/half_life)
    decay_factor = math.pow(2.0, -age_days / half_life)

    # Access-based anti-decay: each access adds a small bonus
    access_bonus = min(0.5, item.access_count * config.access_boost_factor)

    # Recency bonus: recent accesses slow decay
    if item.last_accessed_at:
        since_access_days = max(
            0.0, (now - item.last_accessed_at).total_seconds() / 86400.0
        )
        recency_factor = math.pow(2.0, -since_access_days / half_life)
        access_bonus *= recency_factor

    new_importance = item.importance * decay_factor + access_bonus
    return max(0.0, min(1.0, new_importance))


def geo_decay_score(
    item_geo: Any | None,
    query_center: Any | None,
    *,
    decay_km: float = 1.0,
) -> float:
    """Spatial decay factor: score decreases as distance from the query center grows.

    Applied in the reranker stage to candidates that carry geo coordinates.
    Does not modify stored item data.

    Args:
        item_geo: ``content["geo"]`` dict with ``lat``/``lon``, or ``None``.
        query_center: A ``GeoPoint`` object, or ``None``.
        decay_km: Decay unit in km — score halves every this many kilometres.

    Returns:
        A decay factor in ``[0.0, 1.0]``. Returns 1.0 (no decay) when either
        argument is ``None``.
    """
    if item_geo is None or query_center is None:
        return 1.0

    # item_geo may be a dict {"lat": ..., "lon": ...} or an object with lat/lon attributes
    try:
        if isinstance(item_geo, dict):
            lat = float(item_geo["lat"])
            lon = float(item_geo["lon"])
        else:
            lat = float(item_geo.lat)
            lon = float(item_geo.lon)
        q_lat = float(query_center.lat)
        q_lon = float(query_center.lon)
    except (KeyError, TypeError, ValueError, AttributeError):
        return 1.0

    dist_km = _haversine_km(lat, lon, q_lat, q_lon)
    return 1.0 / (1.0 + dist_km / max(decay_km, 1e-9))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def apply_decay(
    items: list[ContextItem],
    *,
    config: DecayConfig | None = None,
    now: datetime | None = None,
) -> DecayResult:
    """Apply decay to a list of items, mutating them in place.

    Items below the archive threshold are marked as non-searchable.

    Args:
        items: Items to decay (modified in place).
        config: Decay configuration.
        now: Current time (for testing).

    Returns:
        DecayResult with counts of decayed and archived items.
    """
    if config is None:
        config = DecayConfig()
    if now is None:
        now = datetime.now(timezone.utc)

    decayed_count = 0
    archived_count = 0

    for item in items:
        if item.is_deleted:
            continue

        old_importance = item.importance
        new_importance = compute_decay(item, now=now, config=config)

        if new_importance != old_importance:
            item.importance = new_importance
            decayed_count += 1

        # Archive if below threshold
        if new_importance < config.archive_threshold and item.searchable:
            item.searchable = False
            archived_count += 1

    return DecayResult(
        decayed_count=decayed_count,
        archived_count=archived_count,
        details={
            "half_life_days": config.half_life_days,
            "archive_threshold": config.archive_threshold,
        },
    )
