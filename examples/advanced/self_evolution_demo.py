"""Self-evolution demo: exercises the four features added in this round.

Covers:
  1. Conflict detection & resolution (update vs drift) - ConflictResolver / EvolutionEngine Phase 0
  2. Bi-temporal validity - ContextItem.valid_from/valid_to; retrieval shows only currently-valid facts
  3. Utility feedback loop - ContextSeek.record_utility, "use it or lose it" feeding the reranker
  4. Failure-driven reflection - PitfallReflector distils "avoid this" rules from failed traces

Runs fully in-memory with no external dependencies.

Run with:
    python examples/advanced/self_evolution_demo.py
"""

from __future__ import annotations

from datetime import timedelta

from contextseek import ContextSeek
from contextseek.config.strategies import DreamStrategy, EvolutionStrategy
from contextseek.domain.context_item import ContextItem, _utc_now
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.serialization import deserialize_context_item
from contextseek.domain.stages import Stage
from contextseek.evolution.conflict import ConflictResolver
from contextseek.evolution.dreaming import PitfallReflector
from contextseek.evolution.engine import EvolutionEngine

SCOPE = "demo_tenant/default/alice"


def _section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _read(ctx: ContextSeek, item_id: str) -> ContextItem:
    """Read the raw row, bypassing retrieval filters (to inspect valid_to / boost)."""
    return deserialize_context_item(ctx.adapter.read(ctx.resolver.ref_for(SCOPE, item_id)))


# -- 1 + 2. Conflict resolution (update) + bi-temporal validity --
def demo_conflict_update_and_bitemporal() -> None:
    _section("1+2. Conflict resolution (update) + bi-temporal validity")

    # compact() only runs the Phase 0 conflict pass when an evolution_engine is set.
    ctx = ContextSeek(
        evolution_engine=EvolutionEngine(
            strategy=EvolutionStrategy(conflict_sim_threshold=0.5)
        )
    )

    ctx.add("the server port is 8080", scope=SCOPE, source="doc")
    ctx.add("the server port is 9090", scope=SCOPE, source="doc")  # numeric update

    report = ctx.compact(scope=SCOPE)
    print(f"old facts retired (window closed): {report.conflict_updated_count}")

    current = [
        h.item.content_text
        for h in ctx.retrieve("server port", scope=SCOPE, full=True)
    ]
    archived = [
        h.item.content_text
        for h in ctx.retrieve(
            "server port", scope=SCOPE, include_expired=True, full=True
        )
    ]
    print(f"default retrieval (valid only): {current}")
    print(f"include_expired (with retired): {archived}")
    print("-> old value is not deleted, only its validity window is closed (still auditable)")


# -- 1. Conflict resolution (drift quarantine) --
def demo_conflict_drift() -> None:
    _section("1. Conflict resolution (drift quarantine: low authority vs high authority)")

    def mk(text, src, conf, stage, days_ago):
        return ContextItem(
            content=text,
            scope=SCOPE,
            provenance=Provenance(source_type=src, source_id="x", confidence=conf),
            stage=stage,
            created_at=_utc_now() - timedelta(days=days_ago),
        )

    truth = mk("the API rate limit is 100 rps", SourceType.human_input, 1.0, Stage.knowledge, 1)
    drift = mk("the API rate limit is 5 rps", SourceType.dream_divergence, 0.3, Stage.extracted, 0)

    res = ConflictResolver(similarity_threshold=0.5).resolve([truth, drift])
    print(f"verdict: {res.verdicts}")
    print(f"drift item tags: {drift.tags}")
    print(f"drift item window closed: {drift.valid_to is not None}")
    print(f"high-authority fact still valid: {truth.valid_to is None}")


# -- 3. Utility feedback loop --
def demo_utility_feedback() -> None:
    _section("3. Utility feedback loop (use it or lose it)")

    ctx = ContextSeek()
    used = ctx.add("useful fact about caching", scope=SCOPE, source="doc")
    unused = ctx.add("irrelevant fact about fonts", scope=SCOPE, source="doc")

    counts = ctx.record_utility(
        scope=SCOPE,
        retrieved_ids=[used.id, unused.id],
        used_ids=[used.id],
    )
    print(f"feedback result: {counts}")

    u, n = _read(ctx, used.id), _read(ctx, unused.id)
    print(f"used item   boost={u.relevance_boost:.2f} access={u.access_count} (up)")
    print(f"unused item boost={n.relevance_boost:.2f} (down)")
    print("-> boost feeds the reranker via the relevance_boost channel; importance feeds decay")


# -- 4. Failure-driven reflection --
def demo_pitfall_reflection() -> None:
    _section("4. Failure-driven reflection (distil avoid-this rules from failed traces)")

    def fail(i):
        return ContextItem(
            content={"input": "deploy service", "error": "timeout connecting db"},
            scope=SCOPE,
            stage=Stage.raw,
            tags=["failure"],
            provenance=Provenance(
                source_type=SourceType.trace_extraction, source_id=str(i), confidence=0.6
            ),
            created_at=_utc_now(),
        )

    reflector = PitfallReflector(strategy=DreamStrategy(pitfall_min_failures=2))
    result = reflector.reflect([fail(0), fail(1), fail(2)])

    print(f"failure traces detected: {result.failures_seen}")
    for pit in result.items:
        print(f"pitfall rule: {pit.content}")
        print(f"  tags={pit.tags} source_type={pit.provenance.source_type} stage={pit.stage.value}")


def main() -> None:
    demo_conflict_update_and_bitemporal()
    demo_conflict_drift()
    demo_utility_feedback()
    demo_pitfall_reflection()
    print("\n[demo] all four self-evolution features demonstrated.")


if __name__ == "__main__":
    main()
