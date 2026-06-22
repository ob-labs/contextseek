"""Tests for the four self-evolution enrichments:

1. Conflict resolution (update vs drift) — ConflictResolver
2. Bi-temporal validity — ContextItem.valid_from/valid_to + retrieval filtering
3. Utility feedback loop — ContextSeek.record_utility
4. Failure-driven reflection — PitfallReflector
"""

from datetime import timedelta

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.serialization import (
    deserialize_context_item,
    serialize_context_item,
)
from contextseek.domain.stages import Stage
from contextseek.config.strategies import DreamStrategy
from contextseek.evolution.conflict import ConflictResolver
from contextseek.evolution.dreaming import PitfallReflector, _is_failure_trace


def _item(
    content="x",
    *,
    stage=Stage.knowledge,
    source=SourceType.document,
    confidence=0.8,
    scope="t/p/s",
    **kwargs,
):
    defaults = {
        "id": _generate_id(),
        "content": content,
        "scope": scope,
        "provenance": Provenance(
            source_type=source, source_id="src", confidence=confidence
        ),
        "stage": stage,
        "tags": [],
        "links": [],
        "created_at": _utc_now(),
    }
    defaults.update(kwargs)
    return ContextItem(**defaults)


# ── 1. Conflict resolution ──────────────────────────────────────────────
class TestConflictResolver:
    def test_update_closes_old_validity_window(self):
        old = _item(
            "the server port is 8080",
            created_at=_utc_now() - timedelta(days=2),
        )
        new = _item("the server port is 9090", created_at=_utc_now())
        res = ConflictResolver(similarity_threshold=0.5).resolve([old, new])

        assert len(res.updated) == 1
        assert res.updated[0].id == old.id
        assert old.valid_to is not None  # window closed, not deleted
        assert old.is_deleted is False
        assert old.superseded_by == new.id
        assert any(
            lnk.target_id == old.id and lnk.relation == LinkType.supersedes
            for lnk in new.links
        )
        assert res.verdicts[0][2] == "update"

    def test_drift_quarantines_lower_authority_item(self):
        # Established human-entered knowledge.
        established = _item(
            "the API rate limit is 100 rps",
            source=SourceType.human_input,
            confidence=1.0,
            created_at=_utc_now() - timedelta(days=1),
        )
        # A low-confidence dream hypothesis contradicting it (newer but weaker).
        drift = _item(
            "the API rate limit is 5 rps",
            stage=Stage.extracted,
            source=SourceType.dream_divergence,
            confidence=0.3,
            created_at=_utc_now(),
        )
        res = ConflictResolver(similarity_threshold=0.5).resolve([established, drift])

        assert len(res.quarantined) == 1
        assert res.quarantined[0].id == drift.id
        assert drift.valid_to is not None  # drift never becomes valid
        assert "needs_review" in drift.tags
        assert "drift_quarantined" in drift.tags
        assert established.valid_to is None  # ground truth stays valid
        assert res.verdicts[0][2] == "drift"

    def test_identical_content_is_not_a_conflict(self):
        a = _item("same fact", created_at=_utc_now() - timedelta(days=1))
        b = _item("same fact", created_at=_utc_now())
        res = ConflictResolver(similarity_threshold=0.5).resolve([a, b])
        assert res.updated == []
        assert res.quarantined == []

    def test_unrelated_items_do_not_conflict(self):
        a = _item("database connection pool tuning guide")
        b = _item("frontend button hover animation styles")
        res = ConflictResolver(similarity_threshold=0.5).resolve([a, b])
        assert res.verdicts == []

    def test_engine_runs_conflict_phase_and_reports(self):
        from contextseek.config.strategies import EvolutionStrategy
        from contextseek.evolution.engine import EvolutionEngine

        old = _item(
            "the server port is 8080",
            created_at=_utc_now() - timedelta(days=2),
        )
        new = _item("the server port is 9090", created_at=_utc_now())
        engine = EvolutionEngine(strategy=EvolutionStrategy(conflict_sim_threshold=0.5))
        _new_items, archived, report = engine.evolve([old, new])

        assert report.conflict_updated_count == 1
        assert old in archived
        assert old.valid_to is not None


# ── 2. Bi-temporal validity ─────────────────────────────────────────────
class TestBiTemporal:
    def test_close_validity_keeps_item_alive(self):
        it = _item("fact")
        assert it.is_valid_at() is True
        it.close_validity(reason="superseded")
        assert it.valid_to is not None
        assert it.is_valid_at() is False
        assert it.is_deleted is False
        assert it.invalidated_reason == "superseded"

    def test_future_valid_from_is_not_yet_valid(self):
        it = _item("fact", valid_from=_utc_now() + timedelta(days=1))
        assert it.is_valid_at() is False

    def test_validity_fields_round_trip(self):
        it = _item("fact")
        it.close_validity(reason="superseded_by_update:abc")
        restored = deserialize_context_item(serialize_context_item(it))
        assert restored.valid_to is not None
        assert restored.invalidated_reason == "superseded_by_update:abc"
        assert restored.is_valid_at() is False


# ── 4. Failure-driven reflection ────────────────────────────────────────
class TestPitfallReflector:
    def test_detects_failure_traces(self):
        assert _is_failure_trace(_item(content={"input": "x", "error": "boom"}))
        assert _is_failure_trace(_item(content={"input": "x", "success": False}))
        assert _is_failure_trace(_item("note", tags=["failure"]))
        assert not _is_failure_trace(_item("a normal note mentioning nothing"))

    def test_reflects_pitfall_from_failures(self):
        strategy = DreamStrategy(pitfall_min_failures=2)
        reflector = PitfallReflector(strategy=strategy)
        failures = [
            _item(
                content={"input": "deploy service", "error": "timeout connecting db"},
                stage=Stage.raw,
                tags=["failure"],
            )
            for _ in range(3)
        ]
        result = reflector.reflect(failures)
        assert result.failures_seen == 3
        assert len(result.items) >= 1
        pit = result.items[0]
        assert "pitfall" in pit.tags
        assert pit.provenance.source_type == SourceType.pitfall_reflection
        assert pit.stage == Stage.extracted

    def test_below_threshold_produces_nothing(self):
        strategy = DreamStrategy(pitfall_min_failures=5)
        reflector = PitfallReflector(strategy=strategy)
        failures = [_item("x", tags=["failure"]) for _ in range(2)]
        result = reflector.reflect(failures)
        assert result.items == []


# ── 2./3. Client-level: expiry filtering + utility feedback ──────────────
class TestClientIntegration:
    def test_expired_item_hidden_from_retrieval(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        scope = "t/p/s"
        item = ctx.add("the deployment region is us-east", scope=scope, source="doc")
        ref = ctx.resolver.ref_for(scope, item.id)

        # Close its validity window (simulating a conflict-driven supersede).
        stored = deserialize_context_item(ctx.adapter.read(ref))
        stored.close_validity(reason="superseded")
        ctx.adapter.write(ref, serialize_context_item(stored))

        hidden = [h.item.id for h in ctx.retrieve("deployment region", scope=scope)]
        assert item.id not in hidden

        visible = [
            h.item.id
            for h in ctx.retrieve(
                "deployment region", scope=scope, include_expired=True
            )
        ]
        assert item.id in visible

    def test_record_utility_rewards_used_penalizes_unused(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        scope = "t/p/s"
        used = ctx.add("useful fact about caching", scope=scope, source="doc")
        unused = ctx.add("irrelevant fact about fonts", scope=scope, source="doc")

        counts = ctx.record_utility(
            scope=scope,
            retrieved_ids=[used.id, unused.id],
            used_ids=[used.id],
        )
        assert counts == {"rewarded": 1, "penalized": 1}

        used_after = deserialize_context_item(
            ctx.adapter.read(ctx.resolver.ref_for(scope, used.id))
        )
        unused_after = deserialize_context_item(
            ctx.adapter.read(ctx.resolver.ref_for(scope, unused.id))
        )
        assert used_after.relevance_boost > 1.0
        assert used_after.access_count >= 1
        assert unused_after.relevance_boost < 1.0
