"""Conflict detection and resolution — distinguishes *update* from *drift*.

When two items in a scope talk about the same subject but assert different
things, one of two situations holds:

- **Update**: the newer item comes from an equal-or-higher authority source.
  The older fact is retired by closing its bi-temporal validity window
  (``valid_to``) rather than deleting it, and the new item ``supersedes`` it.
- **Drift**: the newer item comes from a *lower* authority source (e.g. a dream
  hypothesis contradicting human-entered knowledge). Accepting it would let the
  store drift away from ground truth, so the incoming item is quarantined
  (validity window closed, ``refuted_by`` link, importance lowered) and the
  established fact stays valid.

Authority blends stage maturity (``STAGE_CONFIDENCE``) with provenance
confidence, so a human-entered raw note can still outrank a low-confidence
inference. Contradiction detection defaults to an LLM-free heuristic but accepts
a pluggable ``contradiction_fn`` for higher precision.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from contextseek.domain.context_item import ContextItem
from contextseek.domain.links import Link, LinkType
from contextseek.domain.stages import STAGE_CONFIDENCE

# Negation markers across English and Chinese — asymmetry between two otherwise
# similar statements is a strong contradiction signal.
_NEGATION = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "cannot",
        "n't",
        "without",
        "无",
        "不",
        "没",
        "別",
        "别",
        "非",
    }
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def _token_similarity(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _event_time(item: ContextItem) -> datetime:
    return item.valid_from or item.created_at


@dataclass
class ConflictResolution:
    """Outcome of a conflict-resolution pass."""

    updated: list[ContextItem] = field(default_factory=list)
    """Established items whose validity window was closed by a newer update."""

    quarantined: list[ContextItem] = field(default_factory=list)
    """Incoming items rejected as drift against higher-authority facts."""

    verdicts: list[tuple[str, str, str]] = field(default_factory=list)
    """``(incoming_id, existing_id, "update"|"drift")`` audit trail."""

    @property
    def touched(self) -> list[ContextItem]:
        """All items mutated by this pass (deduped by id)."""
        seen: dict[str, ContextItem] = {}
        for it in self.updated + self.quarantined:
            seen[it.id] = it
        return list(seen.values())


class ConflictResolver:
    """Detects same-subject contradictions and resolves them.

    Args:
        similarity_threshold: Minimum similarity for two items to count as
            "about the same subject" (and thus comparable for contradiction).
        high_similarity: At or above this similarity a differing content hash is
            treated as a contradiction even without an explicit disagreement
            signal (it is almost certainly a refined/changed value).
        tie_epsilon: Authority gap below which the pair is a tie; ties resolve to
            *update* (newer wins) rather than drift.
        embedder: Optional text→vector fn used when items lack embeddings.
        contradiction_fn: Optional ``(a, b) -> bool | None`` override. Returning
            None defers to the heuristic.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.82,
        high_similarity: float = 0.93,
        tie_epsilon: float = 0.05,
        drift_importance_factor: float = 0.5,
        embedder: Callable[[str], list[float]] | None = None,
        contradiction_fn: Callable[[ContextItem, ContextItem], bool | None]
        | None = None,
    ) -> None:
        self._threshold = similarity_threshold
        self._high = high_similarity
        self._tie = tie_epsilon
        self._drift_factor = drift_importance_factor
        self._embedder = embedder
        self._contradiction_fn = contradiction_fn

    def resolve(self, items: list[ContextItem]) -> ConflictResolution:
        """Scan *items* pairwise, resolving every detected contradiction.

        Only currently-valid, non-deleted items participate. Items already on the
        same evolution lineage (merge/derive/distill links) are skipped — those
        are refinements, not contradictions.
        """
        result = ConflictResolution()
        active = [
            it
            for it in items
            if not it.is_deleted and it.searchable and it.is_valid_at()
        ]
        # Oldest first so a chain of updates collapses deterministically onto the
        # most recent item.
        active.sort(key=_event_time)
        closed: set[str] = set()

        for i, a in enumerate(active):
            if a.id in closed:
                continue
            for b in active[i + 1 :]:
                if b.id in closed or a.id in closed:
                    continue
                if a.scope != b.scope:
                    continue
                if self._on_same_lineage(a, b):
                    continue
                if not self._contradicts(a, b):
                    continue

                # b is newer (active is sorted ascending by event time).
                incoming, existing = b, a
                if self._authority(incoming) + self._tie >= self._authority(existing):
                    self._apply_update(incoming=incoming, existing=existing)
                    result.updated.append(existing)
                    result.verdicts.append((incoming.id, existing.id, "update"))
                    closed.add(existing.id)
                else:
                    self._apply_drift(incoming=incoming, existing=existing)
                    result.quarantined.append(incoming)
                    result.verdicts.append((incoming.id, existing.id, "drift"))
                    closed.add(incoming.id)

        return result

    # ── resolution actions ──────────────────────────────────────────────
    def _apply_update(self, *, incoming: ContextItem, existing: ContextItem) -> None:
        existing.close_validity(
            reason=f"superseded_by_update:{incoming.id}",
            at=_event_time(incoming),
        )
        existing.superseded_by = incoming.id
        if not _has_link(incoming, existing.id, LinkType.supersedes):
            incoming.links.append(
                Link(target_id=existing.id, relation=LinkType.supersedes, strength=0.9)
            )

    def _apply_drift(self, *, incoming: ContextItem, existing: ContextItem) -> None:
        incoming.close_validity(reason=f"drift_vs:{existing.id}")
        incoming.importance = max(0.05, incoming.importance * self._drift_factor)
        if incoming.effective_confidence is None:
            base = incoming.provenance.confidence
        else:
            base = incoming.effective_confidence
        incoming.effective_confidence = base * self._drift_factor
        for tag in ("needs_review", "drift_quarantined"):
            if tag not in incoming.tags:
                incoming.tags.append(tag)
        if not _has_link(incoming, existing.id, LinkType.refuted_by):
            incoming.links.append(
                Link(target_id=existing.id, relation=LinkType.refuted_by, strength=0.8)
            )

    # ── signals ─────────────────────────────────────────────────────────
    def _authority(self, item: ContextItem) -> float:
        stage_conf = STAGE_CONFIDENCE.get(item.stage, 0.3)
        prov_conf = item.provenance.confidence
        score = 0.6 * stage_conf + 0.4 * prov_conf
        if item.provenance.verified:
            score = min(1.0, score + 0.1)
        return score

    def _similarity(self, a: ContextItem, b: ContextItem) -> float:
        if a.embedding and b.embedding:
            return _cosine(a.embedding, b.embedding)
        if self._embedder:
            ea, eb = self._embedder(a.content_text), self._embedder(b.content_text)
            if ea and eb:
                return _cosine(ea, eb)
        return _token_similarity(a.content_text, b.content_text)

    def _contradicts(self, a: ContextItem, b: ContextItem) -> bool:
        if a.hash == b.hash:
            return False  # identical content — dedup territory, not conflict
        if self._contradiction_fn is not None:
            verdict = self._contradiction_fn(a, b)
            if verdict is not None:
                return bool(verdict)
        sim = self._similarity(a, b)
        if sim < self._threshold:
            return False
        if sim >= self._high:
            return True
        return self._disagreement_signal(a.content_text, b.content_text)

    @staticmethod
    def _disagreement_signal(text_a: str, text_b: str) -> bool:
        """Heuristic: same subject but differing numbers or negation asymmetry."""
        nums_a = set(_NUMBER_RE.findall(text_a))
        nums_b = set(_NUMBER_RE.findall(text_b))
        if (nums_a or nums_b) and nums_a != nums_b:
            return True
        neg_a = bool(_tokenize(text_a) & _NEGATION)
        neg_b = bool(_tokenize(text_b) & _NEGATION)
        return neg_a != neg_b

    @staticmethod
    def _on_same_lineage(a: ContextItem, b: ContextItem) -> bool:
        lineage = {
            LinkType.merged_from,
            LinkType.derived_from,
            LinkType.distilled_into,
            LinkType.synthesized_from,
            LinkType.supersedes,
        }
        for src, dst in ((a, b), (b, a)):
            for lnk in src.links:
                if lnk.target_id == dst.id and lnk.relation in lineage:
                    return True
        return False


def _has_link(item: ContextItem, target_id: str, relation: LinkType) -> bool:
    return any(
        lnk.target_id == target_id and lnk.relation == relation for lnk in item.links
    )


__all__ = ["ConflictResolution", "ConflictResolver"]
