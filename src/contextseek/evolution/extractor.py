"""Trace extraction — converts raw trace items to extracted insights.

Migrated from trace/pipeline.py and adapted for ContextItem.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

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
            return []

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

    def extract(self, item: ContextItem) -> list[ContextItem]:
        if not isinstance(item.content, dict):
            return []

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


def _get_geo(content: Any, geo_field: str) -> dict | None:
    """Read a geo dict (``{"lat", "lon", ...}``) from ``content[geo_field]``."""
    if not isinstance(content, dict):
        return None
    geo = content.get(geo_field)
    if not isinstance(geo, dict):
        return None
    if geo.get("lat") is None or geo.get("lon") is None:
        return None
    return geo


class GeoExtractor:
    """Extractor that promotes a raw item's geo field into structured ``content["geo"]``.

    Raw trace items often carry coordinates under an ad-hoc key (e.g.
    ``content["destination_geo"]``) that the GIS backend does not index. This
    extractor reads that field and emits ``stage=extracted`` item(s) whose
    coordinates live under the canonical ``content["geo"]`` key, so that
    :meth:`GeoMetadata.from_content` and ``GeoQuery`` retrieval work without any
    regex / string parsing.

    Two modes:

    * **Structured mode** (``label`` provided): emit exactly one extracted item
      whose content is a small structured dict
      (``{"label", "location_type", "geo", ...}``). Use this for location
      memories where the textual slices are irrelevant. This is what the swap
      station commute demo uses::

          GeoExtractor(geo_field="destination_geo", geo_type="frequent_location",
                       label="workday_destination", location_type="workplace",
                       extra_tags=["commute_destination"])

    * **Decorator mode** (no ``label``): delegate text extraction to ``inner``
      (default :class:`HeuristicExtractor`) and attach ``content["geo"]`` to every
      produced item, falling back to pure ``inner`` behaviour when the geo field
      is absent::

          GeoExtractor(geo_field="origin_geo", geo_type="pickup_point",
                       inner=LLMExtractor(summarize_fn=fn),
                       extra_tags=["pickup_location"])
    """

    def __init__(
        self,
        geo_field: str,
        *,
        geo_type: str,
        label: str | None = None,
        location_type: str | None = None,
        extra_tags: list[str] | None = None,
        skip_keys: set[str] | None = None,
        confidence: float = 0.6,
        inner: Extractor | None = None,
    ):
        self._geo_field = geo_field
        self._geo_type = geo_type
        self._label = label
        self._location_type = location_type
        self._extra_tags = list(extra_tags or [])
        self._skip_keys = frozenset(skip_keys or ())
        self._confidence = confidence
        self._inner = inner or HeuristicExtractor()

    def extract(self, item: ContextItem) -> list[ContextItem]:
        geo_data = _get_geo(item.content, self._geo_field)

        if self._label is not None:
            # Structured mode: one tidy location memory per raw item.
            if geo_data is None:
                return []
            return [self._make_structured(item, geo_data)]

        # Decorator mode: enrich inner extractor output with geo.
        base_items = self._inner.extract(item)
        if geo_data is None:
            return base_items  # degrade to pure inner behaviour
        geo = {**geo_data, "geo_type": self._geo_type}
        for it in base_items:
            if isinstance(it.content, dict):
                it.content["geo"] = geo
            else:
                it.content = {"text": it.content, "geo": geo}
            it.tags = [*it.tags, "geo_extracted", *self._extra_tags]
        return base_items

    # Framework-level keys written by the extractor itself. Always skipped
    # during raw-content passthrough so they cannot be overwritten by raw data.
    _FRAMEWORK_SKIP_KEYS: frozenset[str] = frozenset(
        {"geo", "label", "location_type", "geo_type"}
    )

    def _make_structured(self, source: ContextItem, geo_data: dict) -> ContextItem:
        content: dict[str, Any] = {
            "label": self._label,
            "geo": {**geo_data, "geo_type": self._geo_type},
        }
        if self._location_type is not None:
            content["location_type"] = self._location_type
        # Pass through remaining business fields from raw.content
        # (input/output/dwell_hours/...) to preserve user intent. Only
        # attempted when raw.content is a dict; structured fields take
        # precedence on key conflicts.
        if isinstance(source.content, dict):
            for key, value in source.content.items():
                if key in self._FRAMEWORK_SKIP_KEYS:
                    continue
                if key == self._geo_field:
                    continue
                if key in self._skip_keys:
                    continue
                if key in content:
                    continue
                content[key] = value
        return ContextItem(
            id=_generate_id(),
            content=content,
            scope=source.scope,
            provenance=Provenance(
                source_type=SourceType.trace_extraction,
                source_id=source.id,
                confidence=self._confidence,
                context=f"Geo-extracted from trace {source.id}",
            ),
            stage=Stage.extracted,
            stability=Stability.transient,
            tags=["geo_extracted", "auto_extracted", *self._extra_tags],
            links=[Link(target_id=source.id, relation=LinkType.derived_from)],
            created_at=_utc_now(),
        )
