# src/contextseek/config/mapping.py
"""Explicit agentseek → contextseek configuration mapping.

Migrated from the ``agentseek-contextseek`` contrib's reflective env-aliasing
into a declarative, testable mapping table. Projection output is written to
the config manager's ``projected`` layer (not to ``os.environ``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

AGENTSEEK_CTX_PREFIX = "AGENTSEEK_CTX_"

# Maps a provider name → (api_key_var, base_url_var | None).
PROVIDER_CREDS: dict[str, tuple[str, str | None]] = {
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "google": ("GOOGLE_API_KEY", None),
    "cohere": ("COHERE_API_KEY", None),
    "mistral": ("MISTRAL_API_KEY", None),
    "dashscope": ("DASHSCOPE_API_KEY", None),
    "tongyi": ("DASHSCOPE_API_KEY", None),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
}

# Maps a provider name → LangChain chat class path.
PROVIDER_CLASS_PATH: dict[str, str] = {
    "openai": "langchain_openai.ChatOpenAI",
    "anthropic": "langchain_anthropic.ChatAnthropic",
    "google": "langchain_google_genai.ChatGoogleGenerativeAI",
    "cohere": "langchain_cohere.ChatCohere",
    "mistral": "langchain_mistralai.ChatMistralAI",
    "dashscope": "langchain_community.chat_models.ChatTongyi",
    "tongyi": "langchain_community.chat_models.ChatTongyi",
    "deepseek": "langchain_openai.ChatOpenAI",
}

# Fragments of LangChain class paths → provider name (reverse lookup).
_CLASS_PATH_PROVIDER: dict[str, str] = {
    "langchain_openai": "openai",
    "langchain_anthropic": "anthropic",
    "langchain_google_genai": "google",
    "langchain_google_vertexai": "google",
    "langchain_cohere": "cohere",
    "langchain_mistralai": "mistral",
    "chattongyi": "dashscope",
    "tongyi": "dashscope",
    "deepseek": "deepseek",
}


def detect_provider(*, class_path: str = "", model: str = "") -> str:
    """Return a lowercase provider name from class path or model prefix."""
    if class_path:
        lowered = class_path.lower()
        for fragment, provider in _CLASS_PATH_PROVIDER.items():
            if fragment in lowered:
                return provider
    if ":" in model:
        prefix = model.split(":", 1)[0].lower()
        if prefix in PROVIDER_CREDS:
            return prefix
    return "openai"


def strip_provider_prefix(model: str) -> str:
    """Strip a ``provider:`` prefix from a model name."""
    if ":" in model:
        return model.split(":", 1)[1]
    return model


def _set_path(nested: dict, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = nested
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# agentseek 键 → (contextseek 点分路径, 转换函数, provider hint 或 None)
AGENTSEEK_MAPPING: dict[str, tuple[str, Callable[[str], Any], str | None]] = {
    "AGENTSEEK_API_KEY": ("llm.api_key", lambda v: v, "openai"),
    "AGENTSEEK_API_BASE": ("llm.base_url", lambda v: v, None),
    "AGENTSEEK_MODEL": ("llm.model", strip_provider_prefix, None),
}


def project_agentseek_env(env: Mapping[str, str]) -> tuple[dict, str | None]:
    """Project agentseek env vars into a contextseek ``projected`` payload.

    Returns ``(projected, source_ref)``. Credential/class_path projection only
    runs when contextseek's LLM is enabled (``AGENTSEEK_CTX_LLM_PROVIDER`` !=
    ``none`` or ``AGENTSEEK_CTX_LLM_MODEL`` is set), mirroring the contrib's
    ``_maybe_bridge_llm_credentials``.

    ``source_ref`` is a stable hash of the contributing agentseek env keys
    (used for idempotent ingestion), or None when nothing was projected.
    """
    projected: dict = {}

    llm_provider = env.get(f"{AGENTSEEK_CTX_PREFIX}LLM_PROVIDER", "none")
    llm_model = env.get(f"{AGENTSEEK_CTX_PREFIX}LLM_MODEL", "")
    if llm_provider.lower() == "none" and not llm_model:
        return projected, None

    provider = _detect_from_env(env)

    agentseek_key = env.get("AGENTSEEK_API_KEY", "")
    agentseek_base = env.get("AGENTSEEK_API_BASE", "")
    agentseek_model = env.get("AGENTSEEK_MODEL", "")

    contributing = []
    if agentseek_key:
        _set_path(projected, "llm.api_key", agentseek_key)
        contributing.append(("api_key", agentseek_key))
    if agentseek_base:
        _set_path(projected, "llm.base_url", agentseek_base)
        contributing.append(("base_url", agentseek_base))
    if agentseek_model:
        _set_path(projected, "llm.model", strip_provider_prefix(agentseek_model))
        contributing.append(("model", agentseek_model))

    # class path + provider derivation
    ctx_class_path = env.get(f"{AGENTSEEK_CTX_PREFIX}LLM_CLASS_PATH", "")
    if not ctx_class_path:
        class_path = PROVIDER_CLASS_PATH.get(provider)
        if class_path:
            _set_path(projected, "llm.class_path", class_path)
            contributing.append(("class_path", class_path))

    _set_path(projected, "llm.provider", provider)
    contributing.append(("provider", provider))

    if not contributing:
        return projected, None
    source_ref = "agentseek:env:sha256:" + hashlib.sha256(
        repr(sorted(contributing)).encode("utf-8")
    ).hexdigest()
    return projected, source_ref


def _detect_from_env(env: Mapping[str, str]) -> str:
    class_path = env.get(f"{AGENTSEEK_CTX_PREFIX}LLM_CLASS_PATH", "")
    model = env.get("AGENTSEEK_MODEL", "")
    return detect_provider(class_path=class_path, model=model)
