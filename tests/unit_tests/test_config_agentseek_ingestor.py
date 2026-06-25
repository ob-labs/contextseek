# tests/unit_tests/test_config_agentseek_ingestor.py
"""Tests for AgentseekIngestor."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextseek.config.agentseek_ingestor import AgentseekIngestor
from contextseek.config.manager import ConfigManager


@pytest.fixture()
def manager(tmp_path: Path) -> ConfigManager:
    m = ConfigManager(tmp_path / "config")
    m.init_store()
    return m


def test_ingest_env_creates_projection_version(manager: ConfigManager):
    ing = AgentseekIngestor(manager)
    env = {
        "AGENTSEEK_API_KEY": "sk-xxx",
        "AGENTSEEK_MODEL": "openai:gpt-4o",
        "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
    }
    v = ing.ingest_env(env)
    assert v is not None
    assert v.origin == "agentseek-projection"
    assert v.payload["projected"]["llm"]["model"] == "gpt-4o"
    assert v.source_ref is not None


def test_ingest_env_is_idempotent(manager: ConfigManager):
    ing = AgentseekIngestor(manager)
    env = {
        "AGENTSEEK_API_KEY": "sk-xxx",
        "AGENTSEEK_MODEL": "openai:gpt-4o",
        "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
    }
    v1 = ing.ingest_env(env)
    v2 = ing.ingest_env(env)  # same source_ref → skip
    assert v1 is not None
    assert v2 is None
    # only one version in history
    assert len(manager.history()) == 1


def test_ingest_env_new_source_creates_new_version(manager: ConfigManager):
    ing = AgentseekIngestor(manager)
    ing.ingest_env(
        {
            "AGENTSEEK_API_KEY": "sk-1",
            "AGENTSEEK_MODEL": "openai:gpt-4o",
            "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
        }
    )
    v2 = ing.ingest_env(
        {
            "AGENTSEEK_API_KEY": "sk-2",
            "AGENTSEEK_MODEL": "openai:gpt-4o",
            "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
        }
    )
    assert v2 is not None
    assert len(manager.history()) == 2


def test_ingest_file_records_file_hash(manager: ConfigManager, tmp_path: Path):
    cfg = tmp_path / "agentseek.env"
    cfg.write_text(
        "AGENTSEEK_API_KEY=sk-xxx\nAGENTSEEK_MODEL=openai:gpt-4o\n"
        "AGENTSEEK_CTX_LLM_PROVIDER=openai\n"
    )
    ing = AgentseekIngestor(manager)
    v = ing.ingest_file(cfg)
    assert v is not None
    assert "sha256:" in v.source_ref


def test_ingest_writes_latest_source_snapshot(manager: ConfigManager):
    ing = AgentseekIngestor(manager)
    env = {
        "AGENTSEEK_API_KEY": "sk-xxx",
        "AGENTSEEK_MODEL": "openai:gpt-4o",
        "AGENTSEEK_CTX_LLM_PROVIDER": "openai",
    }
    v = ing.ingest_env(env)
    assert v is not None
    snapshot = manager.sources_dir / "agentseek.json"
    assert snapshot.exists()
    body = snapshot.read_text(encoding="utf-8")
    assert "source_ref" in body
    assert "AGENTSEEK_API_KEY" in body
