# tests/unit_tests/test_config_envreflector.py
"""Tests for ContextSeekSettings env-var reflection."""

from __future__ import annotations

from contextseek.config.envreflector import (
    env_to_section_field,
    iter_env_vars,
    iter_section_env_fields,
)


def test_iter_env_vars_includes_known_keys():
    names = set(iter_env_vars())
    assert "STORAGE_BACKEND" in names
    assert "LLM_PROVIDER" in names
    assert "LLM_MODEL" in names


def test_iter_section_env_fields_pairs_section_field_env():
    triples = list(iter_section_env_fields())
    # (section, field, env_name)
    assert ("storage", "backend", "STORAGE_BACKEND") in triples
    assert ("llm", "model", "LLM_MODEL") in triples
    # every env name is uppercase
    for _section, _field, env in triples:
        assert env == env.upper()


def test_env_to_section_field_reverse_map():
    rev = env_to_section_field()
    assert rev["STORAGE_BACKEND"] == ("storage", "backend")
    assert rev["LLM_MODEL"] == ("llm", "model")
