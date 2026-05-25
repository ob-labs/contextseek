"""Example: ContextSeek + LangChain style retrieval/memory pipeline.

Run from repository root:
    PYTHONPATH=src python examples/langchain_pipeline.py
"""

from __future__ import annotations

from contextseek import ContextSeek, SourceType
from contextseek.bridges.langchain import ContextSeekMemory, ContextSeekRetriever


def main() -> None:
    """Execute a minimal retrieval + memory flow."""
    ctx = ContextSeek()
    scope = "demo_tenant/default/alice"

    # Add some knowledge items
    ctx.add(
        "用户偏好: 默认使用中文回答，输出尽量简洁。",
        scope=scope,
        source="profile",
        source_type=SourceType.human_input,
        tags=["preference", "language"],
    )
    ctx.add(
        "项目目标: 在本周五前交付 ContextSeek MVP。",
        scope=scope,
        source="trace_001",
        source_type=SourceType.trace_extraction,
        tags=["project", "deadline"],
    )

    # LangChain adapters
    memory = ContextSeekMemory(client=ctx, scope=scope, k=10)
    retriever = ContextSeekRetriever(client=ctx, scope=scope, k=5)

    # Save a conversation turn via memory adapter
    memory.save_context(
        {"input": "今天需要完成什么?"},
        {"output": "建议先完成 SDK 基础写读查和 HTTP 路由。"},
    )

    # Retrieve relevant context
    docs = retriever.invoke("本周五")
    history = memory.load_memory_variables({})

    print("=== Retrieved Documents ===")
    for index, doc in enumerate(docs, start=1):
        print(f"{index}. {doc.page_content}")
        print(f"   metadata={doc.metadata}")

    print("\n=== Memory History ===")
    print(history["history"])


if __name__ == "__main__":
    main()
