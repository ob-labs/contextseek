"""Skill distillation — identifies high-frequency success patterns and produces skills.

Knowledge items with procedure-like content that are repeatedly used successfully
get promoted to stage=skill as a structured prompt skill (skill_type="prompt"),
compatible with Hermes, SuperAGI, and any Markdown-injection agent pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage, Stability


@dataclass
class HeuristicDistillRule:
    """Thresholds for LLM-free skill distillation from plain text items.

    Applied to items at any stage (raw/extracted/knowledge) when no LLM
    is configured.  Produces skills with confidence=0.75 to signal they
    are pending LLM review.
    """

    min_access_count: int = 5
    min_age_days: float = 3.0
    min_relevance_boost: float = 1.1


# Keywords that signal procedure-like content in tags or extracted text.
_PROCEDURE_KEYWORDS = frozenset(
    {"procedure", "executable", "step", "steps", "workflow", "guide", "how-to"}
)


def _format_as_markdown(item: ContextItem) -> str:
    """Convert a knowledge item's content into a structured Markdown skill body."""
    content = item.content

    # Already has a "body" key — use directly.
    if isinstance(content, dict) and "body" in content:
        body = content["body"]
        if isinstance(body, str):
            return body

    # Structured dict without body — render key/value pairs as sections.
    if isinstance(content, dict):
        parts = []
        for key, val in content.items():
            if key in ("name", "description", "skill_type", "version", "tags"):
                continue
            parts.append(f"## {key.replace('_', ' ').title()}\n\n{val}")
        if parts:
            return "\n\n".join(parts)
        return str(content)

    # Plain text — wrap in a minimal Markdown template.
    text = str(content).strip()
    return (
        f"## Overview\n\n{text}\n\n"
        f"## Usage\n\n"
        f"Follow the procedure described above step by step.\n"
        f"Verify the result at each stage before proceeding."
    )


def _infer_name(item: ContextItem, fallback_id: str) -> str:
    if isinstance(item.content, dict):
        return item.content.get("name", f"skill_{fallback_id}")
    return f"skill_{fallback_id}"


def _infer_description(item: ContextItem) -> str:
    if isinstance(item.content, dict):
        return item.content.get("description", item.content_text[:200])
    return item.content_text[:200]


def _procedure_tags(item: ContextItem) -> list[str]:
    """Collect procedure-related tags from source item."""
    return [t for t in item.tags if t in _PROCEDURE_KEYWORDS]


class SkillDistiller:
    """Identifies knowledge items eligible for skill distillation.

    Criteria:
    - stage == knowledge
    - content is procedure-like (tags contain a procedure keyword, or content is a dict with "body")
    - access_count >= min_use_count
    - relevance_boost indicates positive feedback history

    Produces prompt skills (skill_type="prompt") whose body is a Markdown document,
    compatible with Hermes SKILL.md conventions and any prompt-injection agent pattern.
    """

    def __init__(
        self,
        *,
        min_use_count: int = 10,
        min_relevance_boost: float = 1.2,
        llm_decide_fn: Callable[[ContextItem], bool] | None = None,
        llm_distill_fn: Callable[[ContextItem], dict[str, str]] | None = None,
        heuristic_rule: HeuristicDistillRule | None = None,
    ):
        self._min_use = min_use_count
        self._min_boost = min_relevance_boost
        self._llm_decide = llm_decide_fn
        self._llm_distill = llm_distill_fn
        self._heuristic_rule = heuristic_rule

    def identify_candidates(self, items: list[ContextItem]) -> list[ContextItem]:
        """Find knowledge items eligible for skill distillation."""
        candidates = [
            it
            for it in items
            if it.stage == Stage.knowledge
            and not it.is_deleted
            and it.searchable
            and self._is_procedure(it)
            and it.access_count >= self._min_use
            and it.relevance_boost >= self._min_boost
        ]
        if self._llm_decide is None:
            return candidates
        decided: list[ContextItem] = []
        for item in candidates:
            try:
                if self._llm_decide(item):
                    decided.append(item)
            except Exception:
                decided.append(item)
        return decided

    def distill(self, item: ContextItem) -> ContextItem:
        """Produce a prompt skill item from a knowledge item.

        The produced ContextItem has stage=skill and content structured as:
            {
                "skill_type": "prompt",
                "name":        str,
                "description": str,
                "version":     "1.0.0",
                "tags":        list[str],
                "body":        str,   # Markdown instruction document
            }

        The original knowledge item is NOT modified here; the caller is responsible
        for recording a distilled_into link on it.
        """
        skill_id = _generate_id()
        skill_id_short = skill_id[:8]

        name = _infer_name(item, skill_id_short)
        description = _infer_description(item)
        body = _format_as_markdown(item)
        if self._llm_distill is not None:
            try:
                llm_payload = self._llm_distill(item)
                if llm_payload.get("name"):
                    name = llm_payload["name"][:120]
                if llm_payload.get("description"):
                    description = llm_payload["description"][:400]
                if llm_payload.get("body"):
                    body = llm_payload["body"]
            except Exception:
                pass

        # Preserve procedure-related source tags; drop internal bookkeeping tags.
        _skip = {
            "auto_extracted",
            "llm_summary",
            "near_duplicate",
            "has_contradiction",
            "needs_review",
            "needs_reverification",
            "evolution_candidate",
        }
        inherited_tags = [t for t in item.tags if t not in _skip]

        skill_content = {
            "skill_type": "prompt",
            "name": name,
            "description": description,
            "version": "1.0.0",
            "tags": inherited_tags,
            "body": body,
        }

        return ContextItem(
            id=skill_id,
            content=skill_content,
            scope=item.scope,
            provenance=Provenance(
                source_type=SourceType.distillation,
                source_id=item.id,
                confidence=0.8,
                context=f"Distilled from knowledge item (used {item.access_count} times)",
            ),
            stage=Stage.skill,
            stability=Stability.permanent,
            tags=["prompt_skill", "auto_distilled"] + inherited_tags,
            links=[Link(target_id=item.id, relation=LinkType.distilled_into)],
            created_at=_utc_now(),
            importance=item.importance,
        )

    def identify_heuristic_candidates(
        self, items: list[ContextItem]
    ) -> list[ContextItem]:
        """Find items eligible for heuristic (LLM-free) skill distillation.

        Operates on items at any stage: raw, extracted, or knowledge.
        Plain text items that have been accessed enough times and have
        existed long enough are promoted to skills without LLM assistance.
        """
        if self._heuristic_rule is None:
            return []
        rule = self._heuristic_rule
        now = datetime.now(timezone.utc)
        candidates: list[ContextItem] = []
        for item in items:
            if item.is_deleted or not item.searchable:
                continue
            if item.stage == Stage.skill:
                continue
            if not isinstance(item.content, str):
                continue
            if item.access_count < rule.min_access_count:
                continue
            if item.relevance_boost < rule.min_relevance_boost:
                continue
            age_days = (now - item.created_at).total_seconds() / 86400.0
            if age_days < rule.min_age_days:
                continue
            candidates.append(item)
        return candidates

    def distill_heuristic(self, item: ContextItem) -> ContextItem:
        """Produce a skill item from a plain text item without LLM assistance.

        The skill body is the first 300 characters of the source content.
        Confidence is set to 0.75 to flag it as pending LLM review.
        """
        skill_id = _generate_id()
        text = item.content_text.strip()
        body = text[:300]
        name = f"skill_{skill_id[:8]}"
        description = text[:120]

        inherited_tags = [
            t
            for t in item.tags
            if t
            not in {
                "auto_extracted",
                "text_extracted",
                "llm_summary",
                "near_duplicate",
                "has_contradiction",
                "needs_review",
                "evolution_candidate",
            }
        ]

        skill_content = {
            "skill_type": "prompt",
            "name": name,
            "description": description,
            "version": "1.0.0",
            "tags": inherited_tags,
            "body": f"## Overview\n\n{body}",
        }

        return ContextItem(
            id=skill_id,
            content=skill_content,
            scope=item.scope,
            provenance=Provenance(
                source_type=SourceType.distillation,
                source_id=item.id,
                confidence=0.75,
                context=(
                    f"Heuristic distillation (accessed {item.access_count} times, "
                    "no LLM configured)"
                ),
            ),
            stage=Stage.skill,
            stability=Stability.permanent,
            tags=["prompt_skill", "heuristic_skill"] + inherited_tags,
            links=[Link(target_id=item.id, relation=LinkType.distilled_into)],
            created_at=_utc_now(),
            importance=item.importance,
        )

    def _is_procedure(self, item: ContextItem) -> bool:
        """Check if item has procedure-like content."""
        # Tag-based: any procedure keyword in tags
        if any(t in _PROCEDURE_KEYWORDS for t in item.tags):
            return True
        # Structure-based: dict with a "body" key
        if isinstance(item.content, dict) and "body" in item.content:
            return True
        return False
