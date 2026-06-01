"""Knowledge-base health check — inspects the item graph for structural problems.

Checks:
  - Orphan items: access_count == 0 and not referenced by any skill provenance
  - Potential contradictions: high similarity but opposing negation/affirmation words
  - Distillation opportunities: items meeting heuristic thresholds but not yet skill

Results feed into:
  - ``contextseek lint`` CLI output
  - ``contextseek overview`` "待确认" section
  - ``LifecycleScheduler._dream_scope()`` target selection
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from contextseek.domain.context_item import ContextItem
from contextseek.domain.stages import Stage
from contextseek.evolution.distiller import HeuristicDistillRule

# ---------------------------------------------------------------------------
# Contradiction detection helpers
# ---------------------------------------------------------------------------

# Word-pairs whose co-occurrence in semantically similar texts signals a
# contradiction.  Each tuple is (negation_words, affirmation_words).
_NEGATION_PAIRS: list[tuple[frozenset[str], frozenset[str]]] = [
    # Chinese
    (
        frozenset({"不", "无", "没有", "不支持", "不能", "不可", "未"}),
        frozenset({"支持", "可以", "能", "有", "已"}),
    ),
    # English
    (
        frozenset({"not", "no", "cannot", "doesn't", "does not", "unsupported", "never"}),
        frozenset({"support", "supports", "can", "does", "yes", "always"}),
    ),
]

_EMBED_SIM_THRESHOLD = 0.85   # cosine similarity when embeddings are present
_TOKEN_SIM_THRESHOLD = 0.50   # Jaccard overlap when embeddings are absent


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class OrphanItem:
    item_id: str
    content_preview: str
    access_count: int
    stage: str


@dataclass
class Contradiction:
    item_a_id: str
    item_b_id: str
    preview_a: str
    preview_b: str
    similarity: float


@dataclass
class DistillOpportunity:
    item_id: str
    content_preview: str
    access_count: int
    stage: str


@dataclass
class ConsolidationHint:
    """Two items that are semantically similar enough to merge."""
    item_a_id: str
    item_b_id: str
    similarity: float


@dataclass
class LintReport:
    scope: str
    orphans: list[OrphanItem] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    distill_opportunities: list[DistillOpportunity] = field(default_factory=list)
    consolidation_hints: list[ConsolidationHint] = field(default_factory=list)
    health_score: int = 100
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_healthy(self) -> bool:
        return (
            not self.orphans
            and not self.contradictions
            and not self.distill_opportunities
        )

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "health_score": self.health_score,
            "orphans": len(self.orphans),
            "contradictions": len(self.contradictions),
            "distill_opportunities": len(self.distill_opportunities),
            "consolidation_hints": len(self.consolidation_hints),
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _jaccard(text_a: str, text_b: str) -> float:
    ta = set(text_a.lower().split())
    tb = set(text_b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _similarity(a: ContextItem, b: ContextItem) -> tuple[float, float]:
    """Return (similarity, threshold) using embeddings when available."""
    if a.embedding and b.embedding:
        return _cosine(a.embedding, b.embedding), _EMBED_SIM_THRESHOLD
    return _jaccard(a.content_text, b.content_text), _TOKEN_SIM_THRESHOLD


def _has_contradiction_signal(text_a: str, text_b: str) -> bool:
    """True when one text negates something the other affirms."""
    la, lb = text_a.lower(), text_b.lower()
    for neg_words, pos_words in _NEGATION_PAIRS:
        if (
            any(w in la for w in neg_words) and any(w in lb for w in pos_words)
        ) or (
            any(w in la for w in pos_words) and any(w in lb for w in neg_words)
        ):
            return True
    return False


def _skill_referenced_ids(items: list[ContextItem]) -> set[str]:
    """IDs explicitly referenced in skill provenance / links."""
    refs: set[str] = set()
    for it in items:
        if it.stage != Stage.skill:
            continue
        if it.provenance and it.provenance.source_id:
            refs.add(it.provenance.source_id)
        for lnk in it.links or []:
            refs.add(lnk.target_id)
    return refs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CONSOLIDATION_SIM_THRESHOLD = 0.88  # high similarity → merge candidate


def run_lint(
    items: list[ContextItem],
    scope: str,
    *,
    quick: bool = False,
    heuristic_rule: HeuristicDistillRule | None = None,
    orphan_max: int = 20,
    contradiction_max: int = 10,
    distill_max: int = 10,
    consolidation_max: int = 10,
) -> LintReport:
    """Run all lint checks on a list of ContextItems.

    All checks are purely in-memory — no storage reads beyond the provided
    ``items`` list.

    Args:
        items: All items in the scope (deleted items are filtered internally).
        scope: Scope label for the report header.
        quick: When True, skip O(n²) pairwise checks (contradictions and
            consolidation hints). Used by ``contextseek overview`` for speed;
            use ``contextseek lint`` for the full report.
        heuristic_rule: Thresholds for distillation opportunity detection.
        orphan_max: Report at most this many orphan items.
        contradiction_max: Report at most this many contradictions.
        distill_max: Report at most this many distillation opportunities.
        consolidation_max: Report at most this many consolidation hints (for dream).

    Returns:
        LintReport with all findings and a health score 0–100.
    """
    rule = heuristic_rule or HeuristicDistillRule()
    report = LintReport(scope=scope)

    active = [it for it in items if not it.is_deleted and it.searchable]
    skill_refs = _skill_referenced_ids(active)

    # ── Check 1: Orphans ─────────────────────────────────────────────────────
    for it in active:
        if len(report.orphans) >= orphan_max:
            break
        if it.stage == Stage.skill:
            continue
        if it.access_count == 0 and it.id not in skill_refs:
            report.orphans.append(
                OrphanItem(
                    item_id=it.id,
                    content_preview=it.content_text[:80],
                    access_count=0,
                    stage=it.stage.value,
                )
            )

    # ── Check 2: Contradictions (pairwise — skip in quick mode) ───────────────
    if not quick:
        candidates = [
            it for it in active if it.stage in (Stage.knowledge, Stage.extracted)
        ]
        found = 0
        for i, a in enumerate(candidates):
            if found >= contradiction_max:
                break
            for b in candidates[i + 1 :]:
                if found >= contradiction_max:
                    break
                sim, threshold = _similarity(a, b)
                if sim >= threshold and _has_contradiction_signal(
                    a.content_text, b.content_text
                ):
                    report.contradictions.append(
                        Contradiction(
                            item_a_id=a.id,
                            item_b_id=b.id,
                            preview_a=a.content_text[:80],
                            preview_b=b.content_text[:80],
                            similarity=round(sim, 3),
                        )
                    )
                    found += 1

    # ── Check 3: Distillation opportunities ──────────────────────────────────
    now = datetime.now(timezone.utc)
    for it in active:
        if len(report.distill_opportunities) >= distill_max:
            break
        if it.stage == Stage.skill:
            continue
        if not isinstance(it.content, str):
            continue
        if it.access_count < rule.min_access_count:
            continue
        if it.relevance_boost < rule.min_relevance_boost:
            continue
        age_days = (now - it.created_at).total_seconds() / 86400.0
        if age_days < rule.min_age_days:
            continue
        report.distill_opportunities.append(
            DistillOpportunity(
                item_id=it.id,
                content_preview=it.content_text[:80],
                access_count=it.access_count,
                stage=it.stage.value,
            )
        )

    # ── Check 4: Consolidation hints (pairwise — skip in quick mode) ────────
    if not quick:
        knowledge = [it for it in active if it.stage == Stage.knowledge]
        found_hints = 0
        seen_pairs: set[frozenset[str]] = set()
        for i, a in enumerate(knowledge):
            if found_hints >= consolidation_max:
                break
            for b in knowledge[i + 1 :]:
                if found_hints >= consolidation_max:
                    break
                pair = frozenset({a.id, b.id})
                if pair in seen_pairs:
                    continue
                sim, _ = _similarity(a, b)
                if sim >= _CONSOLIDATION_SIM_THRESHOLD:
                    report.consolidation_hints.append(
                        ConsolidationHint(
                            item_a_id=a.id,
                            item_b_id=b.id,
                            similarity=round(sim, 3),
                        )
                    )
                    seen_pairs.add(pair)
                    found_hints += 1

    # ── Health score ─────────────────────────────────────────────────────────
    total = max(len(active), 1)
    score = 100
    score -= min(30, len(report.orphans) * 50 // total)
    score -= min(40, len(report.contradictions) * 5)
    score -= min(20, len(report.distill_opportunities) * 2)
    report.health_score = max(0, score)

    return report


__all__ = [
    "LintReport",
    "OrphanItem",
    "Contradiction",
    "DistillOpportunity",
    "ConsolidationHint",
    "run_lint",
]
