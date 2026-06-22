"""Security helpers for source validation, redaction, and ACL checks."""

from __future__ import annotations

import re
from typing import Any

from contextseek.config import WriteStrategy

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_TOKEN_RE = re.compile(r"\b(?:sk|api|key)_[A-Za-z0-9]{8,}\b")


def source_allowed(source: dict[str, Any], *, strategy: WriteStrategy) -> bool:
    """Return whether the source is permitted under strategy."""
    if strategy.allow_any_source:
        return True
    source_name = str(source.get("source", ""))
    return source_name in set(strategy.allowed_sources)


def redact_value(value: Any, *, strategy: WriteStrategy) -> Any:
    """Recursively redact sensitive text fields in content/source payload."""
    if not strategy.redact_sensitive and not strategy.redact_fields:
        return value
    if isinstance(value, str):
        redacted = value
        if strategy.redact_sensitive:
            redacted = _EMAIL_RE.sub(strategy.redaction_token, redacted)
            redacted = _PHONE_RE.sub(strategy.redaction_token, redacted)
            redacted = _TOKEN_RE.sub(strategy.redaction_token, redacted)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, strategy=strategy) for item in value]
    if isinstance(value, dict):
        redacted_fields = set(strategy.redact_fields)
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in redacted_fields:
                out[key] = strategy.redaction_token
                continue
            out[key] = redact_value(item, strategy=strategy)
        return out
    return value


def _drop_fields(value: Any, *, drop_fields: set[str]) -> Any:
    if not drop_fields:
        return value
    if isinstance(value, list):
        return [_drop_fields(item, drop_fields=drop_fields) for item in value]
    if isinstance(value, dict):
        return {
            key: _drop_fields(item, drop_fields=drop_fields)
            for key, item in value.items()
            if key not in drop_fields
        }
    return value


def apply_write_policy(value: Any, *, strategy: WriteStrategy) -> Any:
    """Apply write-time transformations (drop fields, then redact)."""
    dropped = _drop_fields(value, drop_fields=set(strategy.drop_fields))
    return redact_value(dropped, strategy=strategy)


def can_access_payload(
    payload: dict[str, Any],
    *,
    scope: str,
    strategy: WriteStrategy,
    action: str = "read",
) -> bool:
    """Evaluate ACL and scope ownership for one payload and action."""
    payload_scope = str(payload.get("scope", ""))
    if payload_scope and payload_scope != scope:
        # Reads may cross into the queried scope's subtree so hierarchical
        # retrieval can surface items from sub-scopes; writes / manage stay
        # exact-scope. Per-item ACL below is derived from the item's own scope.
        in_subtree = payload_scope.startswith(scope.rstrip("/") + "/")
        if not (action == "read" and in_subtree):
            return False
    if not strategy.acl_enabled:
        return True
    source_meta = dict(payload.get("source_meta", {}))
    acl = source_meta.get("acl")
    if not isinstance(acl, dict):
        return True

    # Extract subject/tenant from the item's OWN scope (format:
    # tenant/project/subject) so subtree reads enforce each item's ACL.
    eff_scope = payload_scope or scope
    parts = eff_scope.split("/")
    subject_id = parts[-1] if parts else ""
    tenant_id = parts[0] if parts else ""

    action_key = "read_subjects" if action == "read" else "manage_subjects"
    allowed_subjects = acl.get(action_key)
    if allowed_subjects is None:
        allowed_subjects = acl.get("read_subjects")
    if allowed_subjects is not None and subject_id not in set(allowed_subjects):
        return False

    allowed_tenants = acl.get("tenants")
    if allowed_tenants is not None and tenant_id not in set(allowed_tenants):
        return False
    return True
