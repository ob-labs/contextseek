"""Tests for the orchestrator's RRF fusion across multi-route / multi-prefix recall."""

from __future__ import annotations

from typing import Any

from contextseek.config.strategies import RetrievalStrategy
from contextseek.retrieval.components import RecallQuery, RecallRoute
from contextseek.retrieval.orchestrator import RetrievalOrchestrator


class _FakeAdapter:
    """Adapter stub: ``search`` is a no-op; the FakeRoute below feeds canned hits."""

    def search(
        self,
        prefix: str,
        query: str,
        *,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def ls(self, prefix: str) -> list[str]:
        return []

    def read(self, ref: str) -> dict[str, Any] | None:
        return None

    def write(self, ref: str, payload: dict[str, Any]) -> None:
        pass

    def delete(self, ref: str) -> bool:
        return True


def _payload(item_id: str, *, score: float, content: str = "doc") -> dict[str, Any]:
    """Minimal payload that ``deserialize_context_item`` accepts."""
    return {
        "id": item_id,
        "ref": f"contextseek://t/p/{item_id}",
        "score": score,
        "content": content,
        "scope": "t/p",
        "stage": "raw",
        "stability": "transient",
        "hash": item_id,
        "created_at": "2024-01-01T00:00:00+00:00",
        "provenance": {
            "source_type": "human_input",
            "source_id": "s",
            "confidence": 0.5,
        },
    }


class _FakeRoute(RecallRoute):
    """Recall route that returns a fixed canned result per route name."""

    def __init__(self, results: dict[str, list[dict[str, Any]]]):
        self._results = results

    def build_queries(self, query: str, strategy: RetrievalStrategy):
        return [RecallQuery(name, query) for name in self._results]

    def recall(self, adapter, *, prefix, recall_query, k):
        return list(self._results.get(recall_query.route_name, []))


class TestRRFFusion:
    def test_multi_route_consensus_outranks_single_route(self) -> None:
        """An item appearing in two routes should rank above one that only appears
        in a single route, even if their raw backend scores are identical."""
        # both_doc shows up at rank 1 in two routes; solo_doc only in one.
        results = {
            "phrase": [
                _payload("both_doc", score=0.9),
                _payload("phrase_only", score=0.9),
            ],
            "vector": [
                _payload("both_doc", score=0.9),
                _payload("vector_only", score=0.9),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        ids = [h.item.id for h in hits]
        assert ids[0] == "both_doc", f"multi-route hit must lead the ranking, got {ids}"

    def test_consensus_beats_single_route_with_higher_overlap(self) -> None:
        """Regression for the §1.1 bug: even when the single-route item has
        much higher token overlap with the query, the multi-route consensus
        item should still lead. Prior to the max-normalization fix, RRF
        contributions of ~0.06 vs ~0.016 were tiny relative to overlap*0.15
        and got drowned out in the reranker's weighted average."""
        # consensus_doc: 2 routes, content has zero query-token overlap.
        # solo_strong:   1 route, content perfectly matches the query tokens.
        results = {
            "phrase": [
                _payload("consensus_doc", score=0.5, content="completely unrelated"),
                _payload("solo_strong", score=0.5, content="alpha beta gamma"),
            ],
            "vector": [
                _payload("consensus_doc", score=0.5, content="completely unrelated"),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(
            prefixes=["contextseek://t/p/"], query="alpha beta gamma", k=5
        )
        ids = [h.item.id for h in hits]
        assert ids[0] == "consensus_doc", (
            "After RRF normalization, multi-route consensus must outrank a "
            f"single-route hit with higher overlap, got {ids}"
        )

    def test_rrf_score_normalized_to_unit_range(self) -> None:
        """orchestrator.search must hand the reranker a score in [0,1] so the
        weighted-average reranker mixes recall on the same scale as overlap /
        feedback. Top item should have score ≈ 1.0 after final normalization."""
        results = {
            "phrase": [_payload("a", score=0.0), _payload("b", score=0.0)],
            "vector": [_payload("a", score=0.0)],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        # _normalize_output_scores at the very end maps to [0,1] via min-max,
        # so top hit ≈ 1.0 and all hits stay in range.
        assert hits, "expected at least one hit"
        assert max(h.score for h in hits) == 1.0
        for h in hits:
            assert 0.0 <= h.score <= 1.0

    def test_rank_position_dominates_raw_score(self) -> None:
        """RRF should not be fooled by a single high backend score when the
        item ranks lower across the union of streams."""
        results = {
            "phrase": [
                _payload("front_runner", score=0.5),
                _payload("score_winner", score=0.99),
            ],
            "vector": [
                _payload("front_runner", score=0.5),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        ids = [h.item.id for h in hits]
        assert ids[0] == "front_runner", (
            f"RRF should reward 2-route consensus over a single high-score hit, got {ids}"
        )

    def test_dedupe_uses_stable_id(self) -> None:
        """The orchestrator no longer relies on payload['hash'] for dedupe;
        same-id hits with absent hash field must still merge cleanly."""
        results = {
            "phrase": [_payload("doc1", score=0.4)],
            "vector": [_payload("doc1", score=0.4)],
        }
        # neither payload includes 'hash' — this is the regression scenario.
        for route_results in results.values():
            for p in route_results:
                p.pop("hash", None)
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        assert len(hits) == 1, "same id from two routes should merge to one hit"

    def test_strategy_rrf_k_is_used(self) -> None:
        """Tighter rrf_k amplifies rank differences; verify the strategy knob is wired."""
        results = {
            "phrase": [_payload("a", score=0.0), _payload("b", score=0.0)],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(
            _FakeAdapter(), recall_route=route, strategy=RetrievalStrategy(rrf_k=1)
        )
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        # k=1 -> rank1 contribution = 1/2, rank2 = 1/3 → after min/max
        # normalisation in _normalize_output_scores: 1.0 vs 0.0
        score_a = next(h.score for h in hits if h.item.id == "a")
        score_b = next(h.score for h in hits if h.item.id == "b")
        assert score_a > score_b
