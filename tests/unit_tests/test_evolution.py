"""Tests for the evolution pipeline (extractor, merger, distiller, engine)."""

from datetime import datetime, timezone, timedelta

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage, Stability
from contextseek.evolution.distiller import SkillDistiller
from contextseek.evolution.engine import EvolutionEngine
from contextseek.evolution.extractor import HeuristicExtractor
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

    def test_extract_plain_text_returns_extracted_item(self):
        extractor = HeuristicExtractor()
        item = _make_item(content="plain text that is long enough to extract")
        results = extractor.extract(item)
        assert len(results) == 1
        assert results[0].stage == Stage.extracted
        assert "text_extracted" in (results[0].tags or [])

    def test_extract_empty_text_returns_empty(self):
        extractor = HeuristicExtractor()
        item = _make_item(content="")
        results = extractor.extract(item)
        assert results == []


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
