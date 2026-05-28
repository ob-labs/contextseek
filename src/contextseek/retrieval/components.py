"""Pluggable retrieval pipeline components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from typing import Protocol
import math
import re

from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.config import RetrievalStrategy
from contextseek.policies.decay import geo_decay_score


_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

# Embedder is a callable that converts a text string to a float vector.
Embedder = Callable[[str], list[float]]


def tokens(text: str) -> list[str]:
    """Return normalized query/content tokens for lightweight local ranking."""
    return [item.lower() for item in _TOKEN_RE.findall(text) if item.strip()]


@dataclass(frozen=True)
class RecallQuery:
    """One backend query emitted by a recall route."""

    route_name: str
    query: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RecallRoute(Protocol):
    """Build and execute backend recall routes."""

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        """Return one or more backend queries for a user query."""

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        """Return raw backend payloads for one recall query."""


class Reranker(Protocol):
    """Rerank recalled payloads after dedupe."""

    def rerank(
        self,
        candidates: list[dict[str, object]],
        *,
        query: str,
        strategy: RetrievalStrategy,
        geo_query: Any | None = None,
    ) -> list[dict[str, object]]:
        """Return candidates ordered by relevance."""


class DefaultRecallRoute:
    """Default phrase + token recall route over the VFS search API."""

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        cleaned = query.strip()
        if not cleaned:
            return []
        routes: list[RecallQuery] = []
        enabled = set(strategy.recall_routes)
        if "phrase" in enabled:
            routes.append(RecallQuery("phrase", cleaned))
        if "terms" in enabled:
            seen = {cleaned.lower()}
            for token in tokens(cleaned):
                if token not in seen:
                    routes.append(RecallQuery("term", token))
                    seen.add(token)
        return routes

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        return adapter.search(prefix, recall_query.query, k=k)


class VectorRecallRoute:
    """Vector similarity recall route using an embedder callable.

    Requires the adapter to implement ``vector_search(prefix, vector, k)``.
    Falls back to an empty result list if the adapter does not support it,
    so it is safe to enable this route on non-vector backends (the phrase/terms
    routes will still return results).
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        cleaned = query.strip()
        if not cleaned or "vector" not in set(strategy.recall_routes):
            return []
        return [RecallQuery("vector", cleaned)]

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        try:
            query_vector = self._embedder(recall_query.query)
        except Exception:  # noqa: BLE001  # embedder errors must not crash the pipeline
            return []
        # Route through search() with query_embedding so ANN-capable backends can hybrid-recall.
        return adapter.search(
            prefix,
            recall_query.query,
            k=k,
            query_embedding=query_vector,
        )


class HybridRecallRoute:
    """Combines DefaultRecallRoute (phrase/terms) with VectorRecallRoute.

    All sub-routes are executed; results are merged by the orchestrator.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._text_route = DefaultRecallRoute()
        self._vector_route = VectorRecallRoute(embedder)

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        return self._text_route.build_queries(
            query, strategy
        ) + self._vector_route.build_queries(query, strategy)

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        if recall_query.route_name == "vector":
            return self._vector_route.recall(
                adapter, prefix=prefix, recall_query=recall_query, k=k
            )
        return self._text_route.recall(
            adapter, prefix=prefix, recall_query=recall_query, k=k
        )


class HeuristicReranker:
    """Local reranker using backend score, token overlap, evidence and feedback."""

    def rerank(
        self,
        candidates: list[dict[str, object]],
        *,
        query: str,
        strategy: RetrievalStrategy,
        geo_query: Any | None = None,
    ) -> list[dict[str, object]]:
        for item in candidates:
            item["_score"] = self.rank_score(
                item, query=query, strategy=strategy, geo_query=geo_query
            )
        return sorted(
            candidates,
            key=lambda item: float(item.get("_score", 0.0)),
            reverse=True,
        )

    @staticmethod
    def rank_score(
        item: dict[str, object],
        *,
        query: str,
        strategy: RetrievalStrategy,
        geo_query: Any | None = None,
    ) -> float:
        """Combine recall score, query-overlap, evidence and feedback into a
        normalized [0, 1] base, then apply multiplicative penalties and the
        importance multiplier.

        Each feature is independently clamped to [0, 1] and combined as a
        weighted sum whose weights renormalize so the base never exceeds 1.0.
        This avoids the prior behaviour where additive bonuses pushed the score
        past 1.0 and were silently clipped, making feedback / overlap weights
        practically inert.
        """
        # ─── Normalize each feature to [0, 1] ─────────────────────
        recall = max(0.0, min(1.0, float(item.get("score", 0.0))))

        query_tokens = set(tokens(query))
        if query_tokens:
            content_tokens = set(tokens(_content_for_score(item)))
            overlap = len(query_tokens & content_tokens) / len(query_tokens)
        else:
            overlap = 0.0

        try:
            feedback_score = float(item.get("feedback_score", 0.0))
        except (TypeError, ValueError):
            feedback_score = 0.0
        # Exclude the feedback channel when there is no interaction signal.
        # sigmoid(0) = 0.5 would apply a uniform positive bias to every item
        # that has never been interacted with; zeroing the weight removes the
        # channel entirely so recall / overlap carry the full weight instead.
        if feedback_score != 0.0:
            feedback = 1.0 / (1.0 + math.exp(-feedback_score))
            feedback_weight = max(0.0, float(strategy.feedback_weight))
        else:
            feedback = 0.0
            feedback_weight = 0.0

        try:
            quality_score = float(item.get("quality_score") or 0.0)
        except (TypeError, ValueError):
            quality_score = 0.0
        quality = max(0.0, min(1.0, quality_score))

        evidence = 1.0 if item.get("evidence_id") else 0.0

        # ─── Weighted linear combination (renormalized so weights sum to 1) ─
        # Weights are taken from RetrievalStrategy. Their sum normally already
        # leaves the recall feature with the dominant share; if a config zeroes
        # out everything we fall back to recall only.
        recall_weight = 1.0  # implicit weight of the recall channel
        weights = {
            "recall": recall_weight,
            "overlap": max(0.0, float(strategy.term_weight)),
            "feedback": feedback_weight,
            "quality": max(0.0, float(strategy.evidence_quality_weight)),
            "evidence": max(0.0, float(strategy.evidence_weight)),
        }
        total_weight = sum(weights.values())
        if total_weight <= 0:
            base = recall
        else:
            base = (
                weights["recall"] * recall
                + weights["overlap"] * overlap
                + weights["feedback"] * feedback
                + weights["quality"] * quality
                + weights["evidence"] * evidence
            ) / total_weight

        # ─── Multiplicative penalties ─────────────────────────────
        conflict_with = item.get("conflict_with")
        if isinstance(conflict_with, list) and conflict_with:
            base *= max(0.0, 1.0 - strategy.conflict_penalty)

        tier = str(item.get("tier", "")).lower()
        if item.get("is_archived") or tier == "cold":
            base *= max(0.0, 1.0 - strategy.archive_penalty)
        elif tier == "warm":
            base *= max(0.0, 1.0 - strategy.archive_penalty / 2)

        if geo_query is not None and getattr(geo_query, "center", None) is not None:
            content = item.get("content", {})
            item_geo = content.get("geo") if isinstance(content, dict) else None
            base *= geo_decay_score(
                item_geo, geo_query.center, decay_km=strategy.distance_decay_km
            )

        # ─── Stage maturity multiplier ────────────────────────────
        # Promotes evolved knowledge / skill items above raw traces at equal
        # recall scores. Falls back to STAGE_CONFIDENCE when stage_weights is
        # not explicitly configured so behaviour stays sensible by default.
        # Missing or unrecognised stage values are treated as Stage.raw (×0.3)
        # so they rank below extracted/knowledge/skill rather than behaving as
        # if no multiplier applies (which was effectively ×1.0, above raw).
        from contextseek.domain.stages import Stage as _Stage

        stage_value = str(item.get("stage") or "").lower() or _Stage.raw.value
        stage_weight = _resolve_stage_weight(strategy, stage_value)
        if stage_weight is None:
            stage_weight = _resolve_stage_weight(strategy, _Stage.raw.value)
        if stage_weight is not None:
            base *= stage_weight

        # ─── Importance multiplier (applied last) ─────────────────
        if strategy.importance_alpha > 0.0:
            try:
                importance = float(item.get("importance") or 1.0)
            except (TypeError, ValueError):
                importance = 1.0
            importance = max(importance, strategy.importance_floor)
            base *= importance**strategy.importance_alpha

        return round(base, 6)


class LLMReranker:
    """LLM-based reranker that delegates relevance scoring to an external callable.

    The ``score_fn`` receives the query and a candidate's content string,
    and returns a relevance score in [0.0, 1.0].  Candidates are then sorted by
    the returned score.

    Falls back gracefully: if ``score_fn`` raises an exception for a candidate,
    that candidate keeps its original score from the upstream reranker.

    Usage::

        async def my_llm_score(query: str, content: str) -> float:
            resp = await llm.score(query=query, passage=content)
            return resp.relevance

        reranker = LLMReranker(score_fn=my_llm_score)
    """

    def __init__(
        self,
        score_fn: Callable[[str, str], float],
        *,
        inner: Reranker | None = None,
        top_n: int | None = None,
    ) -> None:
        self._score_fn = score_fn
        self._inner = inner or HeuristicReranker()
        self._top_n = top_n

    def rerank(
        self,
        candidates: list[dict[str, object]],
        *,
        query: str,
        strategy: RetrievalStrategy,
        geo_query: Any | None = None,
    ) -> list[dict[str, object]]:
        # First pass: use inner reranker to pre-sort and reduce candidate set
        pre_ranked = self._inner.rerank(
            candidates, query=query, strategy=strategy, geo_query=geo_query
        )
        # Limit LLM calls to top_n candidates if configured
        to_score = pre_ranked[: self._top_n] if self._top_n else pre_ranked
        remainder = pre_ranked[self._top_n :] if self._top_n else []
        for item in to_score:
            content = str(item.get("content", ""))
            try:
                llm_score = self._score_fn(query, content)
                item["_score"] = round(float(llm_score), 6)
            except Exception:  # noqa: BLE001
                pass  # keep existing _score from inner reranker
        scored = sorted(
            to_score,
            key=lambda item: float(item.get("_score", 0.0)),
            reverse=True,
        )
        return scored + remainder


class RelationAwareReranker:
    """Reranker that applies relation-based boosts and penalties.

    Wraps an inner reranker (defaults to ``HeuristicReranker``) and adjusts
    scores based on relation metadata present on candidate items:

    - Items with ``relation_type == "supports"`` get a score boost.
    - Items with ``relation_type == "refutes"`` get a penalty.
    - Items with ``relation_type == "supersedes"`` (i.e. the item is superseded) get a penalty.
    """

    def __init__(self, inner: Reranker | None = None) -> None:
        self._inner = inner or HeuristicReranker()

    def rerank(
        self,
        candidates: list[dict[str, object]],
        *,
        query: str,
        strategy: RetrievalStrategy,
        geo_query: Any | None = None,
    ) -> list[dict[str, object]]:
        ranked = self._inner.rerank(
            candidates, query=query, strategy=strategy, geo_query=geo_query
        )
        for item in ranked:
            score = float(item.get("_score", 0.0))
            relation_type = str(item.get("relation_type", "")).lower()
            if relation_type == "supports":
                score += strategy.link_boost
            elif relation_type == "refutes":
                score *= max(0.0, 1.0 - strategy.link_refute_penalty)
            elif relation_type in ("supersedes", "superseded", "expired"):
                score *= max(0.0, 1.0 - strategy.link_supersede_penalty)
            # Apply namespace weight if configured
            ns_weights = dict(strategy.namespace_weights)
            ref = str(item.get("ref", ""))
            for ns_prefix, weight in ns_weights.items():
                if ns_prefix in ref:
                    score *= weight
                    break
            item["_score"] = round(score, 6)
        return sorted(
            ranked,
            key=lambda item: float(item.get("_score", 0.0)),
            reverse=True,
        )


def _content_for_score(item: dict[str, object]) -> str:
    parts = [
        str(item.get("content", "")),
        str(item.get("source_meta", "")),
        str(item.get("tags", "")),
    ]
    return " ".join(parts).lower()


def _resolve_stage_weight(
    strategy: RetrievalStrategy, stage_value: str
) -> float | None:
    """Look up the multiplier for ``stage_value`` from strategy configuration.

    Order of precedence:
      1. ``RetrievalStrategy.stage_weights`` (explicit per-deployment config)
      2. ``STAGE_CONFIDENCE`` from the domain layer (sensible default)
      3. None (no multiplier — keeps backwards-compat for unknown stages)
    """
    weights = getattr(strategy, "stage_weights", None)
    if weights:
        for stage_name, weight in weights:
            if stage_name == stage_value:
                try:
                    return float(weight)
                except (TypeError, ValueError):
                    return None
    # Fallback to STAGE_CONFIDENCE.
    from contextseek.domain.stages import STAGE_CONFIDENCE, Stage

    try:
        stage_enum = Stage(stage_value)
    except ValueError:
        return None
    return STAGE_CONFIDENCE.get(stage_enum)


class GeoRecallRoute:
    """Spatial recall route that runs alongside phrase / vector routes in RRF fusion.

    Activated when ``"geo"`` is listed in ``RETRIEVAL_RECALL_ROUTES``.
    The ``geo_query`` is carried via ``RecallQuery.metadata`` and injected by
    the orchestrator when ``build_queries`` is called.

    Silently returns an empty list when the adapter does not support
    ``geo_search`` (i.e. the backend is not geo-capable), so other routes
    are unaffected.
    """

    def build_queries(
        self,
        query: str,
        strategy: RetrievalStrategy,
        *,
        geo_query: Any | None = None,
    ) -> list[RecallQuery]:
        if geo_query is None or "geo" not in set(strategy.recall_routes):
            return []
        return [RecallQuery("geo", query, metadata={"geo_query": geo_query})]

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        geo_query = recall_query.metadata.get("geo_query")
        if geo_query is None:
            return []
        if not hasattr(adapter, "geo_search"):
            return []
        try:
            return adapter.geo_search(geo_query, prefix=prefix, k=k)  # type: ignore[union-attr]
        except Exception:
            return []
