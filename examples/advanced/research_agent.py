"""Research Agent Demo: Comprehensive ContextSeek Feature Showcase.

Scenario: A research agent investigates "distributed databases" — ingesting
sources, building knowledge through the evolution pipeline, and distilling
skills.

This script exercises all major ContextSeek capabilities:
  - ContextItem add with provenance (multiple source types)
  - retrieve() with full=False (L1 default) vs full=True (L2)
  - expand() to upgrade selected hits to full content
  - Links between items (supports/refutes/supersedes)
  - Evolution pipeline (raw → extracted → knowledge → skill)
  - Lifecycle compaction
  - Trace write → training data export
  - Strategy routing with canary rules
  - Context injection for LLM prompt building
  - Skill export (skill_tools / skill_context)

Requirements: only the contextseek project itself (zero external dependencies).

Run:
    uv run python examples/research_agent_demo.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import seekvfs

from contextseek import ContextSeek, SourceType, Stage, Link, LinkType
from contextseek.storage import FileBackend, SeekVFSStorageAdapter
from contextseek.config.strategies import (
    CanaryRule,
    RetrievalStrategy,
    StrategyConfig,
    StrategyRouter,
)
from contextseek.evolution.engine import EvolutionEngine
from contextseek.retrieval.orchestrator import RetrievalOrchestrator
from contextseek.routing.resolver import ScopeResolver
from contextseek.trace.export import TraceExporter

# ============================================================
# Configuration
# ============================================================
STORAGE_ROOT = "/tmp/seekctx_research_demo"
CLEAN_ON_START = True


# ============================================================
# Mock functions
# ============================================================

# ============================================================
# Main Demo
# ============================================================

def main() -> None:
    root = Path(STORAGE_ROOT)
    if CLEAN_ON_START and root.exists():
        shutil.rmtree(root)

    # ──────────────────────────────────────────────────────────────────────
    # Step 1: Initialize Storage & Client
    # ──────────────────────────────────────────────────────────────────────
    print("=" * 70)
    print("  SEEKCONTEXT RESEARCH AGENT DEMO")
    print("=" * 70)

    print("\n[Step 1] Initializing storage and client...")
    backend = FileBackend(root_dir=root, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)

    ctx = ContextSeek(adapter=adapter)
    scope = "research_lab/dist_sys/agent_alpha"
    print(f"  Scope: {scope}")
    print(f"  Storage: {root}")

    with vfs:
        # ──────────────────────────────────────────────────────────────────
        # Step 2: Add Knowledge from Multiple Sources
        # Demonstrates: ctx.add with different SourceTypes
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 2] Adding knowledge from multiple sources...")

        doc_item = ctx.add(
            "CAP theorem states that a distributed system cannot simultaneously provide consistency, availability, and partition tolerance.",
            scope=scope,
            source="https://docs.distributed-db.org/cap-theorem",
            source_type=SourceType.document,
            tags=["cap", "theory", "fundamentals"],
        )
        print(f"  Document: id={doc_item.id} stage={doc_item.stage.value} stability={doc_item.stability.value}")

        paper_item = ctx.add(
            "Raft consensus algorithm provides strong consistency through leader election and log replication mechanisms.",
            scope=scope,
            source="https://arxiv.org/abs/2024.12345",
            source_type=SourceType.document,
            tags=["consensus", "raft", "algorithm"],
        )
        print(f"  Paper:    id={paper_item.id} stage={paper_item.stage.value}")

        inference_item = ctx.add(
            "Eventual consistency allows temporary divergence between replicas, trading immediate consistency for higher availability.",
            scope=scope,
            source="agent_analysis",
            source_type=SourceType.agent_inference,
            tags=["consistency", "availability", "tradeoff"],
        )
        print(f"  Inferred: id={inference_item.id} stage={inference_item.stage.value}")

        modern_item = ctx.add(
            "Modern distributed databases like CockroachDB use Raft for consensus while providing SQL compatibility.",
            scope=scope,
            source="https://arxiv.org/abs/2024.67890",
            source_type=SourceType.document,
            tags=["raft", "modern", "sql", "cockroachdb"],
        )
        print(f"  Modern:   id={modern_item.id} stage={modern_item.stage.value}")

        human_item = ctx.add(
            "Strong consistency requires all nodes to agree before confirming writes, which reduces availability during partitions.",
            scope=scope,
            source="researcher_note",
            source_type=SourceType.human_input,
            tags=["consistency", "partitions", "tradeoff"],
        )
        print(f"  Human:    id={human_item.id} stage={human_item.stage.value}")

        # ──────────────────────────────────────────────────────────────────
        # Step 3: Add Items with Links
        # Demonstrates: Link model (supports, refutes, supersedes)
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 3] Adding items with links between them...")

        linked_item = ctx.add(
            "Raft consensus directly enables strong consistency claims in distributed systems.",
            scope=scope,
            source="synthesis",
            source_type=SourceType.agent_inference,
            tags=["synthesis", "raft", "consistency"],
            links=[
                Link(target_id=paper_item.id, relation=LinkType.supported_by),
                Link(target_id=human_item.id, relation=LinkType.related_to),
            ],
        )
        print(f"  Linked item: {linked_item.id} → supports paper, related to human note")

        superseding_item = ctx.add(
            "CockroachDB and TiDB demonstrate that Raft-based systems can achieve both strong consistency and high availability for most practical workloads.",
            scope=scope,
            source="updated_research",
            source_type=SourceType.document,
            tags=["modern", "practical", "cap"],
            links=[
                Link(target_id=doc_item.id, relation=LinkType.supersedes),
            ],
        )
        print(f"  Superseding: {superseding_item.id} → supersedes original CAP item")

        # ──────────────────────────────────────────────────────────────────
        # Step 4: retrieve (default L1 summaries) vs full=True (L2) + expand
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 4] Retrieving context (summary mode vs full mode)...")

        query = "consistency"
        print(f"\n  4a) default (summary): query={query!r}")
        response = ctx.retrieve(query, scope=scope, k=5)
        print(f"      meta: layer={response.meta.layer}")
        for i, hit in enumerate(list(response)[:3], 1):
            preview = (hit.item.summary or hit.item.content_text)[:70]
            print(f"      [{i}] score={hit.score:.4f} stage={hit.item.stage.value} layer={hit.layer} | {preview}")

        print(f"\n  4b) full=True (L2 content): query={query!r}")
        response_full = ctx.retrieve(query, scope=scope, k=5, full=True)
        for i, hit in enumerate(list(response_full)[:3], 1):
            print(f"      [{i}] [{hit.item.stage.value}] {hit.item.content_text[:60]}...")

        print("\n  4c) expand: upgrade two summary hits to full L2")
        upgraded = ctx.expand(list(response)[:2])
        print(f"      Upgraded {len(upgraded)} items to full L2 content")

        # ──────────────────────────────────────────────────────────────────
        # Step 5: Write Trace (for training data export)
        # Demonstrates: trace_extraction source type
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 5] Writing execution trace...")
        trace_item = ctx.add(
            {
                "input": "Research the CAP theorem and its implications for modern distributed databases",
                "output": "The CAP theorem fundamentally constrains distributed systems. Modern databases like CockroachDB work around it using Raft consensus.",
                "tool_calls": [
                    {"name": "web_search", "args": {"query": "CAP theorem"}, "result": "Found 5 sources"},
                    {"name": "read_paper", "args": {"url": "https://arxiv.org/abs/2024.12345"}, "result": "Raft paper content"},
                ],
                "feedback": "Good summary, could include more about CRDT alternatives",
                "task_id": "task-cap-research",
                "duration_ms": 4500,
                "status": "success",
            },
            scope=scope,
            source="research_session_001",
            source_type=SourceType.trace_extraction,
            tags=["trace", "research"],
        )
        print(f"  Trace item: {trace_item.id} stage={trace_item.stage.value}")

        # ──────────────────────────────────────────────────────────────────
        # Step 6: Trace Export for Training
        # Demonstrates: TraceExporter → JSONL
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 6] Exporting traces as training data...")
        exporter = TraceExporter(client=ctx, min_output_length=10)
        records = exporter.export_scope(scope)
        print(f"  Exported {len(records)} trace record(s)")

        if records:
            chat_jsonl = exporter.to_jsonl(records, format="chat")
            print(f"  Chat format (first 200 chars): {chat_jsonl[:200]}")

        # ──────────────────────────────────────────────────────────────────
        # Step 7: Evolution (Compact)
        # Demonstrates: ctx.compact + overview
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 7] Running evolution/compaction...")
        report = ctx.compact(scope=scope)
        print(f"  Merged: {report.merged_count}, Archived: {report.archived_count}, Evolved: {report.evolved_count}")

        scope_overview = ctx.overview(scope=scope)
        print(f"  Overview: total={scope_overview.total_items} stages={scope_overview.stage_distribution}")

        # ──────────────────────────────────────────────────────────────────
        # Step 8: Strategy Routing (Canary Deployment)
        # Demonstrates: CanaryRule + StrategyRouter
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 8] Strategy routing with canary rules...")
        v1_config = StrategyConfig(version="v1", retrieval=RetrievalStrategy(default_k=10))
        v2_config = StrategyConfig(version="v2", retrieval=RetrievalStrategy(default_k=50))

        router = StrategyRouter(
            strategies={"v1": v1_config, "v2": v2_config},
            rules=(
                CanaryRule(version="v2", tenant_ids=("research_lab",)),
                CanaryRule(version="v1", percent=100),
            ),
        )

        resolved = router.resolve(tenant_id="research_lab", subject_id="agent_alpha")
        print(f"  Tenant 'research_lab' → strategy {resolved.version} (k={resolved.retrieval.default_k})")

        resolved_other = router.resolve(tenant_id="other_org", subject_id="bob")
        print(f"  Tenant 'other_org'    → strategy {resolved_other.version} (k={resolved_other.retrieval.default_k})")

        # ──────────────────────────────────────────────────────────────────
        # Step 9: LLM tool registration via ctx.tools()
        # Demonstrates: tool/function-calling protocol export
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 9] Exporting LLM tool spec for retrieve/expand...")
        for spec in ctx.tools():
            payload = spec.to_anthropic()
            print(f"  - {payload['name']}: {payload['description'][:80]}")
        print("  Tip: register the above with your agent runtime (Claude/OpenAI/MCP).")
        print("       The LLM will call retrieve(...) for L1 summaries and")
        print("       expand(ids=[...]) when summaries are insufficient.")

        # ──────────────────────────────────────────────────────────────────
        # Step 10: Skill Export
        # Demonstrates: skill_tools() and skill_context()
        # ──────────────────────────────────────────────────────────────────
        print("\n[Step 10] Skill export...")

        # Create a skill ContextItem
        skill_item = ctx.add(
            {"name": "summarize", "skill_type": "tool", "description": "Summarize text to a given length",
             "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "max_length": {"type": "integer"}}}},
            scope=scope,
            source="distillation",
            source_type=SourceType.distillation,
            tags=["skill", "text_processing"],
        )
        print(f"  Skill item: {skill_item.id} stage={skill_item.stage.value} stability={skill_item.stability.value}")

        # Export as OpenAI tool definitions
        tool_defs = ctx.skill_tools(scope, fmt="openai")
        print(f"  skill_tools(fmt='openai') → {len(tool_defs)} tool(s)")
        if tool_defs:
            print(f"  First tool name: {tool_defs[0].get('function', {}).get('name', '?')}")

        # Export as system prompt injection
        ctx.add(
            {"name": "research_style", "skill_type": "prompt", "description": "Research style guide",
             "body": "Always cite sources and include confidence levels in your findings."},
            scope=scope,
            source="style_guide",
            source_type=SourceType.distillation,
            tags=["skill", "prompt"],
        )
        prompt_block = ctx.skill_context(scope)
        print(f"  skill_context() → {len(prompt_block)} chars of system prompt")

        # ──────────────────────────────────────────────────────────────────
        # Step 11: Final Summary
        # ──────────────────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("  DEMO COMPLETE — Summary")
        print("=" * 70)

        resolver = ScopeResolver()
        prefix = resolver.prefix_for(scope)
        all_refs = adapter.ls(prefix)
        print(f"  Total items in scope: {len(all_refs)}")
        print(f"  Storage directory: {root}")
        file_count = sum(1 for _ in root.rglob("*") if _.is_file())
        print(f"  Total files on disk: {file_count}")
        print()


if __name__ == "__main__":
    main()
