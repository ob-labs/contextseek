"""ContextItem — the single unified object in ContextSeek.

Every piece of data in ContextSeek is a ContextItem. It is designed around
three structural pillars: Retrievable, Traceable, Evolvable.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from contextseek.domain.links import Link
from contextseek.domain.provenance import Provenance
from contextseek.domain.stages import STAGE_DEFAULT_STABILITY, Stability, Stage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_id() -> str:
    return uuid.uuid4().hex


def _compute_hash(content: str | dict[str, Any]) -> str:
    raw = str(content) if isinstance(content, dict) else content
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class ContextItem:
    """The single core object in ContextSeek.

    Design pillars:
    - Retrievable: once written it is searchable; content + tags + embedding form the retrieval surface
    - Traceable: provenance links every record back to its origin
    - Evolvable: stage marks maturity; links record evolution paths
    """

    # ═══════════════════════════════════════════
    # Identity
    # ═══════════════════════════════════════════
    content: str | dict[str, Any]
    """Primary payload (text or structured)."""

    scope: str
    """Isolation boundary, shaped like ``{tenant}/{project}/{scope_path}``."""

    provenance: Provenance
    """Source chain (required)."""

    id: str = field(default_factory=_generate_id)
    """Globally unique id."""

    # ═══════════════════════════════════════════
    # Pillar 1: Retrievable
    # ═══════════════════════════════════════════
    abstract: str | None = None
    """L2 short abstract (~100 tokens); source text for embeddings; None if not generated yet."""

    summary: str | None = None
    """L1 overview (~2k tokens); default surface returned to agents; inject summary instead of full text under tight budgets."""

    tags: list[str] = field(default_factory=list)
    """Searchable tags."""

    embedding: list[float] | None = None
    """Vector of abstract (L2); falls back to embedding ``content`` when abstract is None."""

    searchable: bool = True
    """Whether this item participates in search (set False after archival)."""

    relevance_boost: float = 1.0
    """Retrieval weighting (feedback-driven)."""

    # ═══════════════════════════════════════════
    # Pillar 2: Traceable
    # ═══════════════════════════════════════════
    links: list[Link] = field(default_factory=list)
    """Relations to other items."""

    effective_confidence: float | None = None
    """Confidence after propagation along evidence; None means use ``provenance.confidence``."""

    # ═══════════════════════════════════════════
    # Pillar 3: Evolvable
    # ═══════════════════════════════════════════
    stage: Stage = Stage.raw
    """Current evolution stage."""

    stability: Stability | None = None
    """Lifecycle policy; None is inferred from ``stage``."""

    # ═══════════════════════════════════════════
    # Lifecycle (system-managed)
    # ═══════════════════════════════════════════
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime | None = None
    hash: str = ""
    """Content fingerprint (idempotent dedup)."""

    importance: float = 1.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    superseded_by: str | None = None
    deleted_at: datetime | None = None
    deleted_reason: str | None = None

    # ═══════════════════════════════════════════
    # Bi-temporal validity (event time, not ingest time)
    # ═══════════════════════════════════════════
    valid_from: datetime | None = None
    """Event time the fact became true. None falls back to ``created_at``."""

    valid_to: datetime | None = None
    """Event time the fact stopped being true. None means currently valid.
    Closing this window (instead of deleting) is how conflicting updates retire
    a superseded fact while keeping it for temporal/audit queries."""

    invalidated_reason: str | None = None
    """Why the validity window was closed (e.g. ``superseded_by_update``)."""

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = _compute_hash(self.content)
        if self.stability is None:
            self.stability = STAGE_DEFAULT_STABILITY.get(
                self.stage, Stability.transient
            )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def is_valid_at(self, when: datetime | None = None) -> bool:
        """Whether the fact's validity window is open at *when* (default: now).

        A None ``valid_to`` means the window is still open. ``valid_from`` is
        honoured when set, so facts dated to the future are not yet valid.
        """
        moment = when or _utc_now()
        if self.valid_from is not None and moment < self.valid_from:
            return False
        if self.valid_to is not None and moment >= self.valid_to:
            return False
        return True

    def close_validity(
        self, *, reason: str | None = None, at: datetime | None = None
    ) -> None:
        """Close the validity window without deleting the row.

        The item stays in storage (and retains its embedding/links) so temporal
        queries can still reach it; default retrieval skips it once ``valid_to``
        is in the past.
        """
        self.valid_to = at or _utc_now()
        self.invalidated_reason = reason
        self.updated_at = _utc_now()

    @property
    def content_text(self) -> str:
        """Content as string (for indexing/display). Returns empty string when content is None."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return str(self.content)

    def touch(self) -> None:
        """Record an access."""
        self.access_count += 1
        self.last_accessed_at = _utc_now()

    def soft_delete(self, reason: str | None = None) -> None:
        """Mark as deleted."""
        self.deleted_at = _utc_now()
        self.deleted_reason = reason
        self.searchable = False
