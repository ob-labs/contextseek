"""Convergence merger — merges similar extracted items into knowledge.

Migrated from policies/memory.py and adapted for ContextItem.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage, Stability


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def semantic_similarity(a: str, b: str) -> float:
    """Token overlap similarity (local proxy, no external deps)."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def embedding_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between embedding vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def decay_score(item: ContextItem, *, half_life_days: float = 7.0) -> float:
    """Composite score: recency * importance + access + relevance_boost."""
    now = datetime.now(timezone.utc)
    age_days = (now - item.created_at).total_seconds() / 86400
    recency = (
        math.exp(-0.693 * age_days / half_life_days) if half_life_days > 0 else 1.0
    )

    access_boost = min(item.access_count / 20.0, 1.0) * 0.2
    return max(
        0.0,
        item.importance * recency + access_boost + (item.relevance_boost - 1.0) * 0.3,
    )


class ConvergenceMerger:
    """Merges similar extracted items into a single knowledge item.

    When N+ extracted items have high content similarity, they are merged
    into one ContextItem(stage=knowledge) with links back to sources.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.72,
        min_cluster_size: int = 3,
        embedder: Callable[[str], list[float]] | None = None,
        half_life_days: float = 7.0,
        synthesize_fn: Callable[[list[str]], str] | None = None,
    ):
        self._threshold = similarity_threshold
        self._min_cluster = min_cluster_size
        self._embedder = embedder
        self._half_life = half_life_days
        self._synthesize = synthesize_fn

    def merge(
        self, items: list[ContextItem]
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """Merge similar extracted items.

        Returns:
            (kept, archived): kept includes new knowledge items; archived items
            are superseded by the merged result.
        """
        # Only consider extracted, non-deleted, searchable items
        candidates = [
            it
            for it in items
            if it.stage == Stage.extracted and not it.is_deleted and it.searchable
        ]
        if len(candidates) < self._min_cluster:
            return list(items), []

        # Cluster by similarity
        clusters: list[list[ContextItem]] = []
        used: set[str] = set()

        for i, item_a in enumerate(candidates):
            if item_a.id in used:
                continue
            cluster = [item_a]
            used.add(item_a.id)
            for j in range(i + 1, len(candidates)):
                item_b = candidates[j]
                if item_b.id in used:
                    continue
                sim = self._similarity(item_a, item_b)
                if sim >= self._threshold:
                    cluster.append(item_b)
                    used.add(item_b.id)
            if len(cluster) >= self._min_cluster:
                clusters.append(cluster)

        if not clusters:
            return list(items), []

        # Produce merged knowledge items
        kept = [it for it in items if it.id not in used]
        archived: list[ContextItem] = []

        for cluster in clusters:
            # Pick the highest-scoring item as representative
            cluster.sort(
                key=lambda x: decay_score(x, half_life_days=self._half_life),
                reverse=True,
            )
            representative = cluster[0]
            merged_content = representative.content
            if self._synthesize is not None:
                try:
                    synthesized = self._synthesize([it.content_text for it in cluster])
                    if synthesized.strip():
                        merged_content = synthesized.strip()
                except Exception:
                    pass

            # Create merged knowledge item
            merged = ContextItem(
                id=_generate_id(),
                content=merged_content,
                scope=representative.scope,
                provenance=Provenance(
                    source_type=SourceType.merge_result,
                    source_id=representative.id,
                    confidence=min(0.9, representative.provenance.confidence + 0.2),
                    context=f"Merged from {len(cluster)} similar items",
                ),
                stage=Stage.knowledge,
                stability=Stability.stable,
                tags=list(set(tag for it in cluster for tag in it.tags)),
                links=[
                    Link(target_id=it.id, relation=LinkType.merged_from)
                    for it in cluster
                ],
                created_at=_utc_now(),
                importance=max(it.importance for it in cluster),
            )
            kept.append(merged)

            # Mark provenance on the source items, but DO NOT hide them from
            # retrieval. The merged knowledge is a coarser-grained synthesis;
            # the original extracted items still carry independently useful
            # mid-grained detail (own embeddings, tags, geo, ...) and should
            # remain searchable. Only ``raw → extracted`` consumption flips
            # ``searchable`` (see EvolutionEngine), which preserves the
            # "raw was absorbed" semantics without collapsing extracted into
            # invisible artefacts when a higher-stage convergence happens.
            for it in cluster:
                it.superseded_by = merged.id
                it.updated_at = _utc_now()
                archived.append(it)

        return kept, archived

    def _similarity(self, a: ContextItem, b: ContextItem) -> float:
        """Compute similarity between two items."""
        # Prefer embedding similarity if available
        if self._embedder and a.embedding and b.embedding:
            return embedding_similarity(a.embedding, b.embedding)
        if a.embedding and b.embedding:
            return embedding_similarity(a.embedding, b.embedding)
        return semantic_similarity(a.content_text, b.content_text)


class GeoAwareMerger(ConvergenceMerger):
    """Extends ConvergenceMerger with a spatial-distance merge trigger.

    When two ContextItems both carry ``content["geo"]`` coordinates and
    their Haversine distance is below *spatial_merge_threshold_m*, they are
    eligible for merge even if their embedding similarity is below the
    semantic threshold.

    Useful when the same real-world location is recorded multiple times with
    slightly different textual content (e.g. repeated coordinate updates).
    """

    def __init__(
        self,
        *args: Any,
        spatial_merge_threshold_m: float = 500.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._spatial_threshold_m = spatial_merge_threshold_m

    def _similarity(self, a: ContextItem, b: ContextItem) -> float:
        # 1. Try semantic / embedding similarity first (from parent)
        base_sim = super()._similarity(a, b)
        if base_sim >= self._threshold:
            return base_sim

        # 2. Fall back to spatial proximity check
        geo_a = _geo_coords(a)
        geo_b = _geo_coords(b)
        if geo_a is None or geo_b is None:
            return base_sim

        dist_m = _haversine_m(geo_a, geo_b)
        if dist_m <= self._spatial_threshold_m:
            # Return a similarity just above threshold to trigger merge
            return self._threshold + 0.01
        return base_sim


def _geo_coords(item: ContextItem) -> tuple[float, float] | None:
    """Extract (lat, lon) from item.content["geo"] if present."""
    content = item.content
    if not isinstance(content, dict):
        return None
    geo = content.get("geo")
    if not isinstance(geo, dict):
        return None
    lat = geo.get("lat")
    lon = geo.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return (float(lat), float(lon))
    except (TypeError, ValueError):
        return None


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in metres between two (lat, lon) pairs."""
    import math

    r = 6_371_000.0  # Earth radius in metres
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return r * 2 * math.asin(math.sqrt(h))
