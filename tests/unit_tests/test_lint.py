"""Tests for evolution/lint.py — knowledge base health check."""

from datetime import datetime, timedelta, timezone

import pytest

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage
from contextseek.evolution.distiller import HeuristicDistillRule
from contextseek.evolution.lint import (
    ConsolidationHint,
    Contradiction,
    DistillOpportunity,
    LintReport,
    OrphanItem,
    run_lint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    content: str = "test content",
    stage: Stage = Stage.raw,
    access_count: int = 0,
    relevance_boost: float = 1.0,
    created_days_ago: float = 0.0,
    embedding: list[float] | None = None,
    searchable: bool = True,
    links: list | None = None,
) -> ContextItem:
    created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    return ContextItem(
        id=_generate_id(),
        content=content,
        scope="test/scope",
        provenance=Provenance(
            source_type=SourceType.human_input,
            source_id="test",
            confidence=0.6,
        ),
        stage=stage,
        access_count=access_count,
        relevance_boost=relevance_boost,
        created_at=created_at,
        embedding=embedding,
        searchable=searchable,
        links=links or [],
    )


def _skill_item(source_id: str) -> ContextItem:
    """Skill item that references source_id in provenance."""
    it = _item(content={"skill_type": "prompt", "name": "test", "body": "x"}, stage=Stage.skill)
    it.provenance = Provenance(
        source_type=SourceType.distillation,
        source_id=source_id,
        confidence=0.8,
    )
    return it


# ---------------------------------------------------------------------------
# OrphanItem detection
# ---------------------------------------------------------------------------


class TestOrphanDetection:
    def test_zero_access_item_is_orphan(self):
        item = _item(content="never accessed item", access_count=0)
        report = run_lint([item], scope="test/scope")
        assert len(report.orphans) == 1
        assert report.orphans[0].item_id == item.id

    def test_accessed_item_is_not_orphan(self):
        item = _item(content="accessed item", access_count=1)
        report = run_lint([item], scope="test/scope")
        assert len(report.orphans) == 0

    def test_skill_stage_items_are_never_orphans(self):
        item = _item(stage=Stage.skill, access_count=0)
        item.content = {"skill_type": "prompt", "name": "x", "body": "y"}
        report = run_lint([item], scope="test/scope")
        assert len(report.orphans) == 0

    def test_item_referenced_by_skill_provenance_is_not_orphan(self):
        source = _item(content="knowledge base", access_count=0, stage=Stage.knowledge)
        skill = _skill_item(source_id=source.id)
        report = run_lint([source, skill], scope="test/scope")
        assert len(report.orphans) == 0

    def test_deleted_items_are_excluded(self):
        item = _item(content="deleted item", access_count=0)
        item.soft_delete("test")
        report = run_lint([item], scope="test/scope")
        assert len(report.orphans) == 0

    def test_orphan_max_cap(self):
        items = [_item(content=f"orphan {i}") for i in range(30)]
        report = run_lint(items, scope="test/scope", orphan_max=5)
        assert len(report.orphans) <= 5


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


class TestContradictionDetection:
    def test_detects_chinese_negation_contradiction(self):
        a = _item(content="OceanBase 不支持 window function", stage=Stage.knowledge)
        b = _item(content="OceanBase 4.0 已支持 window function", stage=Stage.knowledge)
        # Force high token similarity by sharing most words
        # Use token-based path (no embeddings)
        report = run_lint([a, b], scope="test/scope")
        # Both items must have sufficient token overlap; inject embeddings for reliability
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.98, 0.1, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.contradictions) == 1
        pair_ids = {report.contradictions[0].item_a_id, report.contradictions[0].item_b_id}
        assert pair_ids == {a.id, b.id}

    def test_detects_english_negation_contradiction(self):
        a = _item(content="OceanBase does not support transactions", stage=Stage.knowledge)
        b = _item(content="OceanBase supports distributed transactions", stage=Stage.knowledge)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.98, 0.1, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.contradictions) == 1

    def test_similar_but_non_contradicting_items_not_flagged(self):
        a = _item(content="OceanBase supports MVCC isolation", stage=Stage.knowledge)
        b = _item(content="OceanBase supports read-committed isolation", stage=Stage.knowledge)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.99, 0.05, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.contradictions) == 0

    def test_raw_stage_items_excluded_from_contradiction_check(self):
        a = _item(content="not supported", stage=Stage.raw)
        b = _item(content="supported feature", stage=Stage.raw)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.99, 0.0, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.contradictions) == 0

    def test_contradiction_max_cap(self):
        items = []
        for i in range(20):
            a = _item(content=f"not supported feature {i}", stage=Stage.knowledge)
            b = _item(content=f"supported feature {i}", stage=Stage.knowledge)
            a.embedding = [1.0, float(i) * 0.001, 0.0]
            b.embedding = [0.99, float(i) * 0.001, 0.01]
            items.extend([a, b])
        report = run_lint(items, scope="test/scope", contradiction_max=3)
        assert len(report.contradictions) <= 3


# ---------------------------------------------------------------------------
# Distillation opportunity detection
# ---------------------------------------------------------------------------


class TestDistillOpportunity:
    def test_eligible_item_reported_as_opportunity(self):
        item = _item(
            content="plain text knowledge",
            stage=Stage.knowledge,
            access_count=5,
            relevance_boost=1.15,
            created_days_ago=4.0,
        )
        rule = HeuristicDistillRule(min_access_count=5, min_age_days=3.0, min_relevance_boost=1.1)
        report = run_lint([item], scope="test/scope", heuristic_rule=rule)
        assert len(report.distill_opportunities) == 1
        assert report.distill_opportunities[0].item_id == item.id

    def test_skill_items_excluded_from_distill_opportunities(self):
        item = _item(stage=Stage.skill, access_count=10, relevance_boost=1.5, created_days_ago=7.0)
        item.content = {"skill_type": "prompt", "name": "x", "body": "y"}
        rule = HeuristicDistillRule(min_access_count=5, min_age_days=3.0, min_relevance_boost=1.1)
        report = run_lint([item], scope="test/scope", heuristic_rule=rule)
        assert len(report.distill_opportunities) == 0

    def test_insufficient_access_count_not_flagged(self):
        item = _item(content="not enough access", stage=Stage.knowledge, access_count=2, relevance_boost=1.2, created_days_ago=5.0)
        rule = HeuristicDistillRule(min_access_count=5)
        report = run_lint([item], scope="test/scope", heuristic_rule=rule)
        assert len(report.distill_opportunities) == 0

    def test_too_young_item_not_flagged(self):
        item = _item(content="too young", stage=Stage.knowledge, access_count=10, relevance_boost=1.2, created_days_ago=1.0)
        rule = HeuristicDistillRule(min_age_days=3.0)
        report = run_lint([item], scope="test/scope", heuristic_rule=rule)
        assert len(report.distill_opportunities) == 0

    def test_non_string_content_excluded(self):
        item = _item(stage=Stage.knowledge, access_count=10, relevance_boost=1.5, created_days_ago=7.0)
        item.content = {"body": "structured"}
        rule = HeuristicDistillRule(min_access_count=5)
        report = run_lint([item], scope="test/scope", heuristic_rule=rule)
        assert len(report.distill_opportunities) == 0


# ---------------------------------------------------------------------------
# Consolidation hints
# ---------------------------------------------------------------------------


class TestConsolidationHints:
    def test_high_similarity_knowledge_items_produce_hint(self):
        a = _item(content="OceanBase indexing", stage=Stage.knowledge)
        b = _item(content="OceanBase indexing strategy", stage=Stage.knowledge)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.99, 0.1, 0.0]  # cosine ~0.995
        report = run_lint([a, b], scope="test/scope")
        assert len(report.consolidation_hints) == 1
        pair = {report.consolidation_hints[0].item_a_id, report.consolidation_hints[0].item_b_id}
        assert pair == {a.id, b.id}

    def test_low_similarity_items_produce_no_hint(self):
        a = _item(content="database indexing", stage=Stage.knowledge)
        b = _item(content="machine learning training", stage=Stage.knowledge)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.0, 1.0, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.consolidation_hints) == 0

    def test_non_knowledge_items_excluded_from_consolidation_hints(self):
        a = _item(content="some text", stage=Stage.extracted)
        b = _item(content="some text similar", stage=Stage.extracted)
        a.embedding = [1.0, 0.0, 0.0]
        b.embedding = [0.99, 0.1, 0.0]
        report = run_lint([a, b], scope="test/scope")
        assert len(report.consolidation_hints) == 0


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_clean_knowledge_base_scores_100(self):
        item = _item(content="active knowledge", stage=Stage.knowledge, access_count=3)
        report = run_lint([item], scope="test/scope")
        assert report.health_score == 100

    def test_orphans_reduce_health_score(self):
        items = [_item(content=f"orphan {i}") for i in range(5)]
        report = run_lint(items, scope="test/scope")
        assert report.health_score < 100

    def test_health_score_never_goes_below_zero(self):
        items = (
            [_item(content=f"orphan {i}") for i in range(20)]
            + [
                _item(content=f"not supported feature {i}", stage=Stage.knowledge,
                      embedding=[1.0, float(i) * 0.001, 0.0])
                for i in range(5)
            ]
        )
        report = run_lint(items, scope="test/scope")
        assert report.health_score >= 0

    def test_is_healthy_returns_true_when_no_findings(self):
        item = _item(content="fine item", access_count=1)
        report = run_lint([item], scope="test/scope")
        assert report.is_healthy()

    def test_is_healthy_returns_false_when_orphans_exist(self):
        item = _item(content="orphan item", access_count=0)
        report = run_lint([item], scope="test/scope")
        assert not report.is_healthy()


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


class TestLintReportToDict:
    def test_to_dict_contains_required_keys(self):
        item = _item(content="test")
        report = run_lint([item], scope="my/scope")
        d = report.to_dict()
        assert d["scope"] == "my/scope"
        assert "health_score" in d
        assert "orphans" in d
        assert "contradictions" in d
        assert "distill_opportunities" in d
        assert "consolidation_hints" in d
        assert "timestamp" in d
