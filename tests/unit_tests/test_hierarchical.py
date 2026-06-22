"""Tests for the three OpenViking-inspired features:

- #3 single-LLM summarize (L0 derived from L1)
- #4 scope-node summaries (refresh_scope_summaries + __node__ filtering)
- #1 hierarchical (directory-recursive) retrieval + #2 retrieval trace
"""

from __future__ import annotations

import math

from contextseek.bridges.summarizer import LLMSummarizer, _lead_sentence
from contextseek.client.contextseek import ContextSeek
from contextseek.config.strategies import RetrievalStrategy, StrategyConfig


# ─── shared fakes ───────────────────────────────────────────────────────────

_VOCAB = [
    "distributed",
    "database",
    "htap",
    "paxos",
    "lsm",
    "storage",
    "compaction",
    "react",
    "frontend",
    "css",
    "tailwind",
    "state",
    "components",
    "oceanbase",
]


def _emb(text: str) -> list[float]:
    v = [float(sum(1 for t in text.lower().split() if w in t)) for w in _VOCAB]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a: list[float], b: list[float]) -> float:
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0


class CosAdapter:
    """Minimal SeekVFSAdapter that ranks by query-embedding cosine."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def write(self, ref, payload):
        self.store[ref] = dict(payload)

    def read(self, ref):
        return self.store.get(ref)

    def delete(self, ref):
        return self.store.pop(ref, None) is not None

    def ls(self, prefix):
        return [r for r in self.store if r.startswith(prefix)]

    def search(self, prefix, query, *, k, query_embedding=None):
        qv = query_embedding or _emb(query)
        out = []
        for ref, p in self.store.items():
            if not ref.startswith(prefix) or p.get("searchable") is False:
                continue
            e = p.get("embedding")
            if not e:
                continue
            out.append((_cos(qv, e), p))
        out.sort(key=lambda x: x[0], reverse=True)
        return [dict(p, score=s) for s, p in out[:k]]


class CountingSummarizer:
    """Summarizer that derives L0 from L1 and counts LLM-ish calls."""

    def __init__(self) -> None:
        self.summary_calls = 0
        self.abstract_calls = 0

    def abstract(self, content):
        self.abstract_calls += 1
        return content[:50]

    def summary(self, content):
        self.summary_calls += 1
        return ("SUM " + content)[:160]

    def summarize(self, content):
        s = self.summary(content)
        a = _lead_sentence(s, 50) or self.abstract(content)
        return a, s


def _seed(ctx: ContextSeek) -> None:
    rows = [
        ("acme/db/engineer", "OceanBase distributed HTAP database paxos"),
        ("acme/db/engineer", "LSM tree storage compaction"),
        ("acme/web/ui", "React frontend components state"),
        ("acme/web/ui", "CSS tailwind styling"),
    ]
    for scope, txt in rows:
        ctx.add(txt, scope=scope, source="wiki")


# ─── #3 summarize ───────────────────────────────────────────────────────────


class TestSummarizeOnce:
    def test_lead_sentence_clips(self):
        assert _lead_sentence("First idea. Second.", 100) == "First idea."
        assert _lead_sentence("", 100) == ""

    def test_llm_summarizer_calls_summary_once(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def invoke(self, _prompt):
                self.calls += 1

                class R:
                    content = "Lead sentence here. More detail follows."

                return R()

        llm = FakeLLM()
        s = LLMSummarizer(llm)
        abstract, summary = s.summarize("some long content")
        assert llm.calls == 1  # only the summary prompt ran
        assert abstract == "Lead sentence here."
        assert summary.startswith("Lead sentence")

    def test_add_uses_single_summary_call(self):
        summ = CountingSummarizer()
        ctx = ContextSeek(adapter=CosAdapter(), embedder=_emb, summarizer=summ)
        ctx.add("OceanBase distributed database", scope="a/b", source="wiki")
        ctx.add("React frontend", scope="a/b", source="wiki")
        assert summ.summary_calls == 2
        assert summ.abstract_calls == 0  # derived from the summary lead


# ─── #4 scope-node summaries ─────────────────────────────────────────────────


class TestScopeNodeSummaries:
    def _ctx(self) -> ContextSeek:
        ctx = ContextSeek(
            adapter=CosAdapter(), embedder=_emb, summarizer=CountingSummarizer()
        )
        _seed(ctx)
        return ctx

    def test_refresh_writes_nodes_bottom_up(self):
        ctx = self._ctx()
        written = ctx.refresh_scope_summaries()
        # acme, acme/db, acme/db/engineer, acme/web, acme/web/ui
        assert written == 5
        node = ctx.adapter.read("contextseek://acme/db/engineer/__node__")
        assert node is not None
        assert node["searchable"] is False
        assert "__scope_node__" in node["tags"]
        assert ctx.adapter.read("contextseek://acme/__node__") is not None

    def test_nodes_excluded_from_listings(self):
        ctx = self._ctx()
        ctx.refresh_scope_summaries()
        items = ctx.items(scope="acme/db/engineer")
        assert all("__node__" not in it.id for it in items)
        assert len(items) == 2
        assert ctx.scope_stats("acme/db/engineer").item_count == 2

    def test_compact_refreshes_chain(self):
        ctx = self._ctx()
        assert ctx.adapter.read("contextseek://acme/db/engineer/__node__") is None
        ctx.compact(scope="acme/db/engineer")
        assert ctx.adapter.read("contextseek://acme/db/engineer/__node__") is not None
        assert ctx.adapter.read("contextseek://acme/__node__") is not None

    def test_compact_dry_run_writes_no_node(self):
        ctx = ContextSeek(
            adapter=CosAdapter(), embedder=_emb, summarizer=CountingSummarizer()
        )
        ctx.add("x distributed", scope="z/db", source="wiki")
        ctx.compact(scope="z/db", dry_run=True)
        assert ctx.adapter.read("contextseek://z/db/__node__") is None

    def test_incremental_refresh_handles_disjoint_changed_scopes(self):
        ctx = ContextSeek(
            adapter=CosAdapter(), embedder=_emb, summarizer=CountingSummarizer()
        )
        ctx.add("distributed paxos database", scope="a/db/engineer", source="wiki")
        ctx.add("react frontend components", scope="x/web/ui", source="wiki")

        written = ctx.refresh_scope_summaries(
            changed_scopes=["a/db/engineer", "x/web/ui"]
        )
        assert written == 6
        assert ctx.adapter.read("contextseek://a/__node__") is not None
        assert ctx.adapter.read("contextseek://a/db/__node__") is not None
        assert ctx.adapter.read("contextseek://a/db/engineer/__node__") is not None
        assert ctx.adapter.read("contextseek://x/__node__") is not None
        assert ctx.adapter.read("contextseek://x/web/__node__") is not None
        assert ctx.adapter.read("contextseek://x/web/ui/__node__") is not None


# ─── #1 hierarchical retrieval + #2 trace ────────────────────────────────────


class TestHierarchicalRetrieval:
    def _ctx(self) -> ContextSeek:
        strat = StrategyConfig(
            retrieval=RetrievalStrategy(recall_routes=("hierarchical",))
        )
        ctx = ContextSeek(
            adapter=CosAdapter(),
            embedder=_emb,
            summarizer=CountingSummarizer(),
            strategy=strat,
        )
        _seed(ctx)
        ctx.refresh_scope_summaries()
        return ctx

    def test_parent_scope_returns_subtree_items(self):
        ctx = self._ctx()
        r = ctx.retrieve("distributed database paxos", scope="acme", k=3)
        assert len(r) > 0
        top = r[0]
        assert top.item.scope == "acme/db/engineer"
        assert top.recall_path == "hierarchical"

    def test_leaf_scope_returns_items(self):
        ctx = self._ctx()
        r = ctx.retrieve("distributed", scope="acme/db/engineer", k=3)
        assert len(r) > 0

    def test_trace_records_descent(self):
        ctx = self._ctx()
        r = ctx.retrieve("distributed database", scope="acme", k=3, with_trace=True)
        assert r.trace is not None
        descended = [e.scope for e in r.trace.events if e.type == "descend"]
        assert "acme" in descended
        assert "acme/db" in descended
        d = r.trace.to_dict()
        assert d["events"] and d["events"][0]["type"]

    def test_degrades_without_summaries(self):
        # Children exist but compact / refresh never ran → no node embeddings.
        strat = StrategyConfig(
            retrieval=RetrievalStrategy(recall_routes=("hierarchical",))
        )
        ctx = ContextSeek(
            adapter=CosAdapter(),
            embedder=_emb,
            summarizer=CountingSummarizer(),
            strategy=strat,
        )
        ctx.add("distributed db", scope="x/db", source="wiki")
        ctx.add("react ui", scope="x/web", source="wiki")
        r = ctx.retrieve("distributed", scope="x", k=3)
        assert len(r) == 0  # route yields nothing; flat routes would cover

    def test_no_trace_when_disabled(self):
        ctx = self._ctx()
        r = ctx.retrieve("distributed", scope="acme", k=3)
        assert r.trace is None

    def test_no_prefix_scan_and_no_subtree_pull(self):
        """Regression: each scope contributes only its DIRECT items, the route
        never issues a recursive prefix ``search``, and every item ref is read
        at most once across the whole descent (no subtree re-scans)."""

        class CountingCosAdapter(CosAdapter):
            def __init__(self) -> None:
                super().__init__()
                self.search_calls = 0
                self.read_counts: dict[str, int] = {}

            def search(self, *a, **kw):
                self.search_calls += 1
                return super().search(*a, **kw)

            def read(self, ref):
                self.read_counts[ref] = self.read_counts.get(ref, 0) + 1
                return super().read(ref)

        strat = StrategyConfig(
            retrieval=RetrievalStrategy(recall_routes=("hierarchical",))
        )
        ctx = ContextSeek(
            adapter=CountingCosAdapter(),
            embedder=_emb,
            summarizer=CountingSummarizer(),
            strategy=strat,
        )
        _seed(ctx)
        ctx.refresh_scope_summaries()
        adapter: CountingCosAdapter = ctx.adapter  # type: ignore[assignment]
        adapter.read_counts.clear()
        adapter.search_calls = 0  # ignore setup-time conflict-check searches

        r = ctx.retrieve(
            "distributed database paxos", scope="acme", k=5, with_trace=True
        )
        assert len(r) > 0

        # The hierarchical route must not fall back to a recursive prefix scan.
        assert adapter.search_calls == 0

        # A parent scope with no direct items contributes nothing; its child
        # leaf contributes its own items only — proving no subtree pull.
        leaf = {
            e.scope: e.data.get("items", 0)
            for e in r.trace.events
            if e.type == "leaf_recall"
        }
        assert leaf.get("acme", 0) == 0
        assert leaf.get("acme/db/engineer", 0) == 2

        # No item content ref is read more than once during the descent.
        item_reads = {
            ref: n for ref, n in adapter.read_counts.items() if "/__node__" not in ref
        }
        assert item_reads and all(n == 1 for n in item_reads.values())
