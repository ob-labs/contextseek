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


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on mismatch/empty)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


class HierarchicalRecallRoute:
    """Directory-recursive recall: navigate scope nodes' summaries best-first.

    Instead of flat-scanning a prefix, this route walks the scope tree under the
    search scope, scoring each child node by the cosine similarity between the
    query and that node's L0 abstract embedding (written by
    ``refresh_scope_summaries`` / ``compact``). Relevance propagates down
    (``alpha*child + (1-alpha)*parent``) so a strong parent lifts its children;
    items are collected from visited scopes and returned as a single ranked
    stream that the orchestrator fuses via RRF like any other route.

    Degrades safely to an empty stream when no embedder is configured or no
    scope node carries an embedding (e.g. ``compact`` has not run yet), so the
    phrase/vector routes still cover the query.
    """

    def __init__(self, embedder: Embedder, resolver: Any | None = None) -> None:
        self._embedder = embedder
        if resolver is None:
            from contextseek.routing.resolver import ScopeResolver

            resolver = ScopeResolver()
        self._resolver = resolver
        self._trace: Any | None = None
        self._strategy: RetrievalStrategy | None = None

    def set_trace(self, trace: Any | None) -> None:
        """Attach a ``RetrievalTrace`` sink (called by the orchestrator)."""
        self._trace = trace

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        cleaned = query.strip()
        if not cleaned or "hierarchical" not in set(strategy.recall_routes):
            return []
        # Stash the strategy so recall() (which the protocol calls without it)
        # can read hierarchical_* tuning. build_queries always runs first.
        self._strategy = strategy
        return [RecallQuery("hierarchical", cleaned)]

    def _scope_of(self, prefix: str) -> str:
        scheme = getattr(self._resolver, "scheme", "contextseek://")
        return (
            prefix[len(scheme) :].strip("/")
            if prefix.startswith(scheme)
            else prefix.strip("/")
        )

    def _node_embedding(
        self, adapter: SeekVFSAdapter, scope: str
    ) -> list[float] | None:
        from contextseek.routing.resolver import SCOPE_NODE_ITEM_ID

        node = adapter.read(self._resolver.ref_for(scope, SCOPE_NODE_ITEM_ID))
        if not node:
            return None
        emb = node.get("embedding")
        return emb if isinstance(emb, list) and emb else None

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        if self._embedder is None:
            return []
        try:
            qv = self._embedder(recall_query.query)
        except Exception:  # noqa: BLE001  # embedder errors must not crash recall
            return []

        root_scope = self._scope_of(prefix)
        try:
            refs = adapter.ls(prefix)
        except Exception:  # noqa: BLE001
            return []

        # Build the scope tree under root from listed refs (string math only).
        from contextseek.routing.resolver import is_scope_node_ref

        # Group refs by their EXACT scope. Each item belongs to exactly one
        # scope, so collection later touches every item at most once — no
        # subtree re-scans as the descent moves through parent → child.
        present: set[str] = set()
        scope_item_refs: dict[str, list[str]] = {}
        for ref in refs:
            if is_scope_node_ref(ref):
                continue
            try:
                s, _ = self._resolver.parse_ref(ref)
            except (ValueError, AttributeError):
                continue
            present.add(s)
            scope_item_refs.setdefault(s, []).append(ref)
        if not present:
            return []

        tree: set[str] = set()
        for s in present:
            parts = s.strip("/").split("/")
            for i in range(len(parts), 0, -1):
                cand = "/".join(parts[:i])
                if cand == root_scope or cand.startswith(root_scope + "/"):
                    tree.add(cand)
        tree.add(root_scope)

        children: dict[str, list[str]] = {}
        for s in tree:
            if s == root_scope:
                continue
            parent = s.rsplit("/", 1)[0] if "/" in s else ""
            if parent in tree:
                children.setdefault(parent, []).append(s)

        return self._descend(
            adapter,
            qv=qv,
            root_scope=root_scope,
            children=children,
            scope_item_refs=scope_item_refs,
            k=k,
        )

    def _read_payloads(
        self, adapter: SeekVFSAdapter, refs: list[str]
    ) -> dict[str, dict]:
        """Read item payloads, using ``read_batch`` when the adapter exposes it."""
        if not refs:
            return {}
        read_batch = getattr(adapter, "read_batch", None)
        if read_batch is not None:
            try:
                return {r: p for r, p in read_batch(refs).items() if p is not None}
            except Exception:  # noqa: BLE001
                pass
        out: dict[str, dict] = {}
        for r in refs:
            p = adapter.read(r)
            if p is not None:
                out[r] = p
        return out

    def _collect_direct_items(
        self,
        adapter: SeekVFSAdapter,
        *,
        qv: list[float],
        refs: list[str],
        collected: dict[str, dict[str, object]],
    ) -> int:
        """Score a scope's DIRECT items by query-vector cosine and collect them.

        Pure local scoring over the items that live exactly in this scope: no
        ``adapter.search`` prefix scan, so a parent visit never pulls its whole
        subtree and each item is read at most once across the descent.
        """
        new_count = 0
        for ref, payload in self._read_payloads(adapter, refs).items():
            if payload.get("searchable") is False or payload.get("deleted_at"):
                continue
            emb = payload.get("embedding")
            if not isinstance(emb, list) or not emb:
                continue
            key = str(payload.get("id") or ref)
            if not key:
                continue
            score = _cosine(qv, emb)
            hit = dict(payload)
            hit["score"] = score
            hit.setdefault("ref", ref)
            existing = collected.get(key)
            if existing is None or score > float(existing.get("score", 0.0)):
                collected[key] = hit
                new_count += 1
        return new_count

    def _descend(
        self,
        adapter: SeekVFSAdapter,
        *,
        qv: list[float],
        root_scope: str,
        children: dict[str, list[str]],
        scope_item_refs: dict[str, list[str]],
        k: int,
    ) -> list[dict[str, object]]:
        import heapq

        from contextseek.config import RetrievalStrategy

        strategy = self._strategy or RetrievalStrategy()
        alpha = float(getattr(strategy, "hierarchical_alpha", 0.5))
        max_rounds = int(getattr(strategy, "hierarchical_max_rounds", 24))
        conv_rounds = int(getattr(strategy, "hierarchical_convergence_rounds", 3))
        branch = int(getattr(strategy, "hierarchical_branch", 8))

        trace = self._trace
        collected: dict[str, dict[str, object]] = {}
        any_embedding = False
        has_any_children = bool(children)
        node_emb_cache: dict[str, list[float] | None] = {}

        def node_emb(scope: str) -> list[float] | None:
            if scope not in node_emb_cache:
                node_emb_cache[scope] = self._node_embedding(adapter, scope)
            return node_emb_cache[scope]

        # Heap of (-score, tiebreak, scope, parent_score). Seed with root anchor.
        heap: list[tuple[float, int, str, float]] = [(-1.0, 0, root_scope, 1.0)]
        seen_scopes: set[str] = set()
        tiebreak = 1
        visits = 0
        stable = 0
        last_top: tuple[str, ...] = ()

        while heap and visits < max_rounds:
            neg_score, _, scope, parent_score = heapq.heappop(heap)
            if scope in seen_scopes:
                continue
            seen_scopes.add(scope)
            visits += 1
            node_score = -neg_score
            if node_emb(scope) is not None:
                any_embedding = True

            if trace is not None:
                trace.add(
                    "descend",
                    scope=scope,
                    score=node_score,
                    message=f"visit {scope}",
                )

            # Collect only this scope's DIRECT items (no subtree prefix scan).
            new_for_scope = self._collect_direct_items(
                adapter,
                qv=qv,
                refs=scope_item_refs.get(scope, []),
                collected=collected,
            )
            if trace is not None:
                trace.add(
                    "leaf_recall",
                    scope=scope,
                    score=node_score,
                    message=f"collected {new_for_scope} items",
                    items=new_for_scope,
                )

            # Expand children, scored by node-embedding similarity + propagation.
            kids = children.get(scope, [])
            scored_kids: list[tuple[float, str]] = []
            for c in kids:
                emb = node_emb(c)
                if emb is None:
                    sim = parent_score * 0.5  # no summary yet → inherit, damped
                else:
                    any_embedding = True
                    sim = _cosine(qv, emb)
                final = alpha * sim + (1.0 - alpha) * node_score
                scored_kids.append((final, c))
                if trace is not None:
                    trace.add("node_score", scope=c, score=final, message="child score")
            scored_kids.sort(reverse=True)
            for final, c in scored_kids[:branch]:
                if c not in seen_scopes:
                    tiebreak += 1
                    heapq.heappush(heap, (-final, tiebreak, c, node_score))

            # Convergence: stop when the top-k id set is unchanged.
            top = tuple(
                sorted(
                    collected,
                    key=lambda kk: float(collected[kk].get("score", 0.0)),
                    reverse=True,
                )[:k]
            )
            if top and top == last_top:
                stable += 1
                if stable >= conv_rounds:
                    if trace is not None:
                        trace.add("converged", message=f"stable {stable} rounds")
                    break
            else:
                stable = 0
                last_top = top

        # When there are sub-scopes to navigate but none carry a summary
        # embedding (e.g. compact never ran), this route can't navigate
        # meaningfully — yield nothing so the flat routes own the result. A
        # single leaf scope with a summary still returns its collected items.
        if has_any_children and not any_embedding:
            return []

        results = sorted(
            collected.values(),
            key=lambda it: float(it.get("score", 0.0)),
            reverse=True,
        )
        return results


class CompositeRecallRoute:
    """Run several recall routes as independent RRF streams.

    Each sub-route's queries are tagged with their owner so :meth:`recall`
    dispatches back to the route that produced them. Used to run the
    hierarchical route alongside the flat text/vector route.
    """

    def __init__(self, routes: list[Any]) -> None:
        self._routes = routes

    def set_trace(self, trace: Any | None) -> None:
        for r in self._routes:
            setter = getattr(r, "set_trace", None)
            if setter is not None:
                setter(trace)

    def build_queries(
        self, query: str, strategy: RetrievalStrategy
    ) -> list[RecallQuery]:
        out: list[RecallQuery] = []
        for idx, route in enumerate(self._routes):
            for q in route.build_queries(query, strategy):
                out.append(
                    RecallQuery(
                        q.route_name, q.query, metadata={**q.metadata, "_owner": idx}
                    )
                )
        return out

    def recall(
        self,
        adapter: SeekVFSAdapter,
        *,
        prefix: str,
        recall_query: RecallQuery,
        k: int,
    ) -> list[dict[str, object]]:
        owner = int(recall_query.metadata.get("_owner", 0))
        if owner < 0 or owner >= len(self._routes):
            return []
        return self._routes[owner].recall(
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
        # When no explicit feedback_score is carried, derive the interaction
        # signal from relevance_boost (the persisted utility-feedback channel).
        # boost defaults to 1.0 → signal 0.0 → channel stays inert, so items that
        # were never used keep ranking on recall/overlap alone. Positive feedback
        # (boost > 1) lifts the item; negative feedback (boost < 1) suppresses it.
        if feedback_score == 0.0:
            try:
                boost = float(item.get("relevance_boost", 1.0))
            except (TypeError, ValueError):
                boost = 1.0
            feedback_score = boost - 1.0
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
    scores based on real ``ContextItem.links`` metadata present on candidate
    items:

    - Items with high-scoring ``supported_by`` candidates get a score boost.
    - Items with high-scoring ``refuted_by`` candidates get a penalty.
    - Items targeted by another candidate's ``supersedes`` link get a penalty.
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
        score_by_id = {
            str(item.get("id", "")): max(0.0, float(item.get("_score", 0.0)))
            for item in ranked
            if str(item.get("id", "")).strip()
        }
        supersede_penalty_by_id: dict[str, float] = {}

        for item in ranked:
            source_id = str(item.get("id", ""))
            source_score = min(1.0, score_by_id.get(source_id, 0.0))
            for link in _iter_links(item):
                if str(link.get("relation") or "") != "supersedes":
                    continue
                target_id = str(link.get("target_id") or "")
                if target_id not in score_by_id:
                    continue
                strength = _link_strength(link)
                penalty = max(
                    0.0,
                    1.0 - strategy.link_supersede_penalty * strength * source_score,
                )
                supersede_penalty_by_id[target_id] = min(
                    penalty,
                    supersede_penalty_by_id.get(target_id, 1.0),
                )

        for item in ranked:
            score = float(item.get("_score", 0.0))
            item_id = str(item.get("id", ""))
            for link in _iter_links(item):
                target_id = str(link.get("target_id") or "")
                if target_id not in score_by_id:
                    continue
                strength = _link_strength(link)
                target_score = min(1.0, score_by_id[target_id])
                relation = str(link.get("relation") or "").lower()
                if relation == "supported_by":
                    score += strategy.link_boost * strength * target_score
                elif relation == "refuted_by":
                    score *= max(
                        0.0,
                        1.0 - strategy.link_refute_penalty * strength * target_score,
                    )

            if item.get("superseded_by") or item_id in supersede_penalty_by_id:
                score *= supersede_penalty_by_id.get(
                    item_id, max(0.0, 1.0 - strategy.link_supersede_penalty)
                )

            # Backward-compatible fallback for callers that still pass synthetic
            # relation metadata directly on candidates.
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


def _iter_links(item: dict[str, object]) -> list[dict[str, object]]:
    links = item.get("links", [])
    if not isinstance(links, list):
        return []
    return [link for link in links if isinstance(link, dict)]


def _link_strength(link: dict[str, object]) -> float:
    try:
        return max(0.0, min(1.0, float(link.get("strength", 1.0))))
    except (TypeError, ValueError):
        return 1.0


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
