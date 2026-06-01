"""Tests for orchestrator's ``searchable`` filtering.

Covers the contract that items with ``searchable=False`` (e.g. raw items that
have been consumed by EvolutionEngine into an extracted successor) are hidden
from every recall channel — while items where ``searchable`` is absent or True
remain visible. Legacy payloads predating the field must still be returned.
"""

from __future__ import annotations

from typing import Any

from contextseek.config.strategies import RetrievalStrategy
from contextseek.retrieval.components import RecallQuery, RecallRoute
from contextseek.retrieval.orchestrator import RetrievalOrchestrator


class _FakeAdapter:
    def search(self, prefix, query, *, k, query_embedding=None):
        return []

    def ls(self, prefix):
        return []

    def read(self, ref):
        return None

    def write(self, ref, payload):
        pass

    def delete(self, ref):
        return True


def _payload(
    item_id: str,
    *,
    score: float = 0.5,
    content: str = "doc",
    searchable: bool | None = None,
) -> dict[str, Any]:
    p: dict[str, Any] = {
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
    if searchable is not None:
        p["searchable"] = searchable
    return p


class _FakeRoute(RecallRoute):
    def __init__(self, results: dict[str, list[dict[str, Any]]]):
        self._results = results

    def build_queries(self, query: str, strategy: RetrievalStrategy):
        return [RecallQuery(name, query) for name in self._results]

    def recall(self, adapter, *, prefix, recall_query, k):
        return list(self._results.get(recall_query.route_name, []))


class TestSearchableFilter:
    def test_unsearchable_hit_is_filtered_out(self) -> None:
        """An item explicitly marked ``searchable=False`` must not appear in
        the final hit list, even if recall returns it."""
        results = {
            "phrase": [
                _payload("visible_doc", searchable=True),
                _payload("consumed_raw", searchable=False),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        ids = {h.item.id for h in hits}
        assert "visible_doc" in ids
        assert "consumed_raw" not in ids, (
            "items with searchable=False must be filtered by orchestrator._keep()"
        )

    def test_searchable_default_true_is_kept(self) -> None:
        """Legacy payloads predating the ``searchable`` field (key absent) must
        be treated as searchable=True, not silently dropped."""
        results = {
            "phrase": [_payload("legacy_doc", searchable=None)],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        ids = {h.item.id for h in hits}
        assert ids == {"legacy_doc"}, (
            "missing searchable key must default to True (backwards compat)"
        )

    def test_unsearchable_filtered_before_rrf_fusion(self) -> None:
        """An unsearchable item appearing across multiple routes must not
        consume any rank slots — its searchable=True peer should fuse normally
        and lead the result list."""
        results = {
            "phrase": [
                _payload("consumed_raw", searchable=False, score=0.9),
                _payload("kept", searchable=True, score=0.5),
            ],
            "vector": [
                _payload("consumed_raw", searchable=False, score=0.9),
                _payload("kept", searchable=True, score=0.5),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        ids = [h.item.id for h in hits]
        assert ids == ["kept"], f"consumed_raw should be filtered before RRF; got {ids}"

    def test_only_unsearchable_returns_empty(self) -> None:
        """When every candidate is unsearchable, the orchestrator returns []
        instead of falling through to surface anything."""
        results = {
            "phrase": [
                _payload("a", searchable=False),
                _payload("b", searchable=False),
            ],
        }
        route = _FakeRoute(results)
        orchestrator = RetrievalOrchestrator(_FakeAdapter(), recall_route=route)
        hits = orchestrator.search(prefixes=["contextseek://t/p/"], query="q", k=5)
        assert hits == []
