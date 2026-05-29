"""Tests for the LLM-judge budget added to detect_conflicts and the
ANN-narrowed candidate path used by ContextSeek.add()."""

from __future__ import annotations

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.conflicts import ConflictType, detect_conflicts
from contextseek.domain.context_item import ContextItem
from contextseek.domain.provenance import Provenance, SourceType


def _make_item(text: str, *, scope: str = "t/p") -> ContextItem:
    return ContextItem(
        content=text,
        scope=scope,
        provenance=Provenance(
            source_type=SourceType.human_input,
            source_id="s",
            confidence=0.5,
        ),
    )


class TestLLMJudgeBudget:
    def test_budget_zero_disables_llm(self) -> None:
        new = _make_item("alpha beta gamma delta")
        # Build several candidates that fall in the medium-overlap band.
        candidates = [
            _make_item(f"alpha beta gamma {x}") for x in ("epsilon", "zeta", "eta")
        ]

        calls: list[tuple[str, str]] = []

        def fake_judge(a: str, b: str, overlap: float) -> ConflictType | None:
            calls.append((a, b))
            return ConflictType.near_duplicate

        result = detect_conflicts(new, candidates, llm_judge=fake_judge, llm_budget=0)

        assert calls == []
        assert not result.has_conflicts

    def test_budget_caps_judge_calls(self) -> None:
        new = _make_item("alpha beta gamma delta")
        candidates = [
            _make_item(f"alpha beta gamma {x}") for x in ("e", "f", "g", "h", "i")
        ]

        calls: list[tuple[str, str]] = []

        def fake_judge(a: str, b: str, overlap: float) -> ConflictType | None:
            calls.append((a, b))
            return None  # judge inconclusive — does not consume a "found" slot

        detect_conflicts(new, candidates, llm_judge=fake_judge, llm_budget=2)

        # At most 2 LLM calls, regardless of how many medium-overlap candidates exist.
        assert len(calls) <= 2

    def test_budget_default_is_finite(self) -> None:
        # Defensive: the default budget must not be unbounded — we don't want
        # add() to silently fan out hundreds of LLM calls in pathological cases.
        new = _make_item("alpha beta gamma delta")
        candidates = [_make_item(f"alpha beta gamma {i}") for i in range(20)]
        calls: list[int] = []

        def fake_judge(a: str, b: str, overlap: float) -> ConflictType | None:
            calls.append(1)
            return None

        detect_conflicts(new, candidates, llm_judge=fake_judge)
        assert len(calls) <= 5  # well below "scan everything"

    def test_llm_budget_prefers_high_overlap(self) -> None:
        """With budget=2, the LLM must judge the two highest-overlap band
        candidates regardless of the order they appear in the input list.

        Candidate token sets (new has 10 tokens: a1..a10):
          hi  — 9 shared + 2 unique  → J = 9/12 = 0.750
          mid — 7 shared + 2 unique  → J = 7/12 ≈ 0.583
          lo  — 6 shared + 2 unique  → J = 6/12 = 0.500

        All three fall inside the default band [0.5, 0.95). The input list is
        deliberately ordered [lo, mid, hi] so a naive first-N strategy would
        consume the budget on lo and mid, missing hi. The sorted path must call
        LLM on hi (0.75) and mid (0.583), not lo (0.5).
        """
        new = _make_item("a1 a2 a3 a4 a5 a6 a7 a8 a9 a10")
        hi = _make_item("a1 a2 a3 a4 a5 a6 a7 a8 a9 h1 h2")  # J=9/12=0.750
        mid = _make_item("a1 a2 a3 a4 a5 a6 a7 m1 m2")  # J=7/12≈0.583
        lo = _make_item("a1 a2 a3 a4 a5 a6 l1 l2")  # J=6/12=0.500

        judged_contents: list[str] = []

        def fake_judge(a: str, b: str, overlap: float) -> ConflictType | None:
            judged_contents.append(b)
            return None

        # Input deliberately in low→high order to expose a non-sorting bug.
        detect_conflicts(
            new,
            [lo, mid, hi],
            llm_judge=fake_judge,
            llm_budget=2,
        )

        assert len(judged_contents) == 2, (
            f"budget=2 → exactly 2 LLM calls, got {len(judged_contents)}"
        )
        assert hi.content_text in judged_contents, (
            "highest-overlap candidate (hi) must be judged within budget"
        )
        assert mid.content_text in judged_contents, (
            "second-highest candidate (mid) must be judged within budget"
        )
        assert lo.content_text not in judged_contents, (
            "lowest-overlap candidate (lo) must NOT consume budget ahead of hi/mid"
        )


class TestAddUsesAnnCandidates:
    def _make_ctx_with_vector_adapter(self) -> ContextSeek:
        """ContextSeek backed by a vector-capable adapter so ANN narrows candidates."""
        from contextseek.storage.vector_memory_adapter import VectorMemoryAdapter

        # Embedder uses character bigrams so similar text yields close vectors.
        def embed(text: str) -> list[float]:
            v = [0.0] * 8
            for ch in text.lower():
                v[ord(ch) % 8] += 1.0
            return v

        adapter = VectorMemoryAdapter(embedder=embed)
        return ContextSeek(adapter=adapter, embedder=embed)

    def test_add_uses_search_when_adapter_supports_vector(self) -> None:
        """Vector-capable adapters must receive query_embedding via search()
        instead of being asked to list the entire scope."""
        ctx = self._make_ctx_with_vector_adapter()

        for i in range(3):
            ctx.add(content=f"seed item {i}", scope="t/p", source="seed")

        orig_search = ctx.adapter.search
        orig_ls = ctx.adapter.ls
        search_calls: list[bool] = []  # captures whether query_embedding was passed
        ls_calls: list[str] = []

        def search_spy(prefix, query, *, k, query_embedding=None):
            search_calls.append(query_embedding is not None)
            return orig_search(prefix, query, k=k, query_embedding=query_embedding)

        def ls_spy(prefix):
            ls_calls.append(prefix)
            return orig_ls(prefix)

        ctx.adapter.search = search_spy  # type: ignore[assignment]
        ctx.adapter.ls = ls_spy  # type: ignore[assignment]

        ctx.add(content="brand new item", scope="t/p", source="new")

        assert search_calls and any(search_calls), (
            "add() should ANN-narrow candidates via adapter.search(query_embedding=...)"
        )
        assert ls_calls == [], (
            "add() should not list the whole scope when ANN is available"
        )

    def test_add_exact_duplicate_uses_hash_index(self) -> None:
        """Hash fast-path must return the existing item on exact duplicate —
        the call must NOT raise; the write is silently deduplicated."""
        ctx = ContextSeek(embedder=lambda text: [float(len(text))] * 4)
        first = ctx.add(content="same thing", scope="t/p", source="first")

        # Second add must succeed and return the already-persisted item.
        second = ctx.add(content="same thing", scope="t/p", source="second")
        assert second.id == first.id, (
            "exact duplicate must return the existing item (same id), not a new one"
        )
        assert second.hash == first.hash

    def test_fast_path_skips_soft_deleted_item(self) -> None:
        """If the only hash-matching item in scope is soft-deleted, add() must
        let the new write through — both legacy detect_conflicts and the new
        fast-path are supposed to ignore is_deleted items, so 'revival' is
        an explicit supported workflow."""
        ctx = ContextSeek(embedder=lambda text: [float(len(text))] * 4)
        first = ctx.add(content="reborn content", scope="t/p", source="first")
        ref = ctx.resolver.ref_for("t/p", first.id)
        ctx.forget(ref, scope="t/p", reason="testing revival")

        # Re-adding identical content must NOT raise — soft-deleted predecessor
        # is invisible to duplicate detection.
        second = ctx.add(content="reborn content", scope="t/p", source="second")
        assert second.id != first.id

    def test_fast_path_deduplicates_live_items(self) -> None:
        """Exact duplicate of a live item must be deduplicated: add() returns
        the existing item, no new row is created, and no exception is raised."""
        ctx = ContextSeek(embedder=lambda text: [float(len(text))] * 4)
        first = ctx.add(content="active content", scope="t/p", source="first")

        second = ctx.add(content="active content", scope="t/p", source="second")
        assert second.id == first.id, (
            "dedup must return the existing item, not create a new row"
        )
        # Verify the scope still has exactly one item (no extra row written).
        all_refs = ctx.adapter.ls(ctx.resolver.prefix_for("t/p"))
        assert len(all_refs) == 1, (
            f"scope must contain exactly one item after dedup, found {len(all_refs)}"
        )

    def test_add_with_embedder_does_not_fallback_to_full_scan(self) -> None:
        """With an embedder configured, add() must take the ANN path even when
        the backend silently degrades to FTS. Falling back to _list_items
        would mix unrelated text-matched items into the near-dup candidate
        set — the new contract is "ANN-only, empty-on-degrade"."""
        ctx = ContextSeek(embedder=lambda text: [float(len(text))] * 4)
        ctx.add(content="seed alpha", scope="t/p", source="seed")

        orig_ls = ctx.adapter.ls
        orig_search = ctx.adapter.search
        ls_calls: list[str] = []
        search_calls: list[bool] = []

        def ls_spy(prefix):
            ls_calls.append(prefix)
            return orig_ls(prefix)

        def search_spy(prefix, query, *, k, query_embedding=None):
            search_calls.append(query_embedding is not None)
            return orig_search(prefix, query, k=k, query_embedding=query_embedding)

        ctx.adapter.ls = ls_spy  # type: ignore[assignment]
        ctx.adapter.search = search_spy  # type: ignore[assignment]

        # Use content with no token overlap with the seed so InMemory FTS
        # returns nothing — exercising the "ANN returns empty" path.
        ctx.add(content="totally orthogonal phrase", scope="t/p", source="new")

        assert search_calls and any(search_calls), (
            "embedder path must hit adapter.search with query_embedding"
        )
        assert ls_calls == [], (
            "with embedder, add() must NOT fall back to _list_items even "
            "when ANN returns empty — see Phase C in plan-calm-dusk.md"
        )

    def test_add_without_embedder_uses_full_scan(self) -> None:
        """Embedder-less deployments rely on Jaccard over the full scope, so
        _list_items must keep firing for them."""
        ctx = ContextSeek()  # no embedder
        ctx.add(content="seed", scope="t/p", source="seed")

        orig_ls = ctx.adapter.ls
        ls_calls: list[str] = []

        def ls_spy(prefix):
            ls_calls.append(prefix)
            return orig_ls(prefix)

        ctx.adapter.ls = ls_spy  # type: ignore[assignment]
        ctx.add(content="another", scope="t/p", source="new")
        assert ls_calls, "no embedder → must list the scope for Jaccard checks"
