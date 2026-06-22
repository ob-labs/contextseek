"""Result types for ContextSeek API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Literal

if TYPE_CHECKING:
    from contextseek.domain.context_item import ContextItem


@dataclass(frozen=True)
class SearchHit:
    """Ranked retrieval row returned inside a :class:`RetrieveResponse`."""

    item: ContextItem
    """Matched ``ContextItem``. ``item.summary`` holds L1; ``item.content`` is filled only when ``full=True`` or after ``expand``."""

    score: float
    """Combined relevance score."""

    layer: Literal["summary", "full"]
    """Content tier exposed for this hit. ``"summary"`` means L1 only; ``"full"`` means L0 body is present."""

    provenance_summary: str
    """One-line provenance blurb (e.g. distilled from three deploy traces)."""

    stage_confidence: float
    """Stage-derived trust (skill=1.0, knowledge=0.85, extracted=0.6, raw=0.3)."""

    recall_path: str = ""
    """Recall path label (observability)."""


@dataclass(frozen=True)
class ResponseMeta:
    """Response-level metadata that lets the LLM discover ``expand``.

    ``layer`` states which tier this response exposes; ``full_via`` is for
    programmatic parsing; ``hint`` gives weaker models natural-language
    guidance and shares copy with ``ToolSpec``.
    """

    layer: Literal["summary", "full"]
    full_via: str = "expand"
    hint: str = ""


@dataclass
class RetrieveResponse:
    """Unified return type for ``ContextSeek.retrieve()``.

    Iterate hits with ``for hit in response``; read ``response.meta`` for
    response-level metadata (layer / full_via / hint).
    """

    items: list[SearchHit] = field(default_factory=list)
    meta: ResponseMeta = field(default_factory=lambda: ResponseMeta(layer="full"))

    def __iter__(self) -> Iterator[SearchHit]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> SearchHit:
        return self.items[index]


@dataclass
class CompactReport:
    """Return value of ``compact()``."""

    merged_count: int = 0
    """Number of merged items."""

    archived_count: int = 0
    """Number of archived items."""

    evolved_count: int = 0
    """Number of items promoted along the evolution path."""

    conflict_updated_count: int = 0
    """Established facts retired (validity window closed) by a newer update."""

    conflict_drift_count: int = 0
    """Incoming items quarantined as drift against higher-authority facts."""

    details: dict = field(default_factory=dict)


@dataclass
class EvolutionReport:
    """Return value of ``overview()``."""

    total_items: int = 0
    stage_distribution: dict[str, int] = field(default_factory=dict)
    pending_extraction: int = 0
    """Count of raw items awaiting extraction."""

    pending_convergence: int = 0
    """Extracted clusters that may converge to knowledge."""

    distill_candidates: int = 0
    """Knowledge items that meet distillation criteria."""
