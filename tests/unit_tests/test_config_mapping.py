# tests/unit_tests/test_config_mapping.py
"""Tests for agentseek→contextseek mapping."""

from __future__ import annotations

from contextseek.config.mapping import (
    detect_provider,
    project_agentseek_env,
    strip_provider_prefix,
)


def test_strip_provider_prefix():
    assert strip_provider_prefix("openai:gpt-4o") == "gpt-4o"
    assert strip_provider_prefix("gpt-4o") == "gpt-4o"


def test_detect_provider_from_model_prefix():
    assert detect_provider(model="openai:gpt-4o") == "openai"
    assert detect_provider(model="anthropic:claude-3") == "anthropic"


def test_detect_provider_from_class_path():
    assert detect_provider(class_path="langchain_openai.ChatOpenAI") == "openai"


def test_project_agentseek_env_maps_api_key_and_model():
    env = {
        "AGENTSEEK_API_KEY": "sk-xxx",
        "AGENTSEEK_API_BASE": "https://api.example.com/v1",
        "AGENTSEEK_MODEL": "openai:gpt-4o",
        "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
    }
    projected, source_ref = project_agentseek_env(env)
    assert projected["llm"]["api_key"] == "sk-xxx"
    assert projected["llm"]["base_url"] == "https://api.example.com/v1"
    assert projected["llm"]["model"] == "gpt-4o"
    assert projected["llm"]["provider"] == "openai"
    assert source_ref is not None


def test_project_agentseek_env_noop_when_llm_disabled():
    env = {"AGENTSEEK_API_KEY": "sk-xxx", "AGENTSEEK_MODEL": "openai:gpt-4o"}
    projected, source_ref = project_agentseek_env(env)
    # LLM not enabled (no AGENTSEEK_CTX_LLM_PROVIDER / LLM_MODEL) → no credential projection
    assert "llm" not in projected or "api_key" not in projected.get("llm", {})
