"""Tests for retrieval orchestrator and components."""

from contextseek.retrieval.components import (
    DefaultRecallRoute,
    HeuristicReranker,
    RelationAwareReranker,
    tokens,
)
from contextseek.config.strategies import RetrievalStrategy
from contextseek.domain.context_item import ContextItem
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance
from contextseek.domain.serialization import serialize_context_item
from contextseek.retrieval.orchestrator import (
    RetrievalOrchestrator,
    _normalize_output_scores,
)


class TestTokens:
    def test_basic(self):
        result = tokens("Hello World")
        assert result == ["hello", "world"]

    def test_chinese(self):
        result = tokens("你好世界")
        assert "你好世界" in result


class TestDefaultRecallRoute:
    def test_build_queries(self):
        route = DefaultRecallRoute()
        strategy = RetrievalStrategy()
        queries = route.build_queries("test query", strategy)
        assert len(queries) >= 1
        assert any(q.route_name == "phrase" for q in queries)

    def test_empty_query(self):
        route = DefaultRecallRoute()
        strategy = RetrievalStrategy()
        assert route.build_queries("", strategy) == []


class TestHeuristicReranker:
    def test_rerank_by_score(self):
        reranker = HeuristicReranker()
        candidates = [
            {"content": "low", "score": 0.1},
            {"content": "high", "score": 0.9},
            {"content": "mid", "score": 0.5},
        ]
        result = reranker.rerank(candidates, query="test", strategy=RetrievalStrategy())
        scores = [float(r["_score"]) for r in result]
        assert scores == sorted(scores, reverse=True)


class StaticReranker:
    def rerank(self, candidates, *, query, strategy, geo_query=None):
        for item in candidates:
            item["_score"] = float(item.get("score", 0.0))
        return sorted(candidates, key=lambda item: item["_score"], reverse=True)


class TestRelationAwareReranker:
    def test_uses_real_links_between_candidates(self):
        strategy = RetrievalStrategy(link_boost=0.3)
        reranker = RelationAwareReranker(inner=StaticReranker())
        ranked = reranker.rerank(
            [
                {
                    "id": "claim",
                    "content": "claim",
                    "score": 0.5,
                    "links": [
                        {
                            "target_id": "evidence",
                            "relation": "supported_by",
                            "strength": 1.0,
                        }
                    ],
                },
                {"id": "evidence", "content": "evidence", "score": 0.7},
            ],
            query="claim",
            strategy=strategy,
        )

        claim = next(item for item in ranked if item["id"] == "claim")
        assert claim["_score"] == 0.71

    def test_penalizes_refuted_and_superseded_candidates(self):
        reranker = RelationAwareReranker(inner=StaticReranker())
        ranked = reranker.rerank(
            [
                {
                    "id": "old",
                    "content": "old",
                    "score": 0.8,
                    "links": [
                        {
                            "target_id": "refuter",
                            "relation": "refuted_by",
                            "strength": 1.0,
                        }
                    ],
                },
                {
                    "id": "new",
                    "content": "new",
                    "score": 0.7,
                    "links": [
                        {
                            "target_id": "old",
                            "relation": "supersedes",
                            "strength": 1.0,
                        }
                    ],
                },
                {"id": "refuter", "content": "refuter", "score": 0.9},
            ],
            query="old",
            strategy=RetrievalStrategy(),
        )

        old = next(item for item in ranked if item["id"] == "old")
        assert old["_score"] < 0.8


class FakeAdapter:
    def __init__(self, payloads, search_ids):
        self.payloads = payloads
        self.search_ids = search_ids

    def read(self, ref):
        return self.payloads.get(ref)

    def search(self, prefix, query, k=20, **kwargs):
        return [
            dict(self.payloads[f"{prefix}{item_id}"]) for item_id in self.search_ids[:k]
        ]


class TestRetrievalOrchestratorLinkExpansion:
    def test_expands_linked_items_into_candidates(self):
        scope = "tenant/project/session"
        prefix = f"contextseek://{scope}/"
        seed = _item(
            "seed",
            scope,
            "why scheme x was chosen",
            links=[Link("evidence", LinkType.supported_by, strength=1.0)],
        )
        evidence = _item("evidence", scope, "benchmark logs and tradeoff notes")
        payloads = {
            f"{prefix}seed": serialize_context_item(seed) | {"score": 1.0},
            f"{prefix}evidence": serialize_context_item(evidence),
        }
        orchestrator = RetrievalOrchestrator(adapter=FakeAdapter(payloads, ["seed"]))

        hits, stats = orchestrator.search(
            prefixes=[prefix],
            query="scheme x",
            k=2,
            with_stats=True,
        )

        assert {hit.item.id for hit in hits} == {"seed", "evidence"}
        assert "link" in stats.recall_paths

    def test_keeps_best_score_when_same_target_reached_by_multiple_paths(self):
        scope = "tenant/project/session"
        prefix = f"contextseek://{scope}/"
        seed = _item(
            "seed",
            scope,
            "seed",
            links=[
                Link("path_low", LinkType.supported_by, strength=1.0),
                Link("path_high", LinkType.supported_by, strength=1.0),
            ],
        )
        path_low = _item(
            "path_low",
            scope,
            "path_low",
            links=[Link("shared", LinkType.supported_by, strength=0.2)],
        )
        path_high = _item(
            "path_high",
            scope,
            "path_high",
            links=[Link("shared", LinkType.supported_by, strength=0.9)],
        )
        shared = _item("shared", scope, "shared")

        payloads = {
            f"{prefix}seed": serialize_context_item(seed) | {"score": 1.0},
            f"{prefix}path_low": serialize_context_item(path_low),
            f"{prefix}path_high": serialize_context_item(path_high),
            f"{prefix}shared": serialize_context_item(shared),
        }
        orchestrator = RetrievalOrchestrator(adapter=FakeAdapter(payloads, ["seed"]))

        expanded = orchestrator._expand_linked_candidates(
            seeds=[payloads[f"{prefix}seed"]],
            strategy=RetrievalStrategy(
                link_expansion_max_depth=2,
                link_expansion_decay=1.0,
                link_expansion_relations=("supported_by",),
            ),
            include_deleted=False,
            keep=lambda _: True,
        )

        shared_item = next(item for item in expanded if item["id"] == "shared")
        assert shared_item["_link_expansion_score"] == 0.9
        assert shared_item["_link_expansion_from"] == "path_high"


class TestRerankerAssembly:
    def test_wraps_injected_reranker_so_llm_mode_keeps_relations(self):
        inner = StaticReranker()
        orchestrator = RetrievalOrchestrator(
            adapter=FakeAdapter({}, []), reranker=inner
        )
        reranker = orchestrator._build_reranker(RetrievalStrategy())
        assert isinstance(reranker, RelationAwareReranker)
        assert reranker._inner is inner

    def test_toggle_off_uses_base_reranker_unwrapped(self):
        inner = StaticReranker()
        orchestrator = RetrievalOrchestrator(
            adapter=FakeAdapter({}, []), reranker=inner
        )
        reranker = orchestrator._build_reranker(
            RetrievalStrategy(relation_aware_enabled=False)
        )
        assert reranker is inner

    def test_does_not_double_wrap(self):
        inner = RelationAwareReranker(StaticReranker())
        orchestrator = RetrievalOrchestrator(
            adapter=FakeAdapter({}, []), reranker=inner
        )
        assert orchestrator._build_reranker(RetrievalStrategy()) is inner


class TestOutputScoreNormalization:
    def test_normalize_scores_uses_min_max(self):
        scores = _normalize_output_scores([0.2, 0.6, 1.0])
        assert scores == [0.0, 0.5, 1.0]

    def test_normalize_scores_handles_degenerate_cases(self):
        assert _normalize_output_scores([1.15, 1.15]) == [1.0, 1.0]
        assert _normalize_output_scores([0.0, 0.0]) == [0.0, 0.0]


def _item(item_id, scope, content, links=None):
    return ContextItem(
        id=item_id,
        scope=scope,
        content=content,
        provenance=Provenance(source_type="test", source_id=item_id),
        links=links or [],
    )
