"""Config-management HTTP routes (versioned store + dashboard integration)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from contextseek.config.agentseek_ingestor import AgentseekIngestor
from contextseek.config.manager import ConfigManager
from contextseek.config.materializer import Materializer
from contextseek.config.migrator import migrate_into

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


def register_config_routes(app: Any, *, config_dir: Path) -> None:
    """Register versioned config routes on ``app``."""
    from contextseek.config.envreflector import env_to_section_field

    reverse = env_to_section_field()

    def _flat_field_to_dotted(field_name: str) -> str | None:
        env = FIELD_TO_ENV.get(field_name)
        if env is None or env not in reverse:
            return None
        section, field = reverse[env]
        return f"{section}.{field}"

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        mgr = _manager(config_dir)
        _ensure_migrated(mgr)
        cur = mgr.current()
        # Preserve the existing flat Config shape by reading live settings for
        # backend-specific fields, then enrich with version metadata.
        from contextseek.http.server import _build_config_snapshot

        snapshot = _build_config_snapshot()
        snapshot["config_version"] = cur.version_id if cur else None
        snapshot["override_sources"] = cur.override_sources if cur else {}
        snapshot["drift"] = mgr.status().get("drift", {"env": False, "runtime": False})
        # agentseek source staleness
        agentseek_ref = None
        for v in mgr.history():
            if v.origin == "agentseek-projection":
                agentseek_ref = v.source_ref
                break
        snapshot["agentseek_source_ref"] = agentseek_ref
        snapshot["agentseek_stale"] = agentseek_ref is None  # no projection yet
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
            }
            for v in mgr.history(n=n)
        ]

    @app.get("/config/version/{version_id}")
    async def version_detail(version_id: str, layer: str = "effective") -> dict[str, Any]:
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
        v = mgr.rollback(req["version"], author="dashboard", reason=req.get("reason", "rollback"))
        mgr.apply(_materializer())
        return {"version_id": v.version_id, "restart_required": True}

    @app.post("/config/redo")
    async def redo(req: dict[str, Any]) -> dict[str, Any]:
        mgr = _manager(config_dir)
        v = mgr.redo(author="dashboard", reason=req.get("reason", "redo"))
        if v is None:
            return {"version_id": None, "restart_required": False}
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
