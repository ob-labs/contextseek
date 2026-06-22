"""PowerMem HTTP proxy adapter."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from contextseek.plugs.powermem.adapter import (
    DEFAULT_INSTANCE_ID,
    MEMORIES_SEARCH_PATH,
    PowerMemAdapter,
)
from contextseek.plugs.core.protocols import (
    PlugProxyRequest,
    PlugProxyResponse,
    PlugProxyResult,
)


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


@dataclass
class PowerMemHTTPPlug(PowerMemAdapter):
    """HTTP transport wrapper for a PowerMem-compatible upstream server."""

    base_url: str = ""
    instance_id: str = DEFAULT_INSTANCE_ID
    default_scope: str | None = None
    timeout: float = 30.0

    def handle_write(self, request: PlugProxyRequest) -> PlugProxyResult:
        response = self._request_json(request)
        if not 200 <= response.status_code < 300:
            return PlugProxyResult(response=response, events=[])
        return PlugProxyResult(
            response=response,
            events=self.events_from_write_response(response.body, request),
        )

    def handle_search(self, request: PlugProxyRequest) -> PlugProxyResponse:
        return self._request_json(request)

    def is_write_request(self, request: PlugProxyRequest) -> bool:
        method = request.method.upper()
        path = "/" + request.path.strip("/")
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        return path != MEMORIES_SEARCH_PATH

    def _request_json(self, request: PlugProxyRequest) -> PlugProxyResponse:
        url = self._url_for(request.path, request.query)
        headers = self._headers_for(request.headers)
        data = self._body_bytes(request)
        req = urllib.request.Request(
            url,
            data=data,
            method=request.method.upper(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8")
                return PlugProxyResponse(
                    body=_decode_body(payload),
                    status_code=int(resp.status),
                    headers=dict(resp.headers),
                )
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8")
            return PlugProxyResponse(
                body=_decode_body(payload),
                status_code=int(exc.code),
                headers=dict(exc.headers),
            )

    def _url_for(self, path: str, query: dict[str, Any]) -> str:
        base = self.base_url.rstrip("/") + "/"
        normalized = path.lstrip("/")
        url = urllib.parse.urljoin(base, normalized)
        if query:
            pairs: list[tuple[str, Any]] = []
            for key, value in query.items():
                if value is None:
                    continue
                if isinstance(value, list):
                    pairs.extend((key, item) for item in value)
                else:
                    pairs.append((key, value))
            encoded = urllib.parse.urlencode(pairs, doseq=True)
            if encoded:
                url = f"{url}?{encoded}"
        return url

    @staticmethod
    def _headers_for(headers: dict[str, str] | None) -> dict[str, str]:
        request_headers = {
            "content-type": "application/json",
            "accept": "application/json",
        }
        for key, value in (headers or {}).items():
            if key.lower() not in _HOP_BY_HOP_HEADERS:
                request_headers[key] = value
        return request_headers

    @staticmethod
    def _body_bytes(request: PlugProxyRequest) -> bytes | None:
        method = request.method.upper()
        if method in {"GET", "HEAD"}:
            return None
        if request.body is None:
            return None
        return json.dumps(request.body, ensure_ascii=False).encode("utf-8")


def build_powermem_http_plug(instance_id: str, body: Any) -> PowerMemHTTPPlug:
    return PowerMemHTTPPlug(
        base_url=base_url_for_instance(instance_id),
        instance_id=instance_id,
        default_scope=default_scope(body, instance_id),
    )


def base_url_for_instance(instance_id: str) -> str:
    key = env_key_for_instance(instance_id)
    base_url = (
        os.environ.get(f"CONTEXTSEEK_POWERMEM_{key}_UPSTREAM_BASE_URL")
        or os.environ.get("CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL")
        or os.environ.get(f"POWERMEM_{key}_BASE_URL")
        or os.environ.get("POWERMEM_BASE_URL")
    )
    if not base_url:
        msg = (
            "PowerMem proxy base URL is not configured. Set "
            f"CONTEXTSEEK_POWERMEM_{key}_UPSTREAM_BASE_URL, "
            "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL, "
            f"POWERMEM_{key}_BASE_URL, or POWERMEM_BASE_URL."
        )
        raise ValueError(msg)
    return base_url


def default_scope(body: Any, instance_id: str) -> str | None:
    if isinstance(body, dict):
        metadata = (
            body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        )
        if metadata.get("scope"):
            return str(metadata["scope"])
        if body.get("scope"):
            return str(body["scope"])
    key = env_key_for_instance(instance_id)
    return (
        os.environ.get(f"CONTEXTSEEK_POWERMEM_{key}_DEFAULT_SCOPE")
        or os.environ.get("CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE")
        or os.environ.get(f"POWERMEM_{key}_DEFAULT_SCOPE")
        or os.environ.get("POWERMEM_DEFAULT_SCOPE")
    )


def env_key_for_instance(instance_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", instance_id).upper()


def _decode_body(payload: str) -> Any:
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload


PowerMemProxyPlug = PowerMemHTTPPlug

__all__ = [
    "PowerMemHTTPPlug",
    "PowerMemProxyPlug",
    "base_url_for_instance",
    "build_powermem_http_plug",
    "default_scope",
    "env_key_for_instance",
]
