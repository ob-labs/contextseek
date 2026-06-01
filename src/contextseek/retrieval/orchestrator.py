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
        min_score: float | None = None,
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
            min_score: Optional threshold applied to the reranker's raw ``_score``
                (falling back to ``score``) right after rerank and before the
                ``[:k]`` cut and min-max output normalization. Because it filters
                the reranker's native score, the meaningful threshold range shifts
                with the active reranker — callers own picking a sensible value.
                ``None`` (default) disables the filter.
        """
        strategy = self.strategy or RetrievalStrategy()
        recall_route = self._build_recall_route(strategy)
        reranker = self.reranker or HeuristicReranker()

        # ─── Recall ───────────────────────────────────────────────
        recall_start = perf_counter()
        raw_hits: list[dict[str, object]] = []
        recall_paths: set[str] = set()
        recall_limit = max(k, 1) * max(strategy.candidate_multiplier, 1)

        # Each emitted recall is tagged with both the route name and the
        # prefix it ran against. RRF needs one rank stream per (route, prefix)
        # pair so that scope-local backend rankings stay comparable.
        ranked_streams: list[tuple[str, list[dict[str, object]]]] = []

        for prefix in prefixes:
            for recall_query in recall_route.build_queries(query, strategy):
                recall_paths.add(recall_query.route_name)
                stream_id = f"{recall_query.route_name}@{prefix}"
                stream_hits: list[dict[str, object]] = []
                for item in recall_route.recall(
                    self.adapter,
                    prefix=prefix,
                    recall_query=recall_query,
                    k=recall_limit,
                ):
                    routed = dict(item)
                    routed["_recall_path"] = recall_query.route_name
                    raw_hits.append(routed)
                    stream_hits.append(routed)
                ranked_streams.append((stream_id, stream_hits))

        # ─── Geo recall (optional, runs alongside other routes) ────
        if geo_query is not None:
            geo_route = GeoRecallRoute()
            for prefix in prefixes:
                for geo_rq in geo_route.build_queries(
                    query, strategy, geo_query=geo_query
                ):
                    recall_paths.add(geo_rq.route_name)
                    stream_id = f"{geo_rq.route_name}@{prefix}"
                    stream_hits = []
                    for item in geo_route.recall(
                        self.adapter,
                        prefix=prefix,
                        recall_query=geo_rq,
                        k=recall_limit,
                    ):
                        routed = dict(item)
                        routed["_recall_path"] = geo_rq.route_name
                        raw_hits.append(routed)
                        stream_hits.append(routed)
                    ranked_streams.append((stream_id, stream_hits))

        # ─── Filter: deleted, searchable, stage, tags ─────────────
        # Filtering happens before RRF so excluded items don't consume rank
        # slots in the fusion. We filter the per-stream lists in place.
        #
        # ``searchable`` defaults to True on legacy payloads that predate the
        # field (``payload.get("searchable", True)``). Items explicitly marked
        # ``searchable=False`` are hidden from all recall channels — this is
        # how the evolution pipeline expresses "this raw item has been
        # consumed into an extracted/knowledge successor and should no longer
        # surface in normal retrieval". Side-channel APIs (``ctx.items`` /
        # ``ctx._list_items``) bypass this filter and still see the row.
        def _keep(h: dict[str, object]) -> bool:
            if not include_deleted and h.get("deleted_at"):
                return False
            if h.get("searchable", True) is False:
                return False
            if stage is not None and h.get("stage") != stage.value:
                return False
            if tags:
                if not set(tags).issubset(set(h.get("tags") or [])):
                    return False
            return True

        ranked_streams = [
            (sid, [h for h in s if _keep(h)]) for sid, s in ranked_streams
        ]
        raw_hits = [h for h in raw_hits if _keep(h)]

        recall_elapsed_ms = (perf_counter() - recall_start) * 1000.0

        # ─── RRF fusion across streams ────────────────────────────
        # For each item, sum 1 / (rrf_k + rank) across every stream that
        # returned it. This rewards multi-stream consensus and is invariant to
        # the (often heterogeneous) raw score scales returned by different
        # backends or routes.
        rerank_start = perf_counter()
        rrf_k = max(1, int(getattr(strategy, "rrf_k", 60)))
        merged: dict[str, dict[str, object]] = {}
        for stream_id, stream_hits in ranked_streams:
            for rank, item in enumerate(stream_hits, start=1):
                key = str(item.get("id") or item.get("ref", ""))
                if not key:
                    continue
                contribution = 1.0 / (rrf_k + rank)
                existing = merged.get(key)
                if existing is None:
                    fused = dict(item)
                    fused["_recall_paths"] = [str(item.get("_recall_path", ""))]
                    fused["_rrf"] = contribution
                    # Preserve the highest backend score for debugging /
                    # downstream score features even though _rrf is what
                    # actually drives ranking from here on.
                    fused["_backend_score"] = float(item.get("score", 0.0))
                    merged[key] = fused
                    continue
                paths = set(existing.get("_recall_paths", []))
                paths.add(str(item.get("_recall_path", "")))
                existing["_recall_paths"] = sorted(p for p in paths if p)
                existing["_rrf"] = float(existing.get("_rrf", 0.0)) + contribution
                existing["_backend_score"] = max(
                    float(existing.get("_backend_score", 0.0)),
                    float(item.get("score", 0.0)),
                )

        # Hand the fused score off to the reranker via the conventional
        # ``score`` field so downstream rerankers (Heuristic / LLM / Relation)
        # don't need to know about RRF.
        #
        # Batch-level max normalization: raw RRF accumulates 1/(k+rank) per
        # stream, so absolute values stay in 0.01–0.07 even for top-1 multi-
        # route hits. Without normalization, the recall channel feeds tiny
        # numbers into the reranker's weighted-average (recall ∈ [0.01, 0.07]
        # vs overlap/feedback ∈ [0, 1]), letting overlap/feedback dominate and
        # the multi-route consensus signal evaporate.
        if merged:
            max_rrf = max(float(it.get("_rrf", 0.0)) for it in merged.values())
            if max_rrf <= 0.0:
                max_rrf = 1.0
            for item in merged.values():
                item["score"] = float(item.get("_rrf", 0.0)) / max_rrf
        # Empty merged → loop body below is a no-op; nothing to normalize.

        # ─── Rerank ───────────────────────────────────────────────
        reranked = reranker.rerank(
            list(merged.values()), query=query, strategy=strategy, geo_query=geo_query
        )
        if min_score is not None:
            reranked = [
                p for p in reranked if _passes_min_score(p, min_score, strategy)
            ]
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


def _passes_min_score(
    payload: dict[str, object],
    min_score: float,
    strategy: RetrievalStrategy,
) -> bool:
    """Stage-adjusted min_score check.

    Compares _score against min_score * stage_weight, which cancels out the
    stage multiplier baked into _score. This means min_score=0.5 always means
    'base relevance >= 50%' regardless of the item's stage maturity — stage
    only affects ranking order, not eligibility.
    """
    from contextseek.retrieval.components import _resolve_stage_weight

    score = float(payload.get("_score", payload.get("score", 0.0)))
    stage = str(payload.get("stage") or "raw").lower()
    stage_weight = _resolve_stage_weight(strategy, stage)
    if stage_weight is None:
        stage_weight = _resolve_stage_weight(strategy, "raw") or 1.0
    return score >= min_score * stage_weight


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
