"""Managed PowerMem environment for ContextSeek linkers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_POWERMEM_ENV_PATH_ENV = "CONTEXTSEEK_POWERMEM_ENV_FILE"
_DEFAULT_SQLITE_PATH = "~/.contextseek/plugs/powermem.sqlite3"
_DEFAULT_LOCAL_EMBEDDING_PROVIDER = "huggingface"
_DEFAULT_LOCAL_EMBEDDING_MODEL = "multi-qa-MiniLM-L6-cos-v1"
_DEFAULT_LOCAL_EMBEDDING_DIMS = "384"
_EMBEDDING_PROVIDER_MAP = {
    "dashscope": "qwen",
    "huggingface": "huggingface",
    "lmstudio": "lmstudio",
    "mock": "mock",
    "ollama": "ollama",
    "openai": "openai",
    "qwen": "qwen",
    "siliconflow": "siliconflow",
}
_LLM_PROVIDER_MAP = {
    "anthropic": "anthropic",
    "dashscope": "qwen",
    "deepseek": "deepseek",
    "ollama": "ollama",
    "openai": "openai",
    "qwen": "qwen",
    "siliconflow": "siliconflow",
    "vllm": "vllm",
}
_POWERMEM_OVERRIDE_KEYS = {
    "DATABASE_PROVIDER",
    "SQLITE_PATH",
    "SQLITE_COLLECTION",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMS",
    "EMBEDDING_API_KEY",
}
_POWERMEM_CHILD_ENV_ISOLATION_KEYS = {
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_KWARGS",
    "LLM_BASE_URL",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMS",
    "EMBEDDING_API_KEY",
    "EMBEDDING_KWARGS",
    "EMBEDDING_BASE_URL",
}


@dataclass(frozen=True)
class PowerMemEnvResult:
    """Result of preparing the managed PowerMem env file."""

    changed: bool
    dry_run: bool
    path: Path
    actions: list[str]
    warnings: list[str]


def managed_powermem_env_path() -> Path:
    """Return the ContextSeek-managed PowerMem env path."""
    configured = os.environ.get(_POWERMEM_ENV_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".contextseek" / "plugs" / "powermem.env"


def powermem_child_process_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the official PowerMem child-process env.

    PowerMem loads ``POWERMEM_ENV_FILE`` with dotenv ``override=False``. Remove
    provider keys from the parent environment so ContextSeek/project ``.env``
    values do not shadow the managed PowerMem config or PowerMem defaults.
    """
    env = dict(base or os.environ)
    env_file = Path(
        env.get(_POWERMEM_ENV_PATH_ENV, "") or str(managed_powermem_env_path()),
    ).expanduser()
    managed_values = read_env_file(env_file)
    for key in managed_values.keys() | _POWERMEM_CHILD_ENV_ISOLATION_KEYS:
        env.pop(key, None)
    env.update(managed_values)
    env["POWERMEM_ENV_FILE"] = str(env_file)
    env.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
    return env


def powermem_child_process_cwd() -> Path:
    """Return a safe cwd for PowerMem child processes.

    PowerMem auto-loads ``Path.cwd() / ".env"``. Running it from the ContextSeek
    project can accidentally load ContextSeek's own provider settings.
    """
    cwd = managed_powermem_env_path().parent
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def ensure_managed_powermem_env(
    *,
    dry_run: bool = False,
    check: bool = False,
    extra_defaults: Mapping[str, str] | None = None,
) -> PowerMemEnvResult:
    """Create or update the managed PowerMem env without overwriting user values."""
    path = managed_powermem_env_path()
    existing = read_env_file(path)
    raw = _contextseek_raw_env()
    defaults = build_powermem_env_defaults(raw)
    if extra_defaults:
        defaults.update(
            {key: value for key, value in extra_defaults.items() if value != ""}
        )
    additions = {
        key: value
        for key, value in defaults.items()
        if key not in existing and value != ""
    }
    changed = bool(additions) or not path.exists()
    actions = [
        f"prepare managed PowerMem env: {path}",
    ]
    if additions:
        actions.append(
            "fill PowerMem env from ContextSeek env: " + ", ".join(sorted(additions)),
        )

    merged = {**defaults, **existing}
    warnings = manual_field_warnings(merged, raw=raw)
    if check or dry_run:
        return PowerMemEnvResult(
            changed=changed,
            dry_run=True,
            path=path,
            actions=actions,
            warnings=warnings,
        )

    if changed:
        _write_env_preserving_existing(path, additions)
    return PowerMemEnvResult(
        changed=changed,
        dry_run=False,
        path=path,
        actions=actions,
        warnings=warnings,
    )


def build_powermem_env_defaults(
    raw: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Infer PowerMem env values from ContextSeek env/config when possible."""
    raw = dict(raw or _contextseek_raw_env())
    values: dict[str, str] = {
        "DATABASE_PROVIDER": "sqlite",
        "SQLITE_PATH": _expand_user_value(_DEFAULT_SQLITE_PATH),
        "SQLITE_ENABLE_WAL": raw.get("SQLITE_ENABLE_WAL", "true"),
        "SQLITE_TIMEOUT": raw.get("SQLITE_TIMEOUT", "30"),
        "SQLITE_COLLECTION": raw.get("SQLITE_COLLECTION", "memories"),
        "TIMEZONE": raw.get("TIMEZONE", "Asia/Shanghai"),
        "AGENT_ENABLED": raw.get("AGENT_ENABLED", "true"),
        "AGENT_MEMORY_MODE": raw.get("AGENT_MEMORY_MODE", "auto"),
        "INTELLIGENT_MEMORY_ENABLED": raw.get("INTELLIGENT_MEMORY_ENABLED", "true"),
    }
    _fill_embedding(values, raw)
    _fill_llm(values, raw)
    _fill_storage(values, raw)
    _apply_prefixed_powermem_overrides(values, raw)
    return values


def manual_field_warnings(
    values: dict[str, str],
    *,
    raw: Mapping[str, str] | None = None,
) -> list[str]:
    """Return concise warnings for PowerMem settings ContextSeek cannot infer."""
    warnings: list[str] = []
    raw_values = dict(raw or {})
    embedding_provider = values.get("EMBEDDING_PROVIDER", "").strip()
    contextseek_embedding_provider = (
        raw_values.get("EMBEDDING_PROVIDER", "").strip().lower()
    )
    if (
        not embedding_provider
        and contextseek_embedding_provider
        and contextseek_embedding_provider != "none"
    ):
        warnings.append("PowerMem EMBEDDING_PROVIDER cannot be inferred")
    if (
        embedding_provider
        and embedding_provider != "mock"
        and not values.get("EMBEDDING_MODEL", "").strip()
    ):
        warnings.append("PowerMem EMBEDDING_MODEL cannot be inferred")
    if (
        embedding_provider
        and embedding_provider != "mock"
        and not values.get("EMBEDDING_DIMS", "").strip()
    ):
        warnings.append("PowerMem EMBEDDING_DIMS cannot be inferred")
    if (
        _provider_needs_api_key(embedding_provider)
        and not values.get(
            "EMBEDDING_API_KEY",
            "",
        ).strip()
    ):
        warnings.append("PowerMem EMBEDDING_API_KEY cannot be inferred")

    llm_provider = values.get("LLM_PROVIDER", "").strip()
    contextseek_llm_provider = raw_values.get("LLM_PROVIDER", "").strip().lower()
    if not llm_provider and contextseek_llm_provider != "none":
        warnings.append("PowerMem LLM_PROVIDER cannot be inferred")
    if (
        llm_provider
        and llm_provider != "none"
        and not values.get("LLM_MODEL", "").strip()
    ):
        warnings.append("PowerMem LLM_MODEL cannot be inferred")
    if (
        _provider_needs_api_key(llm_provider)
        and not values.get("LLM_API_KEY", "").strip()
    ):
        warnings.append("PowerMem LLM_API_KEY cannot be inferred")

    if values.get("DATABASE_PROVIDER") == "oceanbase":
        host = values.get("OCEANBASE_HOST", "").strip()
        password = values.get("OCEANBASE_PASSWORD", "").strip()
        if host and not password:
            warnings.append("PowerMem OCEANBASE_PASSWORD cannot be inferred")
    return warnings


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE env file."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _contextseek_raw_env() -> dict[str, str]:
    values: dict[str, str] = {}
    config = os.environ.get("CONTEXTSEEK_CONFIG", "").strip()
    if config:
        values.update(read_env_file(Path(config).expanduser()))
    else:
        for candidate in (
            Path.cwd() / ".env",
            Path.home() / ".contextseek" / "config.env",
        ):
            if candidate.is_file():
                values.update(read_env_file(candidate))
                break
    values.update(
        {key: value for key, value in os.environ.items() if value is not None}
    )
    return values


def _fill_storage(values: dict[str, str], raw: dict[str, str]) -> None:
    backend = raw.get("STORAGE_BACKEND", "").strip().lower()
    if backend == "sqlite":
        values["DATABASE_PROVIDER"] = "sqlite"
        values["SQLITE_PATH"] = _expand_user_value(
            raw.get("SQLITE_PATH") or raw.get("STORAGE_PATH") or _DEFAULT_SQLITE_PATH,
        )
        return
    if backend == "seekdb":
        values["DATABASE_PROVIDER"] = "sqlite"
        values["SQLITE_PATH"] = _expand_user_value(_DEFAULT_SQLITE_PATH)
        return
    if backend == "oceanbase":
        values["DATABASE_PROVIDER"] = "oceanbase"
        values["OCEANBASE_HOST"] = raw.get("OB_HOST", "127.0.0.1")
        values["OCEANBASE_PORT"] = raw.get("OB_PORT", "2881")
        values["OCEANBASE_USER"] = raw.get("OB_USER", "root@test")
        values["OCEANBASE_PASSWORD"] = raw.get("OB_PASSWORD", "")
        values["OCEANBASE_DATABASE"] = raw.get("OB_DB_NAME", "test")
        values["OCEANBASE_COLLECTION"] = raw.get(
            "OB_TABLE_NAME",
            raw.get("OCEANBASE_COLLECTION", "memories"),
        )
        _copy_if_present(
            values, raw, "EMBEDDING_DIMS", "OCEANBASE_EMBEDDING_MODEL_DIMS"
        )


def _fill_embedding(values: dict[str, str], raw: dict[str, str]) -> None:
    configured_provider = raw.get("EMBEDDING_PROVIDER", "").strip().lower()
    if configured_provider == "none":
        return
    provider = _resolve_provider(
        raw,
        provider_key="EMBEDDING_PROVIDER",
        class_path_key="EMBEDDING_CLASS_PATH",
        provider_map=_EMBEDDING_PROVIDER_MAP,
    )
    if not provider:
        if configured_provider == "langchain":
            return
        values["EMBEDDING_PROVIDER"] = _DEFAULT_LOCAL_EMBEDDING_PROVIDER
        values["EMBEDDING_MODEL"] = _DEFAULT_LOCAL_EMBEDDING_MODEL
        values["EMBEDDING_DIMS"] = _DEFAULT_LOCAL_EMBEDDING_DIMS
        return

    values["EMBEDDING_PROVIDER"] = provider
    _copy_if_present(values, raw, "EMBEDDING_MODEL")
    _copy_if_present(values, raw, "EMBEDDING_DIMS")
    api_key = _api_key_from_contextseek_config(
        raw,
        explicit_key="EMBEDDING_API_KEY",
        kwargs_key="EMBEDDING_KWARGS",
        provider=provider,
        configured_provider=configured_provider,
    )
    if not api_key:
        api_key = raw.get("DASHSCOPE_API_KEY", "")
    if api_key:
        values["EMBEDDING_API_KEY"] = api_key
    _copy_provider_base_url(
        values,
        raw,
        provider=provider,
        generic_key="EMBEDDING_BASE_URL",
        target_suffix="EMBEDDING_BASE_URL",
    )


def _fill_llm(values: dict[str, str], raw: dict[str, str]) -> None:
    configured_provider = raw.get("LLM_PROVIDER", "").strip().lower()
    if configured_provider == "none":
        return
    if not configured_provider:
        return
    provider = _resolve_provider(
        raw,
        provider_key="LLM_PROVIDER",
        class_path_key="LLM_CLASS_PATH",
        provider_map=_LLM_PROVIDER_MAP,
    )
    if not provider:
        return
    values["LLM_PROVIDER"] = provider
    if raw.get("LLM_MODEL"):
        values["LLM_MODEL"] = raw["LLM_MODEL"]
    api_key = _api_key_from_contextseek_config(
        raw,
        explicit_key="LLM_API_KEY",
        kwargs_key="LLM_KWARGS",
        provider=provider,
        configured_provider=configured_provider,
    )
    if api_key:
        values["LLM_API_KEY"] = api_key
    _copy_if_present(values, raw, "LLM_TEMPERATURE")
    _copy_if_present(values, raw, "LLM_MAX_TOKENS")
    _copy_if_present(values, raw, "LLM_TOP_P")
    _copy_if_present(values, raw, "LLM_TOP_K")
    _copy_provider_base_url(
        values,
        raw,
        provider=provider,
        generic_key="LLM_BASE_URL",
        target_suffix="LLM_BASE_URL",
    )


def _normalize_provider(value: str, provider_map: dict[str, str]) -> str:
    provider = value.strip().lower()
    return provider_map.get(provider, "")


def _resolve_provider(
    raw: dict[str, str],
    *,
    provider_key: str,
    class_path_key: str,
    provider_map: dict[str, str],
) -> str:
    configured = raw.get(provider_key, "").strip().lower()
    provider = _normalize_provider(configured, provider_map)
    if provider:
        return provider
    if configured and configured != "langchain":
        return ""
    class_path = raw.get(class_path_key, "").strip().lower()
    if "openai" in class_path:
        return "openai"
    if "dashscope" in class_path or "qwen" in class_path or "tongyi" in class_path:
        return "qwen"
    if "anthropic" in class_path:
        return "anthropic"
    if "deepseek" in class_path:
        return "deepseek"
    if "ollama" in class_path:
        return "ollama"
    if "siliconflow" in class_path:
        return "siliconflow"
    if configured == "langchain":
        return _infer_langchain_provider(raw, provider_key=provider_key)
    return ""


def _infer_langchain_provider(raw: dict[str, str], *, provider_key: str) -> str:
    model_key = provider_key.replace("_PROVIDER", "_MODEL")
    base_url_key = provider_key.replace("_PROVIDER", "_BASE_URL")
    kwargs_key = provider_key.replace("_PROVIDER", "_KWARGS")
    model = raw.get(model_key, "").strip().lower()
    base_url = (
        raw.get(base_url_key, "").strip()
        or _kwargs_value(raw, kwargs_key, "base_url", "openai_api_base")
    ).lower()

    if "dashscope" in base_url or "aliyuncs" in base_url:
        return "qwen"

    if model.startswith("qwen-") and _provider_api_key(raw, "qwen"):
        return "qwen"

    if provider_key == "EMBEDDING_PROVIDER":
        if model.startswith("text-embedding-v") and _provider_api_key(raw, "qwen"):
            return "qwen"
        if model.startswith("text-embedding-3-") and (
            _provider_api_key(raw, "openai") or base_url
        ):
            return "openai"
        return ""

    if provider_key == "LLM_PROVIDER" and _is_openai_llm_model(model):
        if _provider_api_key(raw, "openai") or base_url:
            return "openai"
    return ""


def _is_openai_llm_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4"))


def _kwargs_value(raw: dict[str, str], env_key: str, *names: str) -> str:
    payload = _json_env_object(raw.get(env_key, ""))
    for name in names:
        value = payload.get(name)
        if value:
            return str(value)
    return ""


def _api_key_from_contextseek_config(
    raw: dict[str, str],
    *,
    explicit_key: str,
    kwargs_key: str,
    provider: str,
    configured_provider: str,
) -> str:
    explicit = raw.get(explicit_key, "")
    provider_key = _provider_api_key(raw, provider)
    kwargs_key_value = _kwargs_value(raw, kwargs_key, "api_key", "openai_api_key")
    if configured_provider == "langchain":
        return explicit or kwargs_key_value or provider_key
    if configured_provider == "none":
        return explicit or provider_key
    return explicit or provider_key or kwargs_key_value


def _json_env_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        return {}
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _provider_api_key(raw: dict[str, str], provider: str) -> str:
    keys = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "qwen": ("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
        "siliconflow": ("SILICONFLOW_API_KEY",),
        "vllm": ("VLLM_API_KEY",),
    }
    for key in keys.get(provider, ()):
        value = raw.get(key)
        if value:
            return value
    return ""


def _copy_provider_base_url(
    values: dict[str, str],
    raw: dict[str, str],
    *,
    provider: str,
    generic_key: str,
    target_suffix: str,
) -> None:
    prefixes = {
        "anthropic": "ANTHROPIC",
        "deepseek": "DEEPSEEK",
        "huggingface": "HUGGINGFACE",
        "lmstudio": "LMSTUDIO",
        "ollama": "OLLAMA",
        "openai": "OPENAI",
        "qwen": "QWEN",
        "siliconflow": "SILICONFLOW",
        "vllm": "VLLM",
    }
    prefix = prefixes.get(provider)
    if not prefix:
        return
    target_key = f"{prefix}_{target_suffix}"
    if raw.get(target_key):
        values[target_key] = raw[target_key]
    elif raw.get(generic_key):
        values[target_key] = raw[generic_key]


def _copy_if_present(
    values: dict[str, str],
    raw: dict[str, str],
    source_key: str,
    target_key: str | None = None,
) -> None:
    value = raw.get(source_key)
    if value not in (None, ""):
        values[target_key or source_key] = value


def _apply_prefixed_powermem_overrides(
    values: dict[str, str],
    raw: dict[str, str],
) -> None:
    for key in _POWERMEM_OVERRIDE_KEYS:
        for source_key in (f"CONTEXTSEEK_POWERMEM_{key}", f"POWERMEM_{key}"):
            value = raw.get(source_key)
            if value not in (None, ""):
                values[key] = value
                break


def _provider_needs_api_key(provider: str) -> bool:
    return provider in {
        "anthropic",
        "deepseek",
        "openai",
        "qwen",
        "siliconflow",
        "vllm",
    }


def _expand_user_value(value: str) -> str:
    return str(Path(value).expanduser()) if value.startswith("~") else value


def _write_env_preserving_existing(path: Path, additions: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        lines = [
            "# Managed by ContextSeek for PowerMem.",
            "# Existing values are preserved by future plug-install runs.",
        ]
    else:
        lines = path.read_text(encoding="utf-8").rstrip("\n").splitlines()
    if additions:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(f"{key}={value}" for key, value in sorted(additions.items()))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
