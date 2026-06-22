"""State-store helpers for PlugGateway."""

from __future__ import annotations

from typing import Any

from contextseek.storage.protocol import SyncCapableMixin


class PlugStateStoreUnavailable(RuntimeError):
    """Raised when the active storage backend cannot store PlugGateway state."""


def resolve_plug_state_store(adapter: Any) -> SyncCapableMixin:
    """Return the sync-capable backend that owns PlugGateway state."""
    hot = getattr(adapter, "hot", None)
    if hot is not None:
        return resolve_plug_state_store(hot)

    if isinstance(adapter, SyncCapableMixin):
        adapter.ensure_plug_tables()
        return adapter

    try:
        router = adapter._vfs._router
        _, route = router.resolve("contextseek://")
        backend = route.get("backend") if isinstance(route, dict) else None
    except Exception as exc:  # noqa: BLE001
        msg = "active adapter does not expose a PlugGateway state backend"
        raise PlugStateStoreUnavailable(msg) from exc

    if not isinstance(backend, SyncCapableMixin):
        msg = (
            "PlugGateway requires a sync-capable backend (sqlite, seekdb, or oceanbase)"
        )
        raise PlugStateStoreUnavailable(msg)

    backend.ensure_plug_tables()
    return backend
