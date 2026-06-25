"""Tests for `contextseek config` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextseek.cli.main import run_cli


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("CONTEXTSEEK_HOME", str(h))
    monkeypatch.chdir(tmp_path)
    return h


def test_config_set_then_show(home: Path, tmp_path: Path):
    rc = run_cli(["config", "set", "llm.model", "gpt-4o", "--reason", "init"])
    assert rc == 0
    # show prints effective config; capture via capfd not needed—check store
    store = home / "config"
    history_files = list((store / "history").glob("cfg-*.json"))
    assert len(history_files) == 1


def test_config_history(home: Path):
    run_cli(["config", "set", "llm.model", "gpt-4o", "--reason", "r1"])
    run_cli(["config", "set", "llm.provider", "openai", "--reason", "r2"])
    rc = run_cli(["config", "history"])
    assert rc == 0


def test_config_rollback(home: Path):
    run_cli(["config", "set", "llm.model", "gpt-4o", "--reason", "r1"])
    run_cli(["config", "set", "llm.model", "gpt-4o-mini", "--reason", "r2"])
    manifest = (
        (home / "config" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    )
    first_id = json.loads(manifest[0])["version_id"]
    rc = run_cli(["config", "rollback", first_id, "--reason", "back"])
    assert rc == 0
    newest = json.loads(
        (home / "config" / "manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    v3 = json.loads(
        (home / "config" / "history" / f"{newest['version_id']}.json").read_text()
    )
    assert v3["origin"] == "rollback"
    assert v3["payload"]["effective"]["llm"]["model"] == "gpt-4o"


def test_config_verify_ok(home: Path):
    run_cli(["config", "set", "llm.model", "gpt-4o", "--reason", "r1"])
    rc = run_cli(["config", "verify"])
    assert rc == 0


def test_config_status(home: Path):
    run_cli(["config", "set", "llm.model", "gpt-4o", "--reason", "r1"])
    rc = run_cli(["config", "status"])
    assert rc == 0


def test_config_set_from_json_file(home: Path, tmp_path: Path):
    p = tmp_path / "updates.json"
    p.write_text(
        json.dumps({"llm.model": "gpt-4.1", "llm.provider": "openai"}), encoding="utf-8"
    )
    rc = run_cli(["config", "set", "--file", str(p), "--reason", "batch"])
    assert rc == 0
    only_file = next((home / "config" / "history").glob("cfg-*.json"))
    v = json.loads(only_file.read_text(encoding="utf-8"))
    assert v["payload"]["effective"]["llm"]["model"] == "gpt-4.1"
    assert v["payload"]["effective"]["llm"]["provider"] == "openai"


def test_config_set_from_env_file(home: Path, tmp_path: Path):
    p = tmp_path / "updates.env"
    p.write_text("LLM_MODEL=gpt-4o-mini\nLLM_PROVIDER=openai\n", encoding="utf-8")
    rc = run_cli(["config", "set", "--file", str(p), "--reason", "batch"])
    assert rc == 0
    only_file = next((home / "config" / "history").glob("cfg-*.json"))
    v = json.loads(only_file.read_text(encoding="utf-8"))
    assert v["payload"]["effective"]["llm"]["model"] == "gpt-4o-mini"
    assert v["payload"]["effective"]["llm"]["provider"] == "openai"
