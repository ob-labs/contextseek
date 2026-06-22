"""RAG DataPlug — imports RAG retrieval results into ContextSeek's evolution pipeline.

RAG results that are adopted by the agent get reinforced; results that are
rejected decay. This plug streams RAG documents as RawEvents so they enter
the standard evolution pipeline (raw → extracted → knowledge).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from contextseek.plugs.core.protocols import PlugMeta, RawEvent


@dataclass
class RAGPlug:
    """DataPlug that streams documents from a RAG/vector-store retrieval.

    Accepts retrieval results as a list of dicts, each containing at
    minimum a "content" (or "page_content") field.

    Optional fields per document:
    - content / page_content (str): document text
    - metadata (dict): source metadata (url, title, chunk_id, etc.)
    - score (float): retrieval similarity score
    - source (str): document source identifier
    - tags (list[str]): optional tags

    Example::

        from contextseek.plugs import RAGPlug

        results = vector_store.similarity_search("deployment", k=10)
        docs = [{"content": d.page_content, "metadata": d.metadata} for d in results]
        ctx.plug(RAGPlug(documents=docs))
    """

    documents: list[dict[str, Any]] = field(default_factory=list)
    source_name: str = "rag"
    description: str = "RAG retrieval results import"

    def stream(self) -> Iterator[RawEvent]:
        """Yield each retrieved document as a RawEvent."""
        for doc in self.documents:
            content = doc.get("content") or doc.get("page_content", "")
            if not content:
                continue

            tags = list(doc.get("tags", []))
            tags = ["rag", "retrieval"] + tags

            metadata: dict[str, Any] = {}
            if "metadata" in doc and isinstance(doc["metadata"], dict):
                metadata.update(doc["metadata"])
            if "score" in doc:
                metadata["retrieval_score"] = doc["score"]

            source = doc.get("source", self.source_name)
            if "metadata" in doc and isinstance(doc["metadata"], dict):
                source = doc["metadata"].get("source", source)

            yield RawEvent(
                content=content,
                source=source,
                tags=tags,
                metadata=metadata,
            )

    def metadata(self) -> PlugMeta:
        """Return plug metadata."""
        return PlugMeta(
            name=self.source_name,
            source_type="external_api",
            description=self.description,
        )
