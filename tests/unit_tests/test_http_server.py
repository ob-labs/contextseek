"""Unit tests for HTTP API facade (`create_app`)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip("fastapi", reason="http extra not installed")

import contextseek.http.server as http_server
from contextseek.domain.context_item import ContextItem
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.results import ResponseMeta, RetrieveResponse, SearchHit
from contextseek.http.server import create_app
from contextseek.plugs.core.protocols import InstallResult


def _asgi_post(app, path: str, **kwargs) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, **kwargs)

    return asyncio.run(_request())


def _asgi_put(app, path: str, **kwargs) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.put(path, **kwargs)

    return asyncio.run(_request())


def _asgi_get(app, path: str, **kwargs) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path, **kwargs)

    return asyncio.run(_request())


def _wait_plug_job(app, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        res = _asgi_get(app, f"/plugs/jobs/{job_id}")
        assert res.status_code == 200
        last_payload = res.json()
        if last_payload["status"] in {"succeeded", "failed"}:
            return last_payload
        time.sleep(0.01)
    raise AssertionError(f"plug job did not finish: {last_payload}")


def _clear_plug_status_cache() -> None:
    http_server._POWERMEM_STATUS_CACHE.clear()
    http_server._POWERMEM_STATUS_CACHE_UPDATED_AT = None


def _fake_command(path, body: str = "exit 0") -> None:
    path.write_text("#!/bin/sh\n" + body.rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)


def _sample_hit() -> SearchHit:
    item = ContextItem(
        id="item-1",
        scope="tenant/project/session",
        content="full body",
        summary="short summary",
        tags=["ops"],
        provenance=Provenance(
            source_type=SourceType.document,
            source_id="doc://sample",
            confidence=0.8,
        ),
    )
    return SearchHit(
        item=item,
        score=0.91,
        layer="summary",
        provenance_summary="from sample doc",
        stage_confidence=0.85,
        recall_path="phrase",
    )


def test_http_retrieve_forwards_include_expired_and_returns_meta() -> None:
    ctx = MagicMock(name="ContextSeek")
    ctx.retrieve.return_value = RetrieveResponse(
        items=[_sample_hit()],
        meta=ResponseMeta(layer="summary", full_via="expand", hint="use expand"),
    )
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/retrieve",
        json={
            "scope": "tenant/project/session",
            "query": "deploy note",
            "k": 5,
            "include_deleted": False,
            "include_expired": True,
        },
    )

    assert res.status_code == 200
    ctx.retrieve.assert_called_once_with(
        "deploy note",
        scope="tenant/project/session",
        k=5,
        full=False,
        filters=None,
        include_deleted=False,
        include_expired=True,
        with_trace=False,
    )
    body = res.json()
    assert body["_meta"] == {
        "layer": "summary",
        "full_via": "expand",
        "hint": "use expand",
    }
    assert body["items"][0]["id"] == "item-1"
    assert body["items"][0]["scope"] == "tenant/project/session"
    assert body["items"][0]["content"] is None


def test_http_scopes_lists_sqlite_scopes(tmp_path) -> None:
    from seekvfs import VFS

    from contextseek.client.contextseek import ContextSeek
    from contextseek.storage.sqlite_backend import SQLiteBackend
    from contextseek.storage.storage_adapter import SeekVFSStorageAdapter

    backend = SQLiteBackend(path=str(tmp_path / "ctx.sqlite3"))
    backend.initialize()
    vfs = VFS(
        routes={"contextseek://": {"backend": backend}},
        scheme="contextseek://",
    )
    ctx = ContextSeek(adapter=SeekVFSStorageAdapter(vfs))
    ctx.add("user memory", scope="user", source="test")

    app = create_app(client=ctx)

    res = _asgi_get(app, "/scopes")

    assert res.status_code == 200
    assert res.json() == {"scopes": ["user"]}


def test_http_compact_returns_conflict_counts() -> None:
    ctx = MagicMock(name="ContextSeek")
    ctx.compact.return_value = SimpleNamespace(
        merged_count=1,
        archived_count=2,
        evolved_count=3,
        conflict_updated_count=4,
        conflict_drift_count=5,
    )
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/compact",
        json={
            "scope": "tenant/project/session",
            "dry_run": True,
        },
    )

    assert res.status_code == 200
    ctx.compact.assert_called_once_with(scope="tenant/project/session", dry_run=True)
    assert res.json() == {
        "merged": 1,
        "archived": 2,
        "evolved": 3,
        "conflict_updated": 4,
        "conflict_drift": 5,
    }


def test_http_update_config_normalizes_embedding_none(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "config.env"
    env_path.write_text(
        "\n".join(
            [
                "EMBEDDING_PROVIDER=dashscope",
                "EMBEDDING_MODEL=text-embedding-v1",
                "EMBEDDING_DIMS=1024",
                "EMBEDDING_BASE_URL=https://example.test",
                'EMBEDDING_KWARGS={"api_key": "secret"}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(env_path))
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_put(app, "/config", json={"embedding_provider": "none"})

    assert res.status_code == 200
    contents = env_path.read_text(encoding="utf-8")
    assert "EMBEDDING_PROVIDER=none\n" in contents
    assert "EMBEDDING_MODEL=none\n" in contents
    assert "EMBEDDING_DIMS=0\n" in contents
    assert "EMBEDDING_BASE_URL=\n" in contents
    assert 'EMBEDDING_KWARGS={"api_key": ""}\n' in contents


def test_http_update_config_rejects_enabled_embedding_without_model(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / "config.env"
    env_path.write_text(
        "EMBEDDING_PROVIDER=none\nEMBEDDING_MODEL=none\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(env_path))
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_put(
        app,
        "/config",
        json={"embedding_provider": "dashscope", "embedding_model": "none"},
    )

    assert res.status_code == 400
    assert "EMBEDDING_MODEL must be a real model" in res.json()["detail"]
    assert env_path.read_text(encoding="utf-8") == (
        "EMBEDDING_PROVIDER=none\nEMBEDDING_MODEL=none\n"
    )


def test_http_plug_install_returns_job_and_linker_result(monkeypatch, tmp_path) -> None:
    _clear_plug_status_cache()
    calls: list[dict[str, object]] = []
    cursor = tmp_path / "cursor"
    cursor.write_text("#!/bin/sh\n", encoding="utf-8")
    cursor.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        calls.append({"linker": linker, "dry_run": dry_run, "check": check})
        return InstallResult(
            changed=True,
            dry_run=False,
            actions=["install test linker"],
            warnings=[],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/plugs/powermem/install",
        json={"linker": "cursor"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["plug"] == "powermem"
    assert body["linker"] == "cursor"
    assert body["status"] in {"queued", "running", "succeeded"}

    job = _wait_plug_job(app, body["job_id"])
    assert calls == [{"linker": "cursor", "dry_run": False, "check": False}]
    assert job["status"] == "succeeded"
    assert job["phase"] == "done"
    assert job["result"] == {
        "linker": "cursor",
        "status": "connected",
        "changed": True,
        "dry_run": False,
        "actions": ["install test linker"],
        "warnings": [],
        "blocker_stage": None,
        "blocker_code": None,
    }


def test_http_plug_install_fails_fast_when_target_missing(
    monkeypatch, tmp_path
) -> None:
    _clear_plug_status_cache()
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        calls.append({"linker": linker, "dry_run": dry_run, "check": check})
        return InstallResult(
            changed=True,
            dry_run=False,
            actions=["install qoder"],
            warnings=[],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/plugs/powermem/install",
        json={"linker": "qoder"},
    )

    assert res.status_code == 200
    job = _wait_plug_job(app, res.json()["job_id"])
    assert calls == []
    assert job["status"] == "failed"
    assert job["phase"] == "target"
    assert job["result"] == {
        "linker": "qoder",
        "status": "needs_action",
        "changed": False,
        "dry_run": True,
        "actions": ["detect target runtime: Qoder"],
        "warnings": ["Qoder CLI cannot be found; install Qoder"],
        "blocker_stage": "target",
        "blocker_code": "target_not_detected",
    }


def test_http_plug_install_target_only_does_not_install(monkeypatch, tmp_path) -> None:
    _clear_plug_status_cache()
    calls: list[dict[str, object]] = []
    cursor = tmp_path / "cursor"
    cursor.write_text("#!/bin/sh\n", encoding="utf-8")
    cursor.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        calls.append({"linker": linker, "dry_run": dry_run, "check": check})
        return InstallResult(
            changed=True,
            dry_run=False,
            actions=["install cursor"],
            warnings=[],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/plugs/powermem/install",
        json={"linker": "cursor", "target_only": True},
    )

    assert res.status_code == 200
    job = _wait_plug_job(app, res.json()["job_id"])
    assert calls == []
    assert job["status"] == "succeeded"
    assert job["phase"] == "done"
    assert job["result"] == {
        "linker": "cursor",
        "status": "ready",
        "changed": True,
        "dry_run": True,
        "actions": ["detect target runtime: Cursor"],
        "warnings": [],
        "blocker_stage": None,
        "blocker_code": None,
    }


def test_http_plug_status_returns_checking_without_full_check(
    monkeypatch,
    tmp_path,
) -> None:
    _clear_plug_status_cache()
    monkeypatch.setenv("PATH", str(tmp_path))
    calls: list[dict[str, object]] = []

    def fake_available_linker_names():
        return ["qoder"]

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        calls.append({"linker": linker, "dry_run": dry_run, "check": check})
        return InstallResult(
            changed=False,
            dry_run=True,
            actions=["skip Qoder MCP config"],
            warnings=[
                "Qoder MCP config path is not verified; no default file was written"
            ],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.linkers.available_linker_names",
        fake_available_linker_names,
    )
    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_get(app, "/plugs/powermem")

    assert res.status_code == 200
    assert calls == []
    assert res.json()["entries"] == [
        {
            "linker": "qoder",
            "status": "checking",
            "changed": False,
            "dry_run": True,
            "actions": [],
            "warnings": [],
            "blocker_stage": None,
            "blocker_code": None,
        }
    ]


def test_http_plug_status_refresh_classifies_missing_target(
    monkeypatch,
    tmp_path,
) -> None:
    _clear_plug_status_cache()
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_available_linker_names():
        return ["qoder"]

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        return InstallResult(
            changed=False,
            dry_run=True,
            actions=["skip Qoder MCP config"],
            warnings=[
                "Qoder MCP config path is not verified; no default file was written"
            ],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.linkers.available_linker_names",
        fake_available_linker_names,
    )
    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_post(app, "/plugs/powermem/status/refresh", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "status_refresh"
    job = _wait_plug_job(app, body["job_id"])
    assert job["status"] == "succeeded"
    assert job["progress_current"] == 1
    assert job["progress_total"] == 1
    assert job["entries"] == [
        {
            "linker": "qoder",
            "status": "needs_action",
            "changed": False,
            "dry_run": True,
            "actions": ["detect target runtime: Qoder"],
            "warnings": ["Qoder CLI cannot be found; install Qoder"],
            "blocker_stage": "target",
            "blocker_code": "target_not_detected",
        }
    ]

    status = _asgi_get(app, "/plugs/powermem")
    assert status.status_code == 200
    assert status.json()["entries"] == job["entries"]


def test_http_plug_status_refresh_detects_missing_targets_for_all_linkers(
    monkeypatch,
    tmp_path,
) -> None:
    _clear_plug_status_cache()
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_OPENCLAW_COMMAND", raising=False)
    calls: list[dict[str, object]] = []
    linkers = [
        "claude-code",
        "cursor",
        "vscode",
        "codex",
        "windsurf",
        "github-copilot",
        "opencode",
        "claude-desktop",
        "cline",
        "openclaw",
        "qoder",
    ]

    def fake_available_linker_names():
        return linkers

    def fake_install(self, *, linker=None, dry_run=False, check=False):
        calls.append({"linker": linker, "dry_run": dry_run, "check": check})
        return InstallResult(
            changed=True,
            dry_run=False,
            actions=[f"install {linker}"],
            warnings=[],
        )

    monkeypatch.setattr(
        "contextseek.plugs.powermem.linkers.available_linker_names",
        fake_available_linker_names,
    )
    monkeypatch.setattr(
        "contextseek.plugs.powermem.PowerMemAdapter.install",
        fake_install,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    res = _asgi_post(app, "/plugs/powermem/status/refresh", json={})

    assert res.status_code == 200
    job = _wait_plug_job(app, res.json()["job_id"])
    assert calls == []
    assert job["status"] == "succeeded"
    assert job["progress_current"] == len(linkers)
    assert job["progress_total"] == len(linkers)
    assert [entry["linker"] for entry in job["entries"]] == linkers
    assert {entry["status"] for entry in job["entries"]} == {"needs_action"}
    assert {entry["blocker_stage"] for entry in job["entries"]} == {"target"}
    assert {entry["blocker_code"] for entry in job["entries"]} == {
        "target_not_detected"
    }


def test_http_plug_status_refresh_keeps_installed_targets_connected(
    monkeypatch,
    tmp_path,
) -> None:
    _clear_plug_status_cache()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (
        "claude",
        "cursor",
        "code",
        "codex",
        "windsurf",
        "opencode",
        "claude-desktop",
        "qoder",
        "pmem",
    ):
        _fake_command(bin_dir / name)
    _fake_command(
        bin_dir / "openclaw",
        """
if [ "$1" = "plugins" ] && [ "$2" = "list" ]; then
  echo "memory-powermem enabled"
  exit 0
fi
exit 0
""",
    )
    linkers = [
        "claude-code",
        "cursor",
        "vscode",
        "codex",
        "windsurf",
        "github-copilot",
        "opencode",
        "claude-desktop",
        "cline",
        "openclaw",
        "qoder",
    ]
    config_env = {
        "CONTEXTSEEK_POWERMEM_CURSOR_MCP_CONFIG": tmp_path / "cursor-mcp.json",
        "CONTEXTSEEK_POWERMEM_VSCODE_MCP_CONFIG": tmp_path / "vscode-mcp.json",
        "CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG": tmp_path / "codex-context.json",
        "CONTEXTSEEK_POWERMEM_WINDSURF_MCP_CONFIG": tmp_path / "windsurf.json",
        "CONTEXTSEEK_POWERMEM_COPILOT_MCP_CONFIG": tmp_path / "copilot-mcp.json",
        "CONTEXTSEEK_POWERMEM_OPENCODE_MCP_CONFIG": tmp_path / "opencode.json",
        "CONTEXTSEEK_POWERMEM_CLAUDE_DESKTOP_MCP_CONFIG": tmp_path
        / "claude-desktop.json",
        "CONTEXTSEEK_POWERMEM_CLINE_MCP_CONFIG": tmp_path / "cline.json",
        "CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG": tmp_path / "openclaw.json",
        "CONTEXTSEEK_POWERMEM_QODER_MCP_CONFIG": tmp_path / "qoder.json",
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(bin_dir / "claude"))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(bin_dir / "openclaw"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(bin_dir / "pmem"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(tmp_path / "powermem.env"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "0")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", raising=False)
    for key, value in config_env.items():
        monkeypatch.setenv(key, str(value))

    def fake_available_linker_names():
        return linkers

    monkeypatch.setattr(
        "contextseek.plugs.powermem.linkers.available_linker_names",
        fake_available_linker_names,
    )
    ctx = MagicMock(name="ContextSeek")
    app = create_app(client=ctx)

    for linker in linkers:
        res = _asgi_post(
            app,
            "/plugs/powermem/install",
            json={"linker": linker},
        )
        assert res.status_code == 200
        install_job = _wait_plug_job(app, res.json()["job_id"])
        assert install_job["status"] == "succeeded", install_job

    res = _asgi_post(app, "/plugs/powermem/status/refresh", json={})

    assert res.status_code == 200
    job = _wait_plug_job(app, res.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["progress_current"] == len(linkers)
    assert job["progress_total"] == len(linkers)
    assert [entry["linker"] for entry in job["entries"]] == linkers
    assert {entry["status"] for entry in job["entries"]} == {"connected"}
    assert {entry["changed"] for entry in job["entries"]} == {False}
    assert all(not entry["warnings"] for entry in job["entries"])

    status = _asgi_get(app, "/plugs/powermem")
    assert status.status_code == 200
    assert status.json()["entries"] == job["entries"]
