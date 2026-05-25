"""Inference rules — auto-infer stage/stability/confidence from content and source."""

from __future__ import annotations

from typing import Any, Callable

from contextseek.domain.provenance import Provenance, SourceType, get_source_confidence
from contextseek.domain.stages import STAGE_DEFAULT_STABILITY, Stability, Stage


def infer_stage(source_type: str, content: str | dict[str, Any]) -> Stage:
    """Infer the initial Stage from source_type and content structure."""
    # Human input and documents are trusted → knowledge directly
    if source_type in (SourceType.human_input, SourceType.document):
        return Stage.knowledge

    # Distillation results are skills
    if source_type == SourceType.distillation:
        return Stage.skill

    # Merge results are knowledge
    if source_type == SourceType.merge_result:
        return Stage.knowledge

    # Agent inference produces extracted-level content
    if source_type == SourceType.agent_inference:
        return Stage.extracted

    # Trace extraction and external API start as raw
    # (unless content has structured trace shape)
    if isinstance(content, dict) and _is_trace_structure(content):
        return Stage.raw

    if source_type == SourceType.trace_extraction:
        return Stage.raw

    if source_type == SourceType.external_api:
        return Stage.raw

    return Stage.raw


def infer_stage_with_classifier(
    source_type: str,
    content: str | dict[str, Any],
    *,
    classify_fn: Callable[[str, str | dict[str, Any], Stage], Stage | None]
    | None = None,
) -> Stage:
    """Infer stage with optional LLM/classifier override.

    The fallback deterministic inference remains the source of truth when
    classifier output is missing or invalid.
    """
    base = infer_stage(source_type, content)
    if classify_fn is None:
        return base
    try:
        overridden = classify_fn(source_type, content, base)
    except Exception:
        return base
    if isinstance(overridden, Stage):
        return overridden
    return base


def infer_stability(stage: Stage, source_type: str) -> Stability:
    """Infer Stability from stage and source_type."""
    # Explicit overrides
    if source_type == SourceType.human_input:
        return Stability.stable
    if source_type == SourceType.document:
        return Stability.stable

    # Default from stage
    return STAGE_DEFAULT_STABILITY.get(stage, Stability.transient)


def infer_confidence(source_type: str) -> float:
    """Infer confidence from source_type."""
    return get_source_confidence(source_type)


def build_provenance(
    source: str,
    source_type: str,
    confidence: float | None = None,
    created_by: str | None = None,
    context: str | None = None,
    verified: bool = False,
) -> Provenance:
    """Construct a Provenance from minimal user input."""
    return Provenance(
        source_type=source_type,
        source_id=source,
        confidence=confidence
        if confidence is not None
        else infer_confidence(source_type),
        verified=verified,
        created_by=created_by,
        context=context,
    )


def _is_trace_structure(content: dict[str, Any]) -> bool:
    """Check if content dict looks like a trace (has input/output/tool_calls)."""
    trace_keys = {"input", "output", "tool_calls"}
    return len(trace_keys & set(content.keys())) >= 2
