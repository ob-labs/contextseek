"""ContextSeek DataPlug demo: import PowerMem memories via PowerMemPlug.

ContextSeek acts as a "smart socket" (DataPlug): existing PowerMem memories are
streamed in as ContextItems, then unified retrieval, provenance, and evolution
apply on top — without rewriting the agent harness for each data source.

Flow:
  1. PowerMem stores user/agent memories (live SQLite + mock embedder, or mock data)
  2. Export via ``get_all`` → ``PowerMemPlug.from_records()``
  3. ``ctx.plug(plug, scope=...)`` ingests into ContextSeek
  4. Optional ContextSeek-native items on the same scope
  5. ``retrieve()`` recalls PowerMem + ContextSeek content together

Modes (``USE_POWERMEM`` env):
  - ``auto`` (default): use PowerMem when installed, else built-in mock records
  - ``live``: require ``pip install powermem`` (isolated in-memory SQLite)
  - ``mock``: skip PowerMem, use sample records shaped like ``get_all`` output

Run:
    uv run python examples/powermem_plug_demo.py

Simpler entry (same idea, ~50 lines):
    uv run python examples/powermem_minimal.py

With live PowerMem (sibling repo or pip):
    USE_POWERMEM=live uv run python examples/powermem_plug_demo.py

Production-shaped usage (after ``memory.add`` / ``memory.search`` as today)::

    ctx.plug(
        PowerMemPlug.from_memory(memory, user_id=USER_ID, agent_id=AGENT_ID),
        scope=SCOPE,
    )
    ctx.retrieve(query, scope=SCOPE)
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import seekvfs

from contextseek import ContextSeek, SourceType
from contextseek.plugs import PowerMemPlug
from contextseek.storage import FileBackend, SeekVFSStorageAdapter
from contextseek.domain.stages import Stage

# ============================================================
# Configuration
# ============================================================

STORAGE_ROOT = "/tmp/seekctx_powermem_plug_demo"
CLEAN_ON_START = True
SCOPE = "demo_tenant/support_bot/alice"
USER_ID = "alice"
AGENT_ID = "support_bot"

USE_POWERMEM = os.environ.get("USE_POWERMEM", "auto").strip().lower()

MOCK_POWERMEM_RECORDS: list[dict[str, Any]] = [
    {
        "id": 1001,
        "content": "用户偏好：回复使用中文，技术术语保留英文。",
        "user_id": USER_ID,
        "agent_id": AGENT_ID,
        "metadata": {"tags": ["preference", "language"]},
    },
    {
        "id": 1002,
        "content": "用户是 OceanBase 客户，生产环境使用 4.x 集群，关注 HTAP 与向量检索。",
        "user_id": USER_ID,
        "agent_id": AGENT_ID,
        "metadata": {"tags": ["customer", "oceanbase"]},
    },
    {
        "id": 1003,
        "content": "上次工单：混合检索延迟偏高，已建议检查索引与 embedder 批大小。",
        "user_id": USER_ID,
        "agent_id": AGENT_ID,
        "metadata": {"tags": ["ticket", "search"]},
    },
]


def _powermem_available() -> bool:
    try:
        import powermem  # noqa: F401

        return True
    except ImportError:
        return False


def seed_powermem_memories() -> list[dict[str, Any]]:
    """Write demo memories with PowerMem and return get_all rows."""
    from powermem import Memory

    config = {
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": ":memory:",
                "collection_name": f"seekctx_plug_{uuid.uuid4().hex[:8]}",
            },
        },
        "embedder": {"provider": "mock", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-4o-mini", "api_key": "mock-key"},
        },
    }
    memory = Memory(config=config)

    seeds = [
        "用户偏好：回复使用中文，技术术语保留英文。",
        "用户是 OceanBase 客户，生产环境使用 4.x 集群，关注 HTAP 与向量检索。",
        "上次工单：混合检索延迟偏高，已建议检查索引与 embedder 批大小。",
    ]
    for text in seeds:
        memory.add(text, user_id=USER_ID, agent_id=AGENT_ID)

    payload = memory.get_all(user_id=USER_ID, agent_id=AGENT_ID, limit=50)
    return list(payload.get("results", []))


def load_powermem_records() -> tuple[list[dict[str, Any]], str]:
    """Resolve PowerMem rows and a short label for the data source."""
    if USE_POWERMEM == "mock":
        return MOCK_POWERMEM_RECORDS, "mock (USE_POWERMEM=mock)"

    if USE_POWERMEM == "live" or (USE_POWERMEM == "auto" and _powermem_available()):
        if not _powermem_available():
            raise SystemExit(
                "USE_POWERMEM=live but powermem is not installed. "
                "Install from ../powermem or: pip install powermem"
            )
        return seed_powermem_memories(), "live PowerMem (in-memory SQLite + mock embedder)"

    return MOCK_POWERMEM_RECORDS, "mock (powermem not installed; set USE_POWERMEM=live to require it)"


def print_hit(index: int, hit: Any) -> None:
    item = hit.item
    preview = (item.summary or item.content_text or "")[:72]
    tags = ", ".join(item.tags) if item.tags else "-"
    print(f"    [{index}] id={item.id}")
    print(f"         score={hit.score:.4f}  stage={item.stage.value}  layer={hit.layer}")
    print(f"         source={item.provenance.source_id!r}  tags=[{tags}]")
    print(f"         {preview}")


def main() -> None:
    root = Path(STORAGE_ROOT)
    if CLEAN_ON_START and root.exists():
        shutil.rmtree(root)

    records, source_label = load_powermem_records()
    plug = PowerMemPlug.from_records(records)

    print("=" * 72)
    print("  SEEKCONTEXT × POWERMEM — DataPlug (插座) DEMO")
    print("=" * 72)
    print(f"\n[1/5] PowerMem 数据源: {source_label}")
    print(f"      导出 {len(records)} 条记忆 → PowerMemPlug.from_records()")

    backend = FileBackend(root_dir=root, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)
    ctx = ContextSeek(adapter=adapter)

    with vfs:
        print(f"\n[2/5] 挂载插座: ctx.plug(PowerMemPlug, scope={SCOPE!r})")
        ctx.plug(plug, scope=SCOPE)

        print("\n[3/5] ContextSeek 侧追加演进知识（同一 scope，与 PowerMem 共存）")
        ctx.add(
            "Playbook：OceanBase 混合检索排障先查 ANN 索引与全文索引是否同库同表。",
            scope=SCOPE,
            source="playbook://hybrid-search",
            source_type=SourceType.document,
            tags=["playbook", "oceanbase"],
            stage=Stage.knowledge,
        )

        print("\n[4/5] 统一检索 — PowerMem 导入项 + ContextSeek 原生项")
        for query in ["OceanBase", "混合检索", "中文"]:
            response = ctx.retrieve(query, scope=SCOPE, k=5)
            print(f"\n  query={query!r}  命中 {len(response)} 条:")
            for i, hit in enumerate(response, 1):
                print_hit(i, hit)

        print("\n[5/5] 溯源对比（PowerMem 插座 vs ContextSeek 文档）")
        response = ctx.retrieve("OceanBase", scope=SCOPE, k=10)
        powermem_hits = [
            h for h in response if "powermem" in (h.item.tags or [])
        ]
        native_hits = [
            h for h in response
            if h.item.provenance.source_id.startswith("playbook://")
        ]
        print(f"  PowerMem 导入（tags 含 powermem）: {len(powermem_hits)} 条")
        for h in powermem_hits[:2]:
            print(f"    - {h.item.provenance.source_id}")
        print(f"  ContextSeek playbook: {len(native_hits)} 条")
        for h in native_hits:
            print(f"    - {h.item.provenance.source_id}")

        print("\n" + "-" * 72)
        print("要点:")
        print("  • PowerMem 继续负责记忆写入/向量检索；ContextSeek 通过 DataPlug 即插导入。")
        print("  • 同一 scope 下可与 trace、RAG、技能等 ContextItem 统一 retrieve。")
        print("  • provenance.source_id 保留 powermem://<id>，tags 含 powermem 便于过滤。")
        print("-" * 72)


if __name__ == "__main__":
    main()
