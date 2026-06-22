"""Minimal FastAPI server for ContextSeek SDK."""

from __future__ import annotations

import os
import signal
import sys
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
    include_expired: bool = False


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


class ConfigUpdateRequest(BaseModel):
    llm_provider: str | None = None
    embedding_provider: str | None = None
    storage_backend: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dims: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    ob_host: str | None = None
    ob_port: str | None = None
    ob_db_name: str | None = None
    ob_table_name: str | None = None
    seekdb_host: str | None = None
    seekdb_port: str | None = None
    seekdb_database: str | None = None
    seekdb_path: str | None = None
    sqlite_path: str | None = None
    storage_path: str | None = None


class ConfigTestRequest(BaseModel):
    target: str
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    dims: str | None = None


class SkillContextRequest(BaseModel):
    scope: str
    query: str | None = None
    k: int = 5


class SkillMdRequest(BaseModel):
    scope: str


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
    "skill_md",
    "items",
    "overview",
    "global_overview",
    "scopes",
    "config",
    "metrics",
    "plugins",
    "seed",
    "health",
    "install",
    "restart",
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


def _update_env_file(updates: dict[str, str]) -> None:
    """Write key=value pairs into the resolved .env config file.

    Existing lines matching a key are replaced in-place; keys not yet present
    are appended at the end.  KWARGS keys (LLM_KWARGS / EMBEDDING_KWARGS) are
    handled specially: the JSON dict is read, the ``api_key`` field updated,
    and the whole dict written back as JSON.
    """
    import json as _json

    from contextseek.config.settings import _get_default_env_file

    env_file = _get_default_env_file()
    if env_file is None:
        # Web/dev servers may be launched without running `contextseek init`.
        # In that case, create the same CWD .env file that settings discovery
        # already prioritizes on the next reload.
        env_file = str(Path.cwd() / ".env")

    env_path = Path(env_file)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    def _set_line(lines: list[str], key: str, value: str) -> list[str]:
        prefix = f"{key}="
        new_line = f"{key}={value}\n"
        for i, line in enumerate(lines):
            if line.lstrip().startswith(prefix) or line.lstrip().startswith(
                f"# {prefix}"
            ):
                if line.lstrip().startswith(prefix):
                    lines[i] = new_line
                    return lines
        lines.append(new_line)
        return lines

    def _update_kwargs_key(
        lines: list[str], kwargs_key: str, field: str, value: str
    ) -> tuple[list[str], str]:
        """Read KWARGS JSON, update field, write back."""
        prefix = f"{kwargs_key}="
        existing: dict[str, Any] = {}
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(prefix):
                raw = stripped[len(prefix) :].strip().strip('"').strip("'")
                try:
                    existing = _json.loads(raw)
                except Exception:
                    existing = {}
                break
        existing[field] = value
        serialized = _json.dumps(existing)
        return _set_line(lines, kwargs_key, serialized), serialized

    for env_key, env_val in updates.items():
        if env_key == "LLM_API_KEY":
            lines, serialized = _update_kwargs_key(
                lines, "LLM_KWARGS", "api_key", env_val
            )
            os.environ["LLM_KWARGS"] = serialized
        elif env_key == "EMBEDDING_API_KEY":
            lines, serialized = _update_kwargs_key(
                lines, "EMBEDDING_KWARGS", "api_key", env_val
            )
            os.environ["EMBEDDING_KWARGS"] = serialized
        else:
            lines = _set_line(lines, env_key, env_val)
            os.environ[env_key] = env_val

    env_path.write_text("".join(lines), encoding="utf-8")


def _server_argv() -> list[str]:
    """Return an argv that restarts the server without losing ``-m`` execution."""
    orig_argv = getattr(sys, "orig_argv", None)
    if isinstance(orig_argv, list) and len(orig_argv) > 1:
        return [sys.executable, *orig_argv[1:]]
    return [sys.executable, *sys.argv]


def _running_with_reload() -> bool:
    argv = [str(arg) for arg in [*getattr(sys, "orig_argv", []), *sys.argv]]
    return "--reload" in argv


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

    from contextseek.plugs.core.proxy.http import create_plug_proxy_router

    app.include_router(create_plug_proxy_router(ctx))

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
            include_expired=req.include_expired,
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
            "conflict_updated": report.conflict_updated_count,
            "conflict_drift": report.conflict_drift_count,
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
            "pitfall_items": len(report.pitfall.items) if report.pitfall else 0,
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
    async def global_overview(scope: str | None = None) -> dict[str, Any]:
        import datetime

        STAGES = ["raw", "extracted", "knowledge", "skill"]

        all_scopes: list[str] = ctx.list_scopes()
        seen_scopes: list[str] = (
            [scope] if scope and scope in all_scopes else all_scopes
        )

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
            "active_scopes": len(all_scopes),
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
        from contextseek.config.factory import resolve_embedding_dims
        from contextseek.config.settings import ContextSeekSettings, LifecycleSettings

        s = ContextSeekSettings()
        lc = LifecycleSettings()
        embedding_dims = resolve_embedding_dims(s.embedding)
        result: dict[str, Any] = {
            "storage_backend": s.storage.backend,
            "llm_provider": s.llm.provider,
            "llm_model": s.llm.model or s.llm.provider,
            "llm_base_url": s.llm.base_url,
            "llm_api_key": s.llm.kwargs.get("api_key", ""),
            "embedding_provider": s.embedding.provider,
            "embedding_model": s.embedding.model or s.embedding.provider,
            "embedding_dims": str(embedding_dims) if embedding_dims else "",
            "embedding_base_url": s.embedding.base_url,
            "embedding_api_key": s.embedding.kwargs.get("api_key", ""),
            "default_scope": s.default_scope,
            "version": PACKAGE_VERSION,
            "auto_sync": lc.auto_compact,
            "lifecycle_interval_seconds": lc.interval_seconds,
            "watch_paths": _parse_watch_paths(),
        }
        backend = s.storage.backend
        if backend == "oceanbase":
            from contextseek.config.settings import OceanBaseSettings

            ob = OceanBaseSettings()
            result["ob_host"] = ob.host
            result["ob_port"] = ob.port
            result["ob_db_name"] = ob.db_name
            result["ob_table_name"] = ob.table_name
        elif backend == "seekdb":
            from contextseek.config.settings import SeekDBSettings

            sdb = SeekDBSettings()
            if sdb.host:
                result["seekdb_mode"] = "server"
                result["seekdb_host"] = sdb.host
                result["seekdb_port"] = str(sdb.port)
                result["seekdb_database"] = sdb.database
            else:
                result["seekdb_mode"] = "embedded"
                result["seekdb_path"] = str(Path(sdb.path).expanduser())
        elif backend == "sqlite":
            from contextseek.config.settings import SQLiteSettings

            sq = SQLiteSettings()
            result["sqlite_path"] = str(Path(sq.path).expanduser())
        elif backend == "file":
            result["storage_path"] = str(Path(s.storage.path).expanduser())
        return result

    @app.put("/config")
    async def update_config(req: ConfigUpdateRequest) -> dict[str, Any]:
        FIELD_TO_ENV: dict[str, str] = {
            "storage_backend": "STORAGE_BACKEND",
            "llm_provider": "LLM_PROVIDER",
            "llm_model": "LLM_MODEL",
            "llm_base_url": "LLM_BASE_URL",
            "llm_api_key": "LLM_API_KEY",
            "embedding_provider": "EMBEDDING_PROVIDER",
            "embedding_model": "EMBEDDING_MODEL",
            "embedding_dims": "EMBEDDING_DIMS",
            "embedding_base_url": "EMBEDDING_BASE_URL",
            "embedding_api_key": "EMBEDDING_API_KEY",
            "ob_host": "OB_HOST",
            "ob_port": "OB_PORT",
            "ob_db_name": "OB_DB_NAME",
            "ob_table_name": "OB_TABLE_NAME",
            "seekdb_host": "SEEKDB_HOST",
            "seekdb_port": "SEEKDB_PORT",
            "seekdb_database": "SEEKDB_DATABASE",
            "seekdb_path": "SEEKDB_PATH",
            "sqlite_path": "SQLITE_PATH",
            "storage_path": "STORAGE_PATH",
        }
        updates: dict[str, str] = {}
        for field, env_key in FIELD_TO_ENV.items():
            val = getattr(req, field, None)
            if val is not None:
                if field == "embedding_dims" and val.strip() == "":
                    val = "0"
                updates[env_key] = val
        if not updates:
            return {"status": "ok", "restart_required": False}
        try:
            _update_env_file(updates)
        except FileNotFoundError as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"status": "ok", "restart_required": True}

    @app.post("/config/test")
    async def test_config_connection(req: ConfigTestRequest) -> dict[str, Any]:
        from contextseek.config.factory import (
            build_embedder,
            build_llm,
            resolve_embedding_dims,
        )
        from contextseek.config.settings import EmbeddingSettings, LLMSettings
        from contextseek.llm.client import coerce_response_text

        target = req.target.strip().lower()
        provider = req.provider.strip().lower()
        kwargs = {"api_key": req.api_key} if req.api_key else {}
        if provider in {"", "none"}:
            return {
                "ok": False,
                "message": "Provider is disabled.",
            }

        try:
            if target == "llm":
                llm = build_llm(
                    LLMSettings(
                        provider=provider,
                        model=req.model.strip(),
                        base_url=req.base_url.strip(),
                        kwargs=kwargs,
                    )
                )
                if llm is None:
                    return {"ok": False, "message": "LLM is not configured."}
                try:
                    from langchain_core.messages import HumanMessage

                    resp = llm.invoke(
                        [HumanMessage(content="Reply with exactly: pong")]
                    )
                except Exception as exc:
                    return {"ok": False, "message": str(exc)}
                text = coerce_response_text(resp).strip()
                return {
                    "ok": bool(text),
                    "message": "LLM connection succeeded."
                    if text
                    else "LLM returned an empty response.",
                    "detail": text[:200],
                }

            if target == "embedding":
                dims = 0
                if req.dims is not None and req.dims.strip():
                    dims = int(req.dims)
                settings = EmbeddingSettings(
                    provider=provider,
                    model=req.model.strip(),
                    dims=dims,
                    base_url=req.base_url.strip(),
                    kwargs=kwargs,
                )
                embedder = build_embedder(settings)
                if embedder is None:
                    return {"ok": False, "message": "Embedding is not configured."}
                try:
                    vector = embedder("contextseek connectivity test")
                except Exception as exc:
                    return {"ok": False, "message": str(exc)}
                actual_dims = len(vector)
                configured_dims = resolve_embedding_dims(settings)
                ok = actual_dims > 0 and (
                    configured_dims == 0 or actual_dims == configured_dims
                )
                message = (
                    f"Embedding connection succeeded. Dimension: {actual_dims}."
                    if ok
                    else (
                        "Embedding connection succeeded, but returned dimension "
                        f"{actual_dims} differs from configured dimension "
                        f"{configured_dims}."
                    )
                )
                return {
                    "ok": ok,
                    "message": message,
                    "dimension": actual_dims,
                    "configured_dimension": configured_dims,
                }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

        return {"ok": False, "message": f"Unknown config test target: {req.target}"}

    @app.get("/metrics")
    async def metrics() -> str:
        return ctx.audit_log.export_prometheus() if ctx.audit_log is not None else ""

    @app.post("/seed")
    async def seed_examples() -> dict[str, Any]:
        """Populate the ``contextseek`` scope with example data (idempotent)."""
        from contextseek.http.seed import maybe_seed

        seeded = maybe_seed()
        return {"status": "ok", "seeded": seeded}

    @app.post("/install")
    async def install_package(body: dict[str, str]) -> dict[str, Any]:
        """Install a Python package into the current environment.

        Prefers ``uv pip install`` (used by this project) and falls back to
        ``python -m pip install`` when uv is not on PATH.
        """
        import shutil
        import subprocess
        import sys

        package = (body.get("package") or "").strip()
        if not package:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="package is required")

        uv_bin = shutil.which("uv")
        if uv_bin:
            cmd = [uv_bin, "pip", "install", "--python", sys.executable, package]
        else:
            cmd = [sys.executable, "-m", "pip", "install", package]

        result = subprocess.run(cmd, capture_output=True, text=True)
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    @app.post("/restart")
    async def restart_server() -> dict[str, str]:
        """Restart the current server process so updated .env values are loaded.

        In uvicorn ``--reload`` mode, nudge the reloader by touching this file.
        In single-process mode, re-exec the original Python command so ``-m``
        invocations keep their import semantics.  If the background daemon is
        also running it is restarted separately so both processes reload config.
        """
        import asyncio
        import shutil
        import subprocess

        async def _do_restart() -> None:
            await asyncio.sleep(0.8)

            # Restart background daemon if it's running (config change affects it too)
            try:
                from contextseek.daemon.process import DaemonProcess

                daemon = DaemonProcess()
                if daemon.is_running():
                    daemon.stop()
                    await asyncio.sleep(0.3)
                    bin_path = shutil.which("contextseek") or sys.argv[0]
                    subprocess.Popen(
                        [bin_path, "daemon", "start"],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            except Exception:
                pass

            if _running_with_reload():
                os.utime(__file__, None)
                return

            os.execv(sys.executable, _server_argv())

        asyncio.create_task(_do_restart())
        return {"status": "restarting"}

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
