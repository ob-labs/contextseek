"""Tests for HeuristicReranker.rank_score: weighted-linear combination so
feedback / overlap / quality features actually move the ranking."""

from __future__ import annotations

from contextseek.config.strategies import RetrievalStrategy
from contextseek.retrieval.components import HeuristicReranker


def _candidate(**kwargs) -> dict:
    base = {"content": "doc", "score": 0.9}
    base.update(kwargs)
    return base


class TestFeedbackChannel:
    def test_feedback_zero_does_not_bias(self) -> None:
        """feedback_score=0 (explicit) and absent feedback_score must produce
        the same rank_score. sigmoid(0)=0.5 must not leak as a positive bias
        on items that were never interacted with."""
        strategy = RetrievalStrategy()
        with_zero = _candidate(content="x", score=0.5, feedback_score=0.0, id="z")
        without_field = _candidate(content="x", score=0.5, id="nf")
        score_zero = HeuristicReranker.rank_score(
            with_zero, query="x", strategy=strategy
        )
        score_absent = HeuristicReranker.rank_score(
            without_field, query="x", strategy=strategy
        )
        assert score_zero == score_absent, (
            f"feedback_score=0 must not bias the score: {score_zero} vs {score_absent}"
        )

    def test_feedback_breaks_tie_when_recall_equal(self) -> None:
        """Non-zero feedback must still resolve ties — this verifies the sigmoid
        path for feedback_score != 0 was not accidentally disabled."""
        strategy = RetrievalStrategy()
        a = _candidate(content="alpha doc", score=0.9, feedback_score=2.0, id="a")
        b = _candidate(content="alpha doc", score=0.9, feedback_score=-2.0, id="b")
        result = HeuristicReranker().rerank([a, b], query="alpha", strategy=strategy)
        assert result[0]["id"] == "a"


class TestRerankFeatureWeights:
    def test_feedback_breaks_tie_when_recall_equal(self) -> None:
        """Two items with identical recall scores should be split by feedback.

        Pre-fix this would silently clip past 1.0 and produce identical
        post-cap scores."""
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()

        a = _candidate(content="alpha doc", score=0.9, feedback_score=2.0, id="a")
        b = _candidate(content="alpha doc", score=0.9, feedback_score=-2.0, id="b")
        result = reranker.rerank([a, b], query="alpha", strategy=strategy)
        assert result[0]["id"] == "a", (
            f"high-feedback item must rank first, got {[c['id'] for c in result]}"
        )

    def test_overlap_breaks_tie_when_recall_equal(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()

        # 'a' shares all query tokens; 'b' shares none.
        a = _candidate(content="alpha beta gamma", score=0.7, id="a")
        b = _candidate(content="zeta eta theta", score=0.7, id="b")
        result = reranker.rerank([a, b], query="alpha beta gamma", strategy=strategy)
        assert result[0]["id"] == "a"

    def test_quality_score_propagates(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()

        a = _candidate(content="x", score=0.5, quality_score=0.95, id="a")
        b = _candidate(content="x", score=0.5, quality_score=0.0, id="b")
        result = reranker.rerank([a, b], query="x", strategy=strategy)
        assert result[0]["id"] == "a"

    def test_no_feature_overflow_past_one(self) -> None:
        """The base score must stay in [0, 1] regardless of feature values.

        Before the fix, recall=0.95 + overlap*0.15 + feedback*0.20 + ...
        would cross 1.0 and be clipped, hiding feature contributions."""
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()
        cand = _candidate(
            content="alpha beta",
            score=0.99,
            feedback_score=10.0,
            quality_score=1.0,
            evidence_id="e1",
            id="a",
        )
        ranked = reranker.rerank([cand], query="alpha beta", strategy=strategy)
        assert 0.0 <= float(ranked[0]["_score"]) <= 1.0


class TestRerankPenalties:
    def test_archived_penalty_applies(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy(archive_penalty=0.5)
        a = _candidate(content="x", score=0.8, is_archived=True, id="a")
        b = _candidate(content="x", score=0.7, id="b")
        result = reranker.rerank([a, b], query="x", strategy=strategy)
        # Penalty should drop a below b despite higher raw recall.
        assert result[0]["id"] == "b"

    def test_conflict_penalty_applies(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy(conflict_penalty=0.5)
        a = _candidate(content="x", score=0.9, conflict_with=["other"], id="a")
        b = _candidate(content="x", score=0.6, id="b")
        result = reranker.rerank([a, b], query="x", strategy=strategy)
        assert result[0]["id"] == "b"


class TestImportanceLast:
    def test_importance_breaks_final_tie(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy(importance_alpha=1.0)
        a = _candidate(content="x", score=0.5, importance=2.0, id="a")
        b = _candidate(content="x", score=0.5, importance=0.5, id="b")
        result = reranker.rerank([a, b], query="x", strategy=strategy)
        assert result[0]["id"] == "a"


class TestStageWeighting:
    def test_skill_outranks_raw_at_equal_recall(self) -> None:
        """Default stage_weights (skill=1.0 > raw=0.3) should pull skill ahead
        even when raw has a slightly better recall score."""
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()
        skill = _candidate(content="x", score=0.7, stage="skill", id="skill")
        raw = _candidate(content="x", score=0.85, stage="raw", id="raw")
        result = reranker.rerank([skill, raw], query="x", strategy=strategy)
        assert result[0]["id"] == "skill"

    def test_knowledge_above_extracted(self) -> None:
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()
        knowledge = _candidate(content="x", score=0.5, stage="knowledge", id="k")
        extracted = _candidate(content="x", score=0.5, stage="extracted", id="e")
        result = reranker.rerank([knowledge, extracted], query="x", strategy=strategy)
        assert result[0]["id"] == "k"

    def test_strategy_override_applies(self) -> None:
        """Custom stage_weights must take precedence over the STAGE_CONFIDENCE fallback."""
        reranker = HeuristicReranker()
        # Invert the defaults: raw becomes the most preferred stage.
        strategy = RetrievalStrategy(
            stage_weights=(
                ("raw", 1.0),
                ("skill", 0.1),
            )
        )
        skill = _candidate(content="x", score=0.5, stage="skill", id="skill")
        raw = _candidate(content="x", score=0.5, stage="raw", id="raw")
        result = reranker.rerank([skill, raw], query="x", strategy=strategy)
        assert result[0]["id"] == "raw"

    def test_unknown_stage_does_not_zero_out(self) -> None:
        """Items with an unrecognised stage are treated as Stage.raw (×0.3),
        not zeroed out. Relative order must still follow recall."""
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy(stage_weights=())  # disable explicit weights
        a = _candidate(content="x", score=0.5, stage="custom_stage", id="a")
        b = _candidate(content="x", score=0.4, stage="custom_stage", id="b")
        result = reranker.rerank([a, b], query="x", strategy=strategy)
        # Neither should crash; relative order follows recall after raw weight.
        assert result[0]["id"] == "a"
        # Score must reflect raw weight (×0.3) — not the pre-fix ×1.0 identity.
        assert float(result[0]["_score"]) < 0.5, (
            "unknown stage must apply raw multiplier (×0.3), not pass through at ×1.0"
        )

    def test_missing_stage_treated_as_raw(self) -> None:
        """Items missing a stage field entirely are treated as Stage.raw (×0.3),
        so they rank below skill (×1.0) at equal recall."""
        reranker = HeuristicReranker()
        strategy = RetrievalStrategy()
        no_stage = _candidate(content="x", score=0.5, id="no_stage")
        skill = _candidate(content="x", score=0.5, stage="skill", id="skill")
        result = reranker.rerank([no_stage, skill], query="x", strategy=strategy)
        assert result[0]["id"] == "skill", (
            "skill (×1.0) must outrank missing-stage item treated as raw (×0.3)"
        )
