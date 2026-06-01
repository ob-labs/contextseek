"""Tests for the dreaming mechanism (consolidation + divergence)."""

from datetime import datetime, timedelta, timezone

from contextseek.config.strategies import DreamStrategy
from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import STAGE_CONFIDENCE, Stability, Stage
from contextseek.evolution.dreaming import (
    ConsolidationEngine,
    ConsolidationResult,
    DivergenceEngine,
    DreamEngine,
    DreamReport,
)
from contextseek.policies.decay import DecayConfig, compute_decay


def _make_item(
    content="test",
    stage=Stage.extracted,
    scope="t/p/s",
    tags=None,
    access_count=3,
    created_at=None,
    **kwargs,
):
    return ContextItem(
        id=_generate_id(),
        content=content,
        scope=scope,
        provenance=Provenance(
            source_type=SourceType.trace_extraction,
            source_id="test",
            confidence=0.6,
        ),
        stage=stage,
        tags=tags or [],
        access_count=access_count,
        created_at=created_at or _utc_now(),
        **kwargs,
    )


# ═══════════════════════════════════════════
# ConsolidationEngine
# ═══════════════════════════════════════════


class TestConsolidationEngine:
    def test_consolidation_finds_patterns_in_similarity_window(self):
        """Items with similarity in (0.35, 0.72) get consolidated."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.3, 0.8),
            min_items_for_dream=2,
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(
                content="deployment failed due to memory issue in staging server",
                tags=["ops"],
                access_count=3,
            ),
            _make_item(
                content="deployment failed due to cpu issue in staging environment",
                tags=["ops"],
                access_count=3,
            ),
        ]

        result = engine.consolidate(items)
        assert result.patterns_found >= 1
        assert len(result.items) >= 1

        # Pattern item should have correct properties
        pattern = result.items[0]
        assert pattern.stage == Stage.extracted
        assert pattern.stability == Stability.transient
        assert "dreamed" in pattern.tags
        assert "consolidation" in pattern.tags
        assert pattern.provenance.source_type == SourceType.dream_consolidation

    def test_consolidation_filters_low_access_items(self):
        """Items below consolidation_min_access are excluded."""
        strategy = DreamStrategy(consolidation_min_access=5)
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="related topic alpha beta", access_count=1),
            _make_item(content="related topic alpha gamma", access_count=1),
        ]

        result = engine.consolidate(items)
        assert result.patterns_found == 0
        assert len(result.items) == 0

    def test_consolidation_filters_old_items(self):
        """Items outside the consolidation window are excluded."""
        strategy = DreamStrategy(
            consolidation_window_hours=1.0,
            consolidation_min_access=1,
        )
        engine = ConsolidationEngine(strategy=strategy)

        old_time = datetime.now(timezone.utc) - timedelta(hours=5)
        items = [
            _make_item(content="related topic alpha beta", created_at=old_time),
            _make_item(content="related topic alpha gamma", created_at=old_time),
        ]

        result = engine.consolidate(items)
        assert result.patterns_found == 0

    def test_consolidation_excludes_dreamed_items(self):
        """Items already tagged as dreamed are not re-processed."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.3, 0.8),
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(
                content="related alpha beta gamma", tags=["dreamed", "consolidation"]
            ),
            _make_item(
                content="related alpha beta delta", tags=["dreamed", "consolidation"]
            ),
        ]

        result = engine.consolidate(items)
        assert result.patterns_found == 0

    def test_consolidation_max_outputs_cap(self):
        """At most consolidation_max_outputs patterns are produced."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
            consolidation_max_outputs=1,
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="alpha beta gamma delta epsilon"),
            _make_item(content="alpha beta gamma delta zeta"),
            _make_item(content="alpha beta gamma delta eta"),
        ]

        result = engine.consolidate(items)
        assert len(result.items) <= 1

    def test_consolidation_links_point_to_sources(self):
        """Dream items should have synthesized_from links to source items."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="shared topic alpha beta gamma delta"),
            _make_item(content="shared topic alpha beta gamma epsilon"),
        ]

        result = engine.consolidate(items)
        if result.items:
            pattern = result.items[0]
            source_ids = {it.id for it in items}
            for link in pattern.links:
                assert link.relation == LinkType.synthesized_from
                assert link.target_id in source_ids

    def test_consolidation_content_is_pattern_plus_evidence(self):
        """Consolidation item content carries the summary plus primary_evidence."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="shared topic alpha beta gamma delta"),
            _make_item(content="shared topic alpha beta gamma epsilon"),
        ]

        result = engine.consolidate(items)
        assert result.items
        pattern = result.items[0]
        assert isinstance(pattern.content, dict)
        assert "pattern" in pattern.content
        assert "primary_evidence" in pattern.content
        # Clean summary string is preserved on abstract for embeddings/display.
        assert pattern.abstract == pattern.content["pattern"]

    def test_consolidation_primary_evidence_picks_highest_stage(self):
        """primary_evidence is the cluster's highest-authority (stage) source."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
        )
        engine = ConsolidationEngine(strategy=strategy)

        # Same text so they cluster; different stages so authority differs.
        raw_item = _make_item(
            content="commute destination alpha beta gamma",
            stage=Stage.raw,
        )
        knowledge_item = _make_item(
            content="commute destination alpha beta delta",
            stage=Stage.knowledge,
        )

        result = engine.consolidate([raw_item, knowledge_item])
        assert result.items
        ev = result.items[0].content["primary_evidence"]
        # knowledge (0.85) beats raw (0.3)
        assert ev["stage"] == Stage.knowledge.value
        assert ev["confidence"] == STAGE_CONFIDENCE[Stage.knowledge]
        assert ev["source_id"] == knowledge_item.id
        # Source content is preserved verbatim (generic — no field inspection).
        assert ev["content"] == knowledge_item.content

    def test_consolidation_strengthened_links(self):
        """Cluster pairs get strengthened_links recorded."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="common words shared between both items here"),
            _make_item(content="common words shared between both texts here"),
        ]

        result = engine.consolidate(items)
        if result.patterns_found > 0:
            assert len(result.strengthened_links) >= 1
            # Each link is (id_a, id_b, sim)
            for a_id, b_id, sim in result.strengthened_links:
                assert isinstance(sim, float)


# ═══════════════════════════════════════════
# DivergenceEngine
# ═══════════════════════════════════════════


class TestDivergenceEngine:
    def test_divergence_needs_min_clusters(self):
        """Divergence requires at least divergence_min_clusters clusters."""
        strategy = DreamStrategy(divergence_min_clusters=2)
        engine = DivergenceEngine(strategy=strategy)

        # Only one cluster
        result = engine.diverge(
            [
                [_make_item(content="one"), _make_item(content="two")],
            ]
        )
        assert len(result.items) == 0

    def test_divergence_generates_hypotheses(self):
        """Given 2+ clusters, divergence generates hypothesis items."""
        strategy = DreamStrategy(divergence_min_clusters=2, divergence_max_outputs=3)
        engine = DivergenceEngine(strategy=strategy)

        cluster_a = [
            _make_item(content="database replication strategy", tags=["infra"]),
            _make_item(content="database backup plan", tags=["infra"]),
        ]
        cluster_b = [
            _make_item(content="user onboarding flow design", tags=["product"]),
            _make_item(content="user retention metrics", tags=["product"]),
        ]

        result = engine.diverge([cluster_a, cluster_b])
        assert len(result.items) >= 1

        hypothesis = result.items[0]
        assert hypothesis.stage == Stage.extracted
        assert "dreamed" in hypothesis.tags
        assert "divergence" in hypothesis.tags
        assert hypothesis.provenance.source_type == SourceType.dream_divergence

    def test_divergence_links_to_both_sources(self):
        """Hypothesis items link to both cross-cluster representatives."""
        strategy = DreamStrategy(divergence_min_clusters=2)
        engine = DivergenceEngine(strategy=strategy)

        cluster_a = [_make_item(content="topic alpha one", tags=["a"], importance=2.0)]
        cluster_b = [_make_item(content="topic beta two", tags=["b"], importance=2.0)]

        result = engine.diverge([cluster_a, cluster_b])
        if result.items:
            hyp = result.items[0]
            assert len(hyp.links) == 2
            link_targets = {lnk.target_id for lnk in hyp.links}
            assert cluster_a[0].id in link_targets
            assert cluster_b[0].id in link_targets
            for link in hyp.links:
                assert link.relation == LinkType.synthesized_from

    def test_divergence_max_outputs(self):
        """At most divergence_max_outputs hypotheses are produced."""
        strategy = DreamStrategy(divergence_min_clusters=2, divergence_max_outputs=1)
        engine = DivergenceEngine(strategy=strategy)

        clusters = [
            [_make_item(content=f"cluster {i} content") for _ in range(2)]
            for i in range(4)
        ]

        result = engine.diverge(clusters)
        assert len(result.items) <= 1

    def test_divergence_with_llm(self):
        """When LLM is provided, it generates hypothesis text."""
        strategy = DreamStrategy(divergence_min_clusters=2)
        llm_called = []

        def mock_llm(prompt: str) -> str:
            llm_called.append(prompt)
            return "These two observations might be connected through feedback loops."

        engine = DivergenceEngine(strategy=strategy, llm=mock_llm)

        cluster_a = [_make_item(content="system reliability")]
        cluster_b = [_make_item(content="user satisfaction")]

        result = engine.diverge([cluster_a, cluster_b])
        assert len(llm_called) == 1
        assert "feedback loops" in result.items[0].content


# ═══════════════════════════════════════════
# DreamEngine (full cycle)
# ═══════════════════════════════════════════


class TestDreamEngine:
    def test_full_dream_cycle(self):
        """DreamEngine runs consolidation + divergence end-to-end."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
            divergence_min_clusters=2,
            min_items_for_dream=3,
            cooldown_hours=0.0,
        )
        engine = DreamEngine(strategy=strategy)

        items = [
            _make_item(content="alpha beta gamma shared topic one", tags=["a"]),
            _make_item(content="alpha beta gamma shared topic two", tags=["a"]),
            _make_item(content="completely different subject matter x", tags=["b"]),
            _make_item(content="completely different subject matter y", tags=["b"]),
        ]

        report = engine.dream(items)
        assert isinstance(report, DreamReport)
        assert report.total_dream_items >= 0
        assert isinstance(report.consolidation, ConsolidationResult)

    def test_cooldown_prevents_repeat_dream(self):
        """Dream is skipped if cooldown hasn't elapsed."""
        strategy = DreamStrategy(
            min_items_for_dream=2,
            cooldown_hours=1.0,
            consolidation_min_access=1,
        )
        engine = DreamEngine(strategy=strategy)

        items = [
            _make_item(content="alpha beta gamma shared topic one"),
            _make_item(content="alpha beta gamma shared topic two"),
            _make_item(content="alpha beta gamma shared topic three"),
        ]

        # First dream should work
        engine.dream(items)
        # Second dream should be blocked by cooldown
        report2 = engine.dream(items)
        assert report2.total_dream_items == 0

    def test_min_items_threshold(self):
        """Dream is skipped if fewer than min_items_for_dream items."""
        strategy = DreamStrategy(min_items_for_dream=100, cooldown_hours=0.0)
        engine = DreamEngine(strategy=strategy)

        items = [_make_item(content=f"item {i}") for i in range(5)]
        report = engine.dream(items)
        assert report.total_dream_items == 0

    def test_dream_with_deleted_items_excluded(self):
        """Deleted items are excluded from dream input."""
        strategy = DreamStrategy(
            min_items_for_dream=2,
            cooldown_hours=0.0,
            consolidation_min_access=1,
        )
        engine = DreamEngine(strategy=strategy)

        normal = _make_item(content="active item with content")
        deleted = _make_item(content="deleted item")
        deleted.soft_delete("test")

        # Only 1 active item — below min_items_for_dream=2
        report = engine.dream([normal, deleted])
        assert report.total_dream_items == 0


# ═══════════════════════════════════════════
# Decay integration
# ═══════════════════════════════════════════


class TestDreamDecay:
    def test_dreamed_items_decay_faster(self):
        """Items tagged 'dreamed' with no access decay 3x faster."""
        config = DecayConfig(half_life_days=7.0, dream_decay_multiplier=3.0)
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)

        normal = _make_item(content="normal item", access_count=0, created_at=created)
        normal.importance = 1.0

        dreamed = _make_item(
            content="dream item",
            tags=["dreamed", "consolidation"],
            access_count=0,
            created_at=created,
        )
        dreamed.importance = 1.0

        normal_decay = compute_decay(normal, now=now, config=config)
        dream_decay = compute_decay(dreamed, now=now, config=config)

        # Dreamed item should have lower importance after decay
        assert dream_decay < normal_decay

    def test_accessed_dream_items_decay_normally(self):
        """Dreamed items that have been accessed decay at normal rate."""
        config = DecayConfig(half_life_days=7.0, dream_decay_multiplier=3.0)
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)

        normal = _make_item(content="normal item", access_count=2, created_at=created)
        normal.importance = 1.0

        dreamed_accessed = _make_item(
            content="dream item accessed",
            tags=["dreamed", "consolidation"],
            access_count=2,
            created_at=created,
        )
        dreamed_accessed.importance = 1.0

        normal_decay = compute_decay(normal, now=now, config=config)
        dream_decay = compute_decay(dreamed_accessed, now=now, config=config)

        # Accessed dream item should decay at same rate as normal
        assert abs(normal_decay - dream_decay) < 0.01


# ═══════════════════════════════════════════
# Dream item properties
# ═══════════════════════════════════════════


class TestDreamItemProperties:
    def test_consolidation_item_properties(self):
        """Consolidation items have correct stage, tags, source_type."""
        strategy = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
        )
        engine = ConsolidationEngine(strategy=strategy)

        items = [
            _make_item(content="shared common words between these items"),
            _make_item(content="shared common words between those items"),
        ]

        result = engine.consolidate(items)
        if result.items:
            item = result.items[0]
            assert item.stage == Stage.extracted
            assert item.stability == Stability.transient
            assert "dreamed" in item.tags
            assert "consolidation" in item.tags
            assert item.provenance.source_type == SourceType.dream_consolidation
            assert item.provenance.confidence == strategy.dream_initial_confidence

    def test_divergence_item_properties(self):
        """Divergence items have correct properties and lower confidence."""
        strategy = DreamStrategy(
            divergence_min_clusters=2,
            dream_initial_confidence=0.35,
        )
        engine = DivergenceEngine(strategy=strategy)

        clusters = [
            [_make_item(content="cluster a content", tags=["a"])],
            [_make_item(content="cluster b content", tags=["b"])],
        ]

        result = engine.diverge(clusters)
        if result.items:
            item = result.items[0]
            assert item.stage == Stage.extracted
            assert item.stability == Stability.transient
            assert "dreamed" in item.tags
            assert "divergence" in item.tags
            assert item.provenance.source_type == SourceType.dream_divergence
            # Divergence confidence = initial * 0.85
            expected_conf = 0.35 * 0.85
            assert abs(item.provenance.confidence - expected_conf) < 0.01


# ═══════════════════════════════════════════
# ContextSeek.dream() API integration
# ═══════════════════════════════════════════


class TestContextSeekDreamAPI:
    def test_dream_api_dry_run(self):
        """ContextSeek.dream(dry_run=True) returns report without persisting."""
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        scope = "test/dream/api"

        # Add enough items
        for i in range(12):
            item = ctx.add(
                content=f"observation about deployment patterns variant {i}",
                scope=scope,
                source="test",
                source_type=SourceType.trace_extraction,
                tags=["ops"],
                check_conflicts=False,
            )
            # Simulate access to pass consolidation_min_access
            ref = ctx.resolver.ref_for(scope, item.id)
            ctx.feedback(ref, scope=scope, score=0.5)
            ctx.feedback(ref, scope=scope, score=0.5)

        report = ctx.dream(scope=scope, dry_run=True)
        assert isinstance(report, DreamReport)

        # dry_run should not persist new items
        items_after = ctx._list_items(scope)
        # Should still be exactly the 12 we added
        assert len(items_after) == 12

    def test_dream_api_persists_items(self):
        """ContextSeek.dream(dry_run=False) persists dream items."""
        from contextseek.client.contextseek import ContextSeek
        from contextseek.config.strategies import DreamStrategy, StrategyConfig

        dream_cfg = DreamStrategy(
            consolidation_min_access=1,
            consolidation_similarity_range=(0.2, 0.9),
            min_items_for_dream=3,
            cooldown_hours=0.0,
        )
        strategy = StrategyConfig(dream=dream_cfg)
        ctx = ContextSeek(strategy=strategy)
        scope = "test/dream/persist"

        # Add similar items
        for i in range(5):
            ctx.add(
                content=f"shared deployment pattern alpha beta gamma variant {i}",
                scope=scope,
                source="test",
                source_type=SourceType.trace_extraction,
                tags=["ops"],
                check_conflicts=False,
            )

        before_count = len(ctx._list_items(scope))
        report = ctx.dream(scope=scope, dry_run=False)

        if report.total_dream_items > 0:
            after_count = len(ctx._list_items(scope))
            assert after_count > before_count
