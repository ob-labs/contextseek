"""Generic FastAPI proxy router for plug capabilities.

Dictionary upstream write responses receive an added ``_contextseek`` summary.
For non-dictionary upstream responses, the body is preserved and callers should
read ``X-ContextSeek-Materialization`` for the materialization status.
"""

from __future__ import annotations

import os
import re
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.gateway import PlugGateway, PlugStateStoreUnavailable
from contextseek.plugs.core.proxy.materialization import contextseek_meta
from contextseek.plugs.core.proxy.registry import (
    PlugNotConfigured,
    PlugNotFound,
    PlugRegistry,
    create_default_registry,
)
from contextseek.plugs.core.protocols import PlugProxyRequest, PlugProxyResult

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, Response
except ImportError as exc:  # pragma: no cover - guarded by http extra
    msg = (
        "FastAPI dependencies are not installed. "
        "Install with: pip install contextseek[http]"
    )
    raise ImportError(msg) from exc


_DEFAULT_REGISTRY = create_default_registry()


def create_plug_proxy_router(
    ctx: ContextSeek,
    *,
    registry: PlugRegistry | None = None,
) -> APIRouter:
    """Create generic plug proxy routes bound to one ContextSeek client."""
    active_registry = registry or _DEFAULT_REGISTRY
    router = APIRouter(prefix="/plugins", tags=["plugins"])
    gateway_cache: dict[tuple[str, str, int], PlugGateway] = {}

    def gateway_for(plug_name: str, instance_id: str) -> PlugGateway:
        max_retry = _max_retry_for_instance(plug_name, instance_id)
        key = (plug_name, instance_id, max_retry)
        gateway = gateway_cache.get(key)
        if gateway is None:
            gateway = PlugGateway(ctx, max_retry=max_retry)
            gateway_cache[key] = gateway
        return gateway

    @router.api_route(
        "/{plug_name}/{instance_id}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy(
        plug_name: str,
        instance_id: str,
        path: str,
        request: Request,
    ) -> Response:
        body = await _json_body(request)
        try:
            plug = _build_plug(
                plug_name,
                instance_id,
                body,
                registry=active_registry,
            )
        except PlugNotFound as exc:
            return JSONResponse(
                content={"error": "Plug proxy is not registered", "detail": str(exc)},
                status_code=404,
            )
        except (PlugNotConfigured, ValueError) as exc:
            return JSONResponse(
                content={
                    "error": f"{_display_plug_name(plug_name)} proxy is not configured",
                    "detail": str(exc),
                },
                status_code=503,
            )

        proxy_request = PlugProxyRequest(
            method=request.method,
            path="/" + path.strip("/"),
            body=body,
            headers=dict(request.headers),
            query=dict(request.query_params),
        )
        if _is_write_request(plug, proxy_request):
            return _write_response(
                ctx,
                gateway_for,
                plug,
                plug_name,
                instance_id,
                proxy_request,
            )
        is_search_request = getattr(plug, "is_search_request", None)
        contextseek_search = getattr(plug, "handle_contextseek_search", None)
        if (
            callable(is_search_request)
            and is_search_request(proxy_request)
            and callable(contextseek_search)
        ):
            response = contextseek_search(ctx, proxy_request)
        else:
            response = plug.handle_search(proxy_request)
        return JSONResponse(content=response.body, status_code=response.status_code)

    return router


def _build_plug(
    plug_name: str,
    instance_id: str,
    body: Any,
    *,
    registry: PlugRegistry | None = None,
) -> Any:
    return (registry or _DEFAULT_REGISTRY).create(plug_name, instance_id, body)


def _write_response(
    _ctx: ContextSeek,
    gateway_for,
    plug,
    plug_name: str,
    instance_id: str,
    request: PlugProxyRequest,
) -> Response:
    result = plug.handle_write(request)
    if not isinstance(result, PlugProxyResult):
        result = PlugProxyResult(response=result, events=[])
    try:
        gateway = gateway_for(plug_name, instance_id)
    except PlugStateStoreUnavailable as exc:
        return JSONResponse(
            content={
                "error": "PlugGateway requires sqlite, seekdb, or oceanbase storage",
                "detail": str(exc),
            },
            status_code=503,
        )
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

    response_body = result.response.body
    meta = contextseek_meta(materialized)
    headers = {
        "X-ContextSeek-Materialization": str(meta["status"]),
    }
    if isinstance(response_body, dict):
        response_body = dict(response_body)
        response_body.setdefault("_contextseek", meta)

    status_code = result.response.status_code
    if materialized and meta["status"] == "failed":
        status_code = 502
    elif materialized and meta["status"] == "partial_failed":
        status_code = 207
    return JSONResponse(
        content=response_body,
        status_code=status_code,
        headers=headers,
    )


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return {}


def _is_write_request(plug, request: PlugProxyRequest) -> bool:
    is_write = getattr(plug, "is_write_request", None)
    if callable(is_write):
        return bool(is_write(request))
    method = request.method.upper()
    path = request.path.rstrip("/")
    return method in {"POST", "PUT", "PATCH", "DELETE"} and not path.endswith("/search")


def _max_retry_for_instance(plug_name: str, instance_id: str) -> int:
    plug_key = _env_key(plug_name)
    instance_key = _env_key(instance_id)
    raw = (
        os.environ.get(f"CONTEXTSEEK_{plug_key}_{instance_key}_MAX_RETRY")
        or os.environ.get(f"CONTEXTSEEK_{plug_key}_MAX_RETRY")
        or os.environ.get("CONTEXTSEEK_PLUGGATEWAY_MAX_RETRY")
    )
    if raw is None:
        return 3
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _env_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).upper()


def _display_plug_name(name: str) -> str:
    if name.lower() == "powermem":
        return "PowerMem"
    return name
