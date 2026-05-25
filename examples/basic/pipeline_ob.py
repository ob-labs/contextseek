"""完整链路示例：OceanBase backend + 真实 Embedder + ContextSeek。

涵盖：
  1. 用 LangChainEmbedder 包装任意 LangChain Embeddings 模型
  2. 用 OceanBaseBackend 作为向量 + 全文混合检索后端
  3. 通过 ContextSeek 写入 ContextItem，并做语义检索
  4. 直接使用 RetrievalOrchestrator 做底层检索（可选）

运行前依赖安装：
    # OceanBase 后端
    pip install "contextseek[oceanbase]"

    # LangChain 接口层（必装）
    pip install "contextseek[langchain]"

    # Embeddings provider，任选其一：
    pip install "contextseek[openai]"       # OpenAI
    pip install "contextseek[ollama]"       # Ollama 本地
    # 阿里云百炼 / DashScope（Qwen 系 text-embedding-*）：
    pip install langchain-community dashscope

运行方式：
    uv run python examples/full_pipeline_ob.py
"""

from __future__ import annotations

# ============================================================
# == 配置区：请按实际情况修改 ==
# ============================================================

# -- OceanBase 连接信息 --
OB_HOST = "127.0.0.1"
OB_PORT = "2881"
OB_USER = "root@test"
OB_PASSWORD = "atest"
OB_DB_NAME = "contextseek"

TABLE_NAME = "seekctx_full_demo"

# -- 向量维度（必填：须与模型真实输出维数一致，且与 OB 表向量列一致）--
# DashScope text-embedding-v3 / 未指定 dimension 的 v4 常见为 1024；OpenAI 可按模型设 1536 等
VECTOR_DIMS = 1024

FULLTEXT_PARSER = "ngram"
METRIC = "cosine"

# -- 向量来源：openai | dashscope（百炼 DashScope / Qwen 系 text-embedding-*）--
EMBED_BACKEND = "dashscope"

# OpenAI（EMBED_BACKEND=openai）
OPENAI_API_KEY = "sk-"
OPENAI_BASE_URL = ""
OPENAI_MODEL = "text-embedding-v4"

# 百炼 DashScope（EMBED_BACKEND=dashscope）；密钥可留空，改读环境变量 DASHSCOPE_API_KEY
DASHSCOPE_API_KEY = ""  # 可留空，使用环境变量 DASHSCOPE_API_KEY
DASHSCOPE_MODEL = "text-embedding-v4"  # 维度以实测为准；与 VECTOR_DIMS 不一致时会启动报错

# ============================================================

import seekvfs

from contextseek import ContextSeek
from contextseek.embedders import LangChainEmbedder
from contextseek.storage import OceanBaseBackend, SeekVFSStorageAdapter
from contextseek.domain.provenance import SourceType
from contextseek.retrieval.orchestrator import RetrievalOrchestrator
from contextseek.routing.resolver import ScopeResolver


# ---------------------------------------------------------------------------
# 工厂函数：构建 embedder
# ---------------------------------------------------------------------------

def build_embedder() -> LangChainEmbedder:
    """返回一个包装好的 LangChain embedder。"""
    backend = (EMBED_BACKEND or "openai").strip().lower()
    if backend == "dashscope":
        from langchain_community.embeddings import DashScopeEmbeddings

        kwargs: dict = {"model": DASHSCOPE_MODEL}
        if DASHSCOPE_API_KEY:
            kwargs["dashscope_api_key"] = DASHSCOPE_API_KEY
        lc_embeddings = DashScopeEmbeddings(**kwargs)
        return LangChainEmbedder(lc_embeddings, dims=VECTOR_DIMS)

    if backend != "openai":
        raise ValueError(f"未知 EMBED_BACKEND={EMBED_BACKEND!r}，请使用 openai 或 dashscope")

    from langchain_openai import OpenAIEmbeddings

    lc_embeddings = OpenAIEmbeddings(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        dimensions=VECTOR_DIMS,
        check_embedding_ctx_length=False,
        **({"base_url": OPENAI_BASE_URL} if OPENAI_BASE_URL else {}),
    )
    return LangChainEmbedder(lc_embeddings, dims=VECTOR_DIMS)


def _validate_vector_dims(embedder: LangChainEmbedder) -> None:
    """避免 VECTOR_DIMS 与模型实际输出不一致导致 pyobvector 写入失败。"""
    probe = embedder("contextseek-dim-probe")
    actual = len(probe)
    if actual != embedder.dims:
        raise SystemExit(
            f"VECTOR_DIMS={embedder.dims} 与模型实际向量长度 {actual} 不一致。\n"
            f"请将 VECTOR_DIMS 改为 {actual}；若 OceanBase 表已按旧维度创建，请换 TABLE_NAME 或删表重建。"
        )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    print("[1/4] 初始化 embedder...")
    embedder = build_embedder()
    _validate_vector_dims(embedder)
    print(f"[2/4] 获取向量维度: {embedder.dims}")

    vector_dims = embedder.dims

    # 1. 构建 OceanBase 后端
    print("[3/4] 构建 OceanBase backend...")
    backend = OceanBaseBackend(
        table_name=TABLE_NAME,
        vector_dims=vector_dims,
        host=OB_HOST,
        port=OB_PORT,
        user=OB_USER,
        password=OB_PASSWORD,
        db_name=OB_DB_NAME,
        fulltext_parser=FULLTEXT_PARSER,
        vidx_metric_type=METRIC,
        vector_weight=0.6,
        fts_weight=0.4,
        rrf_k=60,
    )

    # 2. VFS + 适配器
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)

    # 3. ContextSeek（注入自定义 adapter + embedder）
    ctx = ContextSeek(adapter=adapter, embedder=embedder)
    scope = "demo_tenant/default/alice"

    print("[4/4] 连接 OceanBase 并初始化...")
    with vfs:
        print("=== 初始化完成，开始写入数据 ===\n")

        # ----------------------------------------------------------------
        # 写入 ContextItem
        # ----------------------------------------------------------------
        items_data = [
            ("OceanBase 是一款金融级分布式关系数据库，支持 HTAP 混合负载。",
             "oceanbase_doc", SourceType.document, ["oceanbase", "database", "distributed"]),
            ("向量检索结合全文检索（混合检索）可以同时利用语义相似度和关键词匹配。",
             "internal_note", SourceType.human_input, ["vector", "search", "hybrid"]),
            ("LangChain 的 Embeddings 接口统一了各家 embedding 模型的调用方式。",
             "internal_note", SourceType.human_input, ["langchain", "embedding"]),
        ]

        for content, source, source_type, tags in items_data:
            item = ctx.add(
                content,
                scope=scope,
                source=source,
                source_type=source_type,
                tags=tags,
            )
            print(f"已写入: {item.id}  stage={item.stage.value}  —  {content[:40]}...")

        # ----------------------------------------------------------------
        # 语义检索（通过 ContextSeek）
        # ----------------------------------------------------------------
        print("\n=== 语义检索：ContextSeek.retrieve ===")
        query = "分布式数据库的向量混合检索"
        response = ctx.retrieve(query, scope=scope, k=3)
        for i, hit in enumerate(response, 1):
            print(f"  [{i}] id={hit.item.id}")
            print(f"       score={hit.score:.6f}  stage={hit.item.stage.value}  layer={hit.layer}")
            preview = (hit.item.summary or hit.item.content_text)[:80]
            print(f"       content={preview}")
        print(f"  meta: {response.meta}")

        # ----------------------------------------------------------------
        # 底层检索（直接使用 RetrievalOrchestrator）
        # ----------------------------------------------------------------
        print("\n=== 底层检索：RetrievalOrchestrator.search ===")
        orchestrator = RetrievalOrchestrator(adapter=adapter, embedder=embedder)
        resolver = ScopeResolver()
        prefix = resolver.prefix_for(scope)
        raw_hits, stats = orchestrator.search(
            prefixes=[prefix],
            query=query,
            k=3,
            with_stats=True,
        )
        for i, hit in enumerate(raw_hits, 1):
            print(f"  [{i}] id={hit.item.id}  score={hit.score:.6f}  layer={hit.layer}")
            preview = (hit.item.summary or hit.item.content_text)[:80]
            print(f"       content={preview}")
        print(f"\n  召回耗时={stats.recall_ms:.1f}ms  重排耗时={stats.rerank_ms:.1f}ms")
        print(f"  候选数={stats.candidate_count}  去重后={stats.deduped_count}  返回={stats.returned_count}")


if __name__ == "__main__":
    main()
