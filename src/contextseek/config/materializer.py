# src/contextseek/config/materializer.py
"""Materialize an effective config into the files existing loaders already read.

- ``.env``        → consumed by :class:`ContextSeekSettings` (pydantic-settings).
- ``config.json`` → consumed by :func:`contextseek.config.runtime.load_runtime_config`
  via the ``CONTEXTSEEK_CONFIG`` env var.

Before writing, ``dry_run_validate`` constructs a ``ContextSeekSettings`` and a
``RuntimeConfig`` from the effective payload to ensure the materialized files
will actually load.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from contextseek.config.envreflector import iter_section_env_fields
from contextseek.config.settings import ContextSeekSettings

SUPPORTED_STORAGE_BACKENDS = frozenset(
    {"memory", "file", "sqlite", "seekdb", "oceanbase"}
)


def _flat_get(d: dict, dotted_key: str) -> Any:
    cur: Any = d
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        # Settings-backed dict/list fields (e.g. LLM_KWARGS) must be valid JSON
        # so pydantic-settings can parse them back; a Python repr would break.
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def effective_to_env(effective: dict) -> str:
    """Render an effective config as ``.env`` text (``KEY=value`` lines).

    Settings-backed fields are emitted via :func:`iter_section_env_fields`.
    Any non-settings keys preserved under ``effective["_extra_env"]`` (during
    migration) are appended verbatim so a full rewrite never drops them.
    """
    section_fields = list(iter_section_env_fields())
    lines: list[str] = []
    for section, field, env_name in section_fields:
        value = _flat_get(effective, f"{section}.{field}")
        if value is None:
            continue
        lines.append(f"{env_name}={_render_value(value)}")
    for key, value in (effective.get("_extra_env") or {}).items():
        lines.append(f"{key}={_render_value(value)}")
    return "\n".join(lines) + ("\n" if lines else "")


def effective_to_runtime_json(effective: dict) -> dict:
    """Render the ``runtime`` section of an effective config as a RuntimeConfig JSON payload."""
    runtime = effective.get("runtime", {})
    # load_runtime_config reads backend/storage_path/uri_scheme/cold_backend/
    # cold_storage_path/strategy/api_keys/ob_* from the JSON top level.
    return dict(runtime)


class Materializer:
    """Write effective config to the ``.env`` and ``config.json`` loaders read."""

    def __init__(self, env_path: Path, runtime_path: Path) -> None:
        self.env_path = Path(env_path)
        self.runtime_path = Path(runtime_path)

    def materialize(self, effective: dict) -> None:
        ok, err = self.dry_run_validate(effective)
        if not ok:
            msg = f"refusing to materialize invalid config: {err}"
            raise ValueError(msg)
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_text(effective_to_env(effective), encoding="utf-8")
        rt = effective_to_runtime_json(effective)
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_path.write_text(
            json.dumps(rt, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def dry_run_validate(self, effective: dict) -> tuple[bool, str | None]:
        """Return ``(ok, error)``. ``ok`` iff both loaders can construct from effective."""
        backend = _flat_get(effective, "storage.backend")
        if backend is not None and backend not in SUPPORTED_STORAGE_BACKENDS:
            return False, f"unsupported storage backend: {backend}"
        env_text = effective_to_env(effective)
        # Validate ContextSeekSettings by populating a fake env and constructing.
        # Backup → set → construct → restore, guarded by try/finally so a
        # validation failure can never leak env state into other tests.
        env_backup = dict(os.environ)
        try:
            for line in env_text.splitlines():
                if not line.strip() or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k] = v
            ContextSeekSettings()
        except Exception as exc:  # noqa: BLE001 - any validation error is a failure
            return False, f"ContextSeekSettings: {exc}"
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

        # Validate RuntimeConfig JSON payload via load_runtime_config (temp file).
        import tempfile

        from contextseek.config.runtime import load_runtime_config

        rt_json = effective_to_runtime_json(effective)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as fh:
                json.dump(rt_json, fh)
                tmp_path = fh.name
            load_runtime_config(tmp_path)
        except Exception as exc:  # noqa: BLE001 - any validation error is a failure
            return False, f"RuntimeConfig: {exc}"
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return True, None

    def expected_hashes(self, effective: dict) -> tuple[str, str]:
        env_text = effective_to_env(effective)
        rt_text = json.dumps(
            effective_to_runtime_json(effective), ensure_ascii=False, indent=2
        )
        env_hash = "sha256:" + hashlib.sha256(env_text.encode("utf-8")).hexdigest()
        rt_hash = "sha256:" + hashlib.sha256(rt_text.encode("utf-8")).hexdigest()
        return env_hash, rt_hash

    def detect_drift(self, effective: dict) -> dict[str, bool]:
        """Return ``{"env": drifted, "runtime": drifted}``. True == file differs from expected."""
        env_hash, rt_hash = self.expected_hashes(effective)
        env_drift = True
        if self.env_path.exists():
            actual = (
                "sha256:"
                + hashlib.sha256(
                    self.env_path.read_text(encoding="utf-8").encode("utf-8")
                ).hexdigest()
            )
            env_drift = actual != env_hash
        rt_drift = True
        if self.runtime_path.exists():
            actual = (
                "sha256:"
                + hashlib.sha256(
                    self.runtime_path.read_text(encoding="utf-8").encode("utf-8")
                ).hexdigest()
            )
            rt_drift = actual != rt_hash
        return {"env": env_drift, "runtime": rt_drift}
