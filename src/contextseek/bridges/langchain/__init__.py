"""LangChain adapter exports."""

from typing import TYPE_CHECKING, Any

from contextseek.bridges.langchain.memory import ContextSeekMemory
from contextseek.bridges.langchain.retriever import ContextSeekRetriever

if TYPE_CHECKING:
    from contextseek.bridges.langchain.middleware import ContextSeekMiddleware

__all__ = ["ContextSeekMemory", "ContextSeekMiddleware", "ContextSeekRetriever"]


def __getattr__(name: str) -> Any:
    if name == "ContextSeekMiddleware":
        from contextseek.bridges.langchain.middleware import ContextSeekMiddleware

        return ContextSeekMiddleware
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
