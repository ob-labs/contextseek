"""Tests for ConfigManager versioned store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextseek.config.manager import ConfigManager


@pytest.fixture()
def manager(tmp_path: Path) -> ConfigManager:
    m = ConfigManager(tmp_path / "config")
    m.init_store()
    return m


def test_init_store_creates_layout(manager: ConfigManager, tmp_path: Path):
    root = tmp_path / "config"
    assert (root / "history").is_dir()
    assert (root / "sources").is_dir()
    assert (root / "manifest.jsonl").is_file()
    # empty store has no current version
    assert manager.current() is None


def test_set_native_creates_first_version(manager: ConfigManager):
    v = manager.set_native("llm.model", "gpt-4o", author="cli:tq", reason="init llm")
    assert v.version_id == "v000001"
    assert v.parent_version_id is None
    assert v.origin == "manual"
    assert v.payload["native"]["llm"]["model"] == "gpt-4o"
    assert v.payload["effective"]["llm"]["model"] == "gpt-4o"
    assert manager.current().version_id == "v000001"


def test_versions_increment_and_chain(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    v2 = manager.set_native("llm.provider", "openai", author="a", reason="r2")
    assert v2.version_id == "v000002"
    assert v2.parent_version_id == "v000001"


def test_manifest_records_each_version(manager: ConfigManager, tmp_path: Path):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.provider", "openai", author="a", reason="r2")
    lines = (tmp_path / "config" / "manifest.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[1])
    assert rec["version_id"] == "v000002"
    assert rec["parent_version_id"] == "v000001"


def test_payload_hash_matches_file(manager: ConfigManager, tmp_path: Path):
    v = manager.set_native("llm.model", "gpt-4o", author="a", reason="r")
    raw = json.loads((tmp_path / "config" / "history" / "v000001.json").read_text())
    assert raw["payload_hash"] == v.payload_hash
    assert raw["payload_hash"].startswith("sha256:")


def test_history_returns_newest_first(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.provider", "openai", author="a", reason="r2")
    hist = manager.history()
    assert [h.version_id for h in hist] == ["v000002", "v000001"]


def test_merge_native_overrides_projected(manager: ConfigManager):
    manager.commit(
        projected={"llm": {"model": "projected-model"}},
        origin="agentseek-projection",
        author="agentseek",
        reason="proj",
        source_ref="agentseek@config.yml:sha256:abc",
    )
    v = manager.set_native("llm.model", "native-model", author="a", reason="override")
    eff = v.payload["effective"]
    assert eff["llm"]["model"] == "native-model"


def test_projected_used_when_native_absent(manager: ConfigManager):
    manager.commit(
        projected={"llm": {"model": "projected-model"}},
        origin="agentseek-projection",
        author="agentseek",
        reason="proj",
        source_ref="agentseek@config.yml:sha256:abc",
    )
    assert manager.current().payload["effective"]["llm"]["model"] == "projected-model"


def test_merge_preserves_projected_sibling_keys(manager: ConfigManager):
    manager.commit(
        projected={"llm": {"model": "p", "max_tokens": 4096}},
        origin="agentseek-projection",
        author="agentseek",
        reason="proj",
        source_ref="agentseek@config.yml:sha256:abc",
    )
    v = manager.set_native(
        "llm.model", "native-model", author="a", reason="override one key"
    )
    eff = v.payload["effective"]
    assert eff["llm"]["model"] == "native-model"
    assert eff["llm"]["max_tokens"] == 4096  # projected sibling preserved
    assert v.override_sources["llm.model"] == "native"
    assert v.override_sources["llm.max_tokens"] == "projected:agentseek"


def test_merge_deep_nested_override(manager: ConfigManager):
    manager.commit(
        projected={"a": {"b": {"c": "proj", "d": "keep"}}},
        origin="agentseek-projection",
        author="agentseek",
        reason="proj",
        source_ref="agentseek@config.yml:sha256:abc",
    )
    v = manager.set_native("a.b.c", "native", author="a", reason="deep override")
    eff = v.payload["effective"]
    assert eff["a"]["b"]["c"] == "native"
    assert eff["a"]["b"]["d"] == "keep"


def test_set_native_many_updates_multiple_keys(manager: ConfigManager):
    v = manager.set_native_many(
        {"llm.model": "gpt-4o", "llm.provider": "openai"},
        author="a",
        reason="batch",
    )
    eff = v.payload["effective"]
    assert eff["llm"]["model"] == "gpt-4o"
    assert eff["llm"]["provider"] == "openai"


def test_get_version_raises_for_unknown(manager: ConfigManager):
    import pytest

    manager.set_native("llm.model", "gpt-4o", author="a", reason="r")
    with pytest.raises(KeyError):
        manager.get_version("v999999")


def test_history_limit_respected(manager: ConfigManager):
    for i in range(5):
        manager.set_native("llm.model", f"m{i}", author="a", reason=f"r{i}")
    assert len(manager.history(n=3)) == 3


def test_rollback_is_append_only(manager: ConfigManager):
    v1 = manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.model", "gpt-4o-mini", author="a", reason="r2")
    v3 = manager.rollback("v000001", author="a", reason="rollback to v1")
    assert v3.version_id == "v000003"
    assert v3.origin == "rollback"
    assert v3.parent_version_id == "v000002"
    assert v3.payload["effective"]["llm"]["model"] == "gpt-4o"
    # v000002 仍在历史中
    ids = [h.version_id for h in manager.history()]
    assert "v000002" in ids
    assert manager.current().version_id == "v000003"


def test_redo_reverts_last_rollback(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.model", "gpt-4o-mini", author="a", reason="r2")
    manager.rollback("v000001", author="a", reason="back")
    v4 = manager.redo(author="a", reason="undo rollback")
    assert v4 is not None
    assert v4.payload["effective"]["llm"]["model"] == "gpt-4o-mini"
    assert v4.parent_version_id == "v000003"


def test_redo_returns_none_when_last_not_rollback(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    assert manager.redo(author="a", reason="x") is None


def test_diff_between_versions(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.model", "gpt-4o-mini", author="a", reason="r2")
    d = manager.diff("v000001", "v000002")
    assert "llm.model" in d["changed"]


def test_blame_finds_last_change(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    manager.set_native("llm.provider", "openai", author="b", reason="r2")
    blame = manager.blame("llm.model")
    assert blame["version_id"] == "v000001"
    assert blame["reason"] == "r1"
    blame_provider = manager.blame("llm.provider")
    assert blame_provider["version_id"] == "v000002"


def test_verify_passes_on_clean_store(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    assert manager.verify() == []


def test_verify_detects_tampered_payload(manager: ConfigManager, tmp_path: Path):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    path = tmp_path / "config" / "history" / "v000001.json"
    raw = json.loads(path.read_text())
    raw["payload"]["effective"]["llm"]["model"] = "tampered"
    path.write_text(json.dumps(raw))
    problems = manager.verify()
    assert any("hash" in p for p in problems)


def test_status_reports_current_and_count(manager: ConfigManager):
    manager.set_native("llm.model", "gpt-4o", author="a", reason="r1")
    s = manager.status()
    assert s["current_version"] == "v000001"
    assert s["version_count"] == 1


def test_apply_materializes_current(manager: ConfigManager, tmp_path: Path):
    from contextseek.config.materializer import Materializer

    manager.set_native("llm.model", "gpt-4o", author="a", reason="r")
    mat = Materializer(env_path=tmp_path / ".env", runtime_path=tmp_path / "config.json")
    manager.apply(mat)
    assert "LLM_MODEL=gpt-4o" in (tmp_path / ".env").read_text()


def test_apply_refuses_invalid_config(manager: ConfigManager, tmp_path: Path):
    from contextseek.config.materializer import Materializer

    # An unknown storage backend will fail RuntimeConfig/materialize validation.
    manager.set_native("storage.backend", "not-a-real-backend", author="a", reason="bad")
    mat = Materializer(env_path=tmp_path / ".env", runtime_path=tmp_path / "config.json")
    import pytest

    with pytest.raises(ValueError):
        manager.apply(mat)
    # files were not written
    assert not (tmp_path / ".env").exists()
