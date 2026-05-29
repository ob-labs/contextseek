"""Write-time conflict detection for ContextItems.

Detects duplicate and conflicting content during add() operations,
enabling the system to warn about redundant writes and identify
contradictions before they enter the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from contextseek.domain.context_item import ContextItem


class ConflictType(str, Enum):
    """Classification of detected conflicts."""

    duplicate = "duplicate"  # Same hash — exact content match
    near_duplicate = "near_duplicate"  # Very similar content (high overlap)
    contradiction = "contradiction"  # Conflicting assertion (refuted_by candidate)


@dataclass(frozen=True)
class WriteConflict:
    """A detected conflict between a new item and an existing item."""

    conflict_type: ConflictType
    existing_item_id: str
    existing_content_preview: str
    similarity: float  # 0.0–1.0
    suggestion: str  # Human-readable resolution hint


@dataclass(frozen=True)
class ConflictCheckResult:
    """Result of write-time conflict detection."""

    has_conflicts: bool
    conflicts: list[WriteConflict]

    @property
    def has_duplicates(self) -> bool:
        return any(c.conflict_type == ConflictType.duplicate for c in self.conflicts)

    @property
    def has_contradictions(self) -> bool:
        return any(
            c.conflict_type == ConflictType.contradiction for c in self.conflicts
        )


def detect_conflicts(
    new_item: ContextItem,
    existing_items: list[ContextItem],
    *,
    near_duplicate_threshold: float = 0.85,
    llm_judge: Callable[[str, str, float], ConflictType | None] | None = None,
    llm_min_similarity: float = 0.5,
    llm_max_similarity: float = 0.95,
    llm_budget: int = 3,
) -> ConflictCheckResult:
    """Detect conflicts between a new item and existing items in scope.

    Checks:
    1. Exact duplicate (same content hash)
    2. Near-duplicate (high token overlap)
    3. Contradiction (content negates existing knowledge)

    Args:
        new_item: The item being written.
        existing_items: Current items in the same scope (or an ANN-narrowed
            candidate set).
        near_duplicate_threshold: Token overlap above which items are
            considered near-duplicates (0.0–1.0).
        llm_judge: Optional callable that disambiguates near-dup vs contradiction
            for medium-similarity pairs.
        llm_min_similarity / llm_max_similarity: LLM judge is only invoked when
            token overlap falls inside this band.
        llm_budget: Maximum number of LLM judge calls per ``detect_conflicts``
            invocation. Caps the worst-case latency / cost when many candidates
            land in the medium-similarity band.

    Returns:
        ConflictCheckResult with detected conflicts.
    """
    conflicts: list[WriteConflict] = []
    remaining_llm_budget = max(0, int(llm_budget))

    new_text = new_item.content_text.lower()
    new_tokens = _tokenize(new_text)

    # Collect LLM-band candidates for deferred sorted processing; hash-dups,
    # near-dups, and negation-heuristic items are resolved inline on first pass.
    band_candidates: list[tuple[float, ContextItem]] = []

    for existing in existing_items:
        if existing.is_deleted:
            continue
        if not existing.searchable:
            continue

        # 1. Exact duplicate by hash
        if new_item.hash == existing.hash and new_item.scope == existing.scope:
            conflicts.append(
                WriteConflict(
                    conflict_type=ConflictType.duplicate,
                    existing_item_id=existing.id,
                    existing_content_preview=_preview(existing.content_text),
                    similarity=1.0,
                    suggestion="Exact duplicate exists. Consider using feedback() to reinforce instead.",
                )
            )
            continue

        # 2. Near-duplicate by token overlap
        existing_text = existing.content_text.lower()
        existing_tokens = _tokenize(existing_text)
        overlap = 0.0

        if new_tokens and existing_tokens:
            overlap = _jaccard_similarity(new_tokens, existing_tokens)
            if overlap >= near_duplicate_threshold:
                conflicts.append(
                    WriteConflict(
                        conflict_type=ConflictType.near_duplicate,
                        existing_item_id=existing.id,
                        existing_content_preview=_preview(existing.content_text),
                        similarity=round(overlap, 4),
                        suggestion=(
                            "Very similar item exists. "
                            "Consider merging via compact() or adding a supersedes link."
                        ),
                    )
                )
                continue

        # 2.5 Collect LLM-band candidates for deferred processing.
        # They are sorted by overlap descending after the first pass so the
        # budget is spent on the highest-similarity pairs first.
        if (
            llm_judge is not None
            and llm_min_similarity <= overlap <= llm_max_similarity
        ):
            band_candidates.append((overlap, existing))
            continue

        # 3. Contradiction detection (simple heuristic: negation patterns)
        if _appears_contradictory(new_text, existing_text):
            similarity = overlap if new_tokens and existing_tokens else 0.0
            conflicts.append(
                WriteConflict(
                    conflict_type=ConflictType.contradiction,
                    existing_item_id=existing.id,
                    existing_content_preview=_preview(existing.content_text),
                    similarity=round(similarity, 4),
                    suggestion=(
                        "Potential contradiction detected. "
                        "Consider adding a refuted_by link or supersedes link."
                    ),
                )
            )

    # 2.5 Process LLM-band candidates sorted by overlap descending.
    # High-similarity pairs are processed first so the budget is consumed where
    # it matters most; budget-exhausted remainders fall back to the negation
    # heuristic rather than being silently dropped.
    band_candidates.sort(key=lambda x: -x[0])
    for overlap, existing in band_candidates:
        if remaining_llm_budget <= 0:
            if _appears_contradictory(new_text, existing.content_text.lower()):
                conflicts.append(
                    WriteConflict(
                        conflict_type=ConflictType.contradiction,
                        existing_item_id=existing.id,
                        existing_content_preview=_preview(existing.content_text),
                        similarity=round(overlap, 4),
                        suggestion=(
                            "Potential contradiction detected. "
                            "Consider adding a refuted_by link or supersedes link."
                        ),
                    )
                )
            continue
        remaining_llm_budget -= 1
        try:
            judged = llm_judge(new_item.content_text, existing.content_text, overlap)
        except Exception:
            judged = None
        if judged == ConflictType.near_duplicate:
            conflicts.append(
                WriteConflict(
                    conflict_type=ConflictType.near_duplicate,
                    existing_item_id=existing.id,
                    existing_content_preview=_preview(existing.content_text),
                    similarity=round(overlap, 4),
                    suggestion="LLM judged as near-duplicate. Consider merging via compact().",
                )
            )
        elif judged == ConflictType.contradiction:
            conflicts.append(
                WriteConflict(
                    conflict_type=ConflictType.contradiction,
                    existing_item_id=existing.id,
                    existing_content_preview=_preview(existing.content_text),
                    similarity=round(overlap, 4),
                    suggestion="LLM judged as contradiction. Consider refuted_by or supersedes links.",
                )
            )

    return ConflictCheckResult(
        has_conflicts=len(conflicts) > 0,
        conflicts=conflicts,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NEGATION_MARKERS = frozenset(
    {
        "not",
        "never",
        "don't",
        "doesn't",
        "shouldn't",
        "cannot",
        "can't",
        "won't",
        "isn't",
        "aren't",
        "wasn't",
        "weren't",
        "no longer",
        # CJK negation markers for Chinese-language content analysis
        "不",
        "不要",
        "不能",
        "不是",
        "不应",
        "无需",
        "禁止",
        "不再",
    }
)


def _tokenize(text: str) -> set[str]:
    """Simple whitespace + punctuation tokenizer."""
    import re

    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _appears_contradictory(text_a: str, text_b: str) -> bool:
    """Heuristic: two texts are contradictory if they share key tokens
    but one contains negation markers the other doesn't."""
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)

    # Need sufficient overlap to be "about the same thing"
    if not tokens_a or not tokens_b:
        return False
    overlap = _jaccard_similarity(tokens_a, tokens_b)
    if overlap < 0.3:
        return False

    # Check negation asymmetry
    neg_in_a = bool(tokens_a & _NEGATION_MARKERS)
    neg_in_b = bool(tokens_b & _NEGATION_MARKERS)

    return neg_in_a != neg_in_b


def _preview(text: str, max_len: int = 80) -> str:
    """Truncate text for display."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
