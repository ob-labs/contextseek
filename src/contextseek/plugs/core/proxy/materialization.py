"""Shared materialization response helpers for plug proxies."""

from __future__ import annotations

from typing import Any


def contextseek_meta(materialized: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(materialized)
    failed = sum(1 for item in materialized if item.get("status") == "failed")
    if total == 0:
        status = "no_events"
    elif failed == 0:
        status = "ok"
    elif failed == total:
        status = "failed"
    else:
        status = "partial_failed"
    return {
        "status": status,
        "materialized": materialized,
        "total": total,
        "failed": failed,
    }
