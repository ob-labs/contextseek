"""End-to-end coverage for the M1-M3 ContextSeek flow."""

from datetime import datetime, timedelta, timezone
from typing import Iterator

from contextseek.plugs.core.protocols import PlugMeta, RawEvent
from contextseek.client.contextseek import ContextSeek
from contextseek.domain.links import LinkType
from contextseek.domain.provenance import SourceType
from contextseek.domain.stages import Stage
from contextseek.evolution.engine import EvolutionEngine
from contextseek.evolution.rules import EvolutionRule


class StaticPlug:
    """Tiny plug used to verify scope and provenance semantics."""

    def metadata(self) -> PlugMeta:
        return PlugMeta(
            name="static_plug",
            source_type=SourceType.document.value,
            description="test plug",
        )

    def stream(self) -> Iterator[RawEvent]:
        yield RawEvent(
            content="Deployment requires migration checks from plug",
            source="wiki://deploy",
            tags=["deploy"],
        )


def test_m1_to_m3_context_flow() -> None:
    scope = "acme/bot/user_123"
    ctx = ContextSeek(
        evolution_engine=EvolutionEngine(
            rules=[
                EvolutionRule(
                    name="extract_from_trace",
                    source_stage=Stage.raw,
                    target_stage=Stage.extracted,
                    link_type=LinkType.derived_from,
                    min_age_seconds=0,
                    content_filter="trace_structure",
                )
            ]
        )
    )

    ctx.plug(StaticPlug(), scope=scope)
    plug_response = ctx.retrieve("migration checks", scope=scope, k=10)
    assert len(plug_response) > 0
    first = plug_response.items[0].item
    assert first.scope == scope
    assert first.provenance.source_id == "wiki://deploy"

    long_item = ctx.add(
        "deployment budget sentinel " + ("detail " * 200),
        scope=scope,
        source="wiki://long",
        source_type=SourceType.document,
    )
    long_item.summary = "deployment budget sentinel summary"
    ctx._write_item(long_item)

    # Default retrieve returns L1 summary in content when summary is present
    summary_response = ctx.retrieve("budget sentinel", scope=scope, k=5)
    matched = [h for h in summary_response if h.item.id == long_item.id]
    assert matched, "long_item should be retrievable"
    assert matched[0].layer == "summary"
    assert matched[0].item.summary == long_item.summary

    # full=True returns the full L0 content
    full_response = ctx.retrieve("budget sentinel", scope=scope, k=5, full=True)
    matched_full = [h for h in full_response if h.item.id == long_item.id]
    assert matched_full and matched_full[0].layer == "full"
    assert matched_full[0].item.content_text.startswith("deployment budget sentinel")

    item_ref = ctx.resolver.ref_for(scope, long_item.id)
    ctx.feedback(item_ref, scope=scope, score=0.5, reason="accepted")
    updated_item = ctx._read_item(item_ref)
    assert updated_item is not None
    assert updated_item.relevance_boost == 1.5
    assert updated_item.access_count >= 1

    raw_trace = ctx.add(
        {"input": "deploy failed", "tool_calls": [], "output": "migration missing"},
        scope=scope,
        source="trace-1",
        source_type=SourceType.trace_extraction,
    )
    raw_trace.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    ctx._write_item(raw_trace)

    report = ctx.compact(scope=scope)
    assert report.evolved_count > 0

    extracted = [
        item
        for item in ctx.items(scope=scope, stage=Stage.extracted)
        if any(link.target_id == raw_trace.id for link in item.links)
    ]
    assert extracted

    extracted_ref = ctx.resolver.ref_for(scope, extracted[0].id)
    chain = ctx.upstream(extracted_ref, scope=scope)
    assert [item.id for item in chain] == [extracted[0].id, raw_trace.id]
