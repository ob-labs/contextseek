"""Tests for PlugGateway materialization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from seekvfs import VFS

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.serialization import deserialize_context_item
from contextseek.plugs.core.gateway import OutboxWorker, PlugGateway
from contextseek.plugs.core.proxy.materialization import contextseek_meta
from contextseek.plugs.core.protocols import (
    PlugChangeEvent,
    PlugProxyResponse,
    PlugProxyResult,
)
from contextseek.storage.sqlite_backend import SQLiteBackend
from contextseek.storage.storage_adapter import SeekVFSStorageAdapter
from contextseek.storage.tiered_adapter import TieredSeekVFSAdapter


def _asgi_post(app: Any, path: str, **kwargs: Any) -> Any:
    httpx = pytest.importorskip("httpx", reason="http extra not installed")

    async def _request() -> Any:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, **kwargs)

    return asyncio.run(_request())


def _proxy_app(ctx: ContextSeek) -> Any:
    proxy_http = _proxy_http_module()
    fastapi = pytest.importorskip("fastapi", reason="http extra not installed")

    app = fastapi.FastAPI()
    app.include_router(proxy_http.create_plug_proxy_router(ctx))
    return app


def _proxy_http_module() -> Any:
    pytest.importorskip("fastapi", reason="http extra not installed")
    return pytest.importorskip(
        "contextseek.plugs.core.proxy.http",
        reason="http extra not installed",
    )


def _contextseek(tmp_path: Path) -> tuple[ContextSeek, SQLiteBackend]:
    backend = SQLiteBackend(path=str(tmp_path / "ctx.sqlite3"))
    backend.initialize()
    vfs = VFS(
        routes={"contextseek://": {"backend": backend}},
        scheme="contextseek://",
    )
    return ContextSeek(adapter=SeekVFSStorageAdapter(vfs)), backend


def _sqlite_adapter(backend: SQLiteBackend) -> SeekVFSStorageAdapter:
    vfs = VFS(
        routes={"contextseek://": {"backend": backend}},
        scheme="contextseek://",
    )
    return SeekVFSStorageAdapter(vfs)


def _event(
    external_id: str,
    content: str,
    *,
    operation: str = "add",
    event_id: str | None = None,
    importance: float = 1.0,
) -> PlugChangeEvent:
    return PlugChangeEvent(
        plug_name="testplug",
        plug_instance_id="default",
        external_id=external_id,
        event_id=event_id or f"evt-{external_id}-{operation}-{content}",
        operation=operation,  # type: ignore[arg-type]
        content=content,
        scope="tenant/agent/user",
        stage_hint="extracted",
        tags=["t"],
        importance=importance,
        raw_payload={"external_id": external_id, "content": content},
    )


def test_same_content_different_external_ids_are_not_folded(tmp_path: Path) -> None:
    ctx, _backend = _contextseek(tmp_path)
    gateway = PlugGateway(ctx)

    first = gateway.apply(_event("m1", "shared memory"))
    second = gateway.apply(_event("m2", "shared memory"))

    assert first.context_item_id != second.context_item_id
    items = ctx.items(scope="tenant/agent/user")
    assert {item.id for item in items} == {
        first.context_item_id,
        second.context_item_id,
    }


def test_update_supersedes_previous_item(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    gateway = PlugGateway(ctx)

    first = gateway.apply(_event("m1", "old memory", event_id="evt-add"))
    second = gateway.apply(
        _event("m1", "new memory", operation="update", event_id="evt-update")
    )

    assert second.context_item_id != first.context_item_id
    record = backend.plug_source_get("testplug", "default", "m1")
    assert record is not None
    assert record["current_context_item_id"] == second.context_item_id

    old_ref = ctx.resolver.ref_for("tenant/agent/user", first.context_item_id)
    old_item = deserialize_context_item(ctx.adapter.read(old_ref))
    assert old_item.searchable is False
    assert old_item.superseded_by == second.context_item_id

    new_ref = ctx.resolver.ref_for("tenant/agent/user", second.context_item_id)
    new_item = deserialize_context_item(ctx.adapter.read(new_ref))
    assert new_item.links[0].target_id == first.context_item_id
    assert new_item.links[0].relation.value == "supersedes"


def test_delete_soft_deletes_current_item(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    gateway = PlugGateway(ctx)

    created = gateway.apply(_event("m1", "to delete", event_id="evt-add"))
    deleted = gateway.apply(_event("m1", "", operation="delete", event_id="evt-delete"))

    assert deleted.context_item_id == created.context_item_id
    record = backend.plug_source_get("testplug", "default", "m1")
    assert record is not None
    assert record["status"] == "deleted"

    ref = ctx.resolver.ref_for("tenant/agent/user", created.context_item_id)
    item = deserialize_context_item(ctx.adapter.read(ref))
    assert item.is_deleted is True
    assert item.searchable is False


def test_outbox_worker_replays_pending_event(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    gateway = PlugGateway(ctx)
    event = _event("m1", "queued memory", event_id="evt-pending")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "pending",
        }
    )

    result = OutboxWorker(gateway).run_once()

    assert len(result.applied) == 1
    assert result.failed_event_ids == []
    assert backend.plug_outbox_get(event.event_id)["status"] == "applied"


def test_tiered_adapter_resolves_hot_plug_state(tmp_path: Path) -> None:
    hot = SQLiteBackend(path=str(tmp_path / "hot.sqlite3"))
    cold = SQLiteBackend(path=str(tmp_path / "cold.sqlite3"))
    hot.initialize()
    cold.initialize()
    ctx = ContextSeek(
        adapter=TieredSeekVFSAdapter(
            hot=_sqlite_adapter(hot),
            cold=_sqlite_adapter(cold),
        )
    )

    receipt = PlugGateway(ctx).apply(_event("m1", "tiered memory"))

    assert (
        hot.plug_source_get("testplug", "default", "m1")["current_context_item_id"]
        == receipt.context_item_id
    )
    cold.close()
    hot.close()


def test_event_metadata_is_preserved_in_provenance_context(tmp_path: Path) -> None:
    ctx, _backend = _contextseek(tmp_path)
    event = PlugChangeEvent(
        plug_name="testplug",
        plug_instance_id="default",
        external_id="m1",
        event_id="evt-meta",
        operation="add",
        content="metadata memory",
        scope="tenant/agent/user",
        tenant_id="tenant",
        subject_id="user",
        metadata={"user_id": "user", "agent_id": "agent"},
    )

    receipt = PlugGateway(ctx).apply(event)

    ref = ctx.resolver.ref_for("tenant/agent/user", receipt.context_item_id)
    item = deserialize_context_item(ctx.adapter.read(ref))
    context = json.loads(item.provenance.context)
    assert context["tenant_id"] == "tenant"
    assert context["subject_id"] == "user"
    assert context["metadata"]["agent_id"] == "agent"


def test_gateway_preserves_zero_importance(tmp_path: Path) -> None:
    ctx, _backend = _contextseek(tmp_path)
    receipt = PlugGateway(ctx).apply(
        _event("m1", "zero importance", event_id="evt-zero", importance=0.0)
    )

    ref = ctx.resolver.ref_for("tenant/agent/user", receipt.context_item_id)
    item = deserialize_context_item(ctx.adapter.read(ref))
    assert item.importance == 0.0


def test_gateway_preserves_retry_count_for_same_failed_payload(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "retry me", event_id="evt-retry")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "failed",
            "retry_count": 2,
            "last_error": "old",
        }
    )

    PlugGateway(ctx).apply(event)

    row = backend.plug_outbox_get(event.event_id)
    assert row["status"] == "applied"
    assert row["retry_count"] == 2
    assert row["last_error"] is None


def test_gateway_returns_applied_receipt_when_terminal_upsert_is_ignored(
    tmp_path: Path,
) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "already applied", event_id="evt-applied")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "materialized_context_item_id": "ctx-existing",
            "event_payload": event.to_payload(),
            "status": "applied",
        }
    )

    receipt = PlugGateway(ctx).apply(event)

    assert receipt.status == "applied"
    assert receipt.context_item_id == "ctx-existing"


def test_outbox_worker_marks_bad_payload_dead(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    backend.plug_outbox_upsert(
        {
            "event_id": "evt-bad",
            "plug_name": "testplug",
            "plug_instance_id": "default",
            "external_id": "bad",
            "materialization_key": "bad-key",
            "event_payload": {"plug_name": "testplug"},
            "status": "pending",
        }
    )

    result = OutboxWorker(PlugGateway(ctx), max_retry=1).run_once()

    assert result.failed_event_ids == ["evt-bad"]
    row = backend.plug_outbox_get("evt-bad")
    assert row["status"] == "dead"
    assert row["retry_count"] == 1


def test_gateway_direct_apply_marks_dead_after_retry_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "will fail", event_id="evt-direct-dead")

    def _fail_source_get(*_args, **_kwargs):
        raise RuntimeError("source store unavailable")

    monkeypatch.setattr(backend, "plug_source_get", _fail_source_get)

    with pytest.raises(RuntimeError, match="source store unavailable"):
        PlugGateway(ctx, max_retry=1).apply(event)

    row = backend.plug_outbox_get(event.event_id)
    assert row["status"] == "dead"
    assert row["retry_count"] == 1


def test_gateway_apply_accumulates_retry_count_until_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "will keep failing", event_id="evt-accumulate-dead")

    def _fail_source_get(*_args, **_kwargs):
        raise RuntimeError("source store unavailable")

    monkeypatch.setattr(backend, "plug_source_get", _fail_source_get)
    gateway = PlugGateway(ctx, max_retry=3)

    for expected_retry in (1, 2):
        with pytest.raises(RuntimeError, match="source store unavailable"):
            gateway.apply(event)
        row = backend.plug_outbox_get(event.event_id)
        assert row["status"] == "failed"
        assert row["retry_count"] == expected_retry

    with pytest.raises(RuntimeError, match="source store unavailable"):
        gateway.apply(event)

    row = backend.plug_outbox_get(event.event_id)
    assert row["status"] == "dead"
    assert row["retry_count"] == 3


def test_dead_outbox_event_is_not_revived_by_apply(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "dead memory", event_id="evt-dead")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "dead",
        }
    )

    with pytest.raises(ValueError, match="dead"):
        PlugGateway(ctx).apply(event)

    assert backend.plug_outbox_get(event.event_id)["status"] == "dead"


def test_gateway_replay_dead_requeues_and_applies_event(tmp_path: Path) -> None:
    ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "dead replay", event_id="evt-dead-replay")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "dead",
            "retry_count": 3,
            "last_error": "old",
        }
    )

    receipt = PlugGateway(ctx).replay_dead(event.event_id)

    row = backend.plug_outbox_get(event.event_id)
    assert receipt.status == "applied"
    assert row["status"] == "applied"
    assert row["retry_count"] == 0
    assert row["last_error"] is None


def test_outbox_upsert_updates_retry_payload(tmp_path: Path) -> None:
    _ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "old payload", event_id="evt-payload")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "failed",
            "last_error": "old",
        }
    )
    changed = _event("m1", "new payload", event_id="evt-payload")
    backend.plug_outbox_upsert(
        {
            "event_id": changed.event_id,
            "plug_name": changed.plug_name,
            "plug_instance_id": changed.plug_instance_id,
            "external_id": changed.external_id,
            "materialization_key": changed.materialization_key,
            "event_payload": changed.to_payload(),
            "status": "pending",
        }
    )

    row = backend.plug_outbox_get(event.event_id)
    assert row["status"] == "pending"
    assert row["event_payload"]["content"] == "new payload"


def test_dead_outbox_event_is_not_revived_by_upsert(tmp_path: Path) -> None:
    _ctx, backend = _contextseek(tmp_path)
    event = _event("m1", "dead payload", event_id="evt-dead-upsert")
    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "dead",
        }
    )
    changed = _event("m1", "new payload", event_id="evt-dead-upsert")
    backend.plug_outbox_upsert(
        {
            "event_id": changed.event_id,
            "plug_name": changed.plug_name,
            "plug_instance_id": changed.plug_instance_id,
            "external_id": changed.external_id,
            "materialization_key": changed.materialization_key,
            "event_payload": changed.to_payload(),
            "status": "pending",
        }
    )

    row = backend.plug_outbox_get(event.event_id)
    assert row["status"] == "dead"
    assert row["event_payload"]["content"] == "dead payload"


def test_seekdb_outbox_upsert_matches_retry_semantics(tmp_path: Path) -> None:
    pytest.importorskip("pyseekdb")
    from contextseek.storage.seekdb_backend import SeekDBBackend

    event = _event("m1", "seekdb old", event_id="evt-seekdb")
    changed = _event("m1", "seekdb new", event_id="evt-seekdb")
    backend = SeekDBBackend(
        path=str(tmp_path / "seekdb"),
        embedding_function=lambda texts: [[0.0] * 8 for _ in texts],
    )
    try:
        backend.initialize()
    except Exception as exc:
        message = str(exc).lower()
        if "connect failed" in message or "not initialized" in message:
            pytest.skip(f"pyseekdb embedded mode is unavailable: {exc}")
        raise

    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "failed",
            "retry_count": 2,
            "last_error": "old",
        }
    )
    backend.plug_outbox_upsert(
        {
            "event_id": changed.event_id,
            "plug_name": changed.plug_name,
            "plug_instance_id": changed.plug_instance_id,
            "external_id": changed.external_id,
            "materialization_key": changed.materialization_key,
            "event_payload": changed.to_payload(),
            "status": "pending",
        }
    )

    row = backend.plug_outbox_get(event.event_id)
    assert row["event_payload"]["content"] == "seekdb new"
    assert row["retry_count"] == 0
    assert row["status"] == "pending"

    backend.plug_outbox_update_status(
        event.event_id,
        status="dead",
        last_error="dead",
    )
    backend.plug_outbox_upsert(
        {
            "event_id": changed.event_id,
            "plug_name": changed.plug_name,
            "plug_instance_id": changed.plug_instance_id,
            "external_id": changed.external_id,
            "materialization_key": changed.materialization_key,
            "event_payload": changed.to_payload(),
            "status": "pending",
        }
    )
    assert backend.plug_outbox_get(event.event_id)["status"] == "dead"
    assert backend.plug_outbox_requeue_dead(event.event_id) is True
    replayed = backend.plug_outbox_get(event.event_id)
    assert replayed["status"] == "pending"
    assert replayed["retry_count"] == 0


def test_oceanbase_outbox_upsert_update_has_terminal_status_guard() -> None:
    pytest.importorskip("pyobvector")
    pytest.importorskip("sqlalchemy")
    from contextseek.storage.ob_backend import OceanBaseBackend

    executed_sql: list[str] = []

    class FakeResult:
        rowcount = 1

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def begin(self):
            return self

        def execute(self, stmt, _params=None):
            executed_sql.append(str(stmt))
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    class FakeObVector:
        engine = FakeEngine()

    backend = OceanBaseBackend.__new__(OceanBaseBackend)
    backend._obvector = FakeObVector()
    backend.plug_outbox_get = lambda _event_id: {"status": "failed"}  # type: ignore[method-assign]
    event = _event("m1", "ob payload", event_id="evt-ob")

    backend.plug_outbox_upsert(
        {
            "event_id": event.event_id,
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "materialization_key": event.materialization_key,
            "event_payload": event.to_payload(),
            "status": "pending",
        }
    )

    assert executed_sql
    assert "status NOT IN ('applied', 'dead')" in executed_sql[0]


def test_plug_change_event_from_payload_validates_operation() -> None:
    payload = _event("m1", "bad op").to_payload()
    payload["operation"] = "patch"

    with pytest.raises(ValueError, match="operation"):
        PlugChangeEvent.from_payload(payload)


def test_gateway_lock_pool_is_bounded() -> None:
    event = _event("m1", "lock")
    assert len(PlugGateway._LOCK_STRIPES) == 1024
    assert PlugGateway._source_lock(event) is PlugGateway._source_lock(event)


def test_contextseek_meta_summarizes_materialization_status() -> None:
    assert contextseek_meta([])["status"] == "no_events"
    assert (
        contextseek_meta([{"status": "failed"}, {"status": "failed"}])["status"]
        == "failed"
    )
    assert (
        contextseek_meta([{"status": "applied"}, {"status": "failed"}])["status"]
        == "partial_failed"
    )


def test_outbox_worker_does_not_mutate_gateway_retry_limit(tmp_path: Path) -> None:
    ctx, _backend = _contextseek(tmp_path)
    gateway = PlugGateway(ctx, max_retry=9)

    OutboxWorker(gateway, max_retry=1)

    assert gateway._max_retry == 9  # noqa: SLF001


def test_powermem_proxy_marks_partial_failed_with_207(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _backend = _contextseek(tmp_path)

    class FakePlug:
        def handle_write(self, _request):
            return PlugProxyResult(
                response=PlugProxyResponse(
                    body={"results": []},
                    status_code=200,
                    headers={},
                ),
                events=[
                    _event("ok", "ok memory", event_id="evt-http-ok"),
                    _event("bad", "bad memory", event_id="evt-http-bad"),
                ],
            )

    original_apply = PlugGateway.apply

    def fail_one(self, event):
        if event.external_id == "bad":
            raise RuntimeError("materialization failed")
        return original_apply(self, event)

    monkeypatch.setattr(
        _proxy_http_module(),
        "_build_plug",
        lambda _plug_name, _instance_id, _body, registry=None: FakePlug(),
    )
    monkeypatch.setattr(PlugGateway, "apply", fail_one)
    app = _proxy_app(ctx)

    response = _asgi_post(
        app,
        "/plugins/powermem/default/api/v1/memories",
        json={"memory": "hello"},
    )

    assert response.status_code == 207
    assert response.headers["x-contextseek-materialization"] == "partial_failed"
    body = response.json()
    assert body["_contextseek"]["status"] == "partial_failed"
    assert body["_contextseek"]["failed"] == 1


def test_powermem_proxy_marks_all_failed_with_502(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _backend = _contextseek(tmp_path)

    class FakePlug:
        def handle_write(self, _request):
            return PlugProxyResult(
                response=PlugProxyResponse(
                    body={"results": []},
                    status_code=200,
                    headers={},
                ),
                events=[
                    _event("bad-1", "bad memory", event_id="evt-http-bad-1"),
                    _event("bad-2", "bad memory", event_id="evt-http-bad-2"),
                ],
            )

    def fail_apply(self, event):
        raise RuntimeError(f"materialization failed: {event.external_id}")

    monkeypatch.setattr(
        _proxy_http_module(),
        "_build_plug",
        lambda _plug_name, _instance_id, _body, registry=None: FakePlug(),
    )
    monkeypatch.setattr(PlugGateway, "apply", fail_apply)
    app = _proxy_app(ctx)

    response = _asgi_post(
        app,
        "/plugins/powermem/default/api/v1/memories",
        json={"memory": "hello"},
    )

    assert response.status_code == 502
    assert response.headers["x-contextseek-materialization"] == "failed"
    assert response.json()["_contextseek"]["failed"] == 2


def test_powermem_proxy_returns_503_when_plug_is_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _backend = _contextseek(tmp_path)

    def fail_build(_plug_name, _instance_id, _body, registry=None):
        raise ValueError("missing base url")

    monkeypatch.setattr(_proxy_http_module(), "_build_plug", fail_build)
    app = _proxy_app(ctx)

    response = _asgi_post(
        app,
        "/plugins/powermem/default/api/v1/memories",
        json={"memory": "hello"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "PowerMem proxy is not configured"


def test_powermem_proxy_preserves_non_dict_response_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _backend = _contextseek(tmp_path)

    class FakePlug:
        def handle_write(self, _request):
            return PlugProxyResult(
                response=PlugProxyResponse(
                    body=["raw", "upstream"],
                    status_code=200,
                    headers={},
                ),
                events=[],
            )

    monkeypatch.setattr(
        _proxy_http_module(),
        "_build_plug",
        lambda _plug_name, _instance_id, _body, registry=None: FakePlug(),
    )
    app = _proxy_app(ctx)

    response = _asgi_post(
        app,
        "/plugins/powermem/default/api/v1/memories",
        json={"memory": "hello"},
    )

    assert response.status_code == 200
    assert response.headers["x-contextseek-materialization"] == "no_events"
    assert response.json() == ["raw", "upstream"]
