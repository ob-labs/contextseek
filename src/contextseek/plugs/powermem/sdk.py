"""PowerMem SDK drop-in wrapper."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.proxy.sdk import SDKProxyBase
from contextseek.plugs.powermem.adapter import MEMORIES_PATH, PowerMemAdapter
from contextseek.plugs.core.protocols import PlugProxyRequest

_POWERMEM_PACKAGE = "powermem"
POWERMEM_SDK_MIN_VERSION = "1.1.1"
POWERMEM_SDK_REQUIREMENT = f"{_POWERMEM_PACKAGE}>={POWERMEM_SDK_MIN_VERSION}"


@dataclass(frozen=True)
class PowerMemSDKVersionInfo:
    """Installed PowerMem SDK version and ContextSeek's compatibility bound."""

    package_name: str
    installed_version: str | None
    min_version: str | None


class PowerMemMemoryProxy(SDKProxyBase):
    """Drop-in wrapper around a PowerMem ``Memory`` instance."""

    def __init__(
        self,
        *args,
        powermem_memory: Any | None = None,
        contextseek_client: ContextSeek | None = None,
        instance_id: str = "default",
        default_scope: str | None = None,
        strict_contextseek: bool = False,
        max_retry: int = 3,
        **kwargs,
    ) -> None:
        super().__init__(
            client=contextseek_client,
            max_retry=max_retry,
            strict_contextseek=strict_contextseek,
        )
        self._memory = powermem_memory or _new_powermem_memory(*args, **kwargs)
        self._adapter = PowerMemAdapter(
            instance_id=instance_id,
            default_scope=default_scope,
        )

    def add(self, *args, **kwargs):
        result = self._memory.add(*args, **kwargs)
        request = self._request("POST", MEMORIES_PATH, _body_from_args(args, kwargs))
        events = self._adapter.events_from_write_response(result, request)
        if not events:
            events = self._adapter.events_from_write_response(
                {"results": [_record_from_body(request.body, event="ADD")]},
                request,
            )
        self.materialize_events(events)
        return result

    def update(self, *args, **kwargs):
        result = self._memory.update(*args, **kwargs)
        body = _body_from_args(args, kwargs, first_arg_is_id=True)
        request = self._request("PUT", _path_for_id(body), body)
        events = self._adapter.events_from_write_response(result, request)
        if not events:
            events = self._adapter.events_from_write_response(
                {"results": [_record_from_body(body, event="UPDATE")]},
                request,
            )
        self.materialize_events(events)
        return result

    def delete(self, *args, **kwargs):
        result = self._memory.delete(*args, **kwargs)
        body = _body_from_args(args, kwargs, first_arg_is_id=True)
        request = self._request("DELETE", _path_for_id(body), body)
        events = self._adapter.events_from_write_response(result, request)
        self.materialize_events(events)
        return result

    def search(self, *args, **kwargs):
        return self._memory.search(*args, **kwargs)

    def get_all(self, *args, **kwargs):
        return self._memory.get_all(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        memory = object.__getattribute__(self, "_memory")
        return getattr(memory, name)

    @staticmethod
    def _request(method: str, path: str, body: dict[str, Any]) -> PlugProxyRequest:
        return PlugProxyRequest(
            method=method,
            path=path,
            body=body,
            headers={},
            query={},
        )


def _new_powermem_memory(*args, **kwargs) -> Any:
    validate_powermem_sdk_version()
    try:
        from powermem import Memory as PowerMemMemory  # type: ignore
    except ImportError as exc:
        msg = (
            "PowerMem SDK is not installed. Pass powermem_memory=..., install "
            "ContextSeek with the PowerMem extra (`contextseek[powermem]`), or "
            f"install {POWERMEM_SDK_REQUIREMENT}."
        )
        raise ImportError(msg) from exc
    return PowerMemMemory(*args, **kwargs)


def powermem_sdk_version_info() -> PowerMemSDKVersionInfo:
    """Return installed PowerMem SDK version and ContextSeek version policy."""
    try:
        installed = importlib_metadata.version(_POWERMEM_PACKAGE)
    except importlib_metadata.PackageNotFoundError:
        installed = None
    return PowerMemSDKVersionInfo(
        package_name=_POWERMEM_PACKAGE,
        installed_version=installed,
        min_version=POWERMEM_SDK_MIN_VERSION,
    )


def validate_powermem_sdk_version() -> PowerMemSDKVersionInfo:
    """Validate installed PowerMem SDK against ContextSeek's minimum version."""
    info = powermem_sdk_version_info()
    if not info.installed_version:
        msg = (
            "PowerMem SDK version cannot be detected; install ContextSeek with "
            "the PowerMem extra (`contextseek[powermem]`) or install "
            f"{POWERMEM_SDK_REQUIREMENT}."
        )
        raise RuntimeError(msg)
    if info.min_version and _version_lt(info.installed_version, info.min_version):
        msg = (
            f"PowerMem SDK version mismatch: installed {info.installed_version}, "
            f"requires >= {info.min_version}."
        )
        raise RuntimeError(msg)
    return info


def _body_from_args(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    first_arg_is_id: bool = False,
) -> dict[str, Any]:
    body = {
        key: value
        for key, value in kwargs.items()
        if key
        in {
            "id",
            "memory_id",
            "memory",
            "content",
            "scope",
            "user_id",
            "agent_id",
            "run_id",
            "infer",
        }
        and value is not None
    }
    remaining = list(args)
    if first_arg_is_id and remaining and "id" not in body and "memory_id" not in body:
        body["id"] = remaining.pop(0)
    if remaining and "memory" not in body and "content" not in body:
        body["memory"] = remaining[0]
    return body


def _path_for_id(body: dict[str, Any]) -> str:
    memory_id = body.get("id") or body.get("memory_id")
    if memory_id:
        return f"{MEMORIES_PATH}/{memory_id}"
    return MEMORIES_PATH


def _record_from_body(body: dict[str, Any], *, event: str) -> dict[str, Any]:
    content = body.get("memory") or body.get("content") or ""
    return {
        "id": body.get("id") or body.get("memory_id") or content,
        "memory": content,
        "event": event,
    }


def _version_lt(left: str, right: str) -> bool:
    try:
        from packaging.version import Version

        return Version(left) < Version(right)
    except Exception:
        return _numeric_version_parts(left) < _numeric_version_parts(right)


def _numeric_version_parts(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value)]
    return tuple(parts or [0])


Memory = PowerMemMemoryProxy
PowerMemSDKAdapter = PowerMemMemoryProxy

__all__ = [
    "Memory",
    "PowerMemMemoryProxy",
    "PowerMemSDKAdapter",
    "PowerMemSDKVersionInfo",
    "POWERMEM_SDK_MIN_VERSION",
    "POWERMEM_SDK_REQUIREMENT",
    "powermem_sdk_version_info",
    "validate_powermem_sdk_version",
]
