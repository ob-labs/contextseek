"""ContextSeek — the unified client for semantic context management.

This is the primary API surface for ContextSeek: one flat class with
factory helpers (``from_settings``, ``from_runtime_config``),
read/write primitives (``add``, ``retrieve``, ``expand``, ``forget``,
``delete``, ``plug``), evolution and provenance helpers (``upstream``,
``overview``, ``feedback``, ``compact``, ``items``), plus
``tools()`` for LLM tool registration and ``tag(...)`` to attach audit
metadata, and ``pin``.
"""

from __future__ import annotations

import json
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterator
from uuid import uuid4

if TYPE_CHECKING:
    from contextseek.scope import ScopeStats, ScopeTree

from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.protocols.plugs import DataPlug, PlugMeta
from contextseek.domain.context_item import ContextItem
from contextseek.domain.conflicts import ConflictType
from contextseek.domain.inference import (
    build_provenance,
    infer_stage,
    infer_stage_with_classifier,
    infer_stability,
)
from contextseek.domain.links import Link, LinkType
from contextseek.domain.provenance import SourceType
from contextseek.domain.results import (
    CompactReport,
    EvolutionReport,
    ResponseMeta,
    RetrieveResponse,
    SearchHit,
)
from contextseek.domain.serialization import (
    deserialize_context_item,
    serialize_context_item,
)
from contextseek.domain.stages import Stage, Stability
from contextseek.domain.tools import EXPAND_HINT, ToolSpec, default_tool_specs
from contextseek.llm.prompts import (
    LLMPromptTemplates,
    conflict_judge_prompt,
    distill_candidate_prompt,
    distill_render_prompt,
    feedback_tag_prompt,
    merge_synthesis_prompt,
    retrieval_relevance_prompt,
    stage_classifier_prompt,
)
from contextseek.llm.client import invoke_json, invoke_text
from contextseek.llm.parsers import extract_json_object
from contextseek.routing.resolver import ScopeResolver

# Lazy imports to avoid hard circular dependencies at module level.
# These are imported inside methods that need them.
# - contextseek.evolution.engine.EvolutionEngine
# - contextseek.observability.audit.AuditLog

# ---------------------------------------------------------------------------
# Context variable for audit metadata (request-scoped)
# ---------------------------------------------------------------------------
_AUDIT_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "contextseek_audit_ctx", default={}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Module-level state: emit no-summarizer warning at most once per process
# ---------------------------------------------------------------------------
_NO_SUMMARIZER_WARNED = False


def _warn_no_summarizer_once() -> None:
    global _NO_SUMMARIZER_WARNED
    if _NO_SUMMARIZER_WARNED:
        return
    _NO_SUMMARIZER_WARNED = True
    warnings.warn(
        "SUMMARIZER_PROVIDER not configured; returning full L2 content. "
        "Configure SUMMARIZER_PROVIDER=llm + LLM_API_KEY for layered retrieval.",
        UserWarning,
        stacklevel=3,
    )


# ---------------------------------------------------------------------------
# Helper: default in-memory adapter factory
# ---------------------------------------------------------------------------


def _make_default_adapter() -> SeekVFSAdapter:
    """Create a default in-memory SeekVFSAdapter for local/test use."""
    from seekvfs import VFS

    from contextseek.storage.in_memory_backend import InMemoryBackend
    from contextseek.storage.storage_adapter import SeekVFSStorageAdapter

    vfs = VFS(
        routes={"contextseek://": {"backend": InMemoryBackend()}},
        scheme="contextseek://",
    )
    return SeekVFSStorageAdapter(vfs)


def _auto_build_summarizer() -> Any | None:
    """Try to build a Summarizer from global env/settings.

    Returns ``None`` when no LLM is configured, so the dataclass default
    behaviour (flat L2-only) is preserved.
    """
    from contextseek.config.factory import build_summarizer
    from contextseek.config.settings import SummarizerSettings

    return build_summarizer(SummarizerSettings())


def _build_adapter_from_settings(settings: Any) -> SeekVFSAdapter:
    """Build a VFS-backed storage adapter from ContextSeekSettings."""
    from seekvfs import VFS

    from contextseek.storage.file_backend import FileBackend
    from contextseek.storage.in_memory_backend import InMemoryBackend
    from contextseek.storage.storage_adapter import SeekVFSStorageAdapter

    storage = settings.storage
    scheme = storage.uri_scheme

    if storage.backend == "oceanbase":
        ob = settings.ob
        vector_dims = settings.embedding.dims
        if not vector_dims:
            raise ValueError(
                "EMBEDDING_DIMS must be set when STORAGE_BACKEND=oceanbase"
            )
        geo = getattr(settings, "geo", None)
        if geo is not None and getattr(geo, "enabled", False):
            from contextseek.storage.ob_geo_backend import OceanBaseGeoBackend

            backend: Any = OceanBaseGeoBackend(
                table_name=ob.table_name,
                vector_dims=vector_dims,
                host=ob.host,
                port=ob.port,
                user=ob.user,
                password=ob.password,
                db_name=ob.db_name,
                geo_table_name=geo.geo_table_name,
                distance_decay_km=geo.distance_decay_km,
                route_sample_interval_km=geo.route_sample_interval_km,
            )
        else:
            from contextseek.storage.ob_backend import OceanBaseBackend

            backend = OceanBaseBackend(
                table_name=ob.table_name,
                vector_dims=vector_dims,
                host=ob.host,
                port=ob.port,
                user=ob.user,
                password=ob.password,
                db_name=ob.db_name,
            )
        backend.initialize()
    elif storage.backend == "file":
        backend = FileBackend(root_dir=storage.path)
        backend.initialize()
    else:
        backend = InMemoryBackend()

    vfs = VFS(routes={scheme: {"backend": backend}}, scheme=scheme)
    adapter = SeekVFSStorageAdapter(vfs)

    # Tiered storage (hot + cold)
    if storage.cold_backend:
        from contextseek.storage.tiered_adapter import TieredSeekVFSAdapter

        if storage.cold_backend == "file":
            cold_backend = FileBackend(root_dir=storage.cold_path)
            cold_backend.initialize()
        else:
            cold_backend = InMemoryBackend()

        cold_vfs = VFS(routes={scheme: {"backend": cold_backend}}, scheme=scheme)
        cold_adapter = SeekVFSStorageAdapter(cold_vfs)
        return TieredSeekVFSAdapter(hot=adapter, cold=cold_adapter)

    return adapter


# ---------------------------------------------------------------------------
# ContextSeek dataclass
# ---------------------------------------------------------------------------


@dataclass
class ContextSeek:
    """Unified ContextSeek client — all operations on ContextItems.

    The ContextSeek client wraps a storage adapter and exposes a clean API
    for adding, retrieving, evolving, and managing context items. It is
    designed for both embedded (in-process) and service (HTTP/MCP) usage.

    Example::

        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        item = ctx.add(
            "Deployment on staging failed due to OOM",
            scope="acme/bot/ops",
            source="incident_123",
        )
        response = ctx.retrieve(
            "why did staging fail?",
            scope="acme/bot/ops",
        )
        for hit in response:
            print(hit.item.summary)  # L1 by default
    """

    # ═══════════════════════════════════════════════════════════════════════
    # Constructor arguments
    # ═══════════════════════════════════════════════════════════════════════

    adapter: SeekVFSAdapter | None = None
    """Storage backend. Defaults to in-memory for local/test use."""

    resolver: ScopeResolver = field(default_factory=ScopeResolver)
    """URI resolver for scopes and refs."""

    embedder: Callable[[str], list[float]] | None = None
    """Optional embedding function: text -> vector."""

    summarizer: Any | None = None
    """Optional Summarizer: generates L0 abstract + L1 overview before embedding."""

    llm: Any | None = None
    """Optional shared LLM for advanced ranking/evolution/classification hooks."""

    llm_prompts: LLMPromptTemplates = field(default_factory=LLMPromptTemplates)
    """Prompt templates used by all LLM-assisted flows."""

    evolution_engine: Any | None = None
    """Optional EvolutionEngine for compact/evolve operations."""

    audit_log: Any | None = None
    """Optional AuditLog for request-level auditing."""

    strategy: Any | None = None
    """Optional StrategyConfig for policy settings."""

    skill_executor: Any | None = None
    """Reserved for future skill execution integration."""

    # ═══════════════════════════════════════════════════════════════════════
    # Internal state
    # ═══════════════════════════════════════════════════════════════════════

    _plugs: list[DataPlug] = field(default_factory=list, repr=False)
    """Registered data plugs."""

    _strategy_version: str = field(default="v1", repr=False)
    """Active strategy version label."""

    _llm_rerank_enabled: bool = field(default=False, repr=False)
    _llm_rerank_top_n: int = field(default=20, repr=False)
    _llm_merge_enabled: bool = field(default=False, repr=False)
    _llm_conflict_check_enabled: bool = field(default=False, repr=False)
    _llm_stage_infer_enabled: bool = field(default=False, repr=False)
    _llm_distill_enabled: bool = field(default=False, repr=False)
    _llm_feedback_enabled: bool = field(default=False, repr=False)
    _dream_llm_enabled: bool = field(default=False, repr=False)
    _scope_lint: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.adapter is None:
            self.adapter = _make_default_adapter()
        if self.summarizer is None:
            self.summarizer = _auto_build_summarizer()

    # ═══════════════════════════════════════════════════════════════════════
    # Core — writes and retrieval
    # ═══════════════════════════════════════════════════════════════════════

    def plug(self, source: DataPlug, *, scope: str | None = None) -> None:
        """Register and consume a DataPlug.

        The plug's ``stream()`` iterator is consumed immediately, adding
        each RawEvent as a ContextItem. Scope is taken from the explicit
        ``scope`` argument first, then ``event.metadata["scope"]``, then the
        plug name as a local fallback. ``event.source`` remains provenance.

        Args:
            source: A DataPlug implementation to register.
            scope: Optional destination scope for all events from the plug.
        """
        self._plugs.append(source)
        meta: PlugMeta = source.metadata()

        source_type: str = meta.source_type or "external_api"

        for event in source.stream():
            event_scope = scope or str(event.metadata.get("scope") or meta.name)
            source_id = event.source or meta.name

            # Allow importers/plugs to override stage/stability (e.g. skill import)
            event_stage: Stage | None = None
            if "stage" in event.metadata:
                try:
                    event_stage = Stage(event.metadata["stage"])
                except ValueError:
                    pass
            event_stability: Stability | None = None
            if "stability" in event.metadata:
                try:
                    event_stability = Stability(event.metadata["stability"])
                except ValueError:
                    pass

            item = self.add(
                content=event.content,
                scope=event_scope,
                source=source_id,
                source_type=source_type,
                tags=event.tags,
                stage=event_stage,
                stability=event_stability,
            )
            if "embedding" in event.metadata and self.embedder is None:
                item.embedding = event.metadata["embedding"]
            if "importance" in event.metadata:
                item.importance = float(event.metadata["importance"])
            if "summary" in event.metadata:
                item.summary = str(event.metadata["summary"])
            if any(
                key in event.metadata for key in ("embedding", "importance", "summary")
            ):
                self._write_item(item)

    def add(
        self,
        content: str | dict[str, Any],
        *,
        scope: str,
        source: str,
        source_type: str = SourceType.human_input,
        tags: list[str] | None = None,
        confidence: float | None = None,
        stage: Stage | None = None,
        stability: Stability | None = None,
        links: list[Link] | None = None,
        check_conflicts: bool = True,
    ) -> ContextItem:
        """Add a new ContextItem to the store.

        This is the primary write path. It handles provenance construction,
        stage/stability inference, optional embedding, conflict detection,
        and persistence.

        Args:
            content: Text or structured content payload.
            scope: Scope string (e.g. "acme/bot/user_123").
            source: Source identifier (URL, user ID, trace ID, etc.).
            source_type: How the data entered the system.
            tags: Optional tags for retrieval filtering.
            confidence: Override confidence (0.0-1.0). Inferred if None.
            stage: Override stage. Inferred from source_type if None.
            stability: Override stability. Inferred from stage if None.
            links: Optional links to other items.
            check_conflicts: Whether to perform conflict detection. Exact
                duplicates are always rejected; near-duplicates and
                contradictions are recorded in the item's tags.

        Returns:
            The created ContextItem with its assigned ID and ref.

        Raises:
            ValueError: If an exact duplicate already exists in the scope.
        """
        # Normalize SourceType enum members to plain strings early so all
        # downstream code (security checks, provenance, LLM prompts) sees str.
        if isinstance(source_type, SourceType):
            source_type = source_type.value

        if self._scope_lint:
            from contextseek.scope import ScopeLintWarning, _lint_scope

            for msg in _lint_scope(scope):
                warnings.warn(msg, ScopeLintWarning, stacklevel=2)

        if self.strategy is not None:
            from contextseek.security.policy import apply_write_policy, source_allowed

            source_payload = {
                "source": source,
                "source_type": source_type,
                "scope": scope,
            }
            if not source_allowed(source_payload, strategy=self.strategy.write):
                msg = f"source not allowed: {source}"
                raise ValueError(msg)
            content = apply_write_policy(content, strategy=self.strategy.write)

        # Build provenance
        provenance = build_provenance(source, source_type, confidence)

        # Infer stage if not provided
        resolved_stage = stage
        if resolved_stage is None:
            if self._llm_stage_infer_enabled and self.llm is not None:
                resolved_stage = infer_stage_with_classifier(
                    source_type,
                    content,
                    classify_fn=self._classify_stage_with_llm,
                )
            else:
                resolved_stage = infer_stage(source_type, content)

        # Infer stability if not provided
        resolved_stability = (
            stability
            if stability is not None
            else infer_stability(resolved_stage, source_type)
        )

        # Create the item
        item = ContextItem(
            content=content,
            scope=scope,
            provenance=provenance,
            tags=tags or [],
            stage=resolved_stage,
            stability=resolved_stability,
            links=links or [],
        )

        # Step 1 (early): generate L0 abstract + L1 summary so the embedder has
        # a tight surface to work with. Step 2 (early): embed the abstract /
        # content. Both run before conflict detection so we can use the
        # embedding to ANN-narrow the candidate set instead of full-scanning.
        if self.summarizer is not None:
            item.abstract = self.summarizer.abstract(item.content_text)
            item.summary = self.summarizer.summary(item.content_text)

        if self.embedder is not None:
            embed_source = item.abstract or item.content_text
            item.embedding = self.embedder(embed_source)

        # Write-time conflict detection
        if check_conflicts:
            from contextseek.domain.conflicts import ConflictType, detect_conflicts

            # Fast-path: exact-duplicate via backend hash index.
            # Match the legacy detect_conflicts behaviour, which skips items
            # whose ``is_deleted`` is True — re-adding the same content into a
            # scope where the prior copy was soft-deleted must succeed.
            existing_ref = None
            find_by_hash = getattr(self.adapter, "find_by_hash", None)
            if find_by_hash is not None:
                try:
                    existing_ref = find_by_hash(
                        self.resolver.prefix_for(scope), item.hash
                    )
                except Exception:  # noqa: BLE001  # backend errors must not block writes
                    existing_ref = None
            if existing_ref:
                existing_item = self._read_item(existing_ref)
                if existing_item is not None and not existing_item.is_deleted:
                    # Policy: exact duplicate → return the existing item
                    # idempotently instead of failing the call.  The write is
                    # skipped; no new row is created.  Callers can detect the
                    # dedup by comparing the returned item.id with the id they
                    # would have expected for a fresh write.
                    return existing_item
                # Soft-deleted (or unreadable) → fall through to candidate-based
                # detection so a revival writes a fresh row instead of failing.

            # Narrow candidate set for near-duplicate / contradiction checks.
            # When an embedding is available, use ANN recall (top-K) instead of
            # listing the entire scope; falls back to a full scan only when the
            # adapter cannot search by embedding.
            candidates: list[ContextItem] = self._candidates_for_conflict_check(
                item, scope
            )
            result = detect_conflicts(
                item,
                candidates,
                llm_judge=(
                    self._llm_conflict_judge
                    if self._llm_conflict_check_enabled and self.llm is not None
                    else None
                ),
            )

            if result.has_duplicates:
                dup = next(
                    c
                    for c in result.conflicts
                    if c.conflict_type == ConflictType.duplicate
                )
                # Policy: exact duplicate → return the existing item
                # idempotently.  Prefer the structured conflict result over
                # the hard-coded ValueError so callers and policies can
                # inspect the result rather than being forced to catch.
                dup_ref = self.resolver.ref_for(scope, dup.existing_item_id)
                dup_item = self._read_item(dup_ref)
                if dup_item is not None:
                    return dup_item
                # Existing item unreadable (race / corruption) — fall through
                # and write the new item so the content is not silently lost.

            # Tag near-duplicates and contradictions for visibility
            if result.has_conflicts:
                if any(
                    c.conflict_type == ConflictType.contradiction
                    for c in result.conflicts
                ):
                    item.tags.append("has_contradiction")
                if any(
                    c.conflict_type == ConflictType.near_duplicate
                    for c in result.conflicts
                ):
                    item.tags.append("near_duplicate")
                # Auto-add refuted_by links for contradictions
                for c in result.conflicts:
                    if c.conflict_type == ConflictType.contradiction:
                        item.links.append(
                            Link(
                                target_id=c.existing_item_id,
                                relation=LinkType.refuted_by,
                                strength=c.similarity,
                            )
                        )

        # Serialize and persist
        payload = serialize_context_item(item)
        ref = self.resolver.ref_for(scope, item.id)
        self.adapter.write(ref, payload)

        # Audit
        self._emit_audit(
            action="add",
            scope=scope,
            detail={"ref": ref, "item_id": item.id, "stage": resolved_stage.value},
        )

        return item

    def retrieve(
        self,
        query: str,
        *,
        scope: str,
        k: int = 10,
        full: bool = False,
        stage: Stage | None = None,
        tags: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        include_deleted: bool = False,
        geo_query: "Any | None" = None,
    ) -> RetrieveResponse:
        """Search stored context; defaults to L1 summaries, ``full=True`` returns L2 bodies.

        Without a summarizer the API degrades to L2-only (empty summary fields and
        ``layer`` naturally ``"full"``), and ``warnings.warn`` is emitted once to
        prompt configuration.

        Args:
            query: Natural-language query.
            scope: Search scope (prefix).
            k: Maximum hits to return.
            full: When True, ``hit.item.content`` carries the L2 body; when False
                (default) L1 summaries replace content to save tokens—call
                :meth:`expand` to upgrade to full text.
            stage: Optional stage filter.
            tags: Optional tag filter (all tags must match).
            filters: Compatibility bag; may include ``stage`` / ``tags`` / ``min_confidence``.
            include_deleted: Whether soft-deleted items are visible.

        Returns:
            :class:`RetrieveResponse` iterable as ``for hit in response``.
        """
        stage_filter = stage
        tag_filter = tags
        min_conf = None
        if filters:
            if filters.get("stage"):
                stage_filter = Stage(filters["stage"])
            if not tag_filter:
                tag_filter = filters.get("tags")
            min_conf = filters.get("min_confidence")

        prefix = self.resolver.prefix_for(scope)
        strategy = self._retrieval_strategy()

        from contextseek.retrieval.orchestrator import RetrievalOrchestrator
        from contextseek.retrieval.components import LLMReranker

        reranker = None
        if self._llm_rerank_enabled and self.llm is not None:
            reranker = LLMReranker(
                score_fn=self._score_relevance_with_llm,
                top_n=max(1, self._llm_rerank_top_n),
            )
        orchestrator = RetrievalOrchestrator(
            self.adapter,
            strategy=strategy,
            embedder=self.embedder,
            reranker=reranker,
        )
        hits = orchestrator.search(
            prefixes=[prefix],
            query=query,
            k=k,
            stage=stage_filter,
            tags=tag_filter,
            include_deleted=include_deleted,
            geo_query=geo_query,
        )
        hits = self._filter_readable_hits(hits, scope=scope)
        if min_conf is not None:
            hits = [h for h in hits if h.item.provenance.confidence >= min_conf]
        hits = hits[:k]

        # Touch hits (access stats) on the raw items before swapping content.
        for h in hits:
            h.item.touch()
            self._write_item(h.item)

        # full=False: clear content and expose only the L1 summary;
        # when a hit lacks a summary (non-tiered mode), keep the item and mark layer "full".
        if not full:
            shaped: list[SearchHit] = []
            for h in hits:
                if h.item.summary:
                    shaped.append(
                        replace(
                            h,
                            item=replace(h.item, content=None),
                            layer="summary",
                        )
                    )
                else:
                    shaped.append(replace(h, layer="full"))
            hits = shaped
        else:
            hits = [replace(h, layer="full") for h in hits]

        # When summarizer is None, warn once (no summaries on hits, so skip per-hit layer checks).
        if self.summarizer is None and not full:
            _warn_no_summarizer_once()

        # Response-level layer: "summary" only if every hit is summary; downgrade when any hit is full.
        response_layer = (
            "full"
            if full or not hits or any(h.layer == "full" for h in hits)
            else "summary"
        )
        meta = ResponseMeta(
            layer=response_layer,
            full_via="expand",
            hint=EXPAND_HINT if response_layer == "summary" else "",
        )

        self._emit_audit(
            action="retrieve",
            scope=scope,
            detail={
                "query": query,
                "k": k,
                "full": full,
                "hits": len(hits),
                "layer": response_layer,
            },
        )

        return RetrieveResponse(items=hits, meta=meta)

    def expand(self, hits: list[SearchHit]) -> list[ContextItem]:
        """Upgrade a list of ``SearchHit`` rows to L2 full text.

        Storage paths are derived from ``hit.item.scope`` + ``hit.item.id``—no
        extra scope argument is required. Typical usage::

            response = ctx.retrieve("query", scope="acme/bot")
            interesting = [h for h in response if h.score > 0.7]
            full_items = ctx.expand(interesting)

        Args:
            hits: ``SearchHit`` objects from :meth:`retrieve` (may be a subset).

        Returns:
            ``ContextItem`` list with L2 ``content`` filled; skips unreadable rows.
        """
        adapter = self.adapter
        if not hasattr(adapter, "read"):
            return []

        result: list[ContextItem] = []
        audit_scopes: set[str] = set()
        for hit in hits:
            scope = hit.item.scope
            ref = self.resolver.ref_for(scope, hit.item.id)
            payload = adapter.read(ref)
            if payload is None:
                continue
            try:
                result.append(deserialize_context_item(payload))
            except (KeyError, TypeError, ValueError):
                continue
            audit_scopes.add(scope)

        self._emit_audit(
            action="expand",
            scope=";".join(sorted(audit_scopes)),
            detail={"requested": len(hits), "returned": len(result)},
        )
        return result

    def expand_by_ids(self, ids: list[str], scope: str) -> list[ContextItem]:
        """Upgrade bare item ids to L2 full text.

        For callers that cannot pass :class:`SearchHit` instances (MCP / HTTP
        bridges, etc.). Behavior matches :meth:`expand` aside from argument shape.

        Args:
            ids:   ``ContextItem`` id list.
            scope: Scope that owns the ids (used to build storage paths).

        Returns:
            ``ContextItem`` list with L2 ``content`` filled; skips missing ids.
        """
        adapter = self.adapter
        if not hasattr(adapter, "read"):
            return []

        result: list[ContextItem] = []
        for item_id in ids:
            ref = self.resolver.ref_for(scope, item_id)
            payload = adapter.read(ref)
            if payload is None:
                continue
            try:
                result.append(deserialize_context_item(payload))
            except (KeyError, TypeError, ValueError):
                continue

        self._emit_audit(
            action="expand",
            scope=scope,
            detail={"requested": len(ids), "returned": len(result)},
        )
        return result

    def tools(self) -> list[ToolSpec]:
        """Return tool specs exposed to the LLM.

        Includes ``retrieve`` and ``expand`` :class:`ToolSpec` entries; serialize
        with ``.to_openai()`` / ``.to_anthropic()`` for each vendor.
        """
        return default_tool_specs()

    def forget(
        self, ref: str, *, scope: str, reason: str, propagate: bool = True
    ) -> None:
        """Soft-delete a ContextItem by reference.

        The item is not physically removed — instead it is marked as
        deleted with a timestamp and reason, and excluded from future
        searches.

        When ``propagate=True`` (default), downstream items that depend on
        this item via evidence links will have their effective_confidence
        recomputed. Items below the reverification threshold get tagged
        with ``"needs_reverification"``.

        Args:
            ref: Full URI reference of the item.
            scope: Scope the item belongs to.
            reason: Human-readable reason for deletion.
            propagate: Whether to propagate invalidation to dependents.

        Raises:
            ValueError: If the item does not exist or is already deleted.
        """
        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        item = deserialize_context_item(payload)

        if item.is_deleted:
            msg = f"item already deleted: {ref}"
            raise ValueError(msg)

        # Soft delete
        item.soft_delete(reason)

        # Serialize and write back
        updated_payload = serialize_context_item(item)
        self.adapter.write(ref, updated_payload)

        # Invalidation propagation
        invalidation_result = None
        if propagate:
            invalidation_result = self._propagate_invalidation(item, scope)

        detail: dict[str, Any] = {"ref": ref, "reason": reason, "item_id": item.id}
        if invalidation_result is not None:
            detail["propagation"] = {
                "degraded_count": len(invalidation_result.degraded_items),
                "reverification_count": len(invalidation_result.reverification_needed),
                "depth": invalidation_result.propagation_depth,
            }

        self._emit_audit(action="forget", scope=scope, detail=detail)

    def delete(
        self, ref: str, *, scope: str, reason: str, propagate: bool = True
    ) -> None:
        """Permanently remove a ContextItem from backing storage.

        Unlike :meth:`forget`, the payload is removed via the adapter's
        ``delete`` method instead of being rewritten as a tombstone. When
        ``propagate=True``,
        dependent items are updated using the same invalidation pass as
        :meth:`forget`, **before** the object is removed.

        Args:
            ref: Full URI reference of the item.
            scope: Scope the item belongs to.
            reason: Human-readable reason for removal.
            propagate: Whether to propagate invalidation to dependents.

        Raises:
            ValueError: If no item exists at ``ref``, or the adapter could not delete it.
        """
        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        item = deserialize_context_item(payload)

        invalidation_result = None
        if propagate:
            invalidation_result = self._propagate_invalidation(item, scope)

        removed = self.adapter.delete(ref)
        if not removed:
            msg = f"delete failed for ref: {ref}"
            raise ValueError(msg)

        detail: dict[str, Any] = {"ref": ref, "reason": reason, "item_id": item.id}
        if invalidation_result is not None:
            detail["propagation"] = {
                "degraded_count": len(invalidation_result.degraded_items),
                "reverification_count": len(invalidation_result.reverification_needed),
                "depth": invalidation_result.propagation_depth,
            }

        self._emit_audit(action="delete", scope=scope, detail=detail)

    # ═══════════════════════════════════════════════════════════════════════
    # Provenance and evolution
    # ═══════════════════════════════════════════════════════════════════════

    def upstream(self, ref: str, *, scope: str) -> list[ContextItem]:
        """Walk ``derived_from`` and ``supported_by`` links to related upstream items.

        Starts at the item for ``ref``, then breadth-first expands along those
        two link types within ``scope``. Handy for "where did this come from?"
        without running the full ``evidence_chain`` analysis.

        Args:
            ref: Full URI reference of the starting item.
            scope: Scope the items belong to.

        Returns:
            ContextItems visited (starting with the queried item).

        Raises:
            ValueError: If the starting item does not exist.
        """
        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        start_item = deserialize_context_item(payload)
        chain: list[ContextItem] = [start_item]
        visited: set[str] = {start_item.id}

        # BFS through derivation links
        queue: list[ContextItem] = [start_item]
        traceable_relations = {LinkType.derived_from, LinkType.supported_by}

        while queue:
            current = queue.pop(0)
            for link in current.links:
                if link.relation not in traceable_relations:
                    continue
                if link.target_id in visited:
                    continue
                visited.add(link.target_id)

                # Resolve the target
                target_ref = self.resolver.ref_for(scope, link.target_id)
                target_payload = self.adapter.read(target_ref)
                if target_payload is None:
                    continue

                try:
                    target_item = deserialize_context_item(target_payload)
                except (KeyError, TypeError, ValueError):
                    continue

                chain.append(target_item)
                queue.append(target_item)

        self._emit_audit(
            action="upstream",
            scope=scope,
            detail={"ref": ref, "chain_length": len(chain)},
        )

        return chain

    def evidence_chain(
        self,
        ref: str,
        *,
        scope: str,
        max_depth: int = 10,
    ):
        """Compute the full evidence chain DAG for an item.

        Returns an EvidenceChain with propagated confidence, critical path,
        conflict reports, and broken link detection.

        Args:
            ref: Full URI reference of the item.
            scope: Scope the item belongs to.
            max_depth: Maximum traversal depth.

        Returns:
            EvidenceChain DAG structure.

        Raises:
            ValueError: If the starting item does not exist.
        """
        from contextseek.domain.evidence_chain import (
            compute_evidence_chain,
        )

        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        root_item = deserialize_context_item(payload)

        def _resolver(item_id: str) -> ContextItem | None:
            target_ref = self.resolver.ref_for(scope, item_id)
            return self._read_item(target_ref)

        reverification_threshold = 0.4
        if self.strategy:
            reverification_threshold = self.strategy.evolution.reverification_threshold

        result = compute_evidence_chain(
            root_item,
            _resolver,
            max_depth=max_depth,
            reverification_threshold=reverification_threshold,
        )

        self._emit_audit(
            action="evidence_chain",
            scope=scope,
            detail={
                "ref": ref,
                "nodes": len(result.nodes),
                "conflicts": len(result.conflicts),
                "overall_confidence": result.overall_confidence,
            },
        )

        return result

    def chain_confidence(self, ref: str, *, scope: str) -> float:
        """Quick propagated confidence lookup for an item.

        Lighter than evidence_chain() when only the confidence value is
        needed without the full DAG structure.

        Args:
            ref: Full URI reference of the item.
            scope: Scope the item belongs to.

        Returns:
            Effective confidence (0.0–1.0).

        Raises:
            ValueError: If the item does not exist.
        """
        from contextseek.domain.evidence_chain import compute_chain_confidence

        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        item = deserialize_context_item(payload)

        def _resolver(item_id: str) -> ContextItem | None:
            target_ref = self.resolver.ref_for(scope, item_id)
            return self._read_item(target_ref)

        return compute_chain_confidence(item, _resolver, max_depth=10)

    def overview(self, *, scope: str) -> EvolutionReport:
        """Read-only summary of items in a scope: stages and evolution-style counts.

        Scans all items and categorises them by stage, identifying
        candidates for extraction, convergence, and distillation.

        Args:
            scope: Scope to analyse.

        Returns:
            EvolutionReport with stage distribution and candidate counts.
        """
        prefix = self.resolver.prefix_for(scope)
        refs = self.adapter.ls(prefix)

        total = 0
        stage_dist: dict[str, int] = {}
        pending_extraction = 0
        pending_convergence = 0
        distill_candidates = 0

        for ref in refs:
            payload = self.adapter.read(ref)
            if payload is None:
                continue
            try:
                item = deserialize_context_item(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if item.is_deleted:
                continue

            total += 1
            stage_key = item.stage.value
            stage_dist[stage_key] = stage_dist.get(stage_key, 0) + 1

            # Count candidates
            if item.stage == Stage.raw:
                # Raw items with structured content are extraction candidates
                if isinstance(item.content, dict):
                    pending_extraction += 1
            elif item.stage == Stage.extracted:
                # Extracted items that have supporting links are convergence candidates
                pending_convergence += 1
            elif item.stage == Stage.knowledge:
                # Knowledge items with high access count are distillation candidates
                if item.access_count >= 5:
                    distill_candidates += 1

        report = EvolutionReport(
            total_items=total,
            stage_distribution=stage_dist,
            pending_extraction=pending_extraction,
            pending_convergence=pending_convergence,
            distill_candidates=distill_candidates,
        )

        self._emit_audit(
            action="overview",
            scope=scope,
            detail={
                "total_items": total,
                "stages": stage_dist,
            },
        )

        return report

    def feedback(
        self,
        ref: str,
        *,
        scope: str,
        score: float,
        reason: str = "",
    ) -> None:
        """Apply relevance feedback to a ContextItem.

        Adjusts the item's ``relevance_boost`` based on the feedback
        score, making it rank higher or lower in future retrievals.

        Feedback also influences evolution priority:
        - Positive feedback increases access_count (accelerates distillation)
        - Negative feedback on raw/extracted items tags them for review
        - Items with high cumulative positive feedback are promoted
          to evolution candidates sooner.

        Args:
            ref: Full URI reference of the item.
            scope: Scope the item belongs to.
            score: Feedback score delta (-1.0 to 1.0).
                   Positive = more relevant, negative = less relevant.
            reason: Optional reason for the feedback.

        Raises:
            ValueError: If the item does not exist.
        """
        payload = self.adapter.read(ref)
        if payload is None:
            msg = f"item not found: {ref}"
            raise ValueError(msg)

        item = deserialize_context_item(payload)

        # Adjust relevance_boost (clamp to [0.1, 5.0])
        new_boost = max(0.1, min(5.0, item.relevance_boost + score))
        item.relevance_boost = new_boost
        item.updated_at = _utc_now()

        # Evolution priority signals
        if score > 0:
            # Positive feedback counts as "usage" — accelerates distillation
            item.access_count += 1
            item.last_accessed_at = _utc_now()
            # High cumulative boost → tag for promotion
            if new_boost >= 2.0 and "evolution_candidate" not in item.tags:
                item.tags.append("evolution_candidate")
        elif score < 0:
            # Negative feedback on low-stage items → needs review
            if item.stage in (Stage.raw, Stage.extracted):
                if "needs_review" not in item.tags:
                    item.tags.append("needs_review")
            # Strong negative → decay importance
            if score <= -0.5:
                item.importance = max(0.1, item.importance * 0.8)

        if self._llm_feedback_enabled and self.llm is not None and reason.strip():
            self._apply_llm_feedback_reason(item, reason)

        # Persist
        updated_payload = serialize_context_item(item)
        self.adapter.write(ref, updated_payload)

        self._emit_audit(
            action="feedback",
            scope=scope,
            detail={
                "ref": ref,
                "score": score,
                "reason": reason,
                "new_boost": new_boost,
                "access_count": item.access_count,
            },
        )

    def compact(
        self,
        *,
        scope: str,
        dry_run: bool = False,
    ) -> CompactReport:
        """Run evolution compaction on a scope.

        If an EvolutionEngine is configured, runs the full evolution
        pipeline (extract, merge, distil, archive). Otherwise performs
        a lightweight deduplication pass.

        Args:
            scope: Scope to compact.
            dry_run: If True, compute what would happen without writing.

        Returns:
            CompactReport with merged, archived, and evolved counts.
        """
        prefix = self.resolver.prefix_for(scope)
        refs = self.adapter.ls(prefix)

        # Read all items
        items: list[ContextItem] = []
        for ref in refs:
            payload = self.adapter.read(ref)
            if payload is None:
                continue
            try:
                item = deserialize_context_item(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if item.is_deleted:
                continue
            items.append(item)

        if not items:
            return CompactReport()

        # Use EvolutionEngine if available
        if self.evolution_engine is not None:
            new_items, archived_items, report = self.evolution_engine.evolve(items)

            if not dry_run:
                # Write new items
                for item in new_items:
                    if self.embedder is not None:
                        source = item.abstract or item.content_text
                        item.embedding = self.embedder(source)
                    new_payload = serialize_context_item(item)
                    new_ref = self.resolver.ref_for(scope, item.id)
                    self.adapter.write(new_ref, new_payload)

                # Update archived items
                for item in archived_items:
                    archived_payload = serialize_context_item(item)
                    archived_ref = self.resolver.ref_for(scope, item.id)
                    self.adapter.write(archived_ref, archived_payload)

            self._emit_audit(
                action="compact",
                scope=scope,
                detail={
                    "dry_run": dry_run,
                    "new_items": len(new_items),
                    "archived": len(archived_items),
                    "merged": report.merged_count,
                    "evolved": report.evolved_count,
                },
            )

            return report

        # Fallback: simple dedup by hash
        seen_hashes: dict[str, ContextItem] = {}
        duplicates: list[ContextItem] = []

        for item in items:
            if item.hash in seen_hashes:
                duplicates.append(item)
            else:
                seen_hashes[item.hash] = item

        if not dry_run:
            for dup in duplicates:
                dup.soft_delete("deduplicated_by_compact")
                dup_payload = serialize_context_item(dup)
                dup_ref = self.resolver.ref_for(scope, dup.id)
                self.adapter.write(dup_ref, dup_payload)

        report = CompactReport(
            merged_count=len(duplicates),
            archived_count=0,
            evolved_count=0,
            details={"dedup_hash_count": len(duplicates)},
        )

        self._emit_audit(
            action="compact",
            scope=scope,
            detail={"dry_run": dry_run, "deduped": len(duplicates)},
        )

        return report

    def items(
        self,
        *,
        scope: str,
        stage: Stage | None = None,
    ) -> list[ContextItem]:
        """List all items in a scope, sorted by ``created_at`` (oldest first).

        Optional ``stage`` filters to one maturity level. This is a full
        enumeration of the scope prefix (not query-ranked like ``retrieve``).

        Args:
            scope: Scope to list.
            stage: Optional stage filter.

        Returns:
            ContextItems sorted by ``created_at`` ascending.
        """
        prefix = self.resolver.prefix_for(scope)
        refs = self.adapter.ls(prefix)

        result: list[ContextItem] = []
        for ref in refs:
            payload = self.adapter.read(ref)
            if payload is None:
                continue
            try:
                item = deserialize_context_item(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if item.is_deleted:
                continue
            if stage is not None and item.stage != stage:
                continue
            result.append(item)

        # Sort by created_at ascending
        result.sort(key=lambda x: x.created_at)

        self._emit_audit(
            action="items",
            scope=scope,
            detail={"count": len(result), "stage": stage.value if stage else None},
        )

        return result

    def scope_tree(self, root: str | None = None) -> "ScopeTree":
        """Return a hierarchical view of all scopes under *root*.

        Args:
            root: Optional scope prefix to restrict the tree (e.g. ``"acme"``).
                  When ``None`` the entire store is included.

        Returns:
            A :class:`~contextseek.scope.ScopeTree` whose ``.print()`` renders
            an annotated directory tree with item/knowledge/skill counts.
        """
        from contextseek.scope import build_scope_tree

        prefix = self.resolver.prefix_for(root) if root else "contextseek://"
        refs = self.adapter.ls(prefix)

        scope_refs: dict[str, list[str]] = {}
        for ref in refs:
            try:
                scope, _ = self.resolver.parse_ref(ref)
            except ValueError:
                continue
            scope_refs.setdefault(scope, []).append(ref)

        scope_items: dict[str, list] = {}
        for scope in scope_refs:
            scope_items[scope] = [item for _, item in self._list_items(scope)]

        return build_scope_tree(scope_items, root)

    def scope_stats(self, scope: str) -> "ScopeStats":
        """Return aggregate statistics for a single scope.

        Args:
            scope: The scope to inspect (exact match, not a prefix).

        Returns:
            A :class:`~contextseek.scope.ScopeStats` with item count, stage
            distribution, average confidence, and last write time.
        """
        from contextseek.scope import ScopeStats

        items = [item for _, item in self._list_items(scope)]

        stage_dist: dict[str, int] = {}
        total_confidence = 0.0
        last_write: "datetime | None" = None

        for item in items:
            key = item.stage.value if hasattr(item.stage, "value") else str(item.stage)
            stage_dist[key] = stage_dist.get(key, 0) + 1
            total_confidence += item.provenance.confidence if item.provenance else 0.0
            created = item.created_at
            if last_write is None or (created is not None and created > last_write):
                last_write = created

        avg_confidence = total_confidence / len(items) if items else 0.0

        return ScopeStats(
            scope=scope,
            item_count=len(items),
            stage_distribution=stage_dist,
            avg_confidence=round(avg_confidence, 4),
            last_write=last_write,
        )

    def skills(
        self,
        scope: str,
        *,
        skill_type: str | None = None,
        query: str | None = None,
        k: int = 50,
    ) -> list[ContextItem]:
        """List or search skill-stage items in a scope.

        Args:
            scope: Scope to query.
            skill_type: Optional filter by skill_type ("prompt", "tool", "mcp").
            query: Optional semantic search query. When provided, uses retrieve()
                   instead of full enumeration.
            k: Maximum results (only used when query is provided).

        Returns:
            ContextItems with stage=skill.
        """
        if query:
            hits = self.retrieve(query, scope=scope, k=k, full=True)
            candidates = [h.item for h in hits if h.item.stage == Stage.skill]
        else:
            candidates = self.items(scope=scope, stage=Stage.skill)

        if skill_type is not None:
            candidates = [
                c
                for c in candidates
                if isinstance(c.content, dict)
                and c.content.get("skill_type") == skill_type
            ]

        return candidates

    def skill_tools(
        self,
        scope: str,
        *,
        fmt: str = "openai",
        query: str | None = None,
        k: int = 20,
    ) -> list[dict[str, Any]]:
        """Export tool/mcp skills as LLM tool definitions.

        Directly usable as the ``tools`` parameter in LLM API calls::

            tools = ctx.skill_tools("acme/bot", fmt="openai")
            client.chat.completions.create(..., tools=tools)

        Args:
            scope: Scope to query.
            fmt: Export format — "openai", "anthropic", or "mcp".
            query: Optional semantic search query.
            k: Maximum skill candidates.

        Returns:
            List of tool definition dicts in the requested format.
        """
        from contextseek.domain.skill_executor import SkillExporter

        exporter = SkillExporter()
        skill_items = self.skills(scope, query=query, k=k)
        tool_items = [
            s
            for s in skill_items
            if isinstance(s.content, dict)
            and s.content.get("skill_type") in ("tool", "mcp")
        ]

        if fmt == "openai":
            return exporter.batch_to_openai(tool_items)
        if fmt == "anthropic":
            return exporter.batch_to_anthropic(tool_items)
        if fmt == "mcp":
            return [exporter.to_mcp_tool(s) for s in tool_items]
        msg = f"unknown format: {fmt!r}. Use 'openai', 'anthropic', or 'mcp'."
        raise ValueError(msg)

    def skill_context(
        self,
        scope: str,
        *,
        query: str | None = None,
        k: int = 5,
    ) -> str:
        """Render prompt skills as a Hermes-style system prompt block.

        Inject into your LLM system prompt::

            system = base_prompt + ctx.skill_context("acme/bot", query=task)

        Args:
            scope: Scope to query.
            query: Optional semantic search query to select relevant skills.
            k: Maximum prompt skills to include.

        Returns:
            Markdown string wrapped in ``<available_skills>`` tags.
        """
        from contextseek.domain.skill_executor import SkillExporter

        exporter = SkillExporter()
        prompt_items = self.skills(scope, skill_type="prompt", query=query, k=k)
        return exporter.to_system_prompt(prompt_items)

    def dream(
        self,
        *,
        scope: str,
        dry_run: bool = False,
    ):
        """Trigger a dream cycle (consolidation + divergence) on a scope.

        Dream items are low-confidence extracted items that decay quickly
        unless reinforced by agent feedback.

        Args:
            scope: Scope to dream over.
            dry_run: If True, compute dream report without persisting items.

        Returns:
            DreamReport with generated items and statistics.
        """
        from contextseek.config.strategies import DreamStrategy
        from contextseek.evolution.dreaming import DreamEngine

        dream_strategy = DreamStrategy()
        if self.strategy:
            dream_strategy = self.strategy.dream

        engine = DreamEngine(
            strategy=dream_strategy,
            embedder=self.embedder,
            llm=self._dream_llm_call
            if self._dream_llm_enabled and self.llm is not None
            else None,
            prompt_templates=self.llm_prompts,
        )

        items = [item for _, item in self._list_items(scope)]
        report = engine.dream(items)

        if not dry_run and report.total_dream_items > 0:
            all_dream_items = list(report.consolidation.items)
            if report.divergence:
                all_dream_items.extend(report.divergence.items)

            for item in all_dream_items:
                if self.embedder is not None:
                    source = item.abstract or item.content_text
                    item.embedding = self.embedder(source)
                self._write_item(item)

        self._emit_audit(
            action="dream",
            scope=scope,
            detail={
                "dry_run": dry_run,
                "consolidation_items": len(report.consolidation.items),
                "divergence_items": len(report.divergence.items)
                if report.divergence
                else 0,
                "total": report.total_dream_items,
            },
        )

        return report

    # ═══════════════════════════════════════════════════════════════════════
    # Prompt assembly and audit
    # ═══════════════════════════════════════════════════════════════════════

    @contextmanager
    def tag(
        self,
        *,
        actor: dict[str, Any] | None = None,
        request: dict[str, Any] | None = None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Iterator[None]:
        """Attach audit metadata for every audited operation in this ``with`` block.

        When ``audit_log`` is configured, :meth:`_emit_audit` merges this metadata
        into each record. Use it to answer *who did what* for a request or tool run.

        Example::

            with ctx.tag(actor={"user": "alice"}, reason="debug"):
                ctx.add(...)
                ctx.retrieve(...)

        Args:
            actor: Actor identity dict.
            request: Request metadata dict.
            source: Source identifier string.
            reason: Reason for the operations.
        """
        context_payload = {
            "actor": dict(actor or {}),
            "request": dict(request or {}),
            "source": source,
            "reason": reason,
        }
        token = _AUDIT_CONTEXT.set(context_payload)
        try:
            yield
        finally:
            _AUDIT_CONTEXT.reset(token)

    def pin(self, version: str) -> "ContextSeek":
        """Return a copy with a different strategy-version label (audit ``policy_version``).

        The returned instance shares the same adapter, audit log, and
        ``strategy`` object; only the internal version string changes. That
        string is written as ``policy_version`` on each audit record—useful for
        canary / A/B labeling. It does not swap retrieval thresholds by itself.

        Args:
            version: Strategy version label (e.g. ``"v2"``, ``"canary"``).

        Returns:
            New ``ContextSeek`` with the given version label.
        """
        new_instance = replace(self, _strategy_version=version)
        return new_instance

    @classmethod
    def from_runtime_config(cls, path: str | None = None) -> "ContextSeek":
        """Factory: create a ContextSeek from a runtime config file.

        Loads adapter, resolver, embedder, evolution engine, and audit
        log settings from a JSON/YAML configuration file.

        Args:
            path: Path to config file. If None, uses defaults.

        Returns:
            Configured ContextSeek instance.
        """
        if path is None:
            return cls()

        from pathlib import Path as FilePath

        config_path = FilePath(path)
        if not config_path.exists():
            return cls()

        config = json.loads(config_path.read_text(encoding="utf-8"))

        # Build adapter
        adapter: SeekVFSAdapter | None = None
        adapter_config = config.get("adapter")
        if adapter_config:
            adapter_type = adapter_config.get("type", "in_memory")
            if adapter_type == "in_memory":
                adapter = _make_default_adapter()
            elif adapter_type == "file":
                from contextseek.storage.file_backend import FileBackend
                from contextseek.storage.storage_adapter import SeekVFSStorageAdapter
                from seekvfs import VFS

                backend = FileBackend(root=adapter_config.get("root", ".contextseek"))
                vfs = VFS(
                    routes={"contextseek://": {"backend": backend}},
                    scheme="contextseek://",
                )
                adapter = SeekVFSStorageAdapter(vfs)

        # Build resolver
        uri_scheme = config.get("uri_scheme", "contextseek://")
        resolver = ScopeResolver(uri_scheme=uri_scheme)

        # Build audit log
        audit_log = None
        audit_config = config.get("audit")
        if audit_config and audit_config.get("enabled", False):
            from contextseek.observability.audit import AuditLog

            audit_log = AuditLog(
                persist_path=audit_config.get("persist_path"),
                metrics_path=audit_config.get("metrics_path"),
            )

        # Build evolution engine
        evolution_engine = None
        if config.get("evolution", {}).get("enabled", False):
            from contextseek.evolution.engine import EvolutionEngine

            evolution_engine = EvolutionEngine()

        return cls(
            adapter=adapter,
            resolver=resolver,
            audit_log=audit_log,
            evolution_engine=evolution_engine,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any | None = None,
    ) -> "ContextSeek":
        """Factory: create a ContextSeek from pydantic-settings configuration.

        Loads adapter, resolver, embedder, evolution engine, and audit log
        from a ContextSeekSettings instance.  If no settings object is passed,
        one is created automatically (reading from environment variables and
        .env file).

        Args:
            settings: Optional ContextSeekSettings instance.  Created from
                environment if None.

        Returns:
            Fully configured ContextSeek instance.
        """
        from contextseek.config.settings import ContextSeekSettings
        from contextseek.config.settings import to_strategy_config
        from contextseek.config.factory import (
            build_embedder,
            build_llm,
            build_summarizer,
        )

        if settings is None:
            settings = ContextSeekSettings()
        strategy = to_strategy_config(settings)

        # 1. Build storage adapter
        adapter = _build_adapter_from_settings(settings)

        # 2. Build resolver
        resolver = ScopeResolver(uri_scheme=settings.storage.uri_scheme)

        # 3. Build embedder (None if provider="none")
        embedder = build_embedder(settings.embedding)

        # 3b. Build a shared LLM (None if provider="none") and reuse it for
        # the summarizer to avoid creating duplicate model instances.
        shared_llm = build_llm(settings.llm)
        prompt_cfg = getattr(settings, "prompts", None)
        if prompt_cfg is None:
            llm_prompts = LLMPromptTemplates()
        else:
            llm_prompts = LLMPromptTemplates(
                summarizer_abstract_template=prompt_cfg.summarizer_abstract_template,
                summarizer_summary_template=prompt_cfg.summarizer_summary_template,
                retrieval_relevance_template=prompt_cfg.retrieval_relevance_template,
                conflict_judge_template=prompt_cfg.conflict_judge_template,
                stage_classifier_template=prompt_cfg.stage_classifier_template,
                feedback_tag_template=prompt_cfg.feedback_tag_template,
                merge_synthesis_template=prompt_cfg.merge_synthesis_template,
                distill_candidate_template=prompt_cfg.distill_candidate_template,
                distill_render_template=prompt_cfg.distill_render_template,
                dream_consolidation_template=prompt_cfg.dream_consolidation_template,
                dream_divergence_template=prompt_cfg.dream_divergence_template,
            )
        summarizer = build_summarizer(
            settings.summarizer,
            llm=shared_llm,
            prompt_templates=llm_prompts,
        )

        llm_rerank_enabled = (
            shared_llm is not None and settings.retrieval.reranker_mode.lower() == "llm"
        )
        llm_rerank_top_n = max(1, int(settings.retrieval.llm_rerank_top_n))
        llm_merge_enabled = bool(
            shared_llm is not None and settings.evolution.llm_merge_enabled
        )
        llm_conflict_check_enabled = bool(
            shared_llm is not None and settings.evolution.llm_conflict_check_enabled
        )
        llm_stage_infer_enabled = bool(
            shared_llm is not None and settings.evolution.llm_stage_infer_enabled
        )
        llm_distill_enabled = bool(
            shared_llm is not None and settings.evolution.llm_distill_enabled
        )
        llm_feedback_enabled = bool(
            shared_llm is not None and settings.evolution.llm_feedback_enabled
        )
        dream_llm_enabled = bool(shared_llm is not None and settings.dream.llm_enabled)

        # 4. Build evolution engine
        evolution_engine = None
        if settings.evolution.enabled:
            from contextseek.evolution.engine import EvolutionEngine
            from contextseek.evolution.extractor import HeuristicExtractor, LLMExtractor

            extractor = HeuristicExtractor()
            if summarizer is not None:
                extractor = LLMExtractor(summarize_fn=summarizer.summary)

            evolution_engine = EvolutionEngine(
                extractor=extractor,
                strategy=strategy.evolution,
                merge_synthesize_fn=(
                    cls._static_merge_synthesis_prompt(shared_llm, llm_prompts)
                    if llm_merge_enabled
                    else None
                ),
                distill_decide_fn=(
                    cls._static_distill_candidate_prompt(shared_llm, llm_prompts)
                    if llm_distill_enabled
                    else None
                ),
                distill_render_fn=(
                    cls._static_distill_render_prompt(shared_llm, llm_prompts)
                    if llm_distill_enabled
                    else None
                ),
            )

        # 5. Build audit log
        audit_log = None
        if settings.observability.audit_enabled:
            from contextseek.observability.audit import AuditLog

            audit_log = AuditLog(
                persist_path=settings.observability.audit_path,
                metrics_path=(
                    settings.observability.metrics_path
                    if settings.observability.metrics_enabled
                    else None
                ),
            )

        return cls(
            adapter=adapter,
            resolver=resolver,
            embedder=embedder,
            summarizer=summarizer,
            llm=shared_llm,
            llm_prompts=llm_prompts,
            evolution_engine=evolution_engine,
            audit_log=audit_log,
            strategy=strategy,
            _strategy_version=strategy.version,
            _llm_rerank_enabled=llm_rerank_enabled,
            _llm_rerank_top_n=llm_rerank_top_n,
            _llm_merge_enabled=llm_merge_enabled,
            _llm_conflict_check_enabled=llm_conflict_check_enabled,
            _llm_stage_infer_enabled=llm_stage_infer_enabled,
            _llm_distill_enabled=llm_distill_enabled,
            _llm_feedback_enabled=llm_feedback_enabled,
            _dream_llm_enabled=dream_llm_enabled,
            _scope_lint=bool(settings.scope_lint),
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _invoke_llm_text(self, prompt: str) -> str:
        return invoke_text(self.llm, prompt)

    def _invoke_llm_json(self, prompt: str) -> dict[str, Any]:
        return invoke_json(self.llm, prompt)

    def _score_relevance_with_llm(self, query: str, content: str) -> float:
        payload = self._invoke_llm_json(
            retrieval_relevance_prompt(
                query=query,
                content=content,
                templates=self.llm_prompts,
            )
        )
        try:
            score = float(payload.get("score", 0.0))
        except Exception:
            score = 0.0
        return max(0.0, min(1.0, score))

    def _dream_llm_call(self, prompt: str) -> str:
        return self._invoke_llm_text(prompt)

    def _llm_conflict_judge(
        self, new_text: str, existing_text: str, overlap: float
    ) -> ConflictType | None:
        payload = self._invoke_llm_json(
            conflict_judge_prompt(
                new_text=new_text,
                existing_text=existing_text,
                overlap=overlap,
                templates=self.llm_prompts,
            )
        )
        label = str(payload.get("label", "")).strip().lower()
        if label == ConflictType.near_duplicate.value:
            return ConflictType.near_duplicate
        if label == ConflictType.contradiction.value:
            return ConflictType.contradiction
        return None

    def _classify_stage_with_llm(
        self,
        source_type: str,
        content: str | dict[str, Any],
        default_stage: Stage,
    ) -> Stage | None:
        content_text = str(content) if isinstance(content, dict) else content
        payload = self._invoke_llm_json(
            stage_classifier_prompt(
                source_type=source_type,
                default_stage=default_stage.value,
                content_text=content_text,
                templates=self.llm_prompts,
            )
        )
        try:
            return Stage(str(payload.get("stage", default_stage.value)))
        except Exception:
            return None

    def _apply_llm_feedback_reason(self, item: ContextItem, reason: str) -> None:
        payload = self._invoke_llm_json(
            feedback_tag_prompt(
                stage=item.stage.value,
                reason=reason,
                templates=self.llm_prompts,
            )
        )
        tag = str(payload.get("tag", "")).strip().lower()
        if tag and tag != "none" and tag not in item.tags:
            item.tags.append(tag)

    @staticmethod
    def _static_merge_synthesis_prompt(
        llm: Any,
        prompt_templates: LLMPromptTemplates,
    ) -> Callable[[list[str]], str]:
        def _call(cluster_texts: list[str]) -> str:
            prompt = merge_synthesis_prompt(
                cluster_texts=cluster_texts,
                templates=prompt_templates,
            )
            return invoke_text(llm, prompt)

        return _call

    @staticmethod
    def _static_distill_candidate_prompt(
        llm: Any,
        prompt_templates: LLMPromptTemplates,
    ) -> Callable[[ContextItem], bool]:
        def _call(item: ContextItem) -> bool:
            payload_prompt = distill_candidate_prompt(
                item=item,
                templates=prompt_templates,
            )
            raw = invoke_text(llm, payload_prompt)
            data = extract_json_object(raw)
            return bool(data.get("distill", True))

        return _call

    @staticmethod
    def _static_distill_render_prompt(
        llm: Any,
        prompt_templates: LLMPromptTemplates,
    ) -> Callable[[ContextItem], dict[str, str]]:
        def _call(item: ContextItem) -> dict[str, str]:
            prompt = distill_render_prompt(
                item=item,
                templates=prompt_templates,
            )
            raw = invoke_text(llm, prompt)
            data = extract_json_object(raw)
            if not isinstance(data, dict):
                return {}
            result: dict[str, str] = {}
            for key in ("name", "description", "body"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    result[key] = val.strip()
            return result

        return _call

    def _emit_audit(
        self,
        *,
        action: str,
        scope: str,
        detail: dict[str, Any],
        status: str = "ok",
    ) -> None:
        """Emit an audit record if audit_log is configured."""
        if self.audit_log is None:
            return

        from contextseek.observability.audit import AuditRecord

        ctx = _AUDIT_CONTEXT.get()
        record = AuditRecord(
            request_id=uuid4().hex,
            action=action,
            scope=scope,
            policy_version=self._strategy_version,
            status=status,
            detail=detail,
            actor=dict(ctx.get("actor", {})),
            request=dict(ctx.get("request", {})),
            source=ctx.get("source"),
            reason=ctx.get("reason"),
        )
        self.audit_log.append(record)

    def _retrieval_strategy(self):
        """Return the active retrieval strategy, falling back to defaults."""
        if self.strategy is not None:
            return self.strategy.retrieval

        from contextseek.config import RetrievalStrategy

        return RetrievalStrategy()

    def _filter_readable_hits(
        self, hits: list[SearchHit], *, scope: str
    ) -> list[SearchHit]:
        """Apply read-side ACL after retrieval as a last isolation guard."""
        if self.strategy is None or not self.strategy.write.acl_enabled:
            return hits

        from contextseek.security.policy import can_access_payload

        return [
            hit
            for hit in hits
            if can_access_payload(
                serialize_context_item(hit.item),
                scope=scope,
                strategy=self.strategy.write,
                action="read",
            )
        ]

    def _read_item(self, ref: str) -> ContextItem | None:
        """Read and deserialize a single item by ref."""
        payload = self.adapter.read(ref)
        if payload is None:
            return None
        try:
            return deserialize_context_item(payload)
        except (KeyError, TypeError, ValueError):
            return None

    def _write_item(self, item: ContextItem) -> str:
        """Serialize and write a ContextItem, returning its ref."""
        payload = serialize_context_item(item)
        ref = self.resolver.ref_for(item.scope, item.id)
        self.adapter.write(ref, payload)
        return ref

    def _candidates_for_conflict_check(
        self,
        new_item: ContextItem,
        scope: str,
        *,
        k: int = 20,
    ) -> list[ContextItem]:
        """Return a small candidate set for write-time near-dup / contradiction checks.

        Selection rule (deliberate, not "ANN with text fallback"):

        - **Embedder configured** → ANN top-K only. The candidates returned
          here feed Jaccard / negation / LLM judge downstream; if the backend
          silently degrades to FTS (e.g. ``InMemoryBackend.search`` discards
          ``query_embedding``), those FTS hits are *not* a meaningful
          near-duplicate candidate set, so we'd rather return an empty list
          than mix in unrelated text matches. An empty candidate list simply
          means no near-dup check fires for this write — better than a false
          negative or a false positive from confusing the two retrieval modes.
        - **No embedder** → full-scope scan via ``_list_items``. This keeps
          the legacy Jaccard pipeline working for local / embedder-less
          deployments.
        """
        if self.embedder is not None:
            try:
                payloads = self.adapter.search(
                    self.resolver.prefix_for(scope),
                    new_item.content_text[:200],
                    k=k,
                    query_embedding=new_item.embedding,
                )
            except Exception:  # noqa: BLE001
                payloads = []
            results: list[ContextItem] = []
            for payload in payloads:
                try:
                    results.append(deserialize_context_item(payload))
                except (KeyError, TypeError, ValueError):
                    continue
            return results
        return [it for _, it in self._list_items(scope)]

    def _list_items(
        self,
        scope: str,
        *,
        include_deleted: bool = False,
    ) -> list[tuple[str, ContextItem]]:
        """List all items in a scope."""
        prefix = self.resolver.prefix_for(scope)
        refs = self.adapter.ls(prefix)
        results: list[tuple[str, ContextItem]] = []
        for ref in refs:
            item = self._read_item(ref)
            if item is None:
                continue
            if item.is_deleted and not include_deleted:
                continue
            results.append((ref, item))
        return results

    def _propagate_invalidation(self, deleted_item: ContextItem, scope: str):
        """Run invalidation propagation after a soft-delete."""
        from contextseek.domain.invalidation import (
            propagate_invalidation,
        )

        # Cache all scope items for the find_dependents scan
        all_items = self._list_items(scope, include_deleted=False)

        def _find_dependents(item_id: str):
            """Find items whose links reference the given item_id."""
            results = []
            for _ref, candidate in all_items:
                for link in candidate.links:
                    if link.target_id == item_id:
                        results.append((candidate, link.relation, link.strength))
                        break  # one match per item is enough
            return results

        def _resolve_item(item_id: str) -> ContextItem | None:
            ref = self.resolver.ref_for(scope, item_id)
            return self._read_item(ref)

        reverification_threshold = 0.4
        if self.strategy:
            reverification_threshold = self.strategy.evolution.reverification_threshold

        result = propagate_invalidation(
            deleted_item,
            _find_dependents,
            _resolve_item,
            reverification_threshold=reverification_threshold,
            max_depth=10,
        )

        # Write back degraded items
        for degraded in result.degraded_items:
            ref = self.resolver.ref_for(scope, degraded.item_id)
            item = self._read_item(ref)
            if item is None:
                continue
            item.effective_confidence = degraded.new_confidence
            if degraded.item_id in result.reverification_needed:
                if "needs_reverification" not in item.tags:
                    item.tags.append("needs_reverification")
            self._write_item(item)

        return result
