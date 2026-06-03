"""Dreaming mechanism — consolidation and divergence for the evolution pipeline.

Inspired by human dreaming:
- Consolidation (light sleep): replays recent high-activity items, discovers shared patterns
- Divergence (deep sleep): cross-pollinates across topic clusters, generates hypotheses

Dream outputs are low-confidence items that decay quickly unless reinforced
by agent feedback (the "use it or lose it" principle).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from contextseek.config.strategies import DreamStrategy
from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import STAGE_CONFIDENCE, Stability, Stage
from contextseek.llm.prompts import (
    LLMPromptTemplates,
    dream_consolidation_prompt,
    dream_divergence_prompt,
)


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def _token_similarity(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ═══════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════


@dataclass
class ConsolidationResult:
    """Output of the consolidation phase."""

    items: list[ContextItem] = field(default_factory=list)
    strengthened_links: list[tuple[str, str, float]] = field(default_factory=list)
    patterns_found: int = 0


@dataclass
class DivergenceResult:
    """Output of the divergence phase."""

    items: list[ContextItem] = field(default_factory=list)
    cross_links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class DreamReport:
    """Combined output of a full dream cycle."""

    consolidation: ConsolidationResult
    divergence: DivergenceResult | None
    total_dream_items: int
    timestamp: datetime = field(default_factory=_utc_now)


# ═══════════════════════════════════════════
# Consolidation Engine (light sleep / consolidation)
# ═══════════════════════════════════════════


class ConsolidationEngine:
    """Scans recent high-frequency items and extracts shared patterns.

    Similarity window: items with similarity in (lower, upper) range are
    "related but not duplicates" — the sweet spot for pattern extraction.
    Below lower = unrelated; above upper = already handled by ConvergenceMerger.
    """

    def __init__(
        self,
        *,
        strategy: DreamStrategy,
        embedder: Callable[[str], list[float]] | None = None,
        llm: Callable[[str], str] | None = None,
        prompt_templates: LLMPromptTemplates | None = None,
    ):
        self._strategy = strategy
        self._embedder = embedder
        self._llm = llm
        self._prompts = prompt_templates

    def consolidate(self, items: list[ContextItem]) -> ConsolidationResult:
        """Run consolidation on a set of items."""
        now = datetime.now(timezone.utc)
        window_seconds = self._strategy.consolidation_window_hours * 3600
        sim_low, sim_high = self._strategy.consolidation_similarity_range

        # Filter: recent, active, non-dreamed, non-deleted
        candidates = [
            it
            for it in items
            if not it.is_deleted
            and it.searchable
            and "dreamed" not in it.tags
            and it.access_count >= self._strategy.consolidation_min_access
            and (now - it.created_at).total_seconds() <= window_seconds
        ]

        if len(candidates) < 2:
            return ConsolidationResult()

        # Find clusters of related items in the similarity sweet-spot
        clusters: list[list[ContextItem]] = []
        used: set[str] = set()

        for i, item_a in enumerate(candidates):
            if item_a.id in used:
                continue
            cluster = [item_a]
            for j in range(i + 1, len(candidates)):
                item_b = candidates[j]
                if item_b.id in used:
                    continue
                sim = self._similarity(item_a, item_b)
                if sim_low <= sim <= sim_high:
                    cluster.append(item_b)
            if len(cluster) >= 2:
                clusters.append(cluster)
                for it in cluster:
                    used.add(it.id)

        if not clusters:
            return ConsolidationResult()

        return self._consolidate_clusters(clusters)

    def consolidate_pairs(
        self, pairs: list[tuple[ContextItem, ContextItem]]
    ) -> ConsolidationResult:
        """Consolidate explicit item pairs (e.g. from :func:`pick_dream_targets`).

        Pairs sharing an item are unioned into a single cluster so that a
        transitively-related group produces one pattern item instead of several.
        """
        if not pairs:
            return ConsolidationResult()

        # Union-find over the items referenced by the pairs.
        parent: dict[str, str] = {}
        items_by_id: dict[str, ContextItem] = {}

        def _find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(x: str, y: str) -> None:
            parent[_find(x)] = _find(y)

        for a, b in pairs:
            items_by_id[a.id] = a
            items_by_id[b.id] = b
            _union(a.id, b.id)

        groups: dict[str, list[ContextItem]] = {}
        for item_id, item in items_by_id.items():
            groups.setdefault(_find(item_id), []).append(item)

        clusters = [g for g in groups.values() if len(g) >= 2]
        if not clusters:
            return ConsolidationResult()
        return self._consolidate_clusters(clusters)

    def _consolidate_clusters(
        self, clusters: list[list[ContextItem]]
    ) -> ConsolidationResult:
        """Produce consolidation pattern items from pre-selected clusters."""
        result = ConsolidationResult()
        for cluster in clusters[: self._strategy.consolidation_max_outputs]:
            pattern_item = self._extract_pattern(cluster)
            result.items.append(pattern_item)
            result.patterns_found += 1

            # Strengthen links between cluster members
            for a, b in itertools.combinations(cluster[:5], 2):
                sim = self._similarity(a, b)
                result.strengthened_links.append((a.id, b.id, sim))

        return result

    def _similarity(self, a: ContextItem, b: ContextItem) -> float:
        if a.embedding and b.embedding:
            return _cosine_similarity(a.embedding, b.embedding)
        if self._embedder:
            emb_a = self._embedder(a.content_text)
            emb_b = self._embedder(b.content_text)
            if emb_a and emb_b:
                return _cosine_similarity(emb_a, emb_b)
        return _token_similarity(a.content_text, b.content_text)

    def _extract_pattern(self, cluster: list[ContextItem]) -> ContextItem:
        """Produce a consolidation item from a cluster."""
        if self._llm is not None:
            prompt = dream_consolidation_prompt(
                cluster_items=cluster,
                templates=self._prompts,
            )
            try:
                pattern_text = self._llm(prompt).strip()
            except Exception:
                pattern_text = ""
        else:
            pattern_text = ""

        # Gather shared tokens for fallback pattern text
        token_sets = [_tokenize(it.content_text) for it in cluster]
        common_tokens = token_sets[0]
        for ts in token_sets[1:]:
            common_tokens = common_tokens & ts

        # Gather shared tags
        tag_sets = [set(it.tags) for it in cluster]
        common_tags = tag_sets[0]
        for ts in tag_sets[1:]:
            common_tags = common_tags & ts

        # Build pattern text (fallback when no LLM output)
        if not pattern_text:
            if common_tokens:
                pattern_text = f"Pattern: {' '.join(sorted(common_tokens))} (consolidated from {len(cluster)} sources)"
            else:
                pattern_text = f"Pattern: common theme across {len(cluster)} items with tags [{', '.join(sorted(common_tags))}]"

        # Attach the cluster's highest-authority source as structured "primary
        # evidence", so the structured fields a source carries (geo, tags, ...)
        # are preserved through consolidation instead of being lost to the
        # natural-language summary. Authority is the stage trust ranking
        # (raw < extracted < knowledge < skill) from STAGE_CONFIDENCE; ties
        # break on insertion order (cluster is already sorted by decay score).
        # This is generic: it copies the source content verbatim and never
        # inspects domain-specific fields.
        primary = max(cluster, key=lambda it: STAGE_CONFIDENCE.get(it.stage, 0.3))
        primary_evidence = {
            "source_id": primary.id,
            "stage": primary.stage.value,
            "confidence": STAGE_CONFIDENCE.get(primary.stage, 0.3),
            "content": primary.content,
        }

        return ContextItem(
            id=_generate_id(),
            # content is a dict: the LLM/fallback summary plus the highest-authority
            # source verbatim. ``abstract`` keeps the clean summary string so the
            # embedding source and tight-budget display stay text, not str(dict).
            content={"pattern": pattern_text, "primary_evidence": primary_evidence},
            abstract=pattern_text,
            scope=cluster[0].scope,
            provenance=Provenance(
                source_type=SourceType.dream_consolidation,
                source_id=cluster[0].id,
                confidence=self._strategy.dream_initial_confidence,
                context=f"Consolidated from {len(cluster)} related items",
            ),
            stage=Stage.extracted,
            stability=Stability.transient,
            tags=["dreamed", "consolidation"]
            + sorted(common_tags - {"dreamed", "consolidation", "divergence"}),
            links=[
                Link(target_id=it.id, relation=LinkType.synthesized_from, strength=0.6)
                for it in cluster
            ],
            importance=0.5,
        )

    @property
    def clusters(self) -> list[list[ContextItem]]:
        """Last computed clusters (for use by DivergenceEngine)."""
        return getattr(self, "_last_clusters", [])


# ═══════════════════════════════════════════
# Divergence Engine (deep sleep / divergence)
# ═══════════════════════════════════════════


class DivergenceEngine:
    """Cross-pollinates between different topic clusters to generate hypotheses.

    Takes clusters from consolidation (or tag-based grouping) and pairs
    representatives from different clusters to generate creative hypotheses.
    """

    def __init__(
        self,
        *,
        strategy: DreamStrategy,
        llm: Callable[[str], str] | None = None,
        prompt_templates: LLMPromptTemplates | None = None,
    ):
        self._strategy = strategy
        self._llm = llm
        self._prompts = prompt_templates

    def diverge(self, clusters: list[list[ContextItem]]) -> DivergenceResult:
        """Generate divergent hypotheses from cross-cluster combinations."""
        if len(clusters) < self._strategy.divergence_min_clusters:
            return DivergenceResult()

        # Select representative from each cluster (highest importance * access)
        representatives: list[ContextItem] = []
        for cluster in clusters:
            rep = max(cluster, key=lambda it: it.importance * max(it.access_count, 1))
            representatives.append(rep)

        # Cross-pollinate pairs (capped by max_outputs)
        result = DivergenceResult()
        pairs = list(itertools.combinations(representatives, 2))
        for rep_a, rep_b in pairs[: self._strategy.divergence_max_outputs]:
            hypothesis_item = self._generate_hypothesis(rep_a, rep_b)
            result.items.append(hypothesis_item)
            result.cross_links.append((rep_a.id, rep_b.id))

        return result

    def _generate_hypothesis(self, a: ContextItem, b: ContextItem) -> ContextItem:
        """Generate a hypothesis item from two cross-domain representatives."""
        if self._llm:
            prompt = dream_divergence_prompt(
                a=a,
                b=b,
                templates=self._prompts,
            )
            hypothesis_text = self._llm(prompt)
        else:
            # Fallback: template-based hypothesis
            tags_a = set(a.tags) - {"dreamed", "consolidation", "divergence"}
            tags_b = set(b.tags) - {"dreamed", "consolidation", "divergence"}
            tokens_a = _tokenize(a.content_text)
            tokens_b = _tokenize(b.content_text)
            overlap = tokens_a & tokens_b

            if overlap:
                hypothesis_text = (
                    f"Hypothesis: [{', '.join(sorted(tags_a)[:3])}] may relate to "
                    f"[{', '.join(sorted(tags_b)[:3])}] via shared concepts: "
                    f"{', '.join(sorted(overlap)[:5])}"
                )
            else:
                hypothesis_text = (
                    f"Hypothesis: [{', '.join(sorted(tags_a)[:3])}] and "
                    f"[{', '.join(sorted(tags_b)[:3])}] may share underlying patterns"
                )

        return ContextItem(
            id=_generate_id(),
            content=hypothesis_text,
            scope=a.scope,
            provenance=Provenance(
                source_type=SourceType.dream_divergence,
                source_id=a.id,
                confidence=self._strategy.dream_initial_confidence * 0.85,
                context=f"Cross-pollinated from items {a.id[:8]} and {b.id[:8]}",
            ),
            stage=Stage.extracted,
            stability=Stability.transient,
            tags=["dreamed", "divergence"],
            links=[
                Link(target_id=a.id, relation=LinkType.synthesized_from, strength=0.4),
                Link(target_id=b.id, relation=LinkType.synthesized_from, strength=0.4),
            ],
            importance=0.4,
        )


# ═══════════════════════════════════════════
# Dream Engine (orchestrator)
# ═══════════════════════════════════════════


class DreamEngine:
    """Orchestrates the full dream cycle: consolidation followed by divergence.

    Usage::
        engine = DreamEngine(strategy=DreamStrategy())
        report = engine.dream(items)
    """

    def __init__(
        self,
        *,
        strategy: DreamStrategy | None = None,
        embedder: Callable[[str], list[float]] | None = None,
        llm: Callable[[str], str] | None = None,
        prompt_templates: LLMPromptTemplates | None = None,
    ):
        self._strategy = strategy or DreamStrategy()
        self._consolidation = ConsolidationEngine(
            strategy=self._strategy,
            embedder=embedder,
            llm=llm,
            prompt_templates=prompt_templates,
        )
        self._divergence = DivergenceEngine(
            strategy=self._strategy,
            llm=llm,
            prompt_templates=prompt_templates,
        )
        self._last_dream_time: datetime | None = None

    @property
    def strategy(self) -> DreamStrategy:
        return self._strategy

    @property
    def last_dream_time(self) -> datetime | None:
        return self._last_dream_time

    def dream(
        self,
        items: list[ContextItem],
        *,
        targets: "DreamTargets | None" = None,
    ) -> DreamReport:
        """Execute one full dream cycle.

        Checks preconditions (cooldown, minimum items) then runs
        consolidation and optionally divergence.

        When *targets* is provided (typically from :func:`pick_dream_targets`,
        seeded by lint's consolidation hints), the cycle is goal-directed:
        consolidation operates on the targeted item pairs and divergence
        cross-pollinates the targeted high-access items, instead of relying
        purely on time-window selection.

        Returns:
            DreamReport with all generated items and statistics.
        """
        now = datetime.now(timezone.utc)

        # Check cooldown
        if self._last_dream_time is not None:
            elapsed_hours = (now - self._last_dream_time).total_seconds() / 3600
            if elapsed_hours < self._strategy.cooldown_hours:
                return DreamReport(
                    consolidation=ConsolidationResult(),
                    divergence=None,
                    total_dream_items=0,
                )

        # Check minimum items threshold
        active_items = [it for it in items if not it.is_deleted and it.searchable]
        if len(active_items) < self._strategy.min_items_for_dream:
            return DreamReport(
                consolidation=ConsolidationResult(),
                divergence=None,
                total_dream_items=0,
            )

        has_targets = targets is not None and (
            targets.consolidation_pairs or targets.divergence_candidates
        )

        # Phase 1: Consolidation
        if has_targets and targets.consolidation_pairs:
            consolidation_result = self._consolidation.consolidate_pairs(
                targets.consolidation_pairs
            )
        else:
            consolidation_result = self._consolidation.consolidate(active_items)

        # Phase 2: Divergence (if enabled and enough clusters)
        divergence_result: DivergenceResult | None = None
        if self._strategy.divergence_enabled:
            if has_targets and targets.divergence_candidates:
                # Treat each targeted item as its own cluster so the divergence
                # engine cross-pollinates exactly the items lint/graph selected.
                clusters = [[c] for c in targets.divergence_candidates]
            else:
                # Build clusters from consolidation or tag-based fallback
                clusters = self._build_clusters_for_divergence(active_items)
            if len(clusters) >= self._strategy.divergence_min_clusters:
                divergence_result = self._divergence.diverge(clusters)

        total = len(consolidation_result.items) + (
            len(divergence_result.items) if divergence_result else 0
        )

        self._last_dream_time = now

        return DreamReport(
            consolidation=consolidation_result,
            divergence=divergence_result,
            total_dream_items=total,
        )

    def _build_clusters_for_divergence(
        self, items: list[ContextItem]
    ) -> list[list[ContextItem]]:
        """Group items into topic clusters for divergence cross-pollination.

        Uses tag-based grouping as a lightweight clustering approach.
        """
        tag_groups: dict[str, list[ContextItem]] = {}
        for item in items:
            # Use the first non-system tag as group key
            key_tags = [
                t
                for t in item.tags
                if t
                not in (
                    "dreamed",
                    "consolidation",
                    "divergence",
                    "needs_reverification",
                )
            ]
            key = key_tags[0] if key_tags else "__untagged__"
            tag_groups.setdefault(key, []).append(item)

        # Only return groups with 2+ items
        return [group for group in tag_groups.values() if len(group) >= 2]


# ═══════════════════════════════════════════
# Graph-structure-driven target selection
# ═══════════════════════════════════════════


@dataclass
class DreamTargets:
    """Pre-computed targets for a dream cycle, produced by :func:`pick_dream_targets`.

    Passing targets to :meth:`DreamEngine.dream` lets lint results steer which
    items get consolidated or explored, instead of purely time-window selection.
    """

    consolidation_pairs: list[tuple[ContextItem, ContextItem]] = field(
        default_factory=list
    )
    """Pairs of knowledge items that are semantically close enough to merge."""

    divergence_candidates: list[ContextItem] = field(default_factory=list)
    """High-access items that lack an abstract — good targets for exploration."""


def pick_dream_targets(
    items: list[ContextItem],
    *,
    consolidation_sim_threshold: float = 0.88,
    divergence_min_access: int = 5,
    max_consolidation_pairs: int = 3,
    max_divergence_candidates: int = 2,
    consolidation_hints: "list | None" = None,
) -> DreamTargets:
    """Select dream targets based on embedding similarity and access patterns.

    No graph database required: uses cosine similarity over existing embeddings
    (or Jaccard token overlap as fallback).  Results are deterministic given the
    same item list.

    Args:
        items: All active items in the scope.
        consolidation_sim_threshold: Cosine similarity above which two knowledge
            items are candidates for merging.
        divergence_min_access: Minimum access_count for divergence candidates.
        max_consolidation_pairs: Cap on consolidation pairs returned.
        max_divergence_candidates: Cap on divergence candidates returned.
        consolidation_hints: Optional pre-computed ConsolidationHint list from
            :func:`run_lint` — avoids recomputing similarities when lint already ran.

    Returns:
        DreamTargets with populated consolidation_pairs and divergence_candidates.
    """
    targets = DreamTargets()
    active = [it for it in items if not it.is_deleted and it.searchable]

    # ── Consolidation: pick from lint hints when available ───────────────────
    if consolidation_hints:
        id_map = {it.id: it for it in active}
        seen: set[frozenset[str]] = set()
        for hint in consolidation_hints:
            if len(targets.consolidation_pairs) >= max_consolidation_pairs:
                break
            pair_key = frozenset({hint.item_a_id, hint.item_b_id})
            if pair_key in seen:
                continue
            a = id_map.get(hint.item_a_id)
            b = id_map.get(hint.item_b_id)
            if a and b:
                targets.consolidation_pairs.append((a, b))
                seen.add(pair_key)
    else:
        # Fallback: brute-force similarity scan over knowledge items
        knowledge = [it for it in active if it.stage.value == "knowledge"]
        seen = set()
        for i, a in enumerate(knowledge):
            if len(targets.consolidation_pairs) >= max_consolidation_pairs:
                break
            for b in knowledge[i + 1 :]:
                pair_key = frozenset({a.id, b.id})
                if pair_key in seen:
                    continue
                sim = (
                    _cosine_similarity(a.embedding, b.embedding)
                    if a.embedding and b.embedding
                    else _token_similarity(a.content_text, b.content_text)
                )
                if sim >= consolidation_sim_threshold:
                    targets.consolidation_pairs.append((a, b))
                    seen.add(pair_key)
                    if len(targets.consolidation_pairs) >= max_consolidation_pairs:
                        break

    # ── Divergence: high-access items lacking abstraction ───────────────────
    divergence_pool = [
        it
        for it in active
        if it.access_count >= divergence_min_access and not it.abstract
    ]
    # Sort by access count descending for most impactful selection
    divergence_pool.sort(key=lambda it: it.access_count, reverse=True)
    targets.divergence_candidates = divergence_pool[:max_divergence_candidates]

    return targets


__all__ = [
    "ConsolidationEngine",
    "ConsolidationResult",
    "DivergenceEngine",
    "DivergenceResult",
    "DreamEngine",
    "DreamReport",
    "DreamTargets",
    "pick_dream_targets",
]
