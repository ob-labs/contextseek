"""Unit tests for ingestion layer v1."""

from __future__ import annotations

from contextseek.client.contextseek import ContextSeek
from contextseek.ingestion import (
    ConnectorConfig,
    ConnectorKind,
    ConnectorMode,
    ConnectorRuntime,
    DefaultPolicyGate,
    GateConfig,
    IngestionControlPlane,
    InMemoryCheckpointStore,
    JsonFileConnectorConfigStore,
    JsonFileCheckpointStore,
    IngestionWriter,
    IngestionScheduler,
    RawEvent,
    SyncCheckpoint,
)
from contextseek.ingestion.connectors.base import PullResult
from contextseek.ingestion.registry import build_connector, build_normalizer


def _sample_event(*, content: str = "hello", source_id: str = "doc-1") -> RawEvent:
    return RawEvent(
        event_id=f"evt-{source_id}-{content}",
        source_type="note",
        source_id=source_id,
        scope="acme/team/user",
        content=content,
        updated_at="1",
        fingerprint=f"fp-{source_id}-{content}",
        metadata={"connector_kind": "notes", "connector_id": "notes-main"},
        acl_principals=["user:alice"],
    )


def test_ingestion_writer_is_idempotent_by_event_and_fingerprint() -> None:
    ctx = ContextSeek()
    writer = IngestionWriter(ctx)
    event = _sample_event()

    first = writer.write(event)
    second = writer.write(event)
    assert first.status == "written"
    assert second.status == "skipped"
    assert second.reason == "duplicate_event_id"

    same_payload_new_event = RawEvent(
        event_id="evt-new",
        source_type=event.source_type,
        source_id=event.source_id,
        scope=event.scope,
        content=event.content,
        updated_at=event.updated_at,
        fingerprint=event.fingerprint,
        metadata=event.metadata,
        acl_principals=event.acl_principals,
    )
    third = writer.write(same_payload_new_event)
    assert third.status == "skipped"
    assert third.reason == "duplicate_fingerprint"


def test_policy_gate_redacts_and_rejects() -> None:
    gate = DefaultPolicyGate(
        GateConfig(
            policy_version="test-v1",
            redact_pii=True,
            allowed_scopes=("acme/",),
            reject_empty_content=True,
        )
    )
    allowed = _sample_event(content="email: foo@example.com")
    redacted = gate.apply(allowed)
    assert redacted is not None
    assert "[REDACTED]" in redacted.content
    assert redacted.metadata["policy_decision"] == "redact"

    denied_scope = _sample_event(content="hello")
    denied_scope.scope = "other/scope"
    assert gate.apply(denied_scope) is None

    empty = _sample_event(content="  ")
    assert gate.apply(empty) is None


class _StaticConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def discover(self) -> list[str]:
        return ["p1"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        cursor = checkpoint.cursor if checkpoint else ""
        if cursor:
            return PullResult(payloads=[], next_cursor=cursor)
        return PullResult(
            payloads=[
                {
                    "source_id": "doc-1",
                    "scope": "acme/team/user",
                    "content": "hello world",
                    "updated_at": "1",
                    "metadata": {"connector_kind": "notes"},
                    "acl_principals": ["user:alice"],
                }
            ],
            next_cursor="done",
        )


class _StaticNormalizer:
    def normalize(
        self,
        payload: dict[str, str],
        *,
        connector_id: str,
        partition: str,
    ) -> RawEvent:
        return RawEvent(
            event_id="evt-1",
            source_type="note",
            source_id=payload["source_id"],
            scope=payload["scope"],
            content=payload["content"],
            updated_at=payload["updated_at"],
            fingerprint="fp-1",
            metadata={
                "connector_kind": "notes",
                "connector_id": connector_id,
                "partition": partition,
            },
            acl_principals=["user:alice"],
        )


class _FailingNormalizer:
    def normalize(
        self,
        payload: dict[str, str],
        *,
        connector_id: str,
        partition: str,
    ) -> RawEvent:
        raise ValueError("normalize failed")


class _TwoPartitionConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def discover(self) -> list[str]:
        return ["p1", "p2"]

    def pull(self, partition: str, checkpoint: SyncCheckpoint | None) -> PullResult:
        cursor = checkpoint.cursor if checkpoint else ""
        if cursor:
            return PullResult(payloads=[], next_cursor=cursor)
        return PullResult(
            payloads=[
                {
                    "source_id": f"doc-{partition}",
                    "scope": "acme/team/user",
                    "content": f"hello {partition}",
                    "updated_at": "1",
                    "metadata": {"connector_kind": "notes"},
                    "acl_principals": ["user:alice"],
                }
            ],
            next_cursor="done",
        )


def test_runtime_runs_and_persists_checkpoint() -> None:
    ctx = ContextSeek()
    writer = IngestionWriter(ctx)
    runtime = ConnectorRuntime(
        writer=writer,
        checkpoint_store=InMemoryCheckpointStore(),
    )
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    runtime.register("notes-main", _StaticConnector(cfg), _StaticNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()

    checkpoints = runtime.checkpoint_snapshot("notes-main")
    assert checkpoints
    assert checkpoints[0]["status"] == "synced"
    assert checkpoints[0]["cursor"] == "done"
    assert runtime.stats.events_written == 1


def test_registry_supports_confluence_notion_github() -> None:
    kinds = (
        ConnectorKind.confluence,
        ConnectorKind.notion,
        ConnectorKind.github,
    )
    for kind in kinds:
        cfg = ConnectorConfig(
            connector_id=f"{kind.value}-main",
            kind=kind,
            mode=ConnectorMode.synced,
            config={},
        )
        connector = build_connector(cfg)
        normalizer = build_normalizer(cfg)
        assert connector.config.connector_id == cfg.connector_id
        assert normalizer is not None


def test_connector_config_store_restore_on_restart(tmp_path) -> None:
    store = JsonFileConnectorConfigStore(tmp_path / "connectors.json")
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime, config_store=store)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
        config={"root": "/tmp/notes"},
    )
    control.create_connector(cfg)
    assert control.list_connectors()

    runtime2 = ConnectorRuntime(
        writer=IngestionWriter(ContextSeek()),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control2 = IngestionControlPlane(runtime2, config_store=store, restore_on_startup=True)
    restored = control2.list_connectors()
    assert len(restored) == 1
    assert restored[0]["connector_id"] == "notes-main"


def test_runtime_exports_prometheus_metrics() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    runtime.register("notes-main", _StaticConnector(cfg), _StaticNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()
    metrics = runtime.export_prometheus_metrics()
    assert 'connector_id="notes-main"' in metrics
    assert "ingestion_events_total" in metrics


def test_scheduler_deduplicates_same_partition_task() -> None:
    scheduler = IngestionScheduler(retry_backoff_seconds=(30,))
    scheduler.enqueue_now("notes-main", "space:eng")
    scheduler.enqueue_now("notes-main", "space:eng")
    first = scheduler.pop_ready()
    assert first is not None
    assert first.connector_id == "notes-main"
    assert first.partition == "space:eng"
    # Upsert semantics: only one pending task should remain.
    assert scheduler.pop_ready() is None


def test_scheduler_requeue_replaces_ready_task_with_backoff() -> None:
    scheduler = IngestionScheduler(retry_backoff_seconds=(30,))
    scheduler.enqueue_now("notes-main", "space:eng")
    scheduler.requeue("notes-main", "space:eng", retry_count=0, reason="retry")
    # If dedup fails, the immediate task would still be ready.
    assert scheduler.pop_ready() is None
    assert scheduler.next_delay() is not None


def test_json_file_checkpoint_store_roundtrip(tmp_path) -> None:
    store = JsonFileCheckpointStore(tmp_path / "checkpoints.json")
    cp = SyncCheckpoint(
        connector_id="notes-main",
        partition="space:eng",
        cursor="updated_at:123",
        status="synced",
    )
    store.save(cp)
    loaded = store.load("notes-main", "space:eng")
    assert loaded is not None
    assert loaded.cursor == "updated_at:123"
    assert loaded.status == "synced"


def test_list_connectors_includes_runtime_metrics() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    control.create_connector(cfg)
    runtime.register("notes-main", _StaticConnector(cfg), _StaticNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()

    connectors = control.list_connectors()
    assert len(connectors) == 1
    metrics = connectors[0]["runtime_metrics"]
    assert metrics["events_total"] >= 1
    assert metrics["events_written"] >= 1
    assert "throughput_per_min" in metrics
    assert metrics["throughput_per_min"] > 0


def test_dead_letter_replay_api() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    control.create_connector(cfg)
    runtime.register("notes-main", _StaticConnector(cfg), _FailingNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()

    records = control.dead_letters("notes-main")
    assert records
    record_id = records[0]["id"]

    # Replace failing normalizer and replay the failed partition.
    runtime.register("notes-main", _StaticConnector(cfg), _StaticNormalizer())
    replay_result = control.replay_dead_letter("notes-main", record_id, run_now=True)
    assert replay_result["record_id"] == record_id
    assert replay_result["scheduled_steps"] >= 1


def test_dead_letter_delete_api() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    control.create_connector(cfg)
    runtime.register("notes-main", _StaticConnector(cfg), _FailingNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()

    records = control.dead_letters("notes-main")
    assert records
    deleted = control.delete_dead_letter("notes-main", records[0]["id"])
    assert deleted is True
    assert control.dead_letters("notes-main") == []


def test_dead_letter_replay_all_api() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    control.create_connector(cfg)
    runtime.register("notes-main", _TwoPartitionConnector(cfg), _FailingNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()
    records = control.dead_letters("notes-main")
    assert len(records) == 2

    runtime.register("notes-main", _TwoPartitionConnector(cfg), _StaticNormalizer())
    result = control.replay_all_dead_letters("notes-main", run_now=True)
    assert result["replayed_count"] == 2
    assert result["scheduled_steps"] >= 1


def test_dead_letter_replay_all_with_cleanup() -> None:
    ctx = ContextSeek()
    runtime = ConnectorRuntime(
        writer=IngestionWriter(ctx),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    control = IngestionControlPlane(runtime)
    cfg = ConnectorConfig(
        connector_id="notes-main",
        kind=ConnectorKind.notes,
        mode=ConnectorMode.synced,
    )
    control.create_connector(cfg)
    runtime.register("notes-main", _TwoPartitionConnector(cfg), _FailingNormalizer())
    runtime.enqueue_discovery("notes-main")
    runtime.run_until_idle()
    assert len(control.dead_letters("notes-main")) == 2

    runtime.register("notes-main", _TwoPartitionConnector(cfg), _StaticNormalizer())
    result = control.replay_all_dead_letters(
        "notes-main",
        run_now=True,
        remove_after_replay=True,
    )
    assert result["remove_after_replay"] is True
    assert result["replayed_count"] == 2
    assert control.dead_letters("notes-main") == []

