"""Idempotent seed script: preload an example knowledge graph into the ContextSeek store.

All 16 items are written to the ``contextseek`` scope in three groups:

Group A — Evidence chain (9 items)
    Full raw → extracted → knowledge → skill pipeline with
    derived_from, supported_by, refuted_by, supersedes link relations.
    The convergence node (knowledge_main) has both support and conflict links,
    so has_conflicts=True; the provenance DAG has a critical path and conflict table.

Group B — Semantic retrieval cluster (5 items)
    Five topic-related knowledge entries with varied wording so a single query
    returns multiple results at different scores.

Group C — Near-duplicate pair (2 items)
    Two semantically near-duplicate raw entries for compact/dream dry-run merge/archive candidates.

:func:`maybe_seed` is only invoked when the dashboard (with frontend) starts;
the bare backend API (``contextseek.http.server``) does not trigger seeding.
"""

from __future__ import annotations


def maybe_seed() -> int:
    """Preload seed data if the store is empty (idempotent).

    Returns the number of items actually written; returns 0 if the store already has data.
    """
    from contextseek.client.contextseek import ContextSeek
    from contextseek.domain.links import Link, LinkType
    from contextseek.domain.provenance import SourceType
    from contextseek.domain.stages import Stage, Stability

    ctx = ContextSeek.from_settings()

    existing = ctx.retrieve("ContextSeek agent 记忆", scope="contextseek", k=1)
    if existing:
        return 0

    # ------------------------------------------------------------------
    # Group A: Evidence chain
    # ------------------------------------------------------------------

    # A1 — Raw run traces (2 items)
    raw_1 = ctx.add(
        (
            "运行 trace #2024-11-01T09:12Z：用户问「如何在多次会话之间复用 agent 上下文？」，"
            "agent 调用 ctx.retrieve（query='跨 session 上下文'），store 返回 3 条命中"
            "（分数 0.91、0.87、0.74），agent 无需二次调用 LLM 直接合成了完整回答。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.5,
        tags=["trace", "记忆", "检索"],
    )

    raw_2 = ctx.add(
        (
            "运行 trace #2024-11-01T14:33Z：agent 处理 12 轮对话时，"
            "对相同子查询重复计算了 9 次向量嵌入，"
            "观测到 P99 延迟较基线上升 +340 ms，当时未启用嵌入缓存。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.5,
        tags=["trace", "性能", "嵌入"],
    )

    # A2 — Extracted patterns (2 items, derived_from raw)
    extracted_1 = ctx.add(
        (
            "从 trace #2024-11-01T09:12Z 提取的规律：ctx.retrieve 可在跨 session 场景中"
            "提供语义记忆召回。当 store 中存在相关条目时，agent 可跳过重复 LLM 调用，"
            "直接复用已缓存知识，从而降低 token 消耗和延迟。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.65,
        tags=["记忆", "检索", "规律"],
        links=[Link(raw_1.id, LinkType.derived_from, strength=1.0)],
    )

    extracted_2 = ctx.add(
        (
            "从 trace #2024-11-01T14:33Z 提取的规律：重复嵌入计算是多轮对话延迟抖动的"
            "主要原因。引入嵌入缓存层预计可将 P99 延迟降低约 30%。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.65,
        tags=["性能", "嵌入", "规律"],
        links=[Link(raw_2.id, LinkType.derived_from, strength=1.0)],
    )

    # A3 — Support and conflict nodes (raw→extracted→knowledge; ids used by knowledge_main links)
    a3s_raw = ctx.add(
        (
            "运行 trace #2024-11-02T08:00Z：OceanBase 50 节点集群压测，"
            "启用 ContextSeek 嵌入缓存前后各跑 120 万次 agent 轮次；"
            "观测 P99 检索延迟 420 ms → 89 ms，LLM token 消耗下降 34%。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "压测", "性能"],
    )
    a3s_ext = ctx.add(
        (
            "从 trace #2024-11-02 提取的规律：嵌入缓存在 OceanBase 压测环境下"
            "可将 P99 检索延迟降低约 79%，并显著减少 LLM token 消耗。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["压测", "性能", "规律"],
        links=[Link(a3s_raw.id, LinkType.derived_from, strength=1.0)],
    )
    knowledge_support = ctx.add(
        (
            "生产压测数据（OceanBase 后端，50 节点集群，2024 年 11 月）："
            "启用 ContextSeek 嵌入缓存后，P99 检索延迟从 420 ms 降至 89 ms，"
            "降幅 79%；7 天窗口内 LLM token 消耗减少 34%。样本量：120 万次 agent 轮次。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.9,
        tags=["性能", "压测", "嵌入", "缓存"],
        links=[Link(a3s_ext.id, LinkType.derived_from, strength=0.9)],
    )

    a3c_raw = ctx.add(
        (
            "运行 trace #2024-12-10T22:15Z：线上高写入环境（约 1200 ops/s），"
            "ContextSeek 嵌入缓存连续失效 3 次，P99 延迟飙升至 980 ms，"
            "每次失效后预热耗时超过 60 秒。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "缓存", "高写入"],
    )
    a3c_ext = ctx.add(
        (
            "从 trace #2024-12-10 提取的规律：写入吞吐超过约 1000 ops/s 时，"
            "嵌入缓存失效风暴会导致 P99 延迟严重恶化，与低负载压测结论相矛盾。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["缓存", "冲突", "规律"],
        links=[Link(a3c_raw.id, LinkType.derived_from, strength=1.0)],
    )
    knowledge_conflict = ctx.add(
        (
            "注意（线上高写入环境，2024 年 12 月现场报告）："
            "ContextSeek 嵌入缓存在写入吞吐超过约 1000 ops/s 时会触发缓存失效风暴，"
            "P99 延迟恶化至 980 ms，与压测结论相矛盾；"
            "每次失效周期仅预热阶段就耗时超过 60 秒。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.transient,
        confidence=0.7,
        tags=["性能", "缓存", "冲突", "高写入"],
        links=[Link(a3c_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # A4 — Convergence knowledge node (support + conflict links → has_conflicts=True)
    knowledge_main = ctx.add(
        (
            "ContextSeek 通过「语义检索 + 嵌入缓存」为 agent 提供高效跨 session 记忆能力。"
            "agent 调用 ctx.retrieve 召回历史上下文，避免重复 LLM 调用；"
            "嵌入缓存在典型负载下可将 P99 延迟再降低至多 79%。"
            "该结论来自两条运行 trace 并经生产压测验证——"
            "但注意：线上报告指出写入速率超过 1000 ops/s 时存在缓存失效风险。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.85,
        tags=["记忆", "检索", "嵌入", "缓存", "性能"],
        links=[
            Link(extracted_1.id, LinkType.derived_from, strength=0.9),
            Link(extracted_2.id, LinkType.derived_from, strength=0.8),
            Link(knowledge_support.id, LinkType.supported_by, strength=0.9),
            Link(knowledge_conflict.id, LinkType.refuted_by, strength=0.7),
        ],
    )

    # A5 — Skill node (distilled from knowledge_main)
    ctx.add(
        (
            "Agent 记忆优化技能："
            "① 每次调用 LLM 前先执行 ctx.retrieve，检查 store 是否已有答案；"
            "② 生成新答案后调用 ctx.add 持久化，供后续 session 复用；"
            "③ 写入吞吐低于 1000 ops/s 时开启嵌入缓存；"
            "④ 监控缓存命中率，低于 40% 时考虑延长 TTL 或启动时预热。"
        ),
        scope="contextseek",
        source="distillation-pipeline",
        source_type=SourceType.distillation,
        stage=Stage.skill,
        stability=Stability.permanent,
        confidence=0.92,
        tags=["技能", "记忆", "缓存", "最佳实践"],
        links=[Link(knowledge_main.id, LinkType.derived_from, strength=1.0)],
    )

    # A6 — Superseding node (resolves knowledge_conflict)
    a6_raw = ctx.add(
        (
            "运行 trace #2025-01-15T10:00Z：升级 ContextSeek v2 分级缓存后，"
            "在 1500 ops/s 高写入负载下重现场景，P99 延迟稳定在 120 ms，"
            "缓存失效传播时延 ≤ 50 ms，未再出现失效风暴。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "v2", "缓存"],
    )
    a6_ext = ctx.add(
        (
            "从 trace #2025-01-15 提取的规律：v2 分级缓存（L1 内存 + L2 磁盘 + 写后批处理）"
            "可在 >1000 ops/s 场景下消除缓存失效风暴。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["v2", "缓存", "规律"],
        links=[Link(a6_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "ContextSeek v2 分级缓存（2025 年 1 月发布）引入 L1 进程内存缓存 + "
            "L2 磁盘缓存，采用写后批处理机制，将失效传播时延控制在每次突发 50 ms 以内，"
            "彻底解决了 2024 年 12 月现场报告中 >1000 ops/s 场景下的失效风暴问题。"
            "本条目取代上述冲突报告。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.95,
        tags=["性能", "缓存", "v2", "分级缓存"],
        links=[
            Link(a6_ext.id, LinkType.derived_from, strength=0.9),
            Link(knowledge_conflict.id, LinkType.supersedes, strength=1.0),
        ],
    )

    # ------------------------------------------------------------------
    # Group B: Semantic retrieval cluster (5 groups, each raw→extracted→knowledge)
    # ------------------------------------------------------------------

    # B1: ContextSeek overview
    b1_raw = ctx.add(
        (
            "运行 trace #2024-11-05T10:00Z：新接入 ContextSeek 的 agent 在首次对话中"
            "直接召回了 2 周前的上下文，无需用户重新描述背景；"
            "系统提示词从 4000 token 缩减至 200 token。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "记忆", "session"],
    )
    b1_ext = ctx.add(
        (
            "从 trace #2024-11-05 提取的规律：ContextSeek 跨 session 持久化语义记忆，"
            "可替代冗长系统提示词，agent 无需每次对话重建上下文。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["记忆", "session", "规律"],
        links=[Link(b1_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "ContextSeek 是一个 agent 记忆层，将运行时经验转化为可复用知识。"
            "它跨 session 持久化语义记忆，使 agent 无需依赖冗长的系统提示词就能"
            "在后续对话中召回历史上下文。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.95,
        tags=["contextseek", "记忆", "概览"],
        links=[Link(b1_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # B2: Semantic retrieval
    b2_raw = ctx.add(
        (
            "运行 trace #2024-11-06T14:20Z：对查询「OceanBase 连接池配置」执行语义搜索，"
            "store 返回余弦相似度 0.88 的命中，命中内容字面与查询截然不同但语义高度吻合；"
            "纯关键词搜索则返回 0 条结果。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "检索", "嵌入"],
    )
    b2_ext = ctx.add(
        (
            "从 trace #2024-11-06 提取的规律：向量嵌入将查询语义化，"
            "ANN 搜索支持跨表达方式的语义匹配，召回率远优于关键词搜索。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["检索", "嵌入", "规律"],
        links=[Link(b2_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "ContextSeek 的语义检索原理：将查询通过嵌入模型编码为稠密向量，"
            "再对 store 中所有向量执行近似最近邻搜索，"
            "结果按余弦相似度排序后结合置信度和时效性信号二次排名，最终返回给 agent。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.9,
        tags=["检索", "嵌入", "相似度", "contextseek"],
        links=[Link(b2_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # B3: OceanBase backend
    b3_raw = ctx.add(
        (
            "运行 trace #2024-11-07T09:45Z：切换至 OceanBase 后端后，"
            "compact 扫描（读密集型 OLAP）与实时写入（OLTP）并行运行，"
            "观测 20 分钟，P99 写入延迟无明显抖动，扫描耗时 18 秒正常完成。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "oceanbase", "存储"],
    )
    b3_ext = ctx.add(
        (
            "从 trace #2024-11-07 提取的规律：OceanBase HTAP 架构允许 OLAP（compact/dream）"
            "与 OLTP（实时读写）并行，互不干扰，适合 ContextSeek 的混合负载场景。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["oceanbase", "存储", "规律"],
        links=[Link(b3_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "OceanBase 可作为 ContextSeek 的持久化存储后端，"
            "通过设置 STORAGE_BACKEND=oceanbase 启用。"
            "其 HTAP 架构允许 ContextSeek 在高频 OLTP 读写的同时并行执行分析查询"
            "（如 compact 扫描、dream 整合），互不干扰。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.9,
        tags=["oceanbase", "存储", "后端", "数据库"],
        links=[Link(b3_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # B4: agentseek framework
    b4_raw = ctx.add(
        (
            "运行 trace #2024-11-08T11:30Z：agentseek-contextseek 插件在 agent 轮次开始时"
            "自动执行 ctx.retrieve，在轮次结束时自动调用 ctx.add 持久化；"
            "agent 代码中未出现任何显式记忆调用，全程透明。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "agentseek", "插件"],
    )
    b4_ext = ctx.add(
        (
            "从 trace #2024-11-08 提取的规律：agentseek-contextseek 将记忆管理透明化，"
            "agent 仅需关注业务逻辑，检索与持久化均由插件自动完成。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["agentseek", "插件", "规律"],
        links=[Link(b4_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "agentseek 是基于 Bub 的 agent 框架，可将任意 Python 函数图暴露为"
            "兼容 AG-UI 的 HTTP 端点。可选插件 agentseek-contextseek 会在每次 agent"
            "轮次中接入 ContextSeek store，将检索到的记忆作为结构化上下文注入，"
            "并在响应后持久化新经验。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.95,
        tags=["agentseek", "框架", "插件", "contextseek"],
        links=[Link(b4_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # B5: ctx HTTP API
    b5_raw = ctx.add(
        (
            "运行 trace #2024-11-09T16:00Z：通过 curl POST /add 直接向 store 注入领域知识，"
            "agent 代码未做任何修改；下一次 agent 轮次立即检索到该条目，score 0.93。"
        ),
        scope="contextseek",
        source="agent-trace-log",
        source_type=SourceType.trace_extraction,
        stage=Stage.raw,
        stability=Stability.ephemeral,
        confidence=0.6,
        tags=["trace", "api", "http"],
    )
    b5_ext = ctx.add(
        (
            "从 trace #2024-11-09 提取的规律：ctx HTTP API 支持外部工具在不修改 agent 代码的"
            "情况下扩充 store，实现知识的热注入。"
        ),
        scope="contextseek",
        source="trace-extractor-v1",
        source_type=SourceType.trace_extraction,
        stage=Stage.extracted,
        stability=Stability.transient,
        confidence=0.75,
        tags=["api", "http", "规律"],
        links=[Link(b5_raw.id, LinkType.derived_from, strength=1.0)],
    )
    ctx.add(
        (
            "ctx HTTP API 允许外部工具在不修改 agent 代码的情况下向运行中的"
            "ContextSeek store 注入知识。核心端点："
            "POST /add（写入文本，支持 tags 和 source）；"
            "GET /retrieve?q=...&scope=...（语义搜索）；"
            "GET /overview（查看所有条目的 stage 和置信度）。"
        ),
        scope="contextseek",
        source="agent-inference",
        source_type=SourceType.agent_inference,
        stage=Stage.knowledge,
        stability=Stability.stable,
        confidence=0.9,
        tags=["api", "http", "contextseek", "检索"],
        links=[Link(b5_ext.id, LinkType.derived_from, strength=0.9)],
    )

    # ------------------------------------------------------------------
    # Group C: Near-duplicate pair (for compact/dream evolution)
    # ------------------------------------------------------------------

    ctx.add(
        (
            "ContextSeek 让 agent 能在多次对话 session 之间记住重要信息，"
            "避免对同一问题反复推理，从而降低整体延迟。"
        ),
        scope="contextseek",
        source="seed-docs",
        source_type=SourceType.human_input,
        stage=Stage.raw,
        stability=Stability.transient,
        confidence=0.7,
        tags=["记忆", "session", "重复候选"],
    )

    ctx.add(
        (
            "ContextSeek 可以让 AI agent 跨 session 保留关键上下文，"
            "无需每次交互都从头推理，从而降低延迟和 token 成本。"
        ),
        scope="contextseek",
        source="seed-docs",
        source_type=SourceType.human_input,
        stage=Stage.raw,
        stability=Stability.transient,
        confidence=0.7,
        tags=["记忆", "session", "重复候选"],
    )

    overview = ctx.overview(scope="contextseek")
    count = overview.total_items
    print(f"已向 ContextSeek store（contextseek scope）写入 {count} 条种子数据。")
    return count


if __name__ == "__main__":
    maybe_seed()
