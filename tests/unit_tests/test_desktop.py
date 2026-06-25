"""Unit tests for desktop sidecar bootstrap helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from contextseek.cli import desktop


def test_configure_desktop_path_adds_nvm_cli_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing-bin"
    nvm_bin = tmp_path / ".nvm" / "versions" / "node" / "v24.14.0" / "bin"
    existing.mkdir()
    nvm_bin.mkdir(parents=True)
    monkeypatch.setenv("PATH", str(existing))
    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path)

    desktop._configure_desktop_path()

    parts = os.environ["PATH"].split(os.pathsep)
    assert str(nvm_bin) in parts
    assert parts.index(str(nvm_bin)) < parts.index(str(existing))


def test_ensure_desktop_config_seeds_from_project_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CONTEXTSEEK_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=sqlite",
                "SQLITE_PATH=/tmp/project-contextseek.sqlite3",
                "LLM_PROVIDER=langchain",
                "LLM_MODEL=qwen-plus",
                "EMBEDDING_PROVIDER=langchain",
                "EMBEDDING_MODEL=text-embedding-3-small",
                "OPENAI_API_KEY=sk-test",
                "UNRELATED_SETTING=skip-me",
                "",
            ]
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "desktop-data"

    desktop._ensure_desktop_config(data_dir)

    config = data_dir / "config.env"
    values = desktop._read_env_file(config)
    assert os.environ["CONTEXTSEEK_CONFIG"] == str(config)
    assert values["STORAGE_BACKEND"] == "sqlite"
    assert values["SQLITE_PATH"] == "/tmp/project-contextseek.sqlite3"
    assert values["LLM_PROVIDER"] == "langchain"
    assert values["LLM_MODEL"] == "qwen-plus"
    assert values["EMBEDDING_PROVIDER"] == "langchain"
    assert values["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert values["OPENAI_API_KEY"] == "sk-test"
    assert "UNRELATED_SETTING" not in values


def test_ensure_desktop_config_falls_back_to_sqlite_none_defaults(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CONTEXTSEEK_CONFIG", raising=False)
    monkeypatch.setattr(desktop, "_desktop_config_seed_candidates", lambda _path: [])
    data_dir = tmp_path / "desktop-data"

    desktop._ensure_desktop_config(data_dir)

    values = desktop._read_env_file(data_dir / "config.env")
    assert values["STORAGE_BACKEND"] == "sqlite"
    assert values["SQLITE_PATH"] == str(data_dir / "contextseek.sqlite3")
    assert values["LLM_PROVIDER"] == "none"
    assert values["LLM_MODEL"] == "none"
    assert values["EMBEDDING_PROVIDER"] == "none"
    assert values["EMBEDDING_MODEL"] == "none"


def test_ensure_desktop_config_does_not_overwrite_existing_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CONTEXTSEEK_CONFIG", raising=False)
    data_dir = tmp_path / "desktop-data"
    data_dir.mkdir()
    config = data_dir / "config.env"
    config.write_text(
        "STORAGE_BACKEND=file\nSTORAGE_PATH=/tmp/store\n", encoding="utf-8"
    )

    desktop._ensure_desktop_config(data_dir)

    assert config.read_text(encoding="utf-8") == (
        "STORAGE_BACKEND=file\nSTORAGE_PATH=/tmp/store\n"
    )


def test_configure_desktop_runtime_marks_release_binary_auto(monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTSEEK_DESKTOP", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", raising=False)

    desktop._configure_desktop_runtime()

    assert os.environ["CONTEXTSEEK_DESKTOP"] == "1"
    assert os.environ["CONTEXTSEEK_POWERMEM_RUNTIME_MODE"] == "auto"


def test_configure_desktop_powermem_proxy_url_uses_desktop_server(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_PROXY_BASE_URL",
        "http://127.0.0.1:8000/plugins/powermem/default",
    )

    proxy_url = desktop._configure_desktop_powermem_proxy_url("0.0.0.0", 8123)

    assert proxy_url == "http://127.0.0.1:8123/plugins/powermem/default"
    assert os.environ["CONTEXTSEEK_POWERMEM_PROXY_BASE_URL"] == proxy_url


def test_publish_desktop_powermem_hook_env_writes_runtime_url(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from contextseek.plugs.powermem.linkers import claude_code_plugin

    monkeypatch.setattr(claude_code_plugin.Path, "home", lambda: tmp_path)
    plugin_dir = tmp_path / "claude-plugin"
    plugin_dir.mkdir()
    installed_plugin_dir = tmp_path.joinpath(
        ".claude",
        "plugins",
        "cache",
        "powermem",
        "memory-powermem",
        "0.1.0",
    )
    installed_plugin_dir.mkdir(parents=True)
    installed_plugins = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    installed_plugins.parent.mkdir(parents=True, exist_ok=True)
    installed_plugins.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "memory-powermem@powermem": [
                        {"installPath": str(installed_plugin_dir), "version": "0.1.0"},
                    ],
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        claude_code_plugin.ClaudeCodePluginRuntimeInstaller,
        "prepared_plugin_dir",
        lambda _self: plugin_dir,
    )

    desktop._publish_desktop_powermem_hook_env(
        "http://127.0.0.1:8123/plugins/powermem/default",
    )

    runtime_env = plugin_dir / "config" / "runtime.env"
    assert "POWERMEM_BASE_URL=http://127.0.0.1:8123/plugins/powermem/default" in (
        runtime_env.read_text(encoding="utf-8")
    )
    global_runtime_env = tmp_path / ".powermem" / "runtime.env"
    assert "POWERMEM_BASE_URL=http://127.0.0.1:8123/plugins/powermem/default" in (
        global_runtime_env.read_text(encoding="utf-8")
    )
    cached_runtime_env = installed_plugin_dir / "config" / "runtime.env"
    assert "POWERMEM_BASE_URL=http://127.0.0.1:8123/plugins/powermem/default" in (
        cached_runtime_env.read_text(encoding="utf-8")
    )


def test_managed_powermem_http_runtime_starts_child_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from contextseek.plugs.powermem import runtime_manager

    runtime_manager.stop_managed_powermem_http_runtime()
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(tmp_path / "pm.env"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_STARTUP_GRACE", "0")
    server = tmp_path / "powermem-server"
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o755)
    created: list[object] = []

    class FakeProcess:
        pid = 4321
        terminated = False

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    def fake_popen(command, **kwargs):
        created.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        runtime_manager.PowerMemHTTPRuntimeInstaller,
        "server_command",
        lambda _self: [str(server)],
    )
    monkeypatch.setattr(runtime_manager.subprocess, "Popen", fake_popen)

    try:
        state = runtime_manager.start_managed_powermem_http_runtime(port=18123)
        assert state is not None
        assert state.pid == 4321
        assert state.upstream_base_url == "http://127.0.0.1:18123"
        assert os.environ["CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL"] == (
            "http://127.0.0.1:18123"
        )
        command, kwargs = created[0]
        assert command == [
            str(server),
            "--host",
            "127.0.0.1",
            "--port",
            "18123",
        ]
        assert kwargs["cwd"] == tmp_path
    finally:
        runtime_manager.stop_managed_powermem_http_runtime()
