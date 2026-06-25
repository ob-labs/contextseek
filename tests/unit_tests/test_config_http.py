"""Tests for config-management HTTP routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONTEXTSEEK_HOME", str(tmp_path))
    # Isolate the materializer's .env / config.json targets so lazy migration
    # and applies never touch the real repo .env.
    monkeypatch.setenv("CONTEXTSEEK_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(tmp_path / "config.json"))
    from contextseek.http.server import create_app

    app = create_app()
    return TestClient(app)


def test_get_config_lazy_migrates_and_reports_version(client):
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert "config_version" in body
    assert body["config_version"] == "v000001"  # migrated


def test_put_config_creates_versioned_commit(client):
    client.get("/config")  # trigger lazy migration
    r = client.put("/config", json={"llm_model": "gpt-4o", "llm_provider": "openai"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version_id"] == "v000002"
    assert body["restart_required"] is True


def test_config_history_endpoint(client):
    client.get("/config")
    client.put("/config", json={"llm_model": "gpt-4o"})
    r = client.get("/config/history")
    assert r.status_code == 200
    versions = r.json()
    assert isinstance(versions, list)
    assert len(versions) >= 2


def test_config_rollback_endpoint(client):
    client.get("/config")
    client.put("/config", json={"llm_model": "gpt-4o"})
    client.put("/config", json={"llm_model": "gpt-4o-mini"})
    r = client.post("/config/rollback", json={"version": "v000002", "reason": "back"})
    assert r.status_code == 200
    assert r.json()["version_id"] == "v000004"


def test_config_status_endpoint(client):
    client.get("/config")
    r = client.get("/config/status")
    assert r.status_code == 200
    body = r.json()
    assert "current_version" in body
    assert "drift" in body
