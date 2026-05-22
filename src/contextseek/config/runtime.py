"""Runtime configuration loading for ContextSeek services."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from seekvfs import VFS

from contextseek.storage.file_backend import FileBackend
from contextseek.storage.in_memory_backend import InMemoryBackend
from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.storage.storage_adapter import SeekVFSStorageAdapter
from contextseek.storage.tiered_adapter import TieredSeekVFSAdapter
from contextseek.config.strategies import (
    EvolutionStrategy,
    LifecycleStrategy,
    ObservabilityStrategy,
    RetrievalStrategy,
    StrategyConfig,
    WriteStrategy,
)
from contextseek.observability.audit import AuditLog


CONFIG_ENV = "SEEKCONTEXT_CONFIG"
HOME_ENV = "SEEKCONTEXT_HOME"


@dataclass(frozen=True)
class ApiKeyPolicy:
    """Authorization policy bound to one API key."""

    tenant_id: str
    subjects: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    allow_observability: bool = True
    allow_write: bool = True
    allow_delete: bool = True
    enabled: bool = True
    expires_at: str | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime options for service entrypoints."""

    backend: str = "file"
    storage_path: str = ".contextseek/store"
    uri_scheme: str = "contextseek://"
    cold_backend: str | None = None
    cold_storage_path: str = ".contextseek/cold"
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    api_keys: dict[str, ApiKeyPolicy] = field(default_factory=dict)
    # OceanBase connection params (used when backend="oceanbase")
    ob_host: str = "127.0.0.1"
    ob_port: str = "2881"
    ob_user: str = "root@test"
    ob_password: str = ""
    ob_db_name: str = "contextseek"
    ob_table_name: str = "contextseek_items"
    ob_vector_dims: int = 0


def load_runtime_config(path: str | Path | None = None) -> RuntimeConfig:
    """Load runtime configuration from JSON, falling back to persistent local defaults."""
    raw_path = path if path is not None else os.environ.get(CONFIG_ENV)
    if not raw_path:
        return _with_home(RuntimeConfig())
    config_path = Path(raw_path)
    if not config_path.exists():
        return _with_home(RuntimeConfig())
    if not config_path.is_file():
        return _with_home(RuntimeConfig())
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    strategy_payload = dict(payload.get("strategy", {}))
    config = RuntimeConfig(
        backend=str(payload.get("backend", "file")),
        storage_path=str(payload.get("storage_path", ".contextseek/store")),
        uri_scheme=str(payload.get("uri_scheme", "contextseek://")),
        cold_backend=payload.get("cold_backend"),
        cold_storage_path=str(payload.get("cold_storage_path", ".contextseek/cold")),
        strategy=_strategy_from_dict(strategy_payload),
        api_keys=normalize_api_keys(dict(payload.get("api_keys", {}))),
        ob_host=str(payload.get("ob_host", "127.0.0.1")),
        ob_port=str(payload.get("ob_port", "2881")),
        ob_user=str(payload.get("ob_user", "root@test")),
        ob_password=str(payload.get("ob_password", "")),
        ob_db_name=str(payload.get("ob_db_name", "contextseek")),
        ob_table_name=str(payload.get("ob_table_name", "contextseek_items")),
        ob_vector_dims=int(payload.get("ob_vector_dims", 0)),
    )
    return _with_home(config)


def build_adapter(config: RuntimeConfig) -> SeekVFSAdapter:
    """Build a VFS-backed storage adapter from runtime config."""
    if config.backend == "memory":
        backend = InMemoryBackend()
    elif config.backend == "file":
        backend = FileBackend(config.storage_path, scheme=config.uri_scheme)
        backend.initialize()
    elif config.backend == "oceanbase":
        from contextseek.storage.ob_backend import OceanBaseBackend

        if not config.ob_vector_dims:
            msg = "ob_vector_dims must be set when backend=oceanbase"
            raise ValueError(msg)
        backend = OceanBaseBackend(
            table_name=config.ob_table_name,
            vector_dims=config.ob_vector_dims,
            host=config.ob_host,
            port=config.ob_port,
            user=config.ob_user,
            password=config.ob_password,
            db_name=config.ob_db_name,
        )
        backend.initialize()
    else:
        msg = f"unsupported backend: {config.backend}"
        raise ValueError(msg)
    vfs = VFS(
        routes={config.uri_scheme: {"backend": backend}}, scheme=config.uri_scheme
    )
    hot_adapter: SeekVFSStorageAdapter = SeekVFSStorageAdapter(vfs)
    if config.cold_backend:
        if config.cold_backend == "memory":
            cold_backend = InMemoryBackend()
        elif config.cold_backend == "file":
            cold_backend = FileBackend(
                config.cold_storage_path, scheme=config.uri_scheme
            )
            cold_backend.initialize()
        else:
            msg = f"unsupported cold backend: {config.cold_backend}"
            raise ValueError(msg)
        cold_vfs = VFS(
            routes={config.uri_scheme: {"backend": cold_backend}},
            scheme=config.uri_scheme,
        )
        cold_adapter = SeekVFSStorageAdapter(cold_vfs)
        return TieredSeekVFSAdapter(hot_adapter, cold_adapter)
    return hot_adapter


def build_audit_log(config: RuntimeConfig) -> AuditLog:
    """Build the audit log configured for this runtime."""
    audit_path = config.strategy.observability.audit_path
    metrics_path = config.strategy.observability.metrics_path
    if config.strategy.observability.persist_audit:
        return AuditLog(persist_path=audit_path, metrics_path=metrics_path)
    return AuditLog(metrics_path=metrics_path)


def _with_home(config: RuntimeConfig) -> RuntimeConfig:
    home = os.environ.get(HOME_ENV)
    if not home:
        return config
    root = Path(home)
    strategy = config.strategy
    observability = strategy.observability
    if not Path(config.storage_path).is_absolute():
        storage_path = str(root / config.storage_path)
    else:
        storage_path = config.storage_path
    cold_storage_path = config.cold_storage_path
    if not Path(cold_storage_path).is_absolute():
        cold_storage_path = str(root / cold_storage_path)
    audit_path = observability.audit_path
    metrics_path = observability.metrics_path
    if not Path(audit_path).is_absolute():
        audit_path = str(root / audit_path)
    if not Path(metrics_path).is_absolute():
        metrics_path = str(root / metrics_path)
    strategy = StrategyConfig(
        version=strategy.version,
        retrieval=strategy.retrieval,
        evolution=strategy.evolution,
        write=strategy.write,
        observability=ObservabilityStrategy(
            persist_audit=observability.persist_audit,
            audit_path=audit_path,
            metrics_path=metrics_path,
        ),
        lifecycle=strategy.lifecycle,
    )
    return RuntimeConfig(
        backend=config.backend,
        storage_path=storage_path,
        uri_scheme=config.uri_scheme,
        cold_backend=config.cold_backend,
        cold_storage_path=cold_storage_path,
        strategy=strategy,
        api_keys=config.api_keys,
    )


def _strategy_from_dict(payload: dict[str, Any]) -> StrategyConfig:
    return StrategyConfig(
        version=str(payload.get("version", "v1")),
        retrieval=RetrievalStrategy(**dict(payload.get("retrieval", {}))),
        evolution=EvolutionStrategy(**dict(payload.get("evolution", {}))),
        write=WriteStrategy(**dict(payload.get("write", {}))),
        observability=ObservabilityStrategy(**dict(payload.get("observability", {}))),
        lifecycle=LifecycleStrategy(**dict(payload.get("lifecycle", {}))),
    )


def normalize_api_keys(raw: dict[str, Any]) -> dict[str, ApiKeyPolicy]:
    """Normalize runtime api_keys payload to ApiKeyPolicy instances."""
    policies: dict[str, ApiKeyPolicy] = {}
    for key, value in raw.items():
        api_key = str(key)
        if isinstance(value, ApiKeyPolicy):
            policies[api_key] = value
            continue
        if isinstance(value, str):
            policies[api_key] = ApiKeyPolicy(tenant_id=value)
            continue
        if isinstance(value, dict):
            tenant_id = str(value.get("tenant_id", "")).strip()
            if not tenant_id:
                msg = f"api key policy missing tenant_id for key {api_key}"
                raise ValueError(msg)
            template = str(value.get("template", "") or value.get("role", "")).strip()
            allow_write = bool(value.get("allow_write", True))
            allow_delete = bool(value.get("allow_delete", True))
            allow_observability = bool(value.get("allow_observability", True))
            if template:
                lowered = template.lower()
                if lowered in {"read_only", "readonly"}:
                    allow_write = False
                    allow_delete = False
                elif lowered in {"read_write", "writer"}:
                    allow_write = True
                    allow_delete = False
                elif lowered in {"admin"}:
                    allow_write = True
                    allow_delete = True
                    allow_observability = True
            subjects = tuple(
                str(item) for item in value.get("subjects", []) if str(item)
            )
            scopes = tuple(str(item) for item in value.get("scopes", []) if str(item))
            policies[api_key] = ApiKeyPolicy(
                tenant_id=tenant_id,
                subjects=subjects,
                scopes=scopes,
                allow_observability=allow_observability,
                allow_write=allow_write,
                allow_delete=allow_delete,
                enabled=bool(value.get("enabled", True)),
                expires_at=str(value.get("expires_at") or "") or None,
            )
            continue
        msg = f"invalid api key policy for key {api_key}"
        raise ValueError(msg)
    return policies
