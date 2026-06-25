"""Config-management HTTP routes (versioned store + dashboard integration)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from contextseek._version import __version__ as PACKAGE_VERSION
from contextseek.config.agentseek_ingestor import AgentseekIngestor
from contextseek.config.manager import ConfigManager
from contextseek.config.materializer import Materializer
from contextseek.config.migrator import migrate_into

# Flat dashboard fields whose target is a leaf *inside* a dict-valued settings
# field (no direct env leaf). Routed to the dotted path into the kwargs dict so
# the key survives the versioned store → materialize → reload round-trip.
FLAT_TO_DOTTED_OVERRIDE: dict[str, str] = {
    "llm_api_key": "llm.kwargs.api_key",
    "embedding_api_key": "embedding.kwargs.api_key",
}

# Flat dashboard field → env var (mirrors server.py's existing FIELD_TO_ENV).
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


def _flat_get(d: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _manager(config_dir: Path) -> ConfigManager:
    mgr = ConfigManager(config_dir)
    mgr.init_store()
    return mgr


def _materializer() -> Materializer:
    env_path = Path(os.environ.get("CONTEXTSEEK_ENV_FILE", ".env"))
    runtime_path = Path(os.environ.get("CONTEXTSEEK_CONFIG", "config.json"))
    return Materializer(env_path=env_path, runtime_path=runtime_path)


def _ensure_migrated(mgr: ConfigManager) -> None:
    if mgr.current() is None:
        env_path = Path(os.environ.get("CONTEXTSEEK_ENV_FILE", ".env"))
        rt_path = Path(os.environ.get("CONTEXTSEEK_CONFIG", "config.json"))
        migrate_into(
            mgr,
            env_path=env_path if env_path.exists() else None,
            runtime_path=rt_path if rt_path.exists() else None,
        )


def _normalize_embedding_updates(req: dict[str, Any]) -> None:
    """Preserve the dashboard's embedding-provider validation UX.

    Mirrors the pre-reroute ``update_config`` logic: when the request touches
    ``embedding_provider`` / ``embedding_model``, normalize the five flat
    embedding fields. If the provider becomes none, reset model/dims/base_url/
    api_key to none/0/empty; if the provider is not none but the model is empty
    or none, raise HTTPException(400).
    """
    from fastapi import HTTPException

    if "embedding_provider" not in req and "embedding_model" not in req:
        return
    from contextseek.config.settings import ContextSeekSettings

    current = ContextSeekSettings()
    provider = str(req.get("embedding_provider", current.embedding.provider)).strip()
    model = str(req.get("embedding_model", current.embedding.model)).strip()
    provider_normalized = provider.lower()
    model_normalized = model.lower()
    if provider_normalized in {"", "none"}:
        req["embedding_provider"] = "none"
        req["embedding_model"] = "none"
        req["embedding_dims"] = "0"
        req["embedding_base_url"] = ""
        req["embedding_api_key"] = ""
    elif model_normalized in {"", "none"}:
        raise HTTPException(
            status_code=400,
            detail=(
                "EMBEDDING_MODEL must be a real model when "
                "EMBEDDING_PROVIDER is not none."
            ),
        )


def _build_snapshot_from_effective(effective: dict[str, Any]) -> dict[str, Any]:
    """Build dashboard ``Config`` payload from managed effective config."""
    from contextseek.config.factory import resolve_embedding_dims
    from contextseek.config.settings import EmbeddingSettings, LLMSettings

    llm_cfg = _flat_get(effective, "llm", {}) or {}
    emb_cfg = _flat_get(effective, "embedding", {}) or {}
    storage_cfg = _flat_get(effective, "storage", {}) or {}
    runtime_cfg = _flat_get(effective, "runtime", {}) or {}
    lifecycle_cfg = _flat_get(effective, "lifecycle", {}) or {}

    llm_model = str(llm_cfg.get("model", "") or "")
    llm_provider = str(llm_cfg.get("provider", "none") or "none")
    emb_model = str(emb_cfg.get("model", "") or "")
    emb_provider = str(emb_cfg.get("provider", "none") or "none")
    emb_dims_raw = emb_cfg.get("dims", 0)
    try:
        emb_dims = int(emb_dims_raw)
    except (TypeError, ValueError):
        emb_dims = 0
    emb_dims = emb_dims or resolve_embedding_dims(EmbeddingSettings(**emb_cfg))

    result: dict[str, Any] = {
        "storage_backend": str(storage_cfg.get("backend", "sqlite")),
        "llm_provider": llm_provider,
        "llm_model": llm_model or llm_provider,
        "llm_base_url": str(llm_cfg.get("base_url", "") or ""),
        "llm_api_key": str(
            (llm_cfg.get("kwargs", {}) or {}).get("api_key")
            or llm_cfg.get("api_key")
            or ""
        ),
        "embedding_provider": emb_provider,
        "embedding_model": emb_model or emb_provider,
        "embedding_dims": str(emb_dims) if emb_dims else "",
        "embedding_base_url": str(emb_cfg.get("base_url", "") or ""),
        "embedding_api_key": str(
            (emb_cfg.get("kwargs", {}) or {}).get("api_key")
            or emb_cfg.get("api_key")
            or ""
        ),
        "default_scope": str(_flat_get(effective, "default_scope", "default")),
        "version": PACKAGE_VERSION,
        "auto_sync": bool(lifecycle_cfg.get("auto_compact", False)),
        "lifecycle_interval_seconds": int(lifecycle_cfg.get("interval_seconds", 300)),
        "watch_paths": _flat_get(effective, "watch.paths", []) or [],
    }

    backend = result["storage_backend"]
    if backend == "oceanbase":
        result["ob_host"] = str(_flat_get(effective, "ob.host", "") or "")
        result["ob_port"] = str(_flat_get(effective, "ob.port", "") or "")
        result["ob_db_name"] = str(_flat_get(effective, "ob.db_name", "") or "")
        result["ob_table_name"] = str(_flat_get(effective, "ob.table_name", "") or "")
    elif backend == "seekdb":
        seekdb_host = str(_flat_get(effective, "seekdb.host", "") or "")
        if seekdb_host:
            result["seekdb_mode"] = "server"
            result["seekdb_host"] = seekdb_host
            result["seekdb_port"] = str(_flat_get(effective, "seekdb.port", "2881"))
            result["seekdb_database"] = str(
                _flat_get(effective, "seekdb.database", "contextseek")
            )
        else:
            result["seekdb_mode"] = "embedded"
            result["seekdb_path"] = str(_flat_get(effective, "seekdb.path", ""))
    elif backend == "sqlite":
        result["sqlite_path"] = str(_flat_get(effective, "sqlite.path", "") or "")
    elif backend == "file":
        result["storage_path"] = str(_flat_get(effective, "storage.path", "") or "")

    # Runtime config can override backend-specific fields for non-settings keys.
    if backend == "file" and runtime_cfg.get("storage_path"):
        result["storage_path"] = str(runtime_cfg.get("storage_path"))

    # Validate shapes for fields expected by the existing dashboard forms.
    LLMSettings(**llm_cfg)
    EmbeddingSettings(**emb_cfg)
    return result


def register_config_routes(app: Any, *, config_dir: Path) -> None:
    """Register versioned config routes on ``app``."""
    from contextseek.config.envreflector import env_to_section_field

    reverse = env_to_section_field()
    dotted_to_flat: dict[str, str] = {}

    def _flat_field_to_dotted(field_name: str) -> str | None:
        # API keys live inside the dict-valued kwargs field — route them to the
        # dotted path into that dict instead of the (non-existent) env leaf.
        if field_name in FLAT_TO_DOTTED_OVERRIDE:
            return FLAT_TO_DOTTED_OVERRIDE[field_name]
        env = FIELD_TO_ENV.get(field_name)
        if env is None or env not in reverse:
            return None
        section, field = reverse[env]
        return f"{section}.{field}"

    for field_name in FIELD_TO_ENV:
        dotted = _flat_field_to_dotted(field_name)
        if dotted:
            dotted_to_flat[dotted] = field_name

    @app.get("/config/ingest/agentseek/check")
    async def ingest_agentseek_check() -> dict[str, Any]:
        required = ["AGENTSEEK_API_KEY", "AGENTSEEK_MODEL", "AGENTSEEK_CTX_LLM_PROVIDER"]
        present = [k for k in required if os.environ.get(k)]
        return {
            "required": required,
            "present": present,
            "missing": [k for k in required if k not in present],
            "ready": len(present) == len(required),
        }

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        mgr = _manager(config_dir)
        _ensure_migrated(mgr)
        cur = mgr.current()
        effective = cur.payload.get("effective", {}) if cur else {}
        snapshot = _build_snapshot_from_effective(effective)
        flat_sources: dict[str, str] = {}
        if cur:
            for dotted_key, source in cur.override_sources.items():
                flat_key = dotted_to_flat.get(dotted_key)
                if flat_key:
                    flat_sources[flat_key] = source
        snapshot["config_version"] = cur.version_id if cur else None
        snapshot["override_sources"] = flat_sources
        snapshot["drift"] = _materializer().detect_drift(effective)
        st = mgr.status()
        snapshot["agentseek_source_ref"] = st.get("agentseek_source_ref")
        snapshot["agentseek_stale"] = st.get("agentseek_stale", True)
        return snapshot

    @app.put("/config")
    async def update_config(req: dict[str, Any]) -> dict[str, Any]:
        mgr = _manager(config_dir)
        _ensure_migrated(mgr)
        # Preserve embedding-provider validation UX (see _normalize_embedding_updates).
        _normalize_embedding_updates(req)
        updates: dict[str, str] = {}
        for field_name, val in req.items():
            if val is None:
                continue
            if field_name == "embedding_dims" and str(val).strip() == "":
                val = "0"
            dotted = _flat_field_to_dotted(field_name)
            if dotted:
                updates[dotted] = str(val)
        cur = mgr.current()
        if not updates:
            return {
                "status": "ok",
                "version_id": cur.version_id if cur else None,
                "restart_required": False,
            }
        v = mgr.set_native_many(updates, author="dashboard", reason="dashboard edit")
        mgr.apply(_materializer())
        return {"status": "ok", "version_id": v.version_id, "restart_required": True}

    @app.get("/config/history")
    async def history(n: int | None = None) -> list[dict[str, Any]]:
        mgr = _manager(config_dir)
        return [
            {
                "version_id": v.version_id,
                "parent_version_id": v.parent_version_id,
                "created_at": v.created_at,
                "origin": v.origin,
                "author": v.author,
                "reason": v.reason,
                "rollback_target_version_id": v.rollback_target_version_id,
            }
            for v in mgr.history(n=n)
        ]

    @app.get("/config/history/page")
    async def history_page(offset: int = 0, limit: int = 20) -> dict[str, Any]:
        mgr = _manager(config_dir)
        if offset < 0:
            offset = 0
        if limit <= 0:
            limit = 20
        records = mgr.history()
        page = records[offset : offset + limit]
        return {
            "offset": offset,
            "limit": limit,
            "total": len(records),
            "items": [
                {
                    "version_id": v.version_id,
                    "parent_version_id": v.parent_version_id,
                    "created_at": v.created_at,
                    "origin": v.origin,
                    "author": v.author,
                    "reason": v.reason,
                    "rollback_target_version_id": v.rollback_target_version_id,
                }
                for v in page
            ],
        }

    @app.get("/config/version/{version_id}")
    async def version_detail(
        version_id: str, layer: str = "effective"
    ) -> dict[str, Any]:
        mgr = _manager(config_dir)
        v = mgr.get_version(version_id)
        return v.payload.get(layer, {})

    @app.get("/config/diff")
    async def diff(a: str, b: str) -> dict[str, Any]:
        mgr = _manager(config_dir)
        return mgr.diff(a, b)

    @app.get("/config/blame")
    async def blame(key: str) -> dict[str, Any]:
        mgr = _manager(config_dir)
        info = mgr.blame(key)
        return info or {}

    @app.post("/config/rollback")
    async def rollback(req: dict[str, Any]) -> dict[str, Any]:
        mgr = _manager(config_dir)
        v = mgr.rollback(
            req["version"], author="dashboard", reason=req.get("reason", "rollback")
        )
        mgr.apply(_materializer())
        return {
            "version_id": v.version_id,
            "restart_required": True,
            "rollback_target_version_id": v.rollback_target_version_id,
        }

    @app.post("/config/redo")
    async def redo(req: dict[str, Any]) -> dict[str, Any]:
        mgr = _manager(config_dir)
        v = mgr.redo(author="dashboard", reason=req.get("reason", "redo"))
        if v is None:
            return {"version_id": None, "restart_required": False}
        # Mirror rollback: a redo commits a new version but must also
        # materialize it, otherwise a server restart would load the
        # rolled-back state (silent divergence).
        mgr.apply(_materializer())
        return {"version_id": v.version_id, "restart_required": True}

    @app.get("/config/status")
    async def status() -> dict[str, Any]:
        mgr = _manager(config_dir)
        st = mgr.status()
        cur = mgr.current()
        st["drift"] = _materializer().detect_drift(
            cur.payload.get("effective", {}) if cur else {}
        )
        st["verify_problems"] = mgr.verify()
        return st

    @app.get("/config/verify")
    async def verify() -> dict[str, Any]:
        mgr = _manager(config_dir)
        problems = mgr.verify()
        return {"ok": not problems, "problems": problems}

    @app.post("/config/ingest/agentseek")
    async def ingest_agentseek(req: dict[str, Any]) -> dict[str, Any]:
        mgr = _manager(config_dir)
        ing = AgentseekIngestor(mgr)
        if req.get("path"):
            v = ing.ingest_file(Path(req["path"]), author="dashboard")
        else:
            v = ing.ingest_env(dict(os.environ), author="dashboard")
        if v is None:
            return {"version_id": None, "source_ref": None}
        if req.get("apply"):
            mgr.apply(_materializer())
        return {"version_id": v.version_id, "source_ref": v.source_ref}
