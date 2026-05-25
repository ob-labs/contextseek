"""Provenance model — every ContextItem must declare its source."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceType(str, Enum):
    """Core source types built into the framework.

    Domain-specific types (GIS, IoT, etc.) should be registered via
    ``register_source_type()`` rather than added here.
    """

    human_input = "human_input"
    """Direct user entry or annotation."""

    document = "document"
    """Imported from documents or knowledge bases."""

    trace_extraction = "trace_extraction"
    """Distilled from execution traces."""

    agent_inference = "agent_inference"
    """Produced by agent reasoning."""

    distillation = "distillation"
    """Distilled from large corpora."""

    external_api = "external_api"
    """Returned by external systems or tools."""

    merge_result = "merge_result"
    """Produced by merging multiple items."""

    dream_consolidation = "dream_consolidation"
    """Emitted by consolidation dreaming."""

    dream_divergence = "dream_divergence"
    """Emitted by divergence dreaming."""


# Core source_type → default confidence (domain extensions use the registry below)
SOURCE_TYPE_CONFIDENCE: dict[SourceType, float] = {
    SourceType.human_input: 1.0,
    SourceType.document: 0.8,
    SourceType.trace_extraction: 0.5,
    SourceType.agent_inference: 0.6,
    SourceType.distillation: 0.7,
    SourceType.external_api: 0.5,
    SourceType.merge_result: 0.7,
    SourceType.dream_consolidation: 0.4,
    SourceType.dream_divergence: 0.3,
}


class SourceTypeRegistry:
    """Registry for domain-specific source types outside the core enum.

    Modules register their types at import time::

        from contextseek.domain.provenance import register_source_type
        register_source_type("sensor_fusion", confidence=0.88)

    Registered types are accepted everywhere a ``source_type: str`` is expected.
    Attempting to shadow a core ``SourceType`` value raises ``ValueError``.
    """

    def __init__(self) -> None:
        self._confidence: dict[str, float] = {}

    def register(self, name: str, *, confidence: float) -> None:
        """Register a new source type with its default confidence.

        Raises:
            ValueError: If *name* collides with a core ``SourceType`` value.
        """
        if name in SourceType._value2member_map_:
            raise ValueError(
                f"{name!r} is a reserved core SourceType and cannot be re-registered. "
                "Add it to the SourceType enum instead."
            )
        self._confidence[name] = confidence

    def get_confidence(self, name: str) -> float | None:
        """Return the registered confidence for *name*, or ``None`` if unknown."""
        return self._confidence.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._confidence


_registry = SourceTypeRegistry()


def register_source_type(name: str, *, confidence: float) -> None:
    """Register a domain-specific source type and its default confidence.

    Call this at module import time from any domain extension package::

        register_source_type("iot_telemetry", confidence=0.92)

    Args:
        name: Unique string identifier for the source type.
        confidence: Default confidence in ``[0.0, 1.0]``.
    """
    _registry.register(name, confidence=confidence)


def get_source_confidence(source_type: str) -> float:
    """Look up the default confidence for any source type string.

    Checks core ``SourceType`` enum first, then the extension registry.
    Returns ``0.5`` for unknown types.
    """
    try:
        return SOURCE_TYPE_CONFIDENCE[SourceType(source_type)]
    except ValueError:
        pass
    conf = _registry.get_confidence(source_type)
    return conf if conf is not None else 0.5


@dataclass(frozen=True)
class Provenance:
    """Source chain — answers where this record came from and why it is trustworthy.

    Provenance is required on every ``ContextItem``. Records without provenance
    must not enter ContextSeek.

    ``source_type`` accepts both core ``SourceType`` enum members and plain
    strings registered via ``register_source_type()``.
    """

    source_type: str
    """Origin channel — a ``SourceType`` value or a registered extension string."""

    source_id: str

    def __post_init__(self) -> None:
        # Normalize SourceType enum members to their plain string value so that
        # Provenance.source_type is always a plain str regardless of what was passed.
        if isinstance(self.source_type, SourceType):
            object.__setattr__(self, "source_type", self.source_type.value)

    """Origin identifier (document URL / trace id / user id / tool name)."""

    confidence: float = 1.0
    """Confidence score (0.0–1.0)."""

    verified: bool = False
    """Whether a human or external verifier confirmed the record."""

    created_by: str | None = None
    """Creator (user / system / agent id)."""

    context: str | None = None
    """Human-readable source context (e.g. extracted from a failed deploy trace)."""
