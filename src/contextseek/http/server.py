"""Minimal FastAPI server for ContextSeek SDK."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any

from contextseek._version import __version__ as PACKAGE_VERSION
from contextseek.client.contextseek import ContextSeek
from contextseek.domain.serialization import (
    deserialize_context_item,
    serialize_context_item,
)

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    from starlette.exceptions import HTTPException as StarletteHTTPException
except ImportError as exc:
    msg = (
        "FastAPI dependencies are not installed. "
        "Install with: pip install contextseek[http]"
    )
    raise ImportError(msg) from exc


class AddRequest(BaseModel):
    scope: str
    content: Any
    source: str = "api"
    tags: list[str] = Field(default_factory=list)


class RetrieveRequest(BaseModel):
    scope: str
    query: str
    k: int = 10
    full: bool = False
    filters: dict[str, Any] | None = None
    include_deleted: bool = False


class ExpandRequest(BaseModel):
    scope: str
    ids: list[str]


class ForgetRequest(BaseModel):
    scope: str
    item_id: str
    reason: str = "api_forget"


class DeleteRequest(BaseModel):
    scope: str
    item_id: str
    reason: str = "api_delete"
    propagate: bool = True


class CompactRequest(BaseModel):
    scope: str
    dry_run: bool = False


class DreamRequest(BaseModel):
    scope: str
    dry_run: bool = False


class FeedbackRequest(BaseModel):
    scope: str
    item_id: str
    score: float
    reason: str = ""


class UpstreamRequest(BaseModel):
    scope: str
    item_id: str


class EvidenceChainRequest(BaseModel):
    scope: str
    item_id: str
    max_depth: int = 10


class ChainConfidenceRequest(BaseModel):
    scope: str
    item_id: str


class SkillToolsRequest(BaseModel):
    scope: str
    fmt: str = "openai"
    query: str | None = None
    k: int = 20


class SkillContextRequest(BaseModel):
    scope: str
    query: str | None = None
    k: int = 5


class ItemsRequest(BaseModel):
    scope: str
    stage: str | None = None


_API_ROOT_SEGMENTS: set[str] = {
    "add",
    "retrieve",
    "expand",
    "forget",
    "delete",
    "compact",
    "dream",
    "feedback",
    "upstream",
    "evidence_chain",
    "chain_confidence",
    "skill_tools",
    "skill_context",
    "items",
    "overview",
    "metrics",
    "seed",
    "health",
    "__desktop",
}


class SPAServingStaticFiles(StaticFiles):
    """StaticFiles that falls back to ``index.html`` for SPA routes."""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Any:
        # Starlette's StaticFiles raises HTTPException(404) (rather than returning
        # a 404 response) when a path has no matching file, so the SPA fallback
        # must catch it instead of inspecting a status code.
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if scope.get("method") not in {"GET", "HEAD"}:
                raise
            root = path.split("/", 1)[0].strip()
            if root in _API_ROOT_SEGMENTS:
                raise
            return await super().get_response("index.html", scope)


def _dashboard_dist_dir() -> Path | None:
    """Locate the built dashboard SPA (``dashboard/dist``).

    Order: ``CTX_DASHBOARD_DIST`` env (packaged builds set this) → package-relative
    ``<repo>/dashboard/dist`` → ``<cwd>/dashboard/dist``. Returns ``None`` when no
    build exists, so the bare API still works without a front-end.
    """
    candidates: list[Path] = []
    env_dir = os.environ.get("CTX_DASHBOARD_DIST", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    # src/contextseek/http/server.py -> repo root is three parents up.
    candidates.append(Path(__file__).resolve().parents[3] / "dashboard" / "dist")
    candidates.append(Path.cwd() / "dashboard" / "dist")
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return None


def create_app(client: ContextSeek | None = None) -> FastAPI:
    """Create FastAPI application backed by ContextSeek."""
    app = FastAPI(title="ContextSeek API", version=PACKAGE_VERSION)

    # CORS — required when the front-end runs on a different origin (separate
    # port / host). Origins come from CTX_CORS_ORIGINS (comma-separated), or "*"
    # by default. allow_credentials stays False so "*" is permitted.
    origins = [
        o.strip()
        for o in os.environ.get("CTX_CORS_ORIGINS", "*").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ctx = client or ContextSeek.from_settings()

    @app.post("/add")
    async def add_item(req: AddRequest) -> dict[str, Any]:
        item = ctx.add(req.content, scope=req.scope, source=req.source, tags=req.tags)
        return {"id": item.id, "stage": item.stage.value}

    @app.post("/retrieve")
    async def retrieve(req: RetrieveRequest) -> dict[str, Any]:
        response = ctx.retrieve(
            req.query,
            scope=req.scope,
            k=req.k,
            full=req.full,
            filters=req.filters,
            include_deleted=req.include_deleted,
        )
        return {
            "items": [
                {
                    "id": h.item.id,
                    "score": h.score,
                    "layer": h.layer,
                    "summary": h.item.summary,
                    "content": h.item.content_text if h.layer == "full" else None,
                    "tags": list[str](h.item.tags or []),
                    "provenance_summary": h.provenance_summary,
                    "stage_confidence": h.stage_confidence,
                    "recall_path": h.recall_path,
                }
                for h in response
            ],
            "_meta": {
                "layer": response.meta.layer,
                "full_via": response.meta.full_via,
                "hint": response.meta.hint,
            },
        }

    @app.post("/expand")
    async def expand(req: ExpandRequest) -> dict[str, Any]:
        items: list[Any] = []
        for iid in req.ids:
            ref = ctx.resolver.ref_for(req.scope, iid)
            payload = ctx.adapter.read(ref)
            if payload is None:
                continue
            try:
                items.append(deserialize_context_item(payload))
            except (KeyError, TypeError, ValueError):
                continue
        return {"items": [serialize_context_item(it) for it in items]}

    @app.post("/forget")
    async def forget_item(req: ForgetRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        ctx.forget(ref, scope=req.scope, reason=req.reason)
        return {"status": "ok", "id": req.item_id}

    @app.post("/delete")
    async def delete_item(req: DeleteRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        ctx.delete(ref, scope=req.scope, reason=req.reason, propagate=req.propagate)
        return {"status": "ok", "id": req.item_id}

    @app.post("/compact")
    async def compact_scope(req: CompactRequest) -> dict[str, Any]:
        report = ctx.compact(scope=req.scope, dry_run=req.dry_run)
        return {
            "merged": report.merged_count,
            "archived": report.archived_count,
            "evolved": report.evolved_count,
        }

    @app.post("/dream")
    async def dream_scope(req: DreamRequest) -> dict[str, Any]:
        report = ctx.dream(scope=req.scope, dry_run=req.dry_run)
        return {
            "total_dream_items": report.total_dream_items,
            "consolidation_patterns": report.consolidation.patterns_found,
            "consolidation_items": len(report.consolidation.items),
            "divergence_items": len(report.divergence.items)
            if report.divergence
            else 0,
        }

    @app.post("/feedback")
    async def feedback_item(req: FeedbackRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        ctx.feedback(ref, scope=req.scope, score=req.score, reason=req.reason)
        return {"status": "ok", "id": req.item_id}

    @app.post("/upstream")
    async def upstream_item(req: UpstreamRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        chain = ctx.upstream(ref, scope=req.scope)
        return {"items": [serialize_context_item(it) for it in chain]}

    @app.post("/evidence_chain")
    async def evidence_chain_item(req: EvidenceChainRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        chain = ctx.evidence_chain(ref, scope=req.scope, max_depth=req.max_depth)
        return chain.to_dict()

    @app.post("/chain_confidence")
    async def chain_confidence_item(req: ChainConfidenceRequest) -> dict[str, Any]:
        ref = (
            req.item_id
            if req.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(req.scope, req.item_id)
        )
        confidence = ctx.chain_confidence(ref, scope=req.scope)
        return {"confidence": confidence}

    @app.post("/skill_tools")
    async def skill_tools(req: SkillToolsRequest) -> dict[str, Any]:
        tools = ctx.skill_tools(req.scope, fmt=req.fmt, query=req.query, k=req.k)
        return {"tools": tools}

    @app.post("/skill_context")
    async def skill_context(req: SkillContextRequest) -> dict[str, Any]:
        context = ctx.skill_context(req.scope, query=req.query, k=req.k)
        return {"context": context}

    @app.post("/items")
    async def list_items(req: ItemsRequest) -> dict[str, Any]:
        from contextseek.domain.stages import Stage

        stage = Stage(req.stage) if req.stage else None
        result_items = ctx.items(scope=req.scope, stage=stage)
        return {"items": [serialize_context_item(it) for it in result_items]}

    @app.get("/overview")
    async def overview_scope(scope: str) -> dict[str, Any]:
        report = ctx.overview(scope=scope)
        return {
            "total_items": report.total_items,
            "stage_distribution": report.stage_distribution,
            "pending_extraction": report.pending_extraction,
            "pending_convergence": report.pending_convergence,
            "distill_candidates": report.distill_candidates,
        }

    @app.get("/metrics")
    async def metrics() -> str:
        return ctx.audit_log.export_prometheus() if ctx.audit_log is not None else ""

    @app.post("/seed")
    async def seed_examples() -> dict[str, Any]:
        """Populate the ``contextseek`` scope with example data (idempotent)."""
        from contextseek.http.seed import maybe_seed

        seeded = maybe_seed()
        return {"status": "ok", "seeded": seeded}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": PACKAGE_VERSION}

    @app.post("/__desktop/shutdown", include_in_schema=False)
    async def desktop_shutdown() -> dict[str, str]:
        """Shutdown hook for the desktop host graceful-exit flow."""
        if os.environ.get("CTX_ENABLE_DESKTOP_SHUTDOWN", "1") != "1":
            return {"status": "disabled"}

        pid = os.getpid()

        def _terminate() -> None:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

        threading.Timer(0.1, _terminate).start()
        return {"status": "stopping"}

    # Serve the built dashboard SPA at "/" for same-origin desktop/single-process
    # use. Mounted LAST so the API routes above take precedence. Skipped when the
    # SPA isn't built or StaticFiles is unavailable (bare-API mode still works).
    dist = _dashboard_dist_dir()
    if dist is not None:
        app.mount("/", SPAServingStaticFiles(directory=str(dist), html=True), name="ui")

    return app


def __getattr__(name: str) -> Any:
    """Lazily build the ASGI ``app`` on first access (PEP 562).

    ``uvicorn contextseek.http.server:app`` still works, but merely importing
    this module no longer constructs a full ContextSeek client (which would load
    settings/LLM/embedder) as an import-time side effect.
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
