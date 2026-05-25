"""完整链路示例：FileBackend 本地文件后端 + ContextSeek。

演示：
  1. 用 FileBackend 把每个 ref 落盘成一个本地文件（零外部依赖）
  2. 通过 ContextSeek 写入 ContextItem
  3. 做关键词/子串检索（FileBackend 自带的 search 是朴素子串匹配）
  4. 直接使用 RetrievalOrchestrator 做底层检索（可选）

与 OB 版的关键差异：
  - FileBackend 不接 embedder，无向量召回，检索走 phrase + term 子串匹配
  - 因此查询要能作为子串命中某条 content（中文整串长 query 很难命中，
    建议用短关键词，例如 "分布式"、"向量"、"LangChain"）

运行前依赖：仅需项目本身，无需安装额外 extras。

运行方式：
    uv run python examples/full_pipeline_file.py
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import seekvfs
import shutil

from contextseek import ContextSeek
from contextseek.domain.provenance import SourceType
from contextseek.retrieval.orchestrator import RetrievalOrchestrator, RetrievalStats
from contextseek.routing.resolver import ScopeResolver
from contextseek.storage import FileBackend, SeekVFSStorageAdapter

# ============================================================
# == 配置区 ==
# ============================================================

STORAGE_ROOT = "/tmp/seekctx_file_demo"
CLEAN_ON_START = True

DEMO_SCOPE = "demo_tenant/default/alice"
DEMO_ITEMS: list[tuple[str, str, SourceType, list[str]]] = [
    (
        "OceanBase 是一款金融级分布式关系数据库，支持 HTAP 混合负载。",
        "oceanbase_doc",
        SourceType.document,
        ["oceanbase", "database", "distributed"],
    ),
    (
        "向量检索结合全文检索（混合检索）可以同时利用语义相似度和关键词匹配。",
        "internal_note",
        SourceType.human_input,
        ["vector", "search", "hybrid"],
    ),
    (
        "LangChain 的 Embeddings 接口统一了各家 embedding 模型的调用方式。",
        "internal_note",
        SourceType.human_input,
        ["langchain", "embedding"],
    ),
]


@dataclass
class FileDemoStack:
    ctx: ContextSeek
    adapter: SeekVFSStorageAdapter
    orchestrator: RetrievalOrchestrator
    scope: str
    root: Path
    item_ids: list[str]


@dataclass
class FileDemoSummary:
    item_ids: list[str]
    disk_files: int
    hits_by_query: dict[str, tuple[str, ...]]
    orchestrator_hit_ids: tuple[str, ...]
    stats: RetrievalStats | None


@contextmanager
def file_backend_demo_stack(
    root: Path,
    *,
    clean_start: bool = True,
) -> Iterator[FileDemoStack]:
    if clean_start and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    backend = FileBackend(root_dir=root, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)
    ctx = ContextSeek(adapter=adapter)
    orchestrator = RetrievalOrchestrator(adapter=adapter)

    item_ids: list[str] = []
    with vfs:
        for content, source, source_type, tags in DEMO_ITEMS:
            item = ctx.add(
                content,
                scope=DEMO_SCOPE,
                source=source,
                source_type=source_type,
                tags=tags,
            )
            item_ids.append(item.id)

        yield FileDemoStack(
            ctx=ctx,
            adapter=adapter,
            orchestrator=orchestrator,
            scope=DEMO_SCOPE,
            root=root,
            item_ids=item_ids,
        )


def run_file_backend_demo(
    root: Path,
    *,
    clean_start: bool = True,
    verbose: bool = False,
) -> FileDemoSummary:
    hits_by_query: dict[str, tuple[str, ...]] = {}
    orchestrator_hit_ids: tuple[str, ...] = ()
    stats: RetrievalStats | None = None
    item_ids: list[str] = []
    disk_files = 0

    with file_backend_demo_stack(root, clean_start=clean_start) as stack:
        item_ids = list(stack.item_ids)
        for query in ["分布式", "向量", "LangChain"]:
            response = stack.ctx.retrieve(query, scope=stack.scope, k=3)
            hits_by_query[query] = tuple(h.item.id for h in response)
            if verbose:
                print(f"query={query!r} hits={hits_by_query[query]}")

        resolver = ScopeResolver()
        prefix = resolver.prefix_for(stack.scope)
        raw_hits, stats = stack.orchestrator.search(
            prefixes=[prefix],
            query="分布式",
            k=3,
            with_stats=True,
        )
        orchestrator_hit_ids = tuple(h.item.id for h in raw_hits)
        disk_files = sum(1 for p in stack.root.rglob("*") if p.is_file())

    return FileDemoSummary(
        item_ids=item_ids,
        disk_files=disk_files,
        hits_by_query=hits_by_query,
        orchestrator_hit_ids=orchestrator_hit_ids,
        stats=stats,
    )


def main() -> None:
    root = Path(STORAGE_ROOT)
    print(f"[demo] storage root: {root}")
    summary = run_file_backend_demo(root, clean_start=CLEAN_ON_START, verbose=True)
    print(f"written {len(summary.item_ids)} items, disk files={summary.disk_files}")
    print(f"hits_by_query={summary.hits_by_query}")


if __name__ == "__main__":
    main()
