"""Evolution engine — orchestrates the full Stage progression pipeline.

Called by compact() and LifecycleScheduler to drive:
  raw → extracted → knowledge → skill
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from contextseek.domain.context_item import ContextItem
from contextseek.domain.inference import _is_trace_structure
from contextseek.domain.results import CompactReport
from contextseek.domain.stages import Stage
from contextseek.evolution.distiller import SkillDistiller
from contextseek.evolution.extractor import Extractor, HeuristicExtractor
from contextseek.evolution.merger import ConvergenceMerger
from contextseek.evolution.rules import DEFAULT_RULES, EvolutionRule


class EvolutionEngine:
    """Drives the evolution pipeline for a set of ContextItems.

    Usage::
        engine = EvolutionEngine()
        new_items, archived_items, report = engine.evolve(existing_items)
    """

    def __init__(
        self,
        *,
        rules: list[EvolutionRule] | None = None,
        extractor: Extractor | None = None,
        merger: ConvergenceMerger | None = None,
        distiller: SkillDistiller | None = None,
        strategy: Any | None = None,
        merge_synthesize_fn: Callable[[list[str]], str] | None = None,
        distill_decide_fn: Callable[[ContextItem], bool] | None = None,
        distill_render_fn: Callable[[ContextItem], dict[str, str]] | None = None,
        summarizer: Any | None = None,
    ):
        self._rules = rules or DEFAULT_RULES

        # Resolve strategy fields — fall back to hardcoded defaults when absent
        ephemeral_ttl = 3600.0
        merger_threshold = 0.72
        merger_min_cluster = 3
        merger_half_life = 7.0
        distiller_min_use = 10
        distiller_min_boost = 1.2
        if strategy is not None:
            ephemeral_ttl = getattr(strategy, "ephemeral_ttl_seconds", ephemeral_ttl)
            merger_threshold = getattr(
                strategy, "semantic_merge_threshold", merger_threshold
            )
            merger_min_cluster = getattr(
                strategy, "min_cluster_size", merger_min_cluster
            )
            merger_half_life = getattr(
                strategy, "decay_half_life_days", merger_half_life
            )
            distiller_min_use = getattr(
                strategy, "distill_min_use_count", distiller_min_use
            )
            distiller_min_boost = getattr(
                strategy, "distill_min_relevance_boost", distiller_min_boost
            )

        self._ephemeral_ttl = ephemeral_ttl
        self._extractor = extractor or HeuristicExtractor()
        self._merger = merger or ConvergenceMerger(
            similarity_threshold=merger_threshold,
            min_cluster_size=merger_min_cluster,
            half_life_days=merger_half_life,
            synthesize_fn=merge_synthesize_fn,
        )
        self._distiller = distiller or SkillDistiller(
            min_use_count=distiller_min_use,
            min_relevance_boost=distiller_min_boost,
            llm_decide_fn=distill_decide_fn,
            llm_distill_fn=distill_render_fn,
        )
        self._summarizer = summarizer

    def evolve(
        self, items: list[ContextItem]
    ) -> tuple[list[ContextItem], list[ContextItem], CompactReport]:
        """Run the full evolution pipeline.

        Returns:
            (new_items, archived_items, report):
            - new_items: newly created items (extracted/knowledge/skill)
            - archived_items: items that were superseded
            - report: summary of what happened
        """
        new_items: list[ContextItem] = []
        archived_items: list[ContextItem] = []
        report = CompactReport()

        # Phase 1: raw → extracted (trace extraction)
        raw_traces = [
            it
            for it in items
            if it.stage == Stage.raw
            and not it.is_deleted
            and self._eligible_for_extraction(it)
        ]
        for item in raw_traces:
            extracted = self._extractor.extract(item)
            if extracted:
                new_items.extend(extracted)
                item.searchable = False
                item.superseded_by = extracted[0].id
                item.updated_at = datetime.now(timezone.utc)
                archived_items.append(item)
        report.evolved_count += len(new_items)

        # Phase 2: extracted → knowledge (convergence merge)
        extracted_items = [
            it for it in items if it.stage == Stage.extracted and not it.is_deleted
        ]
        # Include newly extracted items
        all_extracted = extracted_items + [
            it for it in new_items if it.stage == Stage.extracted
        ]
        if all_extracted:
            kept, archived = self._merger.merge(all_extracted)
            # Find new knowledge items (those not in original list)
            original_ids = {it.id for it in all_extracted}
            new_knowledge = [it for it in kept if it.id not in original_ids]
            new_items.extend(new_knowledge)
            archived_items.extend(archived)
            report.merged_count += len(archived)
            report.evolved_count += len(new_knowledge)

            if self._summarizer is not None:
                for it in new_knowledge:
                    if it.abstract is None:
                        it.abstract = self._summarizer.abstract(it.content_text)
                        it.summary = self._summarizer.summary(it.content_text)
            else:
                # When synthesize_fn has already written content as a natural
                # language string, use it directly as abstract/summary to ensure
                # middleware injection and semantic retrieval can match this
                # knowledge entry.
                for it in new_knowledge:
                    if it.abstract is None and isinstance(it.content, str):
                        it.abstract = it.content
                        it.summary = it.content

        # Phase 3: knowledge → skill (distillation)
        knowledge_items = [
            it for it in items if it.stage == Stage.knowledge and not it.is_deleted
        ]
        candidates = self._distiller.identify_candidates(knowledge_items)
        for candidate in candidates:
            skill_item = self._distiller.distill(candidate)
            new_items.append(skill_item)
            report.evolved_count += 1

        # Phase 4: Archive expired items (stability=ephemeral past TTL)
        for item in items:
            if not item.is_deleted and self._should_archive(item):
                item.searchable = False
                item.deleted_at = datetime.now(timezone.utc)
                item.deleted_reason = "auto_archived_by_evolution"
                archived_items.append(item)
                report.archived_count += 1

        return new_items, archived_items, report

    def _eligible_for_extraction(self, item: ContextItem) -> bool:
        """Check if a raw item is eligible for extraction."""
        # Must have trace structure
        if not isinstance(item.content, dict):
            return False
        if not _is_trace_structure(item.content):
            return False
        # Check minimum age (from extraction rule)
        extraction_rule = next(
            (r for r in self._rules if r.name == "extract_from_trace"), None
        )
        if extraction_rule and extraction_rule.min_age_seconds > 0:
            age = (datetime.now(timezone.utc) - item.created_at).total_seconds()
            if age < extraction_rule.min_age_seconds:
                return False
        return True

    def _should_archive(self, item: ContextItem) -> bool:
        """Check if item should be auto-archived based on stability."""
        from contextseek.domain.stages import Stability

        if item.stability != Stability.ephemeral:
            return False
        age = (datetime.now(timezone.utc) - item.created_at).total_seconds()
        return age > self._ephemeral_ttl
