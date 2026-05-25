"""Minimal PowerMem → ContextSeek path (for users who already use PowerMem).

You only need ContextSeek when context is not *just* memories — e.g. trace,
RAG, playbooks in one ``retrieve()``. Otherwise keep using ``memory.search``.

Run:
    uv run python examples/powermem_minimal.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import seekvfs

from contextseek import ContextSeek
from contextseek.plugs import PowerMemPlug
from contextseek.storage import FileBackend, SeekVFSStorageAdapter

SCOPE = "demo/alice/bot"
STORAGE_ROOT = "/tmp/seekctx_powermem_minimal"

# Same shape as powermem Memory.get_all()["results"] — no powermem install required.
MOCK_MEMORY = type(
    "MockMemory",
    (),
    {
        "get_all": staticmethod(
            lambda user_id=None, agent_id=None, run_id=None, limit=100, offset=0: {
                "results": [
                    {
                        "id": 1,
                        "content": "用户偏好：回复使用中文。",
                        "user_id": user_id,
                        "agent_id": agent_id,
                    }
                ]
            }
        )
    },
)()


def main() -> None:
    root = Path(STORAGE_ROOT)
    if root.exists():
        shutil.rmtree(root)

    backend = FileBackend(root_dir=root, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    ctx = ContextSeek(adapter=SeekVFSStorageAdapter(vfs))

    with vfs:
        plug = PowerMemPlug.from_memory(
            MOCK_MEMORY, user_id="alice", agent_id="bot"
        )
        ctx.plug(plug, scope=SCOPE)
        print(f"plugged {len(plug.entries)} memories into scope {SCOPE!r}")

        hits = ctx.retrieve("中文", scope=SCOPE, k=3)
        for hit in hits:
            print(f"  - {hit.item.provenance.source_id}: {hit.item.content_text[:40]}")


if __name__ == "__main__":
    main()
