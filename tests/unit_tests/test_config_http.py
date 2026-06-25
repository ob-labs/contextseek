"""Tests for config-management HTTP routes."""

from __future__ import annotations

import os
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
    assert body["config_version"].startswith("cfg-")  # migrated


def test_put_config_creates_versioned_commit(client):
    client.get("/config")  # trigger lazy migration
    r = client.put("/config", json={"llm_model": "gpt-4o", "llm_provider": "openai"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version_id"].startswith("cfg-")
    assert body["restart_required"] is True


def test_config_history_endpoint(client):
    client.get("/config")
    client.put("/config", json={"llm_model": "gpt-4o"})
    r = client.get("/config/history")
    assert r.status_code == 200
    versions = r.json()
    assert isinstance(versions, list)
    assert len(versions) >= 2


def test_config_history_page_endpoint(client):
    client.get("/config")
    client.put("/config", json={"llm_model": "gpt-4o"})
    r = client.get("/config/history/page", params={"offset": 0, "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 0
    assert body["limit"] == 1
    assert body["total"] >= 2
    assert len(body["items"]) == 1


def test_config_rollback_endpoint(client):
    client.get("/config")
    r1 = client.put("/config", json={"llm_model": "gpt-4o"})
    client.put("/config", json={"llm_model": "gpt-4o-mini"})
    target_id = r1.json()["version_id"]
    r = client.post("/config/rollback", json={"version": target_id, "reason": "back"})
    assert r.status_code == 200
    assert r.json()["version_id"].startswith("cfg-")
    assert r.json()["rollback_target_version_id"] == target_id
    latest = client.get(
        "/config/history/page", params={"offset": 0, "limit": 1}
    ).json()["items"][0]
    assert latest["rollback_target_version_id"] == target_id


def test_config_redo_materializes(client, tmp_path, monkeypatch):
    # Lazy-migrate, then set + rollback + redo, assert materialized .env reflects the redo.
    client.get("/config")
    r1 = client.put("/config", json={"llm_model": "gpt-4o"})
    client.put("/config", json={"llm_model": "gpt-4o-mini"})
    # rollback reverts to first edited version, and redo should re-apply newer one.
    client.post(
        "/config/rollback", json={"version": r1.json()["version_id"], "reason": "back"}
    )
    # redo re-applies v000003's state (gpt-4o-mini), committing v000005, and
    # must materialize it — otherwise a restart would load the rolled-back .env.
    r = client.post("/config/redo", json={"reason": "undo rollback"})
    assert r.status_code == 200
    body = r.json()
    assert body["version_id"] is not None
    assert body["restart_required"] is True
    env_file = Path(os.environ.get("CONTEXTSEEK_ENV_FILE", ".env"))
    assert "LLM_MODEL=gpt-4o-mini" in env_file.read_text(encoding="utf-8")


def test_config_status_endpoint(client):
    client.get("/config")
    r = client.get("/config/status")
    assert r.status_code == 200
    body = r.json()
    assert "current_version" in body
    assert "drift" in body
    assert "agentseek_stale" in body
    assert "override_conflicts" in body


def test_put_config_preserves_api_key_round_trip(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The dashboard edits api keys via flat fields; they must persist through
    # the store → materialize → reload round-trip (Fix 1).
    client.get("/config")  # trigger lazy migration
    r = client.put("/config", json={"llm_api_key": "sk-roundtrip"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version_id"].startswith("cfg-")
    # Simulate a restart: GET /config should still serve from managed effective state.
    env_path = tmp_path / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        monkeypatch.setenv(k, v)
    r2 = client.get("/config")
    assert r2.status_code == 200
    assert r2.json()["llm_api_key"] == "sk-roundtrip"


def test_get_config_reports_real_drift(client, tmp_path: Path):
    # GET /config's drift must reflect a hand-edited .env, not a placeholder (Fix 2).
    client.get("/config")  # lazy migrate
    # A PUT applies the effective config to .env, establishing a no-drift baseline.
    client.put("/config", json={"llm_model": "gpt-4o"})
    assert client.get("/config").json()["drift"]["env"] is False
    # hand-edit the .env file
    env_path = tmp_path / ".env"
    env_path.write_text(env_path.read_text() + "\n# tampered\n")
    body = client.get("/config").json()
    assert body["drift"]["env"] is True


def test_get_config_returns_flat_override_sources(client):
    client.get("/config")
    client.put("/config", json={"llm_model": "gpt-4o"})
    body = client.get("/config").json()
    assert body["override_sources"]["llm_model"] == "native"


def test_ingest_agentseek_updates_status_and_sources(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENTSEEK_API_KEY", "sk-xxx")
    monkeypatch.setenv("AGENTSEEK_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("AGENTSEEK_CTX_LLM_PROVIDER", "openai")
    r = client.post("/config/ingest/agentseek", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["version_id"] is not None
    st = client.get("/config/status").json()
    assert st["agentseek_stale"] is False
    sources_file = tmp_path / "config" / "sources" / "agentseek.json"
    assert sources_file.exists()


def test_ingest_agentseek_apply_updates_env_file(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENTSEEK_API_KEY", "sk-xyz")
    monkeypatch.setenv("AGENTSEEK_MODEL", "openai:gpt-4o-mini")
    monkeypatch.setenv("AGENTSEEK_CTX_LLM_PROVIDER", "openai")
    r = client.post("/config/ingest/agentseek", json={"apply": True})
    assert r.status_code == 200
    env_file = tmp_path / ".env"
    assert env_file.exists()
    assert "LLM_MODEL=gpt-4o-mini" in env_file.read_text(encoding="utf-8")


def test_ingest_agentseek_check_endpoint(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENTSEEK_API_KEY", "sk-check")
    monkeypatch.setenv("AGENTSEEK_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("AGENTSEEK_CTX_LLM_PROVIDER", "openai")
    r = client.get("/config/ingest/agentseek/check")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["missing"] == []
