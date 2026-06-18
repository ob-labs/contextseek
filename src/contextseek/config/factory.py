"""Lazy model factory for ContextSeek.

Builds embedder and LLM instances from Settings using dynamic imports.
LangChain is imported only when a provider is actually configured,
so users without LangChain installed incur no import cost.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from contextseek.config.settings import (
    ContextSeekSettings,
    EmbeddingSettings,
    LLMSettings,
    SummarizerSettings,
)

_EMBEDDING_PROVIDERS: dict[str, tuple[str, int]] = {
    "openai": ("langchain_openai.OpenAIEmbeddings", 1536),
    "dashscope": ("langchain_community.embeddings.DashScopeEmbeddings", 1024),
    "ollama": ("langchain_ollama.OllamaEmbeddings", 768),
    "huggingface": ("langchain_huggingface.HuggingFaceEmbeddings", 512),
}

_LLM_PROVIDERS: dict[str, str] = {
    "openai": "langchain_openai.ChatOpenAI",
    "dashscope": "langchain_community.chat_models.ChatTongyi",
    "ollama": "langchain_ollama.ChatOllama",
}

DoctorStatus = Literal["PASS", "FAIL", "SKIP"]

_SECRET_KEY_PARTS = ("KEY", "PASSWORD", "SECRET", "TOKEN")
_OPENAI_LIKE_SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b")
_URL_USERINFO_RE = re.compile(r"([a-z][a-z0-9+.-]*://)[^/?#\s@]+@")
_SECRET_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|token|password|secret)=)[^&#\s]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DoctorCheck:
    """One ``contextseek doctor`` diagnostic result."""

    component: str
    status: DoctorStatus
    summary: str
    hint: str = ""


def _import_class(class_path: str) -> type:
    """Dynamically import a class from a dotted path.

    Example::

        cls = _import_class("langchain_openai.OpenAIEmbeddings")
    """
    module_path, _, class_name = class_path.rpartition(".")
    if not module_path:
        raise ImportError(
            f"Invalid class_path '{class_path}': expected 'module.ClassName'"
        )
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _normalize_legacy_openai_kwargs(init_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy OpenAI kwarg names to aliases expected by some versions."""
    normalized = {**init_kwargs}
    if "openai_api_base" in normalized and "base_url" not in normalized:
        normalized["base_url"] = normalized.pop("openai_api_base")
    else:
        normalized.pop("openai_api_base", None)

    if "openai_api_key" in normalized and "api_key" not in normalized:
        normalized["api_key"] = normalized.pop("openai_api_key")
    else:
        normalized.pop("openai_api_key", None)
    return normalized


def _normalize_provider(provider: str) -> str:
    return provider.strip().lower()


def _default_embedding_dims(provider: str, class_path: str = "") -> int:
    if provider in _EMBEDDING_PROVIDERS:
        return _EMBEDDING_PROVIDERS[provider][1]
    for known_class_path, dims in _EMBEDDING_PROVIDERS.values():
        if class_path == known_class_path:
            return dims
    return 1536


def _resolve_embedding_provider(settings: EmbeddingSettings) -> tuple[str, int] | None:
    provider = _normalize_provider(settings.provider)
    if provider in {"", "none"}:
        return None
    if settings.class_path:
        return settings.class_path, settings.dims or _default_embedding_dims(
            provider, settings.class_path
        )
    if provider == "langchain":
        return None
    if provider not in _EMBEDDING_PROVIDERS:
        supported = ", ".join(["none", "langchain", *_EMBEDDING_PROVIDERS])
        raise ValueError(
            f"Unknown embedding provider '{settings.provider}'. "
            f"Supported providers: {supported}."
        )
    class_path, default_dims = _EMBEDDING_PROVIDERS[provider]
    return class_path, settings.dims or default_dims


def _resolve_llm_provider(settings: LLMSettings) -> str | None:
    provider = _normalize_provider(settings.provider)
    if provider in {"", "none"}:
        return None
    if settings.class_path:
        return settings.class_path
    if provider == "langchain":
        return None
    if provider not in _LLM_PROVIDERS:
        supported = ", ".join(["none", "langchain", *_LLM_PROVIDERS])
        raise ValueError(
            f"Unknown LLM provider '{settings.provider}'. "
            f"Supported providers: {supported}."
        )
    return _LLM_PROVIDERS[provider]


def resolve_embedding_dims(settings: EmbeddingSettings) -> int:
    """Return the vector dimensions that will be used for embedding settings."""
    resolved = _resolve_embedding_provider(settings)
    if resolved is None:
        return 0
    _, dims = resolved
    return dims


def build_embedder(settings: EmbeddingSettings) -> Callable[[str], list[float]] | None:
    """Build an embedder callable from settings.

    Returns None when provider is "none" (default).
    """
    resolved = _resolve_embedding_provider(settings)
    if resolved is None:
        return None
    class_path, dims = resolved

    import contextseek.embedders.langchain_embedder as _lc_mod

    LangChainEmbedder = _lc_mod.LangChainEmbedder

    cls = _import_class(class_path)
    init_kwargs: dict[str, Any] = {**settings.kwargs}
    init_kwargs = _normalize_legacy_openai_kwargs(init_kwargs)
    if settings.model:
        init_kwargs.setdefault("model", settings.model)
    if settings.base_url:
        init_kwargs.setdefault("base_url", settings.base_url)

    embeddings_instance = cls(**init_kwargs)
    return LangChainEmbedder(embeddings_instance, dims=dims)


def build_llm(settings: LLMSettings) -> Any | None:
    """Build an LLM instance from settings.

    Returns None when provider is "none" (default).
    The returned object is a LangChain BaseChatModel that can be
    wrapped into score_fn / summarize_fn by callers.
    """
    class_path = _resolve_llm_provider(settings)
    if class_path is None:
        return None

    cls = _import_class(class_path)
    init_kwargs: dict[str, Any] = {**settings.kwargs}
    init_kwargs = _normalize_legacy_openai_kwargs(init_kwargs)
    if settings.model:
        init_kwargs.setdefault("model", settings.model)
    if settings.base_url:
        init_kwargs.setdefault("base_url", settings.base_url)

    return cls(**init_kwargs)


def build_summarizer(
    settings: SummarizerSettings,
    *,
    llm: Any | None = None,
    prompt_templates: Any | None = None,
) -> Any | None:
    """Build a Summarizer instance from settings.

    Args:
        settings: ``SummarizerSettings`` controlling provider + token budgets.
        llm: Optional pre-built LangChain chat model. When supplied and
            ``provider == "llm"``, this instance is reused instead of
            re-constructing a separate LLM (avoids duplicate instances when
            both Summarizer and other components need the same model).

    Returns:
        ``None`` when ``provider == "none"`` or when ``provider == "llm"``
        but no usable LLM is configured (graceful fallback to flat L0-only).
        :class:`~contextseek.bridges.summarizer.LLMSummarizer` when
        ``provider == "llm"`` and an LLM is available (uses ``llm`` if
        provided, otherwise builds one from the global ``LLM_*`` env vars).
    """
    if settings.provider == "none":
        return None

    if settings.provider == "llm":
        from contextseek.bridges.summarizer import LLMSummarizer
        import warnings

        try:
            effective_llm = llm if llm is not None else build_llm(LLMSettings())
        except Exception as exc:
            warnings.warn(
                f"build_summarizer: LLM init failed ({exc}); falling back to L0-only.",
                RuntimeWarning,
                stacklevel=2,
            )
            return None
        if effective_llm is None:
            return None
        return LLMSummarizer(
            effective_llm,
            l2_max_chars=settings.l2_max_chars,
            l1_max_chars=settings.l1_max_chars,
            prompts=prompt_templates,
        )
    return None


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in _SECRET_KEY_PARTS)


def _collect_secret_values(value: Any, *, key: str = "") -> set[str]:
    """Collect configured secret values without exposing their names or payloads."""
    secrets: set[str] = set()
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            child_key_str = str(child_key)
            if _is_secret_key(child_key_str) and isinstance(child_value, str):
                if child_value:
                    secrets.add(child_value)
            secrets.update(_collect_secret_values(child_value, key=child_key_str))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            secrets.update(_collect_secret_values(item, key=key))
    elif _is_secret_key(key) and isinstance(value, str) and value:
        secrets.add(value)
    return secrets


def _env_secret_values() -> set[str]:
    return {
        value
        for key, value in os.environ.items()
        if _is_secret_key(key) and isinstance(value, str) and value
    }


def redact_diagnostic_text(
    text: str, settings: ContextSeekSettings | None = None
) -> str:
    """Remove known secret values from diagnostic output."""
    redacted = text
    secrets = _env_secret_values()
    if settings is not None:
        try:
            secrets.update(_collect_secret_values(settings.model_dump()))
        except Exception:
            pass

    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= 3:
            redacted = redacted.replace(secret, "***")
    redacted = _URL_USERINFO_RE.sub(r"\1***@", redacted)
    redacted = _SECRET_QUERY_PARAM_RE.sub(r"\1***", redacted)
    return _OPENAI_LIKE_SECRET_RE.sub("***", redacted)


def _model_summary(settings: Any, *, include_dims: bool = False) -> str:
    parts = [f"provider={settings.provider}"]
    if getattr(settings, "class_path", ""):
        parts.append(f"class_path={settings.class_path}")
    if getattr(settings, "model", ""):
        parts.append(f"model={settings.model}")
    if include_dims and getattr(settings, "dims", 0):
        parts.append(f"dims={settings.dims}")
    if getattr(settings, "base_url", ""):
        parts.append(f"base_url={settings.base_url}")
    return " ".join(parts)


def _embedding_summary(settings: EmbeddingSettings) -> str:
    parts = [f"provider={settings.provider}"]
    try:
        resolved = _resolve_embedding_provider(settings)
    except Exception:
        return _model_summary(settings, include_dims=True)

    if resolved is not None:
        class_path, dims = resolved
        parts.append(f"class_path={class_path}")
        if dims:
            parts.append(f"dims={dims}")
    elif settings.class_path:
        parts.append(f"class_path={settings.class_path}")
    if settings.model:
        parts.append(f"model={settings.model}")
    if settings.base_url:
        parts.append(f"base_url={settings.base_url}")
    return " ".join(parts)


def _llm_summary(settings: LLMSettings) -> str:
    parts = [f"provider={settings.provider}"]
    try:
        class_path = _resolve_llm_provider(settings)
    except Exception:
        return _model_summary(settings)

    if class_path is not None:
        parts.append(f"class_path={class_path}")
    elif settings.class_path:
        parts.append(f"class_path={settings.class_path}")
    if settings.model:
        parts.append(f"model={settings.model}")
    if settings.base_url:
        parts.append(f"base_url={settings.base_url}")
    return " ".join(parts)


def _check_storage(settings: ContextSeekSettings) -> DoctorCheck:
    summary = f"backend={settings.storage.backend}"
    try:
        from contextseek.client.contextseek import _build_adapter_from_settings

        adapter = _build_adapter_from_settings(settings)
        adapter.ls(settings.storage.uri_scheme)
    except Exception as exc:  # noqa: BLE001 - diagnostics should report all failures.
        return DoctorCheck(
            component="storage",
            status="FAIL",
            summary=f"{summary} failed: {redact_diagnostic_text(str(exc), settings)}",
            hint="Check STORAGE_* and backend-specific values in .env.example.",
        )
    return DoctorCheck(
        component="storage",
        status="PASS",
        summary=f"{summary} initialized and listed successfully",
    )


def _check_embedding(settings: ContextSeekSettings) -> DoctorCheck:
    provider = _normalize_provider(settings.embedding.provider)
    summary = _embedding_summary(settings.embedding)
    if provider in {"", "none"}:
        return DoctorCheck(
            component="embedding",
            status="SKIP",
            summary=summary,
        )

    try:
        embedder = build_embedder(settings.embedding)
        if embedder is None:
            raise ValueError(
                "EMBEDDING_CLASS_PATH is required when EMBEDDING_PROVIDER=langchain"
            )
        vector = embedder("contextseek doctor ping")
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("embedding probe returned an empty vector")
    except Exception as exc:  # noqa: BLE001 - diagnostics should report all failures.
        return DoctorCheck(
            component="embedding",
            status="FAIL",
            summary=f"{summary} failed: {redact_diagnostic_text(str(exc), settings)}",
            hint="Check EMBEDDING_* and provider credentials in .env.example.",
        )

    return DoctorCheck(
        component="embedding",
        status="PASS",
        summary=f"{summary} probe_dims={len(vector)}",
    )


def _invoke_llm_probe(llm: Any) -> str:
    try:
        from langchain_core.messages import HumanMessage

        prompt: Any = [HumanMessage(content="Reply with exactly: OK")]
    except Exception:
        prompt = "Reply with exactly: OK"

    resp = llm.invoke(prompt)
    from contextseek.llm.client import coerce_response_text

    return coerce_response_text(resp).strip()


def _check_llm(settings: ContextSeekSettings) -> DoctorCheck:
    provider = _normalize_provider(settings.llm.provider)
    summary = _llm_summary(settings.llm)
    if provider in {"", "none"}:
        return DoctorCheck(
            component="llm",
            status="SKIP",
            summary=summary,
        )

    try:
        llm = build_llm(settings.llm)
        if llm is None:
            raise ValueError("LLM_CLASS_PATH is required when LLM_PROVIDER=langchain")
        text = _invoke_llm_probe(llm)
        if not text:
            raise RuntimeError("LLM probe returned an empty response")
    except Exception as exc:  # noqa: BLE001 - diagnostics should report all failures.
        return DoctorCheck(
            component="llm",
            status="FAIL",
            summary=f"{summary} failed: {redact_diagnostic_text(str(exc), settings)}",
            hint="Check LLM_* and provider credentials in .env.example.",
        )

    return DoctorCheck(
        component="llm",
        status="PASS",
        summary=f"{summary} probe_response={text[:40]!r}",
    )


def run_config_diagnostics(settings: ContextSeekSettings) -> list[DoctorCheck]:
    """Run lightweight config and component diagnostics for ``contextseek doctor``."""
    return [
        DoctorCheck(
            component="config",
            status="PASS",
            summary="loaded ContextSeekSettings",
        ),
        _check_storage(settings),
        _check_embedding(settings),
        _check_llm(settings),
    ]


__all__ = [
    "DoctorCheck",
    "build_embedder",
    "build_llm",
    "build_summarizer",
    "redact_diagnostic_text",
    "run_config_diagnostics",
    "resolve_embedding_dims",
]
