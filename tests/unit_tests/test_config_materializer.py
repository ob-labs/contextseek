# tests/unit_tests/test_config_materializer.py
"""Tests for Materializer."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextseek.config.materializer import (
    Materializer,
    effective_to_env,
    effective_to_runtime_json,
)


@pytest.fixture()
def materializer(tmp_path: Path) -> Materializer:
    return Materializer(
        env_path=tmp_path / ".env", runtime_path=tmp_path / "config.json"
    )


def test_effective_to_env_writes_known_keys():
    env = effective_to_env({"llm": {"model": "gpt-4o", "provider": "openai"}})
    assert "LLM_MODEL=gpt-4o" in env
    assert "LLM_PROVIDER=openai" in env


def test_effective_to_runtime_json_includes_runtime_section():
    rt = effective_to_runtime_json(
        {"runtime": {"backend": "file", "storage_path": "/data"}}
    )
    assert rt["backend"] == "file"
    assert rt["storage_path"] == "/data"


def test_materialize_writes_both_files(materializer: Materializer, tmp_path: Path):
    materializer.materialize({"llm": {"model": "gpt-4o"}})
    assert (tmp_path / ".env").is_file()
    assert (tmp_path / "config.json").is_file()
    assert "LLM_MODEL=gpt-4o" in (tmp_path / ".env").read_text()


def test_dry_run_validate_ok_for_minimal(materializer: Materializer):
    ok, err = materializer.dry_run_validate({"storage": {"backend": "file"}})
    assert ok is True
    assert err is None


def test_dry_run_validate_rejects_unknown_backend(materializer: Materializer):
    ok, err = materializer.dry_run_validate(
        {"storage": {"backend": "not-a-real-backend"}}
    )
    assert ok is False
    assert "unsupported storage backend" in err


def test_detect_drift_when_file_hand_edited(materializer: Materializer, tmp_path: Path):
    eff = {"llm": {"model": "gpt-4o"}}
    materializer.materialize(eff)
    # hand-edit the .env
    (tmp_path / ".env").write_text("LLM_MODEL=tampered\n")
    drift = materializer.detect_drift(eff)
    assert drift["env"] is True


def test_effective_to_env_passes_through_extra_env():
    # _extra_env holds non-settings keys preserved during migration.
    env = effective_to_env(
        {"llm": {"model": "gpt-4o"}, "_extra_env": {"SOME_OTHER_VAR": "keep-me"}}
    )
    assert "LLM_MODEL=gpt-4o" in env
    assert "SOME_OTHER_VAR=keep-me" in env


def test_effective_to_env_serializes_dict_kwargs_as_json():
    # dict-valued settings fields (LLM_KWARGS) must be valid JSON, not a repr.
    env = effective_to_env({"llm": {"kwargs": {"api_key": "sk-x"}}})
    import json as _json

    # find the LLM_KWARGS line
    line = next(ln for ln in env.splitlines() if ln.startswith("LLM_KWARGS="))
    raw = line.split("=", 1)[1]
    parsed = _json.loads(raw)  # must parse
    assert parsed == {"api_key": "sk-x"}
