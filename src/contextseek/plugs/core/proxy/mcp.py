"""Generic MCP proxy helpers for plug capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.gateway import PlugGateway
from contextseek.plugs.core.proxy.materialization import contextseek_meta
from contextseek.plugs.core.protocols import PlugProxyRequest, PlugProxyResult


@dataclass
class PlugMCPProxy:
    """Dispatch MCP tool calls through a plug adapter and PlugGateway."""

    client: ContextSeek
    adapter: Any
    max_retry: int = 3

    def list_tools(self) -> list[dict[str, Any]]:
        list_tools = getattr(self.adapter, "list_tools", None)
        if callable(list_tools):
            return [_mcp_tool_schema(tool) for tool in list_tools()]
        return []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = PlugProxyRequest(
            method="MCP",
            path=f"mcp://{name}",
            body=arguments,
            headers={},
            query={},
            context={"mcp_tool_name": name},
        )
        is_search_request = getattr(self.adapter, "is_search_request", None)
        if callable(is_search_request) and is_search_request(request):
            contextseek_search = getattr(
                self.adapter, "handle_contextseek_search", None
            )
            if callable(contextseek_search):
                response = contextseek_search(self.client, request)
            else:
                response = self.adapter.handle_search(request)
            body = response.body
            structured = body if isinstance(body, dict) else {"result": body}
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(body, ensure_ascii=False),
                    }
                ],
                "structuredContent": structured,
            }

        result = self.adapter.handle_write(request)
        if not isinstance(result, PlugProxyResult):
            result = PlugProxyResult(response=result, events=[])
        gateway = PlugGateway(self.client, max_retry=self.max_retry)
        materialized: list[dict[str, Any]] = []
        for event in result.events:
            try:
                receipt = gateway.apply(event)
                materialized.append(
                    {
                        "event_id": receipt.event_id,
                        "context_item_id": receipt.context_item_id,
                        "status": receipt.status,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                materialized.append(
                    {
                        "event_id": event.event_id,
                        "context_item_id": None,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        body = result.response.body
        if isinstance(body, dict):
            body = dict(body)
            body.setdefault("_contextseek", contextseek_meta(materialized))
        structured = body if isinstance(body, dict) else {"result": body}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(body, ensure_ascii=False),
                }
            ],
            "structuredContent": structured,
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = str(request.get("method", ""))
        params = dict(request.get("params", {}))
        if method == "initialize":
            return _success_response(request_id, _initialize_result(params))
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _success_response(request_id, {"tools": self.list_tools()})
        if method == "tools/call":
            try:
                payload = self.call_tool(
                    str(params.get("name", "")),
                    dict(params.get("arguments", {})),
                )
            except Exception as exc:  # noqa: BLE001
                return _error_response(request_id, -32000, str(exc))
            return _success_response(request_id, payload)
        return _error_response(request_id, -32601, f"method not found: {method}")


def _success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _initialize_result(params: dict[str, Any] | None = None) -> dict[str, Any]:
    protocol_version = str(
        (params or {}).get("protocolVersion") or "2024-11-05",
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": "contextseek-powermem-proxy",
            "version": "0.0.0",
        },
    }


def _mcp_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    if "inputSchema" in tool:
        return tool
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return tool
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, raw_spec in parameters.items():
        if not isinstance(raw_spec, dict):
            properties[name] = {}
            continue
        spec = dict(raw_spec)
        if spec.pop("required", False):
            required.append(name)
        default = spec.pop("default", None)
        if default is not None:
            spec["default"] = default
        properties[name] = spec
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "inputSchema": schema,
    }
