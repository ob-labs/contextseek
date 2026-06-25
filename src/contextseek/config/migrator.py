"""Migrate existing ``.env`` / ``config.json`` into the managed config store.

First-time adoption: the managed store is empty, so a full-rewrite materialize
would drop keys present in ``.env`` but not tracked by ``ContextSeekSettings``.
``import_existing`` reflects env vars back to ``section.field`` paths and parks
untracked keys under ``_extra_env`` so the materializer re-emits them verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextseek.config.envreflector import env_to_section_field
from contextseek.config.manager import ConfigManager, ConfigVersion


def _parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _set_path(nested: dict, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = nested
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def import_existing(env_path: Path | None, runtime_path: Path | None) -> dict:
    """Build a ``native`` payload from existing ``.env`` / ``config.json`` files."""
    native: dict = {"_extra_env": {}}
    reverse = env_to_section_field()
    if env_path is not None:
        env_path = Path(env_path)
        if env_path.exists():
            for key, value in _parse_env_file(env_path).items():
                if key in reverse:
                    section, field = reverse[key]
                    stored: Any = value
                    # ``kwargs`` is a dict field exposed as a JSON env string
                    # (LLM_KWARGS/EMBEDDING_KWARGS). Parse it back into a dict
                    # so a later ``set_native("llm.kwargs.api_key", ...)``
                    # can walk into it.
                    if (
                        field == "kwargs"
                        and isinstance(value, str)
                        and value
                        and value[0] in "{["
                    ):
                        try:
                            stored = json.loads(value)
                        except (json.JSONDecodeError, ValueError):
                            stored = value
                    _set_path(native, f"{section}.{field}", stored)
                else:
                    native["_extra_env"][key] = value
    if runtime_path is not None:
        runtime_path = Path(runtime_path)
        if runtime_path.exists():
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
            native["runtime"] = dict(payload)
    return native


def migrate_into(
    manager: ConfigManager,
    *,
    env_path: Path | None = None,
    runtime_path: Path | None = None,
    author: str = "system",
    reason: str = "migrate existing config",
) -> ConfigVersion | None:
    """Commit existing config as v1 (origin=migration). No-op if store non-empty."""
    if manager.current() is not None:
        return None
    native = import_existing(env_path, runtime_path)
    return manager.commit(
        native=native,
        origin="migration",
        author=author,
        reason=reason,
    )
