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
from contextseek.ingestion import (
    ConnectorConfig,
    ConnectorKind,
    ConnectorMode,
    ConnectorRuntime,
    IngestionControlPlane,
    IngestionWriter,
    JsonFileCheckpointStore,
    JsonFileConnectorConfigStore,
    JsonlDeadLetterStore,
)

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse
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


class SkillMdRequest(BaseModel):
    scope: str


class ItemsRequest(BaseModel):
    scope: str
    stage: str | None = None


class ConnectorCreateRequest(BaseModel):
    connector_id: str
    kind: ConnectorKind
    mode: ConnectorMode
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    owner: str = "api"


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
    "skill_md",
    "items",
    "overview",
    "global_overview",
    "scopes",
    "config",
    "metrics",
    "seed",
    "health",
    "connectors",
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


def _parse_watch_paths() -> list[dict[str, str]]:
    """Parse WATCH_PATHS setting into a list of {path, scope} dicts.

    Reads from the ``WATCH_PATHS`` environment variable first (populated by the
    loaded .env file), then falls back to scanning the resolved config file
    directly.  Format: ``path1:scope1,path2:scope2``.
    """
    raw = os.environ.get("WATCH_PATHS", "").strip()
    if not raw:
        from contextseek.config.settings import _get_default_env_file

        env_file = _get_default_env_file()
        if env_file:
            try:
                for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("WATCH_PATHS="):
                        raw = line[len("WATCH_PATHS=") :].strip().strip('"').strip("'")
                        break
            except OSError:
                pass

    if not raw:
        return []

    results: list[dict[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            path_part, scope_part = entry.split(":", 1)
            expanded = str(Path(path_part.strip()).expanduser())
            results.append({"path": expanded, "scope": scope_part.strip()})
    return results


def _ingestion_state_dir() -> Path:
    raw = os.environ.get("INGESTION_STATE_DIR", "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = Path.home() / ".contextseek" / "ingestion"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    ingestion_dir = _ingestion_state_dir()
    ingestion_runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=JsonFileCheckpointStore(ingestion_dir / "checkpoints.json"),
        dead_letter_store=JsonlDeadLetterStore(ingestion_dir / "dead_letters.jsonl"),
    )
    control = IngestionControlPlane(
        ingestion_runtime,
        config_store=JsonFileConnectorConfigStore(ingestion_dir / "connectors.json"),
        restore_on_startup=True,
    )
    ingestion_runtime.event_callback = control.record_event

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

    @app.post("/skill_md")
    async def skill_md(req: SkillMdRequest) -> dict[str, Any]:
        from contextseek.domain.skill_executor import SkillExporter

        exporter = SkillExporter()
        skills = ctx.skills(req.scope, skill_type="prompt")
        result = [
            {"name": s.summary or s.id, "content": exporter.to_hermes_skill_md(s)}
            for s in skills
        ]
        return {"skills": result}

    @app.post("/items")
    async def list_items(req: ItemsRequest) -> dict[str, Any]:
        from contextseek.domain.stages import Stage

        stage = Stage(req.stage) if req.stage else None
        result_items = ctx.items(scope=req.scope, stage=stage)
        return {"items": [serialize_context_item(it) for it in result_items]}

    @app.post("/connectors")
    async def create_connector(req: ConnectorCreateRequest) -> dict[str, Any]:
        config = ConnectorConfig(
            connector_id=req.connector_id,
            kind=req.kind,
            mode=req.mode,
            config=req.config,
            enabled=req.enabled,
            owner=req.owner,
        )
        created = control.create_connector(config)
        return {
            "connector": created.connector_id,
            "kind": created.kind.value,
            "mode": created.mode.value,
        }

    @app.get("/connectors")
    async def list_connectors() -> dict[str, Any]:
        return {"connectors": control.list_connectors()}

    @app.post("/connectors/{connector_id}/sync")
    async def sync_connector(connector_id: str) -> dict[str, Any]:
        if connector_id not in {cfg["connector_id"] for cfg in control.list_connectors()}:
            raise HTTPException(status_code=404, detail=f"connector not found: {connector_id}")
        steps = control.trigger_sync(connector_id)
        return {"connector_id": connector_id, "scheduled_steps": steps}

    @app.post("/connectors/{connector_id}/pause")
    async def pause_connector(connector_id: str) -> dict[str, Any]:
        try:
            control.pause(connector_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"connector not found: {connector_id}",
            ) from exc
        return {"connector_id": connector_id, "status": "paused"}

    @app.post("/connectors/{connector_id}/resume")
    async def resume_connector(connector_id: str) -> dict[str, Any]:
        try:
            control.resume(connector_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"connector not found: {connector_id}",
            ) from exc
        return {"connector_id": connector_id, "status": "enabled"}

    @app.get("/connectors/{connector_id}/checkpoints")
    async def connector_checkpoints(connector_id: str) -> dict[str, Any]:
        if connector_id not in {cfg["connector_id"] for cfg in control.list_connectors()}:
            raise HTTPException(status_code=404, detail=f"connector not found: {connector_id}")
        return {"connector_id": connector_id, "checkpoints": control.checkpoints(connector_id)}

    @app.get("/connectors/{connector_id}/events")
    async def connector_events(connector_id: str) -> dict[str, Any]:
        if connector_id not in {cfg["connector_id"] for cfg in control.list_connectors()}:
            raise HTTPException(status_code=404, detail=f"connector not found: {connector_id}")
        return {"connector_id": connector_id, "events": control.events(connector_id)}

    @app.get("/connectors/{connector_id}/dead-letters")
    async def connector_dead_letters(connector_id: str) -> dict[str, Any]:
        if connector_id not in {cfg["connector_id"] for cfg in control.list_connectors()}:
            raise HTTPException(status_code=404, detail=f"connector not found: {connector_id}")
        return {
            "connector_id": connector_id,
            "dead_letters": control.dead_letters(connector_id),
        }

    @app.post("/connectors/{connector_id}/dead-letters/{record_id}/replay")
    async def replay_dead_letter(connector_id: str, record_id: str) -> dict[str, Any]:
        try:
            result = control.replay_dead_letter(connector_id, record_id, run_now=True)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"connector not found: {connector_id}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

    @app.post("/connectors/{connector_id}/dead-letters/replay-all")
    async def replay_all_dead_letters(
        connector_id: str,
        remove_after_replay: bool = False,
    ) -> dict[str, Any]:
        try:
            return control.replay_all_dead_letters(
                connector_id,
                run_now=True,
                remove_after_replay=remove_after_replay,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"connector not found: {connector_id}",
            ) from exc

    @app.delete("/connectors/{connector_id}/dead-letters/{record_id}")
    async def delete_dead_letter(connector_id: str, record_id: str) -> dict[str, Any]:
        try:
            deleted = control.delete_dead_letter(connector_id, record_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"connector not found: {connector_id}",
            ) from exc
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"dead-letter record not found: {record_id}",
            )
        return {"connector_id": connector_id, "record_id": record_id, "deleted": True}

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

    @app.get("/global_overview")
    async def global_overview() -> dict[str, Any]:
        import datetime

        STAGES = ["raw", "extracted", "knowledge", "skill"]

        seen_scopes: list[str] = ctx.list_scopes()

        # Single pass: load all items across all scopes
        total_items = 0
        total_pending_extraction = 0
        total_pending_convergence = 0
        stage_distribution: dict[str, int] = {}
        scope_counts: dict[str, int] = {}

        today = datetime.date.today()
        day_labels: list[str] = []
        day_counts: dict[str, int] = {}
        for offset in range(6, -1, -1):
            d = today - datetime.timedelta(days=offset)
            lbl = d.strftime("%m-%d")
            day_labels.append(lbl)
            day_counts[lbl] = 0

        # item_id → stage string (for heatmap link resolution)
        item_stage_map: dict[str, str] = {}
        # deferred links: (source_stage, target_id)
        all_links: list[tuple[str, str]] = []

        # Risk metrics accumulators
        # conflict: scope → count of items that have at least one refuted_by link
        scope_conflict_counts: dict[str, int] = {}
        # orphan: collect all item ids and all referenced target ids
        all_item_ids: set[str] = set()
        all_target_ids: set[str] = set()
        items_with_outlinks: set[str] = set()

        for scope_str in seen_scopes:
            try:
                scope_items = ctx.items(scope=scope_str)
            except Exception:
                scope_counts[scope_str] = 0
                continue

            pending_extraction = 0
            pending_convergence = 0
            scope_total = 0
            scope_conflicts = 0

            for item in scope_items:
                if item.is_deleted:
                    continue
                scope_total += 1
                stage_key = item.stage.value
                stage_distribution[stage_key] = stage_distribution.get(stage_key, 0) + 1

                # Heatmap: record stage and collect outbound links
                item_stage_map[item.id] = stage_key
                all_item_ids.add(item.id)
                has_outlink = False
                has_refuted_by = False
                for link in item.links or []:
                    all_links.append((stage_key, link.target_id))
                    all_target_ids.add(link.target_id)
                    has_outlink = True
                    if link.relation == "refuted_by":
                        has_refuted_by = True
                if has_outlink:
                    items_with_outlinks.add(item.id)
                if has_refuted_by:
                    scope_conflicts += 1

                # Pending counts
                from contextseek.domain.stages import Stage as _Stage

                if item.stage == _Stage.raw and isinstance(item.content, dict):
                    pending_extraction += 1
                elif item.stage == _Stage.extracted:
                    pending_convergence += 1

                # Trend
                if item.created_at:
                    try:
                        ts = item.created_at
                        if isinstance(ts, str):
                            item_date = datetime.date.fromisoformat(ts[:10])
                        else:
                            item_date = ts.date() if hasattr(ts, "date") else None
                        if item_date is not None:
                            lbl = item_date.strftime("%m-%d")
                            if lbl in day_counts:
                                day_counts[lbl] += 1
                    except (ValueError, AttributeError):
                        pass

            scope_counts[scope_str] = scope_total
            scope_conflict_counts[scope_str] = scope_conflicts
            total_items += scope_total
            total_pending_extraction += pending_extraction
            total_pending_convergence += pending_convergence

        trend_values = [day_counts[lbl] for lbl in day_labels]

        # health_score: penalise pending work relative to total
        pending_ratio = (total_pending_extraction + total_pending_convergence) / max(
            total_items, 1
        )
        health_score = max(0, min(100, round(100 - pending_ratio * 50)))

        scope_top = sorted(
            [{"label": s, "value": v} for s, v in scope_counts.items()],
            key=lambda x: x["value"],
            reverse=True,
        )[:10]

        # Build stage×stage heatmap matrix (row=source stage, col=target stage)
        stage_idx = {s: i for i, s in enumerate(STAGES)}
        n = len(STAGES)
        matrix = [[0] * n for _ in range(n)]
        for src_stage, tgt_id in all_links:
            tgt_stage = item_stage_map.get(tgt_id)
            if tgt_stage and src_stage in stage_idx and tgt_stage in stage_idx:
                matrix[stage_idx[src_stage]][stage_idx[tgt_stage]] += 1

        # Risk 1: top conflict subject — scope with the most refuted_by links
        top_conflict_scope: str | None = None
        top_conflict_count = 0
        for scope_str, cnt in scope_conflict_counts.items():
            if cnt > top_conflict_count:
                top_conflict_count = cnt
                top_conflict_scope = scope_str
        conflict_subject = (
            f"{top_conflict_scope} ({top_conflict_count})"
            if top_conflict_scope and top_conflict_count > 0
            else None
        )

        # Risk 2: orphan ratio — items with neither outgoing nor incoming links
        truly_orphaned = all_item_ids - items_with_outlinks - all_target_ids
        orphan_count = len(truly_orphaned)
        orphan_ratio = round(orphan_count / max(total_items, 1), 3)

        # Risk 3: suggest compact — extracted backlog is large enough to warrant compaction
        suggest_compact = (
            total_pending_convergence >= 10
            or total_pending_convergence / max(total_items, 1) >= 0.3
        )

        return {
            "total_items": total_items,
            "health_score": health_score,
            "active_scopes": len(seen_scopes),
            "stage_distribution": stage_distribution,
            "scope_top": scope_top,
            "trend": {"labels": day_labels, "values": trend_values},
            "heatmap": {"stages": STAGES, "matrix": matrix},
            "risk_conflict_subject": conflict_subject,
            "risk_orphan_ratio": orphan_ratio,
            "risk_suggest_compact": suggest_compact,
        }

    @app.get("/scopes")
    async def list_scopes() -> dict[str, Any]:
        return {"scopes": ctx.list_scopes()}

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        from contextseek.config.settings import ContextSeekSettings, LifecycleSettings

        s = ContextSeekSettings()
        lc = LifecycleSettings()
        return {
            "storage_backend": s.storage.backend,
            "llm_model": s.llm.model or s.llm.provider,
            "embedding_model": s.embedding.model or s.embedding.provider,
            "default_scope": s.default_scope,
            "version": PACKAGE_VERSION,
            "auto_sync": lc.auto_compact,
            "lifecycle_interval_seconds": lc.interval_seconds,
            "watch_paths": _parse_watch_paths(),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        chunks: list[str] = []
        if ctx.audit_log is not None:
            chunks.append(ctx.audit_log.export_prometheus())
        ingestion_metrics = ingestion_runtime.export_prometheus_metrics()
        if ingestion_metrics:
            chunks.append(ingestion_metrics)
        return "\n".join(part for part in chunks if part)

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
