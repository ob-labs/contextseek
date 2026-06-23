"""Unit tests for desktop sidecar bootstrap helpers."""

from __future__ import annotations

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
