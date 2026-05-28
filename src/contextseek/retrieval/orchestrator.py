"""Retrieval orchestration — recall, dedupe, rerank, return SearchHit."""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter
from typing import Any, Callable

from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.config import RetrievalStrategy
from contextseek.domain.context_item import ContextItem
from contextseek.domain.results import SearchHit
from contextseek.domain.serialization import deserialize_context_item
from contextseek.domain.stages import STAGE_CONFIDENCE, Stage
from contextseek.retrieval.components import (
    DefaultRecallRoute,
    GeoRecallRoute,
    HeuristicReranker,
    HybridRecallRoute,
    RecallRoute,
    Reranker,
    VectorRecallRoute,
)


@dataclass(frozen=True)
class RetrievalStats:
    """Pipeline metrics for one retrieval call."""

    recall_ms: float
    rerank_ms: float
    candidate_count: int
    deduped_count: int
    returned_count: int
    hit_rate: float
    recall_paths: tuple[str, ...] = ()


@dataclass
class RetrievalOrchestrator:
    """Compose multi-step retrieval: recall -> dedupe -> rerank -> SearchHit.

    When ``embedder`` is provided and ``strategy.recall_routes`` includes
    ``"vector"``, a :class:`HybridRecallRoute` is used automatically.
    If only ``"vector"`` is listed without a text route, a pure
    :class:`VectorRecallRoute` is used.  Falls back to
    :class:`DefaultRecallRoute` when no embedder is set.
    """

    adapter: SeekVFSAdapter
    strategy: RetrievalStrategy | None = None
    recall_route: RecallRoute | None = None
    reranker: Reranker | None = None
    embedder: Callable[[str], list[float]] | None = None

    def _build_recall_route(self, strategy: RetrievalStrategy) -> RecallRoute:
        """Select recall route based on strategy config and embedder availability."""
        if self.recall_route is not None:
            return self.recall_route
        enabled = set(strategy.recall_routes)
        has_vector = "vector" in enabled
        has_text = bool(enabled - {"vector"})
        if has_vector and self.embedder is not None:
            if has_text:
                return HybridRecallRoute(self.embedder)
            return VectorRecallRoute(self.embedder)
        return DefaultRecallRoute()

    def search(
        self,
        *,
        prefixes: list[str],
        query: str,
        k: int,
        stage: Stage | None = None,
        tags: list[str] | None = None,
        include_deleted: bool = False,
        with_stats: bool = False,
        geo_query: Any | None = None,
    ) -> list[SearchHit] | tuple[list[SearchHit], RetrievalStats]:
        """Recall, dedupe, rerank and return SearchHit results.

        Args:
            prefixes: Storage prefixes to search across.
            query: User query string.
            k: Maximum number of results to return.
            stage: Optional stage filter — only include items matching this stage.
            tags: Optional tags filter — only include items having ALL these tags.
            include_deleted: Whether to include soft-deleted items.
            with_stats: If True, return (hits, stats) tuple.
        """
        strategy = self.strategy or RetrievalStrategy()
        recall_route = self._build_recall_route(strategy)
        reranker = self.reranker or HeuristicReranker()

        # ─── Recall ───────────────────────────────────────────────
        recall_start = perf_counter()
        raw_hits: list[dict[str, object]] = []
        recall_paths: set[str] = set()
        recall_limit = max(k, 1) * max(strategy.candidate_multiplier, 1)

        for prefix in prefixes:
            for recall_query in recall_route.build_queries(query, strategy):
                recall_paths.add(recall_query.route_name)
                for item in recall_route.recall(
                    self.adapter,
                    prefix=prefix,
                    recall_query=recall_query,
                    k=recall_limit,
                ):
                    routed = dict(item)
                    routed["_recall_path"] = recall_query.route_name
                    raw_hits.append(routed)

        # ─── Geo recall (optional, runs alongside other routes) ────
        if geo_query is not None:
            geo_route = GeoRecallRoute()
            for prefix in prefixes:
                for geo_rq in geo_route.build_queries(
                    query, strategy, geo_query=geo_query
                ):
                    recall_paths.add(geo_rq.route_name)
                    for item in geo_route.recall(
                        self.adapter,
                        prefix=prefix,
                        recall_query=geo_rq,
                        k=recall_limit,
                    ):
                        routed = dict(item)
                        routed["_recall_path"] = geo_rq.route_name
                        raw_hits.append(routed)

        # ─── Filter: deleted, stage, tags ─────────────────────────
        if not include_deleted:
            raw_hits = [h for h in raw_hits if not h.get("deleted_at")]

        if stage is not None:
            raw_hits = [h for h in raw_hits if h.get("stage") == stage.value]

        if tags:
            tag_set = set(tags)
            raw_hits = [
                h for h in raw_hits if tag_set.issubset(set(h.get("tags") or []))
            ]

        recall_elapsed_ms = (perf_counter() - recall_start) * 1000.0

        # ─── Dedupe by item hash ──────────────────────────────────
        rerank_start = perf_counter()
        merged: dict[str, dict[str, object]] = {}
        for item in raw_hits:
            dedupe_key = str(item.get("hash") or item.get("id") or item.get("ref", ""))
            existing = merged.get(dedupe_key)
            if existing is None:
                item["_recall_paths"] = [str(item.get("_recall_path", ""))]
                merged[dedupe_key] = item
                continue
            # Merge recall paths
            paths = set(existing.get("_recall_paths", []))
            paths.add(str(item.get("_recall_path", "")))
            existing["_recall_paths"] = sorted(p for p in paths if p)
            # Keep the higher-scoring variant
            if float(item.get("score", 0.0)) > float(existing.get("score", 0.0)):
                item["_recall_paths"] = existing["_recall_paths"]
                merged[dedupe_key] = item

        # ─── Rerank ───────────────────────────────────────────────
        reranked = reranker.rerank(
            list(merged.values()), query=query, strategy=strategy, geo_query=geo_query
        )
        limited = reranked[:k]
        normalized_scores = _normalize_output_scores(
            [
                float(payload.get("_score", payload.get("score", 0.0)))
                for payload in limited
            ]
        )

        # ─── Convert to SearchHit ─────────────────────────────────
        hits: list[SearchHit] = []
        for payload, normalized_score in zip(limited, normalized_scores):
            context_item = deserialize_context_item(payload)
            layer = "summary" if context_item.summary else "full"
            stage_confidence = STAGE_CONFIDENCE.get(context_item.stage, 0.3)
            provenance_summary = _build_provenance_summary(context_item)
            recall_path = ",".join(payload.get("_recall_paths", []))

            hits.append(
                SearchHit(
                    item=context_item,
                    score=normalized_score,
                    layer=layer,
                    provenance_summary=provenance_summary,
                    stage_confidence=stage_confidence,
                    recall_path=recall_path,
                )
            )

        rerank_elapsed_ms = (perf_counter() - rerank_start) * 1000.0

        # ─── Stats ────────────────────────────────────────────────
        candidate_count = len(raw_hits)
        returned_count = len(hits)
        stats = RetrievalStats(
            recall_ms=round(recall_elapsed_ms, 3),
            rerank_ms=round(rerank_elapsed_ms, 3),
            candidate_count=candidate_count,
            deduped_count=len(merged),
            returned_count=returned_count,
            hit_rate=(returned_count / max(k, 1)),
            recall_paths=tuple(sorted(recall_paths)),
        )

        if with_stats:
            return hits, stats
        return hits


def _build_provenance_summary(item: ContextItem) -> str:
    """Build a human-readable one-line provenance description."""
    prov = item.provenance
    source = prov.source_type.replace("_", " ")
    parts = [f"source: {source}"]
    if prov.context:
        parts.append(prov.context)
    elif prov.source_id:
        parts.append(f"id={prov.source_id}")
    if prov.verified:
        parts.append("verified")
    return "; ".join(parts)


def _normalize_output_scores(scores: list[float]) -> list[float]:
    """Normalize final API scores into [0, 1] while preserving order."""
    if not scores:
        return []
    sanitized = [score if math.isfinite(score) else 0.0 for score in scores]
    min_score = min(sanitized)
    max_score = max(sanitized)
    span = max_score - min_score
    if span <= 1e-12:
        fill = 1.0 if max_score > 0.0 else 0.0
        return [fill] * len(sanitized)
    return [round((score - min_score) / span, 6) for score in sanitized]
