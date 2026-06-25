"""Tests for migrating existing .env / config.json into the managed store."""

from __future__ import annotations

from pathlib import Path

from contextseek.config.manager import ConfigManager
from contextseek.config.migrator import import_existing, migrate_into


def test_import_existing_maps_env_to_native(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=gpt-4o\nLLM_PROVIDER=openai\nSOME_OTHER=keep\n")
    native = import_existing(env_path=env, runtime_path=None)
    assert native["llm"]["model"] == "gpt-4o"
    assert native["llm"]["provider"] == "openai"
    # non-settings key preserved in _extra_env
    assert native["_extra_env"]["SOME_OTHER"] == "keep"


def test_import_existing_parses_kwargs_json_into_dict(tmp_path: Path):
    # LLM_KWARGS is a dict field exposed as a JSON string; import must parse it
    # so a later set_native("llm.kwargs.api_key", ...) can walk into the dict.
    env = tmp_path / ".env"
    env.write_text('LLM_KWARGS={"api_key":"sk-imported","timeout":30}\n')
    native = import_existing(env_path=env, runtime_path=None)
    assert native["llm"]["kwargs"] == {"api_key": "sk-imported", "timeout": 30}


def test_import_existing_keeps_kwargs_raw_on_bad_json(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("LLM_KWARGS=not-json-but-starts-with-text\n")
    native = import_existing(env_path=env, runtime_path=None)
    assert native["llm"]["kwargs"] == "not-json-but-starts-with-text"


def test_migrate_into_creates_v1_migration(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=gpt-4o\n")
    mgr = ConfigManager(tmp_path / "config")
    mgr.init_store()
    v = migrate_into(mgr, env_path=env, runtime_path=None)
    assert v is not None
    assert v.origin == "migration"
    assert v.version_id.startswith("cfg-")
    assert v.payload["native"]["llm"]["model"] == "gpt-4o"


def test_migrate_into_noop_when_store_nonempty(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=gpt-4o\n")
    mgr = ConfigManager(tmp_path / "config")
    mgr.init_store()
    mgr.set_native("llm.model", "existing", author="a", reason="r")
    v = migrate_into(mgr, env_path=env, runtime_path=None)
    assert v is None
    assert len(mgr.history()) == 1
