"""Trace extraction — converts raw trace items to extracted insights.

Migrated from trace/pipeline.py and adapted for ContextItem.
"""

from __future__ import annotations

from typing import Callable, Protocol

from contextseek.domain.context_item import ContextItem, _generate_id, _utc_now
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage, Stability


class Extractor(Protocol):
    """Protocol for trace-to-insight extraction."""

    def extract(self, item: ContextItem) -> list[ContextItem]:
        """Extract insights from a raw ContextItem."""


class HeuristicExtractor:
    """Deterministic extractor that slices trace content into insights.

    Handles ContextItems whose content is a dict with trace structure
    (input, output, tool_calls, etc.).
    """

    def __init__(self, mode: str = "full"):
        """Args:
        mode: "full" (all slices), "summary" (first 3 tool_calls), "" (input/output only)
        """
        self._mode = mode

    def extract(self, item: ContextItem) -> list[ContextItem]:
        if not isinstance(item.content, dict):
            return self._extract_text(item)

        content = item.content
        results: list[ContextItem] = []

        # Extract input insight
        if content.get("input"):
            results.append(
                self._make_insight(item, f"Task: {content['input']}", "trace_input")
            )

        # Extract tool call insights
        tool_calls = content.get("tool_calls", [])
        limit = 3 if self._mode == "summary" else len(tool_calls)
        if self._mode != "":
            for i, tc in enumerate(tool_calls[:limit]):
                tool_name = (
                    tc.get("tool", "unknown") if isinstance(tc, dict) else str(tc)
                )
                result = tc.get("result", "") if isinstance(tc, dict) else ""
                text = f"Tool '{tool_name}' → {result}"
                results.append(self._make_insight(item, text, f"trace_tool_{i}"))

        # Extract output insight
        if content.get("output"):
            results.append(
                self._make_insight(item, f"Result: {content['output']}", "trace_output")
            )

        # Extract feedback insight
        if content.get("feedback"):
            results.append(
                self._make_insight(
                    item, f"Feedback: {content['feedback']}", "trace_feedback"
                )
            )

        return results

    def _extract_text(self, item: ContextItem) -> list[ContextItem]:
        """Extract a plain-text item into a single extracted insight.

        Uses the first 200 characters as the insight body when no LLM is
        available, giving the convergence merger something to cluster on.
        """
        text = item.content_text.strip()
        if not text:
            return []
        summary = text[:200]
        return [self._make_insight(item, summary, "text_extracted")]

    def _make_insight(self, source: ContextItem, text: str, tag: str) -> ContextItem:
        return ContextItem(
            id=_generate_id(),
            content=text,
            scope=source.scope,
            provenance=Provenance(
                source_type=SourceType.trace_extraction,
                source_id=source.id,
                confidence=0.6,
                context=f"Extracted from trace {source.id}",
            ),
            stage=Stage.extracted,
            stability=Stability.transient,
            tags=[tag, "auto_extracted"],
            links=[Link(target_id=source.id, relation=LinkType.derived_from)],
            created_at=_utc_now(),
        )


class LLMExtractor:
    """LLM-powered extractor that summarizes a trace into a single insight.

    Falls back to HeuristicExtractor on failure.
    """

    def __init__(self, summarize_fn: Callable[[str], str]):
        self._summarize = summarize_fn
        self._fallback = HeuristicExtractor(mode="summary")

    def _extract_text(self, item: ContextItem) -> list[ContextItem]:
        """Summarize a plain-text item into a single extracted insight.

        Falls back to the heuristic text extractor when the LLM call fails so
        that text items still progress past ``raw`` when an LLM is configured.
        """
        text = item.content_text.strip()
        if not text:
            return []
        try:
            summary = self._summarize(text).strip()
        except Exception:
            return self._fallback.extract(item)
        if not summary:
            summary = text[:200]
        return [
            ContextItem(
                id=_generate_id(),
                content=summary,
                scope=item.scope,
                provenance=Provenance(
                    source_type=SourceType.trace_extraction,
                    source_id=item.id,
                    confidence=0.7,
                    context="LLM-summarized text insight",
                ),
                stage=Stage.extracted,
                stability=Stability.transient,
                tags=["llm_summary", "text_extracted", "auto_extracted"],
                links=[Link(target_id=item.id, relation=LinkType.derived_from)],
                created_at=_utc_now(),
            )
        ]

    def extract(self, item: ContextItem) -> list[ContextItem]:
        if not isinstance(item.content, dict):
            return self._extract_text(item)

        content = item.content
        # Build text for LLM
        parts = []
        if content.get("input"):
            parts.append(f"Task: {content['input']}")
        for tc in content.get("tool_calls", []):
            if isinstance(tc, dict):
                parts.append(f"Tool: {tc.get('tool', '?')} → {tc.get('result', '')}")
        if content.get("output"):
            parts.append(f"Output: {content['output']}")
        if content.get("feedback"):
            parts.append(f"Feedback: {content['feedback']}")

        full_text = "\n".join(parts)
        try:
            summary = self._summarize(full_text)
        except Exception:
            return self._fallback.extract(item)

        return [
            ContextItem(
                id=_generate_id(),
                content=summary,
                scope=item.scope,
                provenance=Provenance(
                    source_type=SourceType.trace_extraction,
                    source_id=item.id,
                    confidence=0.7,
                    context="LLM-summarized trace insight",
                ),
                stage=Stage.extracted,
                stability=Stability.transient,
                tags=["llm_summary", "auto_extracted"],
                links=[Link(target_id=item.id, relation=LinkType.derived_from)],
                created_at=_utc_now(),
            )
        ]
