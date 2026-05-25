"""Public exports for ContextSeek SDK."""

from contextseek._version import __version__

from contextseek.client.contextseek import ContextSeek
from contextseek.config.settings import ContextSeekSettings
from contextseek.domain.context_item import ContextItem
from contextseek.domain.geo import GeoMetadata, GeoPoint, GeoQuery
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.results import (
    CompactReport,
    EvolutionReport,
    ResponseMeta,
    RetrieveResponse,
    SearchHit,
)
from contextseek.domain.stages import Stage, Stability
from contextseek.domain.tools import ToolSpec, default_tool_specs
from contextseek.scope import (
    ScopeBuilder,
    ScopeLintWarning,
    ScopeStats,
    ScopeTemplates,
    ScopeTree,
)

__all__ = [
    "__version__",
    "ContextItem",
    "CompactReport",
    "EvolutionReport",
    "GeoMetadata",
    "GeoPoint",
    "GeoQuery",
    "Link",
    "LinkType",
    "Provenance",
    "ResponseMeta",
    "RetrieveResponse",
    "ScopeBuilder",
    "ScopeLintWarning",
    "ScopeStats",
    "ScopeTemplates",
    "ScopeTree",
    "SearchHit",
    "ContextSeek",
    "ContextSeekSettings",
    "SourceType",
    "Stage",
    "Stability",
    "ToolSpec",
    "default_tool_specs",
]
