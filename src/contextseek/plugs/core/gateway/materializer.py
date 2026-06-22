"""Materialize PlugChangeEvent objects into ContextSeek items."""

from __future__ import annotations

import json
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.context_item import ContextItem
from contextseek.domain.links import Link, LinkType
from contextseek.domain.stages import Stage
from contextseek.plugs.core.protocols import MaterializationReceipt, PlugChangeEvent


class PlugMaterializer:
    """Apply add/update/delete/noop semantics for one PlugChangeEvent."""

    def __init__(self, seek: ContextSeek) -> None:
        self._seek = seek

    def apply(
        self,
        event: PlugChangeEvent,
        old_record: dict[str, Any] | None,
    ) -> MaterializationReceipt:
        """Materialize one event against an optional existing source record."""
        if event.operation == "delete":
            return self.materialize_delete(event, old_record)
        if event.operation == "noop":
            return self.materialize_noop(event, old_record)
        if (
            old_record is not None
            and old_record.get("write_projection_hash") == event.write_projection_hash
        ):
            return self.materialize_noop(event, old_record)
        if old_record is None or not old_record.get("current_context_item_id"):
            return self.materialize_add(event)
        return self.materialize_update(event, old_record)

    def materialize_add(self, event: PlugChangeEvent) -> MaterializationReceipt:
        item = self._build_item(event)
        item_id = self._seek._upsert_item(  # noqa: SLF001
            item,
            materialization_key=event.materialization_key,
        )
        return MaterializationReceipt(
            event_id=event.event_id,
            materialization_key=event.materialization_key,
            context_item_id=item_id,
            operation=event.operation,
            status="applied",
        )

    def materialize_update(
        self,
        event: PlugChangeEvent,
        old_record: dict[str, Any],
    ) -> MaterializationReceipt:
        old_id = old_record.get("current_context_item_id")
        item = self._build_item(event)
        if old_id:
            item.links.append(
                Link(
                    target_id=str(old_id),
                    relation=LinkType.supersedes,
                    strength=1.0,
                )
            )
        item_id = self._seek._upsert_item(  # noqa: SLF001
            item,
            materialization_key=event.materialization_key,
        )
        if old_id:
            self._supersede_old_item(event, str(old_id), item_id)
        return MaterializationReceipt(
            event_id=event.event_id,
            materialization_key=event.materialization_key,
            context_item_id=item_id,
            operation=event.operation,
            status="applied",
        )

    def materialize_delete(
        self,
        event: PlugChangeEvent,
        old_record: dict[str, Any] | None,
    ) -> MaterializationReceipt:
        old_id = old_record.get("current_context_item_id") if old_record else None
        if old_id:
            self._soft_delete_old_item(event, str(old_id))
        return MaterializationReceipt(
            event_id=event.event_id,
            materialization_key=event.materialization_key,
            context_item_id=str(old_id) if old_id else None,
            operation=event.operation,
            status="applied",
        )

    def materialize_noop(
        self,
        event: PlugChangeEvent,
        old_record: dict[str, Any] | None,
    ) -> MaterializationReceipt:
        return MaterializationReceipt(
            event_id=event.event_id,
            materialization_key=event.materialization_key,
            context_item_id=(
                str(old_record.get("current_context_item_id"))
                if old_record and old_record.get("current_context_item_id")
                else None
            ),
            operation=event.operation,
            status="skipped",
        )

    def _build_item(self, event: PlugChangeEvent) -> ContextItem:
        stage = self._stage_from_hint(event.stage_hint)
        source_type = self._seek._normalize_source_type(event.source_type)  # noqa: SLF001
        resolved_stage, resolved_stability = self._seek._resolve_stage_stability(  # noqa: SLF001
            stage=stage,
            stability=None,
            content=event.content or "",
            source_type=source_type,
        )
        provenance = self._seek._build_provenance(  # noqa: SLF001
            f"{event.plug_name}://{event.external_id}",
            source_type,
            None,
            context=self._provenance_context(event),
        )
        metadata_tags = [
            f"plug:{event.plug_name}",
            f"plug_instance:{event.plug_instance_id}",
        ]
        tags = [*metadata_tags, *list(event.tags or [])]
        item = ContextItem(
            content=event.content or "",
            scope=event.scope,
            provenance=provenance,
            tags=tags,
            stage=resolved_stage,
            stability=resolved_stability,
        )
        item.importance = (
            float(event.importance) if event.importance is not None else 1.0
        )
        return item

    @staticmethod
    def _provenance_context(event: PlugChangeEvent) -> str | None:
        context = {
            "plug_name": event.plug_name,
            "plug_instance_id": event.plug_instance_id,
            "external_id": event.external_id,
            "tenant_id": event.tenant_id,
            "subject_id": event.subject_id,
            "metadata": event.metadata,
        }
        compact = {k: v for k, v in context.items() if v not in (None, {}, [])}
        if not compact:
            return None
        return json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _stage_from_hint(stage_hint: str | None) -> Stage | None:
        if not stage_hint:
            return None
        try:
            return Stage(stage_hint)
        except ValueError:
            return None

    def _supersede_old_item(
        self,
        event: PlugChangeEvent,
        old_item_id: str,
        new_item_id: str,
    ) -> None:
        ref = self._seek.resolver.ref_for(event.scope, old_item_id)
        old_item = self._seek._read_item(ref)  # noqa: SLF001
        if old_item is None:
            return
        old_item.searchable = False
        old_item.superseded_by = new_item_id
        self._seek._write_item_with_audit(  # noqa: SLF001
            old_item,
            action="plug_supersede",
            detail={
                "new_item_id": new_item_id,
                "event_id": event.event_id,
                "materialization_key": event.materialization_key,
            },
        )

    def _soft_delete_old_item(self, event: PlugChangeEvent, old_item_id: str) -> None:
        ref = self._seek.resolver.ref_for(event.scope, old_item_id)
        old_item = self._seek._read_item(ref)  # noqa: SLF001
        if old_item is None or old_item.is_deleted:
            return
        old_item.soft_delete(f"plug_delete:{event.plug_name}:{event.external_id}")
        self._seek._write_item_with_audit(  # noqa: SLF001
            old_item,
            action="plug_delete",
            detail={
                "event_id": event.event_id,
                "materialization_key": event.materialization_key,
            },
        )
