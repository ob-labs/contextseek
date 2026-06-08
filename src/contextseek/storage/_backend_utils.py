"""Shared utilities for vector-store backends (OceanBase, SeekDB).

Functions and constants here are engine-agnostic: they deal only with
payload serialisation, path manipulation, and SQL-string helpers that both
backends need identically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

_HOISTED: frozenset[str] = frozenset(
    {
        "embedding",
        "content",
        "abstract",
        "summary",
        "scope",
        "stage",
        "searchable",
        "hash",
    }
)
"""Fields stored in dedicated columns/metadata keys; stripped from payload_json on write."""


def _namespace_of(ref: str) -> str:
    """Return the parent-directory prefix of *ref* (always ends with ``/``)."""
    if "/" not in ref:
        return ref
    return ref.rsplit("/", 1)[0] + "/"


def _serialize_dt(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    if v is None:
        return ""
    return str(v)


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip through json to coerce datetimes (and other non-JSON types) safely."""

    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    return json.loads(json.dumps(payload, default=_default))


def _parse_vector(v: Any) -> list[float] | None:
    """Coerce a VECTOR column value (numpy ndarray or list) to a Python list of floats."""
    if v is None:
        return None
    if hasattr(v, "tolist"):
        try:
            return [float(x) for x in v.tolist()]
        except (TypeError, ValueError):
            return None
    if isinstance(v, list):
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None
    return None


def _merge_hoisted(
    payload_json: Any,
    content: str | None,
    abstract: str | None,
    summary: str | None,
    abstract_embedding: Any,
    *,
    scope: str | None = None,
    stage: str | None = None,
    searchable: int | bool | None = None,
    hash_val: str | None = None,
) -> dict[str, Any]:
    """Reconstruct a full payload dict by merging hoisted column/metadata values back in.

    ``payload_json`` is the slim JSON stored in the DB (hoisted fields stripped).
    The remaining args come from their dedicated columns and are merged back so
    callers get a complete payload identical to what was originally written.
    """
    if isinstance(payload_json, str):
        d: dict[str, Any] = json.loads(payload_json) if payload_json else {}
    elif isinstance(payload_json, dict):
        d = dict(payload_json)
    else:
        d = {}

    if content:
        try:
            d["content"] = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            d["content"] = content
    elif "content" not in d:
        # Backward compat: old payloads stored content inside the JSON blob itself;
        # only fall back to empty string when the field is genuinely absent.
        d["content"] = ""

    d["abstract"] = abstract if abstract is not None else d.get("abstract", "")
    d["summary"] = summary if summary is not None else d.get("summary", "")

    emb = _parse_vector(abstract_embedding)
    if emb is not None:
        d["embedding"] = emb
    else:
        d.pop("embedding", None)

    if scope is not None:
        d["scope"] = scope
    if stage is not None:
        d["stage"] = stage
    if searchable is not None:
        d["searchable"] = bool(searchable)
    if hash_val is not None:
        d["hash"] = hash_val

    return d


def _parse_updated_at(updated_at: Any) -> datetime:
    if not updated_at:
        return datetime.now(tz=UTC)
    try:
        dt = datetime.fromisoformat(str(updated_at))
    except Exception:
        return datetime.now(tz=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _prefix_from_pattern(path_pattern: str | None) -> str | None:
    """Strip a single trailing ``*`` from a glob pattern to derive a prefix.

    ``seekvfs://ns/*`` → ``seekvfs://ns/``. ``None`` is passed through.
    """
    if path_pattern is None:
        return None
    return path_pattern.removesuffix("*")
