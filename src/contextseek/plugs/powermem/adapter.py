"""Shared PowerMem adapter logic.

The adapter owns PowerMem-specific request/response interpretation and emits
ContextSeek's generic PlugChangeEvent objects. Transport-specific wrappers
reuse this module for HTTP, CLI, MCP, and SDK entry points.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from contextseek.plugs.core.protocols import (
    DataPlug,
    InstallResult,
    PlugChangeEvent,
    PlugMeta,
    PlugOperation,
    PlugProxyRequest,
    PlugProxyResponse,
    PlugProxyResult,
)


logger = logging.getLogger(__name__)

POWERMEM_PLUG_NAME = "powermem"
POWERMEM_SOURCE_TYPE = "external_api"
POWERMEM_TAG = "powermem"
DEFAULT_INSTANCE_ID = "default"
MEMORIES_PATH = "/api/v1/memories"
MEMORIES_SEARCH_PATH = "/api/v1/memories/search"

_EVENT_MAP: dict[str, PlugOperation] = {
    "ADD": "add",
    "UPDATE": "update",
    "DELETE": "delete",
    "NONE": "noop",
    "NOOP": "noop",
}


@dataclass
class PowerMemAdapter:
    """PowerMem-specific adapter shared by every proxy transport."""

    instance_id: str = DEFAULT_INSTANCE_ID
    default_scope: str | None = None
    snapshot_source: DataPlug | None = None

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]],
        *,
        source_prefix: str = POWERMEM_PLUG_NAME,
        instance_id: str = DEFAULT_INSTANCE_ID,
        default_scope: str | None = None,
        **plug_kwargs: Any,
    ) -> "PowerMemAdapter":
        """Build an adapter with a manual-import snapshot DataPlug."""
        from contextseek.plugs.powermem.plug import PowerMemPlug

        return cls(
            instance_id=instance_id,
            default_scope=default_scope,
            snapshot_source=PowerMemPlug.from_records(
                records,
                source_prefix=source_prefix,
                **plug_kwargs,
            ),
        )

    @classmethod
    def from_memory(
        cls,
        memory: Any,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
        instance_id: str = DEFAULT_INSTANCE_ID,
        default_scope: str | None = None,
        **plug_kwargs: Any,
    ) -> "PowerMemAdapter":
        """Build an adapter snapshot from a PowerMem-style memory object."""
        from contextseek.plugs.powermem.plug import PowerMemPlug

        return cls(
            instance_id=instance_id,
            default_scope=default_scope,
            snapshot_source=PowerMemPlug.from_memory(
                memory,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                limit=limit,
                offset=offset,
                **plug_kwargs,
            ),
        )

    def metadata(self) -> PlugMeta:
        return PlugMeta(
            name=POWERMEM_PLUG_NAME,
            source_type=POWERMEM_SOURCE_TYPE,
            description="PowerMem capability adapter",
        )

    def install(
        self,
        *,
        linker: str | None = None,
        dry_run: bool = False,
        check: bool = False,
    ) -> InstallResult:
        from contextseek.plugs.powermem.linkers import (
            available_linker_names,
            disabled_linker_message,
            get_linker,
            is_linker_disabled,
        )

        names = available_linker_names()
        if not linker:
            joined = ", ".join(names)
            return InstallResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[f"available linkers: {joined}"],
                warnings=["pass --linker explicitly before installing PowerMem"],
            )
        if is_linker_disabled(linker):
            joined = ", ".join(names)
            return InstallResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[f"available linkers: {joined}"],
                warnings=[disabled_linker_message(linker)],
            )
        try:
            selected = get_linker(linker)
        except KeyError:
            joined = ", ".join(names)
            return InstallResult(
                changed=False,
                dry_run=dry_run or check,
                actions=[f"available linkers: {joined}"],
                warnings=[f"unknown linker: {linker}"],
            )
        result = selected.install(
            plug_name=POWERMEM_PLUG_NAME,
            dry_run=dry_run,
            check=check,
        )
        return InstallResult(
            changed=result.changed,
            dry_run=result.dry_run,
            actions=result.actions,
            warnings=result.warnings,
        )

    def snapshot(self) -> DataPlug | None:
        return self.snapshot_source

    def handle_write(self, request: PlugProxyRequest) -> PlugProxyResult:
        response = PlugProxyResponse(body=request.body, status_code=200, headers={})
        return PlugProxyResult(
            response=response,
            events=self.events_from_write_response(request.body, request),
        )

    def handle_search(self, request: PlugProxyRequest) -> PlugProxyResponse:
        return PlugProxyResponse(body=request.body, status_code=200, headers={})

    def events_from_write_response(
        self,
        response_body: Any,
        request: PlugProxyRequest,
    ) -> list[PlugChangeEvent]:
        if request.method.upper() == "DELETE":
            return self.events_from_delete_response(response_body, request)
        records = self._records_from_response(response_body)
        return self._events_from_records(
            records,
            request.body,
            default_operation="add",
            raw_payload={"request": request.body, "response": response_body},
        )

    def events_from_delete_response(
        self,
        response_body: Any,
        request: PlugProxyRequest,
    ) -> list[PlugChangeEvent]:
        external_id = self._external_id_from_request(request)
        if not external_id:
            return []
        request_body = request.body if isinstance(request.body, dict) else {}
        return [
            PlugChangeEvent(
                plug_name=POWERMEM_PLUG_NAME,
                plug_instance_id=self.instance_id,
                external_id=external_id,
                operation="delete",
                content="",
                scope=self._scope_from_request(request_body),
                source_type=POWERMEM_SOURCE_TYPE,
                stage_hint=self._stage_hint_from_request(request_body),
                tags=[POWERMEM_TAG],
                metadata=self._metadata_from_request({}, request_body),
                raw_payload={"request": request_body, "response": response_body},
            )
        ]

    def _events_from_write_response(
        self,
        response_body: Any,
        request_body: Any,
    ) -> list[PlugChangeEvent]:
        """Compatibility wrapper for tests and earlier integrations."""
        return self.events_from_write_response(
            response_body,
            PlugProxyRequest(
                method="POST",
                path=MEMORIES_PATH,
                body=request_body,
                headers={},
                query={},
            ),
        )

    def _events_from_records(
        self,
        records: list[dict[str, Any]],
        request_body: Any,
        *,
        default_operation: str,
        raw_payload: dict[str, Any],
    ) -> list[PlugChangeEvent]:
        body_dict = request_body if isinstance(request_body, dict) else {}
        scope = self._scope_from_request(body_dict)
        stage_hint = self._stage_hint_from_request(body_dict)
        events: list[PlugChangeEvent] = []
        for index, rec in enumerate(records):
            raw_event = str(rec.get("event") or default_operation).upper()
            operation = _EVENT_MAP.get(raw_event)
            if operation is None:
                logger.warning("unknown PowerMem event %s mapped to noop", raw_event)
                operation = "noop"
            external_id = str(
                rec.get("id")
                or rec.get("memory_id")
                or self._fallback_external_id(rec, index)
            )
            content = self._content_from_record(rec)
            if operation == "delete" and content is None:
                content = ""
            events.append(
                PlugChangeEvent(
                    plug_name=POWERMEM_PLUG_NAME,
                    plug_instance_id=self.instance_id,
                    external_id=external_id,
                    operation=operation,
                    content=content or "",
                    scope=scope,
                    source_type=POWERMEM_SOURCE_TYPE,
                    stage_hint=str(rec.get("stage_hint") or stage_hint),
                    tags=[POWERMEM_TAG],
                    metadata=self._metadata_from_request(rec, body_dict),
                    importance=self._importance_from_record(rec),
                    raw_payload={
                        **raw_payload,
                        "result": rec,
                    },
                )
            )
        return events

    def _records_from_response(self, response_body: Any) -> list[dict[str, Any]]:
        if isinstance(response_body, list):
            return [item for item in response_body if isinstance(item, dict)]
        if not isinstance(response_body, dict):
            return []
        for key in ("results", "memories"):
            records = self._records_from_candidate(response_body.get(key))
            if records:
                return records
        data = response_body.get("data")
        if isinstance(data, dict):
            for key in ("results", "memories", "items"):
                records = self._records_from_candidate(data.get(key))
                if records:
                    return records
            if self._looks_like_memory_record(data):
                return [data]
        records = self._records_from_candidate(data)
        if records:
            return records
        if self._looks_like_memory_record(response_body):
            return [response_body]
        return []

    @staticmethod
    def _records_from_candidate(candidate: Any) -> list[dict[str, Any]]:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            return [candidate]
        return []

    @staticmethod
    def _looks_like_memory_record(value: dict[str, Any]) -> bool:
        return bool({"id", "memory_id", "memory", "content", "event"} & set(value))

    @staticmethod
    def _content_from_record(record: dict[str, Any]) -> Any:
        for key in ("memory", "content", "text"):
            if key in record:
                return record[key]
        return None

    @staticmethod
    def _importance_from_record(record: dict[str, Any]) -> float:
        value = record.get("importance")
        return float(value) if value is not None else 1.0

    def _scope_from_request(self, body: dict[str, Any]) -> str:
        metadata = (
            body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        )
        if metadata.get("scope"):
            return str(metadata["scope"])
        if body.get("scope"):
            return str(body["scope"])
        if self.default_scope:
            return self.default_scope
        user_id = body.get("user_id") or metadata.get("user_id")
        agent_id = body.get("agent_id") or metadata.get("agent_id")
        if user_id and agent_id:
            return f"{POWERMEM_PLUG_NAME}/{agent_id}/{user_id}"
        if user_id:
            return f"{POWERMEM_PLUG_NAME}/{user_id}"
        return f"{POWERMEM_PLUG_NAME}/{self.instance_id}"

    @staticmethod
    def _stage_hint_from_request(body: dict[str, Any]) -> str:
        metadata = (
            body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        )
        explicit_stage = body.get("stage_hint") or metadata.get("stage_hint")
        if explicit_stage:
            return str(explicit_stage)
        infer = body.get("infer", True)
        return "extracted" if infer is not False else "raw"

    @staticmethod
    def _metadata_from_request(
        record: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = dict(record.get("metadata") or {})
        for key in ("user_id", "agent_id", "run_id"):
            if key in record and record[key] is not None:
                metadata.setdefault(key, record[key])
            if key in body and body[key] is not None:
                metadata.setdefault(key, body[key])
        return metadata

    @staticmethod
    def _external_id_from_request(request: PlugProxyRequest) -> str | None:
        body = request.body if isinstance(request.body, dict) else {}
        for key in ("id", "memory_id"):
            if body.get(key):
                return str(body[key])
        path = request.path.rstrip("/")
        if path and path != MEMORIES_PATH:
            return path.rsplit("/", 1)[-1]
        return None

    @staticmethod
    def _fallback_external_id(rec: dict[str, Any], index: int) -> str:
        raw = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"result-{index}-{digest}"
