"""Self-contained desktop Env Vault feature.

A small, generic environment-variable manager that is intentionally decoupled
from the rest of ContextSeek (it does not touch ``config.env``,
``_update_env_file`` or ``ContextSeekSettings``). It stores reusable KEY=value
records in an encrypted, application-keyed vault file and can apply them to any
project's ``.env.example`` template to generate a ``.env`` file.

Scope (MVP):
  - encrypted KV store with a fixed application-level key (no passphrase)
  - parse a target ``.env.example`` into ordered keys
  - generate ``.env`` from the template, filling values from the vault
  - sync newly entered/updated values back into the vault

Out of scope: comment/format preservation, multiple env files, OS keychain,
``${VAR}`` expansion.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# --- Encryption -----------------------------------------------------------

# Application-level fixed secret. This is deliberately NOT a user passphrase:
# the MVP only needs at-rest obfuscation so the vault is not plain text on disk.
# A real secret manager would derive this from an OS keychain / user passphrase.
_APP_SECRET = "contextseek.env-vault.v1.fixed-app-key"


def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(_APP_SECRET.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# --- Paths ----------------------------------------------------------------


def _default_data_dir() -> Path:
    """Platform-standard application data directory (kept local to stay decoupled)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "contextseek"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "contextseek"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / "contextseek"


def _vault_path() -> Path:
    override = os.environ.get("CONTEXTSEEK_ENV_VAULT_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return _default_data_dir() / "env_vault.enc"


def _read_contextseek_config_values() -> dict[str, str]:
    """Read ContextSeek's currently effective ``KEY=value`` config values.

    Used only by the "seed from ContextSeek" bridge. Values come from the
    resolved config file (e.g. desktop ``config.env``) with the process
    environment as a fallback. API keys are stored under ``*_KWARGS`` JSON, so
    they are unpacked here into ``*_API_KEY`` to match ``.env.example`` keys.
    """
    values: dict[str, str] = {}

    try:
        from contextseek.config.settings import _get_default_env_file

        env_file = _get_default_env_file()
    except Exception:
        env_file = None

    if env_file:
        path = Path(env_file)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = _ENV_LINE_RE.match(line)
                if not match:
                    continue
                raw = match.group(2).strip().strip('"').strip("'")
                values[match.group(1)] = raw

    # Unpack api_key from *_KWARGS JSON (config file first, then environment).
    for kwargs_key, api_key_field in (
        ("LLM_KWARGS", "LLM_API_KEY"),
        ("EMBEDDING_KWARGS", "EMBEDDING_API_KEY"),
    ):
        raw = values.get(kwargs_key) or os.environ.get(kwargs_key, "")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        api_key = parsed.get("api_key") if isinstance(parsed, dict) else None
        if api_key:
            values.setdefault(api_key_field, str(api_key))

    return values


# --- Secret heuristic -----------------------------------------------------

_SECRET_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|PRIVATE)",
    re.IGNORECASE,
)


def _is_secret(key: str) -> bool:
    return bool(_SECRET_RE.search(key))


# --- Vault store ----------------------------------------------------------


def _load_vault() -> dict[str, str]:
    path = _vault_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_bytes()
        if not raw:
            return {}
        decrypted = _fernet().decrypt(raw)
        data = json.loads(decrypted.decode("utf-8"))
    except Exception as exc:  # corrupt / wrong key -> surface clearly
        raise HTTPException(
            status_code=500, detail=f"failed to read env vault: {exc}"
        ) from exc
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _save_vault(data: dict[str, str]) -> None:
    path = _vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    token = _fernet().encrypt(payload)
    path.write_bytes(token)


# --- Template parsing -----------------------------------------------------

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _parse_env_keys(text: str) -> list[tuple[str, str]]:
    """Return ordered (key, default_value) pairs, ignoring comments/blank lines."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        default = match.group(2).strip()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key, default))
    return pairs


def _resolve_template_path(template_path: str) -> Path:
    """Resolve a template path, accepting either a file or a directory.

    A directory (e.g. ``/path/to/project/``) resolves to ``<dir>/.env.example``;
    a full file path is used as-is. Raises 404 if nothing usable is found.
    """
    path = Path(template_path).expanduser()
    if path.is_dir():
        path = path / ".env.example"
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail=f"template not found: {path}"
        )
    return path


def _parse_env_comments(text: str) -> dict[str, str]:
    """Map each key to the contiguous comment block directly above it.

    Leading ``#`` and surrounding spaces are stripped; multiple comment lines
    are joined with newlines. A blank line or a non-comment line resets the
    pending comment buffer so only comments immediately above a key count.
    """
    comments: dict[str, str] = {}
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            buffer = []
            continue
        if stripped.startswith("#"):
            buffer.append(stripped.lstrip("#").strip())
            continue
        match = _ENV_LINE_RE.match(line)
        if match:
            key = match.group(1)
            if buffer and key not in comments:
                comments[key] = "\n".join(buffer)
        buffer = []
    return comments


def _read_template(template_path: str) -> tuple[Path, str]:
    path = _resolve_template_path(template_path)
    try:
        return path, path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"failed to read template: {exc}"
        ) from exc


def _env_template_sort_key(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    if name == ".env.example":
        priority = 0
    elif name.endswith(".example") or ".example." in name:
        priority = 1
    elif "example" in name or "template" in name or "sample" in name:
        priority = 2
    elif name == ".env":
        priority = 9
    else:
        priority = 5
    return priority, name


def _env_template_candidate(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    key_count = len(_parse_env_keys(text))
    if key_count == 0:
        return None
    return {
        "name": path.name,
        "path": str(path),
        "key_count": key_count,
    }


def _list_env_templates(root_path: str) -> tuple[Path, list[dict[str, Any]]]:
    root = Path(root_path).expanduser()
    if not root.exists():
        raise HTTPException(
            status_code=404, detail=f"path not found: {root}"
        )

    if root.is_file():
        candidate = _env_template_candidate(root)
        return root.parent, [candidate] if candidate else []

    if not root.is_dir():
        raise HTTPException(
            status_code=400, detail=f"path is not a file or directory: {root}"
        )

    candidates: list[dict[str, Any]] = []
    try:
        children = sorted(
            (child for child in root.iterdir() if child.name.startswith(".env")),
            key=_env_template_sort_key,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"failed to scan directory: {exc}"
        ) from exc

    for child in children:
        candidate = _env_template_candidate(child)
        if candidate:
            candidates.append(candidate)

    return root, candidates


# --- API models -----------------------------------------------------------


class VaultItem(BaseModel):
    key: str
    value: str = ""
    is_secret: bool = False


class VaultUpsertRequest(BaseModel):
    items: list[VaultItem] = Field(default_factory=list)


class ParseTemplateRequest(BaseModel):
    template_path: str


class ListTemplatesRequest(BaseModel):
    path: str


class TemplateKey(BaseModel):
    key: str
    default: str = ""
    value_from_vault: str = ""
    is_missing: bool = True
    is_secret: bool = False
    comment: str = ""


class GenerateRequest(BaseModel):
    template_path: str
    output_path: str
    values: dict[str, str] = Field(default_factory=dict)
    overwrite: bool = False


# --- Router ---------------------------------------------------------------


def create_env_vault_router() -> APIRouter:
    router = APIRouter(prefix="/env-vault", tags=["env-vault"])

    @router.get("/items")
    async def list_items() -> dict[str, Any]:
        vault = _load_vault()
        items = [
            VaultItem(key=k, value=v, is_secret=_is_secret(k)).model_dump()
            for k, v in sorted(vault.items())
        ]
        return {"items": items}

    @router.put("/items")
    async def upsert_items(req: VaultUpsertRequest) -> dict[str, Any]:
        vault = _load_vault()
        for item in req.items:
            key = item.key.strip()
            if not key:
                continue
            vault[key] = item.value
        _save_vault(vault)
        return {"status": "ok", "count": len(vault)}

    @router.delete("/items/{key}")
    async def delete_item(key: str) -> dict[str, Any]:
        vault = _load_vault()
        existed = vault.pop(key, None) is not None
        if existed:
            _save_vault(vault)
        return {"status": "ok", "deleted": existed}

    @router.post("/list-templates")
    async def list_templates(req: ListTemplatesRequest) -> dict[str, Any]:
        root, templates = _list_env_templates(req.path)
        return {"root_path": str(root), "templates": templates}

    @router.post("/parse-template")
    async def parse_template(req: ParseTemplateRequest) -> dict[str, Any]:
        resolved, text = _read_template(req.template_path)
        vault = _load_vault()
        comments = _parse_env_comments(text)
        keys: list[dict[str, Any]] = []
        for key, default in _parse_env_keys(text):
            in_vault = key in vault
            keys.append(
                TemplateKey(
                    key=key,
                    default=default,
                    value_from_vault=vault.get(key, ""),
                    is_missing=not in_vault,
                    is_secret=_is_secret(key),
                    comment=comments.get(key, ""),
                ).model_dump()
            )
        return {"template_path": str(resolved), "keys": keys}

    @router.post("/generate")
    async def generate(req: GenerateRequest) -> dict[str, Any]:
        _resolved, text = _read_template(req.template_path)
        ordered = _parse_env_keys(text)
        vault = _load_vault()

        out_path = Path(req.output_path).expanduser()
        # Don't clobber an existing .env without explicit confirmation.
        if out_path.exists() and not req.overwrite:
            return {"status": "exists", "output_path": str(out_path)}

        # Resolve the final value per key (provided > vault > template default)
        # and decide what to sync back into the vault.
        final_values: dict[str, str] = {}
        synced = 0
        for key, default in ordered:
            if key in req.values:
                value = req.values[key]
            elif key in vault:
                value = vault[key]
            else:
                value = default
            final_values[key] = value
            # Sync back only real, reusable values: skip empty values and
            # untouched template defaults (those are project boilerplate, not
            # something worth keeping in the vault). Only persist on change.
            provided = req.values.get(key)
            if (
                provided is not None
                and provided.strip()
                and provided != default
                and vault.get(key) != provided
            ):
                vault[key] = provided
                synced += 1

        # Render line-by-line from the template so comments, blank lines and
        # overall layout are preserved; only KEY= lines get their value swapped.
        out_lines: list[str] = []
        for line in text.splitlines():
            match = _ENV_LINE_RE.match(line)
            if match and match.group(1) in final_values:
                key = match.group(1)
                prefix = line.split("=", 1)[0]  # keep 'export '/spacing/key as-is
                out_lines.append(f"{prefix}={final_values[key]}")
            else:
                out_lines.append(line)

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"failed to write .env: {exc}"
            ) from exc

        if synced:
            _save_vault(vault)

        return {
            "status": "ok",
            "output_path": str(out_path),
            "written_keys": len(final_values),
            "synced_to_vault": synced,
        }

    @router.post("/seed-contextseek")
    async def seed_contextseek() -> dict[str, Any]:
        # Import ContextSeek's actually-configured values (e.g. desktop
        # config.env), not the mostly-commented .env.example template. Raw
        # *_KWARGS JSON is dropped since its api_key is unpacked into *_API_KEY.
        config_values = _read_contextseek_config_values()
        skip_keys = {"LLM_KWARGS", "EMBEDDING_KWARGS"}
        vault = _load_vault()
        added = 0
        removed = 0
        for key, raw in config_values.items():
            if key in skip_keys:
                continue
            value = raw.strip()
            # Disabled/unset in config ("none" or empty): drop any stale entry
            # so the vault reflects the current ContextSeek configuration.
            if not value or value.lower() == "none":
                if vault.pop(key, None) is not None:
                    removed += 1
                continue
            # Overwrite existing keys with the current value.
            if vault.get(key) != value:
                vault[key] = value
                added += 1
        _save_vault(vault)
        return {"status": "ok", "added": added, "removed": removed}

    return router
