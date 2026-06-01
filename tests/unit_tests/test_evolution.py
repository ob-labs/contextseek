"""Tests for the evolution pipeline (extractor, merger, distiller, engine)."""

from datetime import datetime, timezone, timedelta

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.geo import GeoMetadata
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage, Stability
from contextseek.evolution.distiller import SkillDistiller
from contextseek.evolution.engine import EvolutionEngine
from contextseek.evolution.extractor import GeoExtractor, HeuristicExtractor
from contextseek.evolution.merger import (
    ConvergenceMerger,
    semantic_similarity,
    decay_score,
)


def _make_item(content="test", stage=Stage.raw, scope="t/p/s", **kwargs):
    defaults = {
        "id": _generate_id(),
        "content": content,
        "scope": scope,
        "provenance": Provenance(
            source_type=SourceType.trace_extraction,
            source_id="test",
            confidence=0.6,
        ),
        "stage": stage,
        "tags": [],
        "links": [],
        "created_at": _utc_now(),
    }
    defaults.update(kwargs)
    return ContextItem(**defaults)


class TestHeuristicExtractor:
    def test_extract_trace(self):
        extractor = HeuristicExtractor()
        item = _make_item(
            content={
                "input": "write a function",
                "output": "here is the code",
                "tool_calls": [{"tool": "editor", "result": "file saved"}],
            }
        )
        results = extractor.extract(item)
        assert len(results) >= 2  # input + tool + output
        assert all(r.stage == Stage.extracted for r in results)

    def test_extract_non_trace_returns_empty(self):
        extractor = HeuristicExtractor()
        item = _make_item(content="plain text")
        results = extractor.extract(item)
        assert results == []


class TestGeoExtractor:
    def test_structured_mode_promotes_geo_field(self):
        extractor = GeoExtractor(
            geo_field="destination_geo",
            geo_type="frequent_location",
            label="工作日目的地",
            location_type="workplace",
            extra_tags=["commute_destination"],
        )
        item = _make_item(
            content={
                "input": "工作日早上出发导航",
                "output": "已到达目的地",
                "destination_geo": {"lat": 31.2285, "lon": 121.4762},
            }
        )
        results = extractor.extract(item)
        assert len(results) == 1
        out = results[0]
        assert out.stage == Stage.extracted
        # coordinates promoted under canonical content["geo"]
        assert out.content["geo"]["lat"] == 31.2285
        assert out.content["geo"]["geo_type"] == "frequent_location"
        assert out.content["label"] == "工作日目的地"
        assert out.content["location_type"] == "workplace"
        assert "commute_destination" in out.tags
        assert GeoMetadata.from_content(out.content) is not None

    def test_structured_mode_skips_when_geo_field_missing(self):
        extractor = GeoExtractor(
            geo_field="destination_geo", geo_type="frequent_location", label="x"
        )
        item = _make_item(content={"input": "no geo here", "output": "done"})
        assert extractor.extract(item) == []

    def test_structured_mode_carries_through_business_fields(self):
        """structured mode must pass through non-location/control fields from
        raw.content to extracted.content; otherwise the downstream merger's
        LLM won't see the original semantics (e.g. dwell time, wait behavior),
        and will only produce geo-only knowledge.
        """
        extractor = GeoExtractor(
            geo_field="destination_geo",
            geo_type="frequent_location",
            label="周末课外班",
            location_type="kids_class",
        )
        item = _make_item(
            content={
                "input": "周末早上送孩子去课外班",
                "output": "已到达课外班，等待约 2 小时",
                "destination_geo": {"lat": 31.2185, "lon": 121.4815},
                "trip_phase": "weekend_morning",
                "weekday": False,
                "dwell_hours": 2.0,
                "wait_behavior": "nearby_parking",
            }
        )
        out = extractor.extract(item)[0]
        # Location / control fields: still handled by structured backbone;
        # destination_geo is not retained separately.
        assert "destination_geo" not in out.content
        assert out.content["geo"]["lat"] == 31.2185
        assert out.content["label"] == "周末课外班"
        assert out.content["location_type"] == "kids_class"
        # Business fields: must be passed through so merger's content_text
        # includes the intent semantics.
        assert out.content["input"] == "周末早上送孩子去课外班"
        assert out.content["output"] == "已到达课外班，等待约 2 小时"
        assert out.content["trip_phase"] == "weekend_morning"
        assert out.content["weekday"] is False
        assert out.content["dwell_hours"] == 2.0
        assert out.content["wait_behavior"] == "nearby_parking"

    def test_structured_mode_does_not_override_label_with_raw(self):
        """If raw.content coincidentally carries label/location_type or other
        structured backbone fields, the structured backbone values take
        precedence (to prevent business data from overriding control fields)."""
        extractor = GeoExtractor(
            geo_field="destination_geo",
            geo_type="frequent_location",
            label="周末课外班",
            location_type="kids_class",
        )
        item = _make_item(
            content={
                "destination_geo": {"lat": 1.0, "lon": 2.0},
                "label": "raw 自带的脏 label",
                "location_type": "raw_should_be_ignored",
                "dwell_hours": 1.5,
            }
        )
        out = extractor.extract(item)[0]
        assert out.content["label"] == "周末课外班"
        assert out.content["location_type"] == "kids_class"
        assert out.content["dwell_hours"] == 1.5

    def test_decorator_mode_enriches_inner_output(self):
        extractor = GeoExtractor(
            geo_field="destination_geo",
            geo_type="frequent_location",
            extra_tags=["pickup"],
        )
        item = _make_item(
            content={
                "input": "go somewhere",
                "output": "arrived",
                "destination_geo": {"lat": 1.0, "lon": 2.0},
            }
        )
        results = extractor.extract(item)
        assert len(results) >= 2  # delegates to HeuristicExtractor (input + output)
        assert all(r.content["geo"]["lat"] == 1.0 for r in results)
        assert all("geo_extracted" in r.tags and "pickup" in r.tags for r in results)

    def test_decorator_mode_degrades_without_geo(self):
        extractor = GeoExtractor(
            geo_field="destination_geo", geo_type="frequent_location"
        )
        item = _make_item(content={"input": "go", "output": "done"})
        results = extractor.extract(item)
        # falls back to pure inner behaviour: content stays plain string slices
        assert len(results) >= 2
        assert all(isinstance(r.content, str) for r in results)


class TestConvergenceMerger:
    def test_no_merge_below_threshold(self):
        merger = ConvergenceMerger(min_cluster_size=3)
        items = [
            _make_item(content=f"unique content {i}", stage=Stage.extracted)
            for i in range(5)
        ]
        kept, archived = merger.merge(items)
        assert len(archived) == 0

    def test_merge_similar_items(self):
        merger = ConvergenceMerger(similarity_threshold=0.5, min_cluster_size=2)
        items = [
            _make_item(
                content="the quick brown fox jumps over the lazy dog",
                stage=Stage.extracted,
            ),
            _make_item(
                content="the quick brown fox jumps over the lazy cat",
                stage=Stage.extracted,
            ),
            _make_item(
                content="the quick brown fox jumps over the lazy bird",
                stage=Stage.extracted,
            ),
        ]
        kept, archived = merger.merge(items)
        assert len(archived) >= 2
        knowledge_items = [it for it in kept if it.stage == Stage.knowledge]
        assert len(knowledge_items) >= 1

    def test_merged_sources_remain_searchable(self):
        """Convergence merge must NOT hide the source extracted items from
        retrieval. They are still independently useful mid-grained memories;
        only ``superseded_by`` is recorded as merge provenance.
        Pairs with ``RetrievalOrchestrator._keep()`` which filters on
        ``searchable=False`` — extracted items absorbed into a knowledge
        synthesis stay searchable so multi-granularity recall keeps working.
        """
        merger = ConvergenceMerger(similarity_threshold=0.5, min_cluster_size=2)
        items = [
            _make_item(
                content="the quick brown fox jumps over the lazy dog",
                stage=Stage.extracted,
            ),
            _make_item(
                content="the quick brown fox jumps over the lazy cat",
                stage=Stage.extracted,
            ),
        ]
        kept, archived = merger.merge(items)
        knowledge = [it for it in kept if it.stage == Stage.knowledge]
        assert knowledge, "merge should produce at least one knowledge item"
        merged_id = knowledge[0].id
        for src in archived:
            assert src.searchable is True, (
                "merged source extracted items must remain searchable"
            )
            assert src.superseded_by == merged_id, (
                "merged source must record superseded_by as merge provenance"
            )

    def test_semantic_similarity(self):
        assert semantic_similarity("hello world", "hello world") == 1.0
        assert semantic_similarity("hello world", "goodbye moon") == 0.0
        assert 0.0 < semantic_similarity("hello world foo", "hello world bar") < 1.0


class TestSkillDistiller:
    def test_identify_candidates(self):
        distiller = SkillDistiller(min_use_count=5, min_relevance_boost=1.0)
        eligible = _make_item(
            content={"body": "do something", "name": "test_skill"},
            stage=Stage.knowledge,
            tags=["procedure"],
            access_count=10,
            relevance_boost=1.5,
        )
        not_eligible = _make_item(
            content="plain text",
            stage=Stage.knowledge,
            access_count=1,
        )
        candidates = distiller.identify_candidates([eligible, not_eligible])
        assert len(candidates) == 1
        assert candidates[0].id == eligible.id

    def test_distill(self):
        distiller = SkillDistiller()
        item = _make_item(
            content={
                "body": "run tests",
                "name": "run_tests",
                "description": "Run test suite",
            },
            stage=Stage.knowledge,
            tags=["procedure"],
            access_count=20,
            relevance_boost=1.5,
        )
        skill = distiller.distill(item)
        assert skill.stage == Stage.skill
        assert skill.stability == Stability.permanent
        assert "auto_distilled" in skill.tags


class TestEvolutionEngine:
    def test_evolve_empty(self):
        engine = EvolutionEngine()
        new_items, archived, report = engine.evolve([])
        assert new_items == []
        assert archived == []

    def test_evolve_raw_traces(self):
        engine = EvolutionEngine()
        item = _make_item(
            content={"input": "hello", "output": "world", "tool_calls": []},
            stage=Stage.raw,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        new_items, archived, report = engine.evolve([item])
        assert report.evolved_count > 0
        assert all(
            it.stage == Stage.extracted
            for it in new_items
            if it.stage == Stage.extracted
        )


class TestDecayScore:
    def test_recent_items_score_higher(self):
        recent = _make_item(created_at=_utc_now())
        old = _make_item(created_at=datetime.now(timezone.utc) - timedelta(days=30))
        assert decay_score(recent) > decay_score(old)
