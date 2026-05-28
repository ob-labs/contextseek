"""In-memory SeekVFSAdapter with cosine-similarity vector search.

Used in tests and local development to exercise the vector recall path
without requiring an OceanBase (or any external) deployment.
"""

from __future__ import annotations

import json
import math
from typing import Any

from contextseek.storage.protocol import HashIndexMixin
from contextseek.storage.protocol import VectorSearchMixin


def _cosine(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [0, 1] between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class VectorMemoryAdapter(HashIndexMixin, VectorSearchMixin):
    """Flat in-memory adapter supporting both text and vector search.

    Payloads are stored as plain dicts.  Text search uses simple substring
    matching; vector search uses cosine similarity over stored embeddings.

    Usage::

        embedder = lambda text: my_model.encode(text)
        adapter = VectorMemoryAdapter(embedder=embedder)
        adapter.write("agentseek://memories/t/u/m1", payload)
        hits = adapter.vector_search("agentseek://memories/t/u/", query_vec, k=5)
    """

    def __init__(
        self,
        embedder: Any | None = None,
    ) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._vectors: dict[str, list[float]] = {}
        self._embedder = embedder

    # ------------------------------------------------------------------
    # SeekVFSAdapter protocol
    # ------------------------------------------------------------------

    def write(self, ref: str, payload: dict[str, Any]) -> None:
        self._store[ref] = dict(payload)
        # Prefer API-precomputed vectors (from L0 abstract), matching the OB backend;
        # fall back to this adapter's embedder when missing (tests / standalone use).
        pre_computed = payload.get("embedding")
        if pre_computed is not None:
            self._vectors[ref] = list(pre_computed)
            return
        if self._embedder is not None:
            text = _extract_text(payload)
            if text:
                try:
                    self._vectors[ref] = self._embedder(text)
                except Exception:  # noqa: BLE001
                    pass

    def read(self, ref: str) -> dict[str, Any] | None:
        payload = self._store.get(ref)
        return dict(payload) if payload is not None else None

    def search(
        self,
        prefix: str,
        query: str,
        *,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        # When callers supply a query embedding, reuse cosine vector recall;
        # otherwise keep the legacy keyword substring matcher.
        if query_embedding is not None:
            return self.vector_search(prefix, query_embedding, k=k)

        q_low = query.lower()
        hits: list[dict[str, Any]] = []
        for ref, payload in self._store.items():
            if not ref.startswith(prefix):
                continue
            text = _extract_text(payload).lower()
            score = 1.0 if q_low and q_low in text else 0.0
            if score <= 0:
                continue
            item = dict(payload)
            item["ref"] = ref
            item["score"] = score
            hits.append(item)
        hits.sort(key=lambda h: float(h.get("score", 0.0)), reverse=True)
        return hits[:k]

    def ls(self, prefix: str) -> list[str]:
        return sorted(ref for ref in self._store if ref.startswith(prefix))

    def delete(self, ref: str) -> bool:
        existed = ref in self._store
        self._store.pop(ref, None)
        self._vectors.pop(ref, None)
        return existed

    def find_by_hash(self, prefix: str, hash_value: str) -> str | None:
        if not hash_value:
            return None
        for ref, payload in self._store.items():
            if not ref.startswith(prefix):
                continue
            if str(payload.get("hash") or "") == hash_value:
                return ref
        return None

    # ------------------------------------------------------------------
    # VectorSearchMixin override
    # ------------------------------------------------------------------

    def vector_search(
        self,
        prefix: str,
        query_vector: list[float],
        *,
        k: int,
    ) -> list[dict[str, Any]]:
        """Return payloads ranked by cosine similarity to *query_vector*."""
        scored: list[tuple[float, str]] = []
        for ref, vec in self._vectors.items():
            if not ref.startswith(prefix):
                continue
            sim = _cosine(query_vector, vec)
            if sim > 0.0:
                scored.append((sim, ref))
        scored.sort(reverse=True)
        hits: list[dict[str, Any]] = []
        for sim, ref in scored[:k]:
            payload = self._store.get(ref)
            if payload is None:
                continue
            item = dict(payload)
            item["ref"] = ref
            item["score"] = round(sim, 6)
            hits.append(item)
        return hits

    def index_vector(self, ref: str, vector: list[float]) -> None:
        """Manually index a pre-computed vector for *ref*.

        Useful when the payload was written without an embedder but you
        want to add the vector afterwards (e.g. in tests).
        """
        self._vectors[ref] = list(vector)


def _extract_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    content = payload.get("content", "")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, dict):
        parts.append(json.dumps(content, ensure_ascii=False))
    tags = payload.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(t) for t in tags)
    source_meta = payload.get("source_meta", {})
    if isinstance(source_meta, dict):
        text_field = source_meta.get("text", "")
        if text_field:
            parts.append(str(text_field))
    return " ".join(parts)


__all__ = ["VectorMemoryAdapter"]
