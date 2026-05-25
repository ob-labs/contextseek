"""Evidence Chain Demo — provenance tracing with upstream / evidence_chain / chain_confidence.

Scenario: an SRE agent investigates a production latency spike, building a
multi-layer evidence DAG from raw alerts to a final rollback recommendation.

Demonstrates:
  - ctx.add with provenance (source + source_type + confidence)
  - Link edges: derived_from, supported_by, refuted_by
  - ctx.upstream() — quick ancestor walk
  - ctx.evidence_chain() — full DAG with confidence propagation and conflicts
  - ctx.chain_confidence() — lightweight confidence lookup

Requirements: only the contextseek project itself (zero external dependencies).

Run:
    uv run python examples/advanced/evidence_chain.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import seekvfs

from contextseek import ContextSeek, Link, LinkType, SourceType, Stage
from contextseek.storage import FileBackend, SeekVFSStorageAdapter

STORAGE_ROOT = "/tmp/seekctx_evidence_chain_demo"
CLEAN_ON_START = True
SCOPE = "acme/sre/incident_2026_051"


def _preview(text: str, width: int = 56) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 3] + "..."


def _label(item) -> str:
    return f"[{item.stage.value}] {item.provenance.source_id}"


def main() -> None:
    root = Path(STORAGE_ROOT)
    if CLEAN_ON_START and root.exists():
        shutil.rmtree(root)

    print("=" * 72)
    print("  CONTEXTSEEK EVIDENCE CHAIN DEMO")
    print("=" * 72)

    backend = FileBackend(root_dir=root, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)
    ctx = ContextSeek(adapter=adapter)

    with vfs:
        # ── Step 1: raw sources ───────────────────────────────────────────
        print("\n[Step 1] 写入原始证据（监控告警 + 日志片段）")

        alert = ctx.add(
            "P99 latency on payment-api exceeded 2s for 5 consecutive minutes (threshold: 800ms).",
            scope=SCOPE,
            source="prometheus/alert_latency_p99",
            source_type=SourceType.external_api,
            tags=["alert", "latency", "payment-api"],
            stage=Stage.raw,
            confidence=0.85,
        )
        print(f"  告警: {alert.id}  {_label(alert)}")

        log_snippet = ctx.add(
            "Stack trace shows DB connection pool exhausted on shard-3; 47 threads waiting.",
            scope=SCOPE,
            source="loki/query/shard3_pool",
            source_type=SourceType.external_api,
            tags=["logs", "database", "shard-3"],
            stage=Stage.raw,
            confidence=0.75,
        )
        print(f"  日志: {log_snippet.id}  {_label(log_snippet)}")

        # ── Step 2: derived analysis + human confirmation ─────────────────
        print("\n[Step 2] 写入衍生分析与人工确认，建立 Link 边")

        analysis = ctx.add(
            "Root cause likely shard-3 connection pool saturation triggered by a bad deploy at 14:32 UTC.",
            scope=SCOPE,
            source="agent/root_cause_analyser",
            source_type=SourceType.agent_inference,
            tags=["analysis", "root_cause"],
            stage=Stage.extracted,
            confidence=0.72,
            links=[
                Link(target_id=alert.id, relation=LinkType.derived_from),
                Link(target_id=log_snippet.id, relation=LinkType.derived_from),
            ],
        )
        print(f"  分析: {analysis.id}  derived_from → 告警 + 日志")

        human_note = ctx.add(
            "On-call confirmed deploy v2.4.1 rolled out to shard-3 at 14:30 UTC; matches the timeline.",
            scope=SCOPE,
            source="oncall/alice",
            source_type=SourceType.human_input,
            tags=["human", "deploy", "confirmed"],
            stage=Stage.knowledge,
            links=[
                Link(target_id=analysis.id, relation=LinkType.supported_by),
            ],
        )
        print(f"  人工: {human_note.id}  supported_by → 分析")

        counter_claim = ctx.add(
            "Metrics show shard-3 pool recovered after auto-scaling at 14:40; rollback may be unnecessary.",
            scope=SCOPE,
            source="agent/metrics_watcher",
            source_type=SourceType.agent_inference,
            tags=["counter", "metrics"],
            stage=Stage.knowledge,
            confidence=0.65,
        )
        print(f"  反驳: {counter_claim.id}  {_label(counter_claim)}")

        recommendation = ctx.add(
            "Recommend rolling back deploy v2.4.1 on shard-3 and scaling connection pool to 200.",
            scope=SCOPE,
            source="agent/incident_commander",
            source_type=SourceType.agent_inference,
            tags=["action", "rollback", "recommendation"],
            stage=Stage.knowledge,
            confidence=0.88,
            links=[
                Link(target_id=analysis.id, relation=LinkType.derived_from),
                Link(target_id=human_note.id, relation=LinkType.supported_by),
                Link(target_id=counter_claim.id, relation=LinkType.refuted_by),
            ],
        )
        print(f"  建议: {recommendation.id}  derived_from 分析 + supported_by 人工 + refuted_by 反驳")

        rec_ref = ctx.resolver.ref_for(SCOPE, recommendation.id)

        # ── Step 3: upstream() ────────────────────────────────────────────
        print("\n[Step 3] upstream() — 沿 derived_from / supported_by 追溯来源")
        ancestors = ctx.upstream(rec_ref, scope=SCOPE)
        print(f"  可达上游节点: {len(ancestors)} 条（含根节点本身）")
        for idx, item in enumerate(ancestors, 1):
            prefix = "根" if idx == 1 else f"上游 {idx - 1}"
            print(f"  {prefix}: {_label(item)} | {_preview(item.content_text)}")

        # ── Step 4: evidence_chain() ──────────────────────────────────────
        print("\n[Step 4] evidence_chain() — 完整证据 DAG + 置信度传播")
        chain = ctx.evidence_chain(rec_ref, scope=SCOPE)

        id_to_item = {item.id: item for item in ctx.items(scope=SCOPE)}

        print(f"  根节点: {chain.root_item_id}")
        print(f"  DAG 节点数: {len(chain.nodes)}  边数: {len(chain.edges)}  最大深度: {chain.max_depth}")
        print(f"  综合置信度: {chain.overall_confidence:.3f}")
        print(f"  独立来源数: {chain.total_sources}")
        print(f"  存在冲突: {'是' if chain.has_conflicts else '否'}  冲突数: {len(chain.conflicts)}")

        print("\n  节点置信度（intrinsic → effective）:")
        for node in sorted(chain.nodes, key=lambda n: n.depth):
            item = id_to_item.get(node.item_id)
            source = item.provenance.source_id if item else node.item_id
            missing = " [missing]" if node.is_missing else ""
            print(
                f"    depth={node.depth}  {source}{missing}: "
                f"{node.intrinsic_confidence:.2f} → {node.effective_confidence:.2f}"
            )

        if chain.conflicts:
            print("\n  检测到的冲突:")
            for conflict in chain.conflicts:
                refuter = id_to_item.get(conflict.refuter_id)
                refuter_src = refuter.provenance.source_id if refuter else conflict.refuter_id
                print(
                    f"    refuted_by {refuter_src}  "
                    f"strength={conflict.refutation_strength:.2f}  "
                    f"impact={conflict.net_confidence_impact:.3f}"
                )

        if chain.critical_path:
            print("\n  关键路径（最高权重溯源链）:")
            for item_id in chain.critical_path:
                item = id_to_item.get(item_id)
                if item is None:
                    print(f"    - {item_id} (missing)")
                    continue
                print(f"    - {_label(item)} | {_preview(item.content_text)}")
            print(f"  关键路径置信度: {chain.critical_path_confidence:.3f}")

        if chain.broken_links:
            print(f"\n  断裂链接: {chain.broken_links}")

        # ── Step 5: chain_confidence() ──────────────────────────────────────
        print("\n[Step 5] chain_confidence() — 轻量置信度查询")
        quick_conf = ctx.chain_confidence(rec_ref, scope=SCOPE)
        print(f"  有效置信度: {quick_conf:.3f}  （与 evidence_chain 一致: {quick_conf == chain.overall_confidence}）")

        print("\n" + "-" * 72)
        print("要点:")
        print("  • upstream() 适合快速回答「这条结论从哪来」。")
        print("  • evidence_chain() 遍历全部可溯源 Link，做 Noisy-OR 置信度传播与冲突检测。")
        print("  • refuted_by 会降低综合置信度并在 conflicts 中报告。")
        print("  • chain_confidence() 是 evidence_chain() 的轻量替代。")
        print("-" * 72)


if __name__ == "__main__":
    main()
