"""PowerMem MCP adapter and stdio entry point."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.proxy.mcp import PlugMCPProxy
from contextseek.plugs.powermem.adapter import (
    DEFAULT_INSTANCE_ID,
    MEMORIES_PATH,
    PowerMemAdapter,
)
from contextseek.plugs.powermem.env import (
    powermem_child_process_cwd,
    powermem_child_process_env,
)
from contextseek.plugs.powermem.linkers.runtime import PowerMemMCPRuntimeInstaller
from contextseek.plugs.core.protocols import (
    PlugChangeEvent,
    PlugProxyRequest,
    PlugProxyResponse,
    PlugProxyResult,
)


_WRITE_TOOLS = {"add_memory", "update_memory", "delete_memory"}
_SEARCH_TOOLS = {"search_memories"}
_SUPPORTED_TOOLS = _WRITE_TOOLS | _SEARCH_TOOLS
_SDK_RESULT_PREFIX = "__CONTEXTSEEK_POWERMEM_SDK_RESULT__"
_SDK_TOOL_RUNNER = r"""
import json
import sys

RESULT_PREFIX = "__CONTEXTSEEK_POWERMEM_SDK_RESULT__"


def _coerce_memory_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _filtered_kwargs(arguments, names):
    return {
        name: arguments[name]
        for name in names
        if name in arguments and arguments[name] is not None
    }


def _call_tool(memory, name, arguments):
    if name == "add_memory":
        return memory.add(
            messages=arguments.get("messages"),
            **_filtered_kwargs(
                arguments,
                (
                    "user_id",
                    "agent_id",
                    "run_id",
                    "metadata",
                    "filters",
                    "scope",
                    "memory_type",
                    "prompt",
                    "infer",
                ),
            ),
        )
    if name == "search_memories":
        return memory.search(
            query=arguments.get("query", ""),
            **_filtered_kwargs(
                arguments,
                ("user_id", "agent_id", "run_id", "filters", "limit", "threshold"),
            ),
        )
    if name == "update_memory":
        return memory.update(
            memory_id=_coerce_memory_id(arguments.get("memory_id")),
            content=arguments.get("content", ""),
            **_filtered_kwargs(arguments, ("user_id", "agent_id", "metadata")),
        )
    if name == "delete_memory":
        return {
            "success": bool(
                memory.delete(
                    memory_id=_coerce_memory_id(arguments.get("memory_id")),
                    **_filtered_kwargs(arguments, ("user_id", "agent_id")),
                )
            ),
            "memory_id": arguments.get("memory_id"),
        }
    raise ValueError(f"unsupported PowerMem SDK tool: {name}")


def _emit(payload, *, exit_code=0):
    print(
        RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, default=str),
        flush=True,
    )
    raise SystemExit(exit_code)


try:
    payload = json.load(sys.stdin)
    from powermem import Memory
    from powermem.config_loader import _load_dotenv_if_available, load_config_from_env

    _load_dotenv_if_available()
    memory = Memory(config=load_config_from_env())
    result = _call_tool(
        memory,
        str(payload.get("name") or ""),
        dict(payload.get("arguments") or {}),
    )
    _emit({"ok": True, "result": result})
except Exception as exc:
    _emit(
        {
            "ok": False,
            "error": str(exc),
            "type": exc.__class__.__name__,
        },
        exit_code=1,
    )
"""


def _default_powermem_mcp_command() -> list[str]:
    return PowerMemMCPRuntimeInstaller().backend_command()


def _default_powermem_sdk_python() -> str:
    return PowerMemMCPRuntimeInstaller().python_command()


class PowerMemMCPClient(Protocol):
    """Minimal client contract for forwarding calls to PowerMem MCP."""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a PowerMem MCP tool and return its JSON-RPC result payload."""
        ...

    def close(self) -> None:
        """Release any child process resources."""
        ...


@dataclass
class PowerMemMCPStdioClient:
    """Small line-delimited JSON-RPC client for ``powermem-mcp stdio``."""

    command: list[str] = field(default_factory=_default_powermem_mcp_command)
    timeout: float = 30.0
    env: dict[str, str] | None = None
    _process: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _selector: selectors.DefaultSelector | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _next_id: int = field(default=1, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_started()
        request_id = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._allocate_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        response = self._read_response(request_id)
        if "error" in response:
            raise RuntimeError(response["error"].get("message") or response["error"])
        return response.get("result", {})

    def close(self) -> None:
        process = self._process
        self._process = None
        self._initialized = False
        if self._selector is not None:
            self._selector.close()
            self._selector = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.poll() is None:
            if not self._initialized:
                self._initialize()
            return
        env = powermem_child_process_env(self.env)
        self._process = subprocess.Popen(  # noqa: S603
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        if self._process.stdout is None or self._process.stdin is None:
            raise RuntimeError("failed to start PowerMem MCP stdio process")
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        self._initialize()

    def _initialize(self) -> None:
        request_id = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._allocate_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "contextseek-powermem-proxy",
                        "version": "0.0.0",
                    },
                },
            }
        )
        response = self._read_response(request_id)
        if "error" in response:
            raise RuntimeError(response["error"].get("message") or response["error"])
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            wait_for_response=False,
        )
        self._initialized = True

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _send(self, message: dict[str, Any], *, wait_for_response: bool = True) -> int:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("PowerMem MCP stdio process is not running")
        request_id = int(message.get("id", 0) or 0)
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()
        return request_id if wait_for_response else 0

    def _read_response(self, request_id: int) -> dict[str, Any]:
        process = self._process
        selector = self._selector
        if process is None or selector is None:
            raise RuntimeError("PowerMem MCP stdio process is not running")

        while True:
            if process.poll() is not None:
                raise RuntimeError("PowerMem MCP stdio process exited")
            events = selector.select(timeout=self.timeout)
            if not events:
                raise TimeoutError("timed out waiting for PowerMem MCP response")
            for key, _mask in events:
                line = key.fileobj.readline()
                if not line:
                    continue
                response = _decode_json_line(line)
                if not isinstance(response, dict):
                    continue
                if response.get("id") == request_id:
                    return response


@dataclass
class PowerMemSDKSubprocessClient:
    """Invoke the PowerMem SDK in the managed runtime for MCP tool calls."""

    python: str = field(default_factory=_default_powermem_sdk_python)
    timeout: float = 60.0
    env: dict[str, str] | None = None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        payload = json.dumps(
            {"name": name, "arguments": arguments},
            ensure_ascii=False,
        )
        try:
            completed = subprocess.run(  # noqa: S603
                [self.python, "-c", _SDK_TOOL_RUNNER],
                input=payload,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout,
                env=powermem_child_process_env(self.env),
                cwd=powermem_child_process_cwd(),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("timed out waiting for PowerMem SDK response") from exc
        except OSError as exc:
            raise RuntimeError(f"failed to start PowerMem SDK runtime: {exc}") from exc

        result = _extract_sdk_result(completed.stdout)
        if result is None:
            raise RuntimeError(
                "PowerMem SDK response was not valid JSON: "
                + _short_process_output(completed),
            )
        if not result.get("ok"):
            message = result.get("error") or _short_process_output(completed)
            raise RuntimeError(str(message))
        body = result.get("result", {})
        return {
            "structuredContent": body if isinstance(body, dict) else {"result": body}
        }

    def close(self) -> None:
        return None


@dataclass
class PowerMemMCPAdapter(PowerMemAdapter):
    """Forward ContextSeek MCP tool calls to PowerMem and materialize writes."""

    mcp_client: PowerMemMCPClient | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "add_memory",
                "description": "Add memory through PowerMem and ContextSeek proxy",
                "parameters": {
                    "messages": {"type": "string", "required": True},
                    "scope": {"type": "string", "default": None},
                    "user_id": {"type": "string", "default": None},
                    "agent_id": {"type": "string", "default": None},
                    "run_id": {"type": "string", "default": None},
                    "metadata": {"type": "object", "default": None},
                    "infer": {"type": "boolean", "default": True},
                },
            },
            {
                "name": "search_memories",
                "description": "Search memories through PowerMem MCP",
                "parameters": {
                    "query": {"type": "string", "required": True},
                    "scope": {"type": "string", "default": None},
                    "user_id": {"type": "string", "default": None},
                    "agent_id": {"type": "string", "default": None},
                    "run_id": {"type": "string", "default": None},
                    "limit": {"type": "integer", "default": 10},
                    "threshold": {"type": "number", "default": None},
                    "filters": {"type": "object", "default": None},
                },
            },
            {
                "name": "update_memory",
                "description": "Update memory through PowerMem and ContextSeek proxy",
                "parameters": {
                    "memory_id": {"type": "integer", "required": True},
                    "content": {"type": "string", "required": True},
                    "scope": {"type": "string", "default": None},
                    "user_id": {"type": "string", "default": None},
                    "agent_id": {"type": "string", "default": None},
                    "metadata": {"type": "object", "default": None},
                },
            },
            {
                "name": "delete_memory",
                "description": "Delete memory through PowerMem and ContextSeek proxy",
                "parameters": {
                    "memory_id": {"type": "integer", "required": True},
                    "scope": {"type": "string", "default": None},
                    "user_id": {"type": "string", "default": None},
                    "agent_id": {"type": "string", "default": None},
                },
            },
        ]

    def handle_write(self, request: PlugProxyRequest) -> PlugProxyResult:
        tool_name = str(request.context.get("mcp_tool_name") or "").lower()
        if tool_name not in _SUPPORTED_TOOLS:
            raise ValueError(f"unsupported PowerMem MCP tool: {tool_name}")

        body = request.body if isinstance(request.body, dict) else {}
        upstream_body = _upstream_body_for_tool(tool_name, body)
        upstream_result = self._client().call_tool(tool_name, upstream_body)
        response_body = _decode_tool_result(upstream_result)
        response = PlugProxyResponse(body=response_body, status_code=200, headers={})

        if tool_name in _SEARCH_TOOLS or _response_failed(response_body):
            return PlugProxyResult(response=response, events=[])

        event_request = PlugProxyRequest(
            method=_method_for_tool(tool_name),
            path=_path_for_tool(tool_name, body),
            body=_event_body_for_tool(tool_name, body),
            headers=request.headers,
            query=request.query,
            context=request.context,
        )
        events = self.events_from_write_response(response_body, event_request)
        if not events:
            events = self._fallback_events(tool_name, response_body, event_request)
        return PlugProxyResult(response=response, events=events)

    def handle_search(self, request: PlugProxyRequest) -> PlugProxyResponse:
        result = self.handle_write(request)
        return result.response

    def _client(self) -> PowerMemMCPClient:
        if self.mcp_client is None:
            if os.environ.get("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", "").strip():
                self.mcp_client = PowerMemMCPStdioClient()
            else:
                self.mcp_client = PowerMemSDKSubprocessClient()
        return self.mcp_client

    def _fallback_events(
        self,
        tool_name: str,
        response_body: Any,
        request: PlugProxyRequest,
    ) -> list[PlugChangeEvent]:
        if tool_name == "delete_memory":
            return self.events_from_delete_response(response_body, request)
        body = request.body if isinstance(request.body, dict) else {}
        content = body.get("memory") or body.get("content")
        if not content:
            return []
        record = {
            "id": body.get("id")
            or body.get("memory_id")
            or self._fallback_external_id({"content": content}, 0),
            "memory": content,
            "event": "UPDATE" if tool_name == "update_memory" else "ADD",
        }
        return self.events_from_write_response({"results": [record]}, request)


def build_powermem_mcp_adapter(
    *,
    instance_id: str | None = None,
    mcp_client: PowerMemMCPClient | None = None,
) -> PowerMemMCPAdapter:
    active_instance = instance_id or os.environ.get(
        "CONTEXTSEEK_POWERMEM_INSTANCE_ID",
        DEFAULT_INSTANCE_ID,
    )
    return PowerMemMCPAdapter(
        instance_id=active_instance,
        default_scope=os.environ.get("CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"),
        mcp_client=mcp_client,
    )


def create_powermem_mcp_proxy(
    *,
    client: ContextSeek | None = None,
    instance_id: str | None = None,
    mcp_client: PowerMemMCPClient | None = None,
) -> PlugMCPProxy:
    return PlugMCPProxy(
        client=client or ContextSeek.from_settings(),
        adapter=build_powermem_mcp_adapter(
            instance_id=instance_id,
            mcp_client=mcp_client,
        ),
    )


def run_stdio_server() -> int:
    """Run a line-delimited JSON-RPC MCP proxy over stdio."""
    proxy = create_powermem_mcp_proxy()
    try:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            try:
                request = json.loads(raw)
                response = proxy.handle_request(request)
            except json.JSONDecodeError:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                }
            if response is None:
                continue
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        close = getattr(proxy.adapter, "mcp_client", None)
        if close is not None:
            close.close()
    return 0


def _method_for_tool(tool_name: str) -> str:
    if tool_name == "delete_memory":
        return "DELETE"
    if tool_name == "update_memory":
        return "PUT"
    return "POST"


def _path_for_tool(tool_name: str, body: dict[str, Any]) -> str:
    memory_id = body.get("memory_id") or body.get("id")
    if tool_name in {"delete_memory", "update_memory"} and memory_id:
        return f"{MEMORIES_PATH}/{memory_id}"
    return MEMORIES_PATH


def _upstream_body_for_tool(tool_name: str, body: dict[str, Any]) -> dict[str, Any]:
    normalized = _without_contextseek_fields(body)
    if tool_name == "add_memory":
        if "messages" not in normalized:
            for key in ("memory", "content"):
                if key in normalized:
                    normalized["messages"] = normalized.pop(key)
                    break
    elif tool_name in {"update_memory", "delete_memory"}:
        if "memory_id" not in normalized and "id" in normalized:
            normalized["memory_id"] = normalized.pop("id")
        if tool_name == "update_memory" and "content" not in normalized:
            for key in ("memory", "messages"):
                if key in normalized:
                    normalized["content"] = normalized.pop(key)
                    break
    return normalized


def _event_body_for_tool(tool_name: str, body: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(body)
    if tool_name == "add_memory" and "memory" not in normalized:
        for key in ("messages", "content"):
            if key in normalized:
                normalized["memory"] = normalized[key]
                break
    elif tool_name == "update_memory":
        if "id" not in normalized and "memory_id" in normalized:
            normalized["id"] = normalized["memory_id"]
        if "memory" not in normalized:
            for key in ("content", "messages"):
                if key in normalized:
                    normalized["memory"] = normalized[key]
                    break
    elif tool_name == "delete_memory" and "id" not in normalized:
        if "memory_id" in normalized:
            normalized["id"] = normalized["memory_id"]
    return normalized


def _without_contextseek_fields(body: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in body.items() if key not in {"scope", "stage_hint"}
    }


def _decode_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    structured = result.get("structuredContent")
    if structured is not None:
        return _decode_structured_content(structured)
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                return _decode_json_text(text)
    return result


def _decode_structured_content(value: Any) -> Any:
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, str):
            return _decode_json_text(result)
    return value


def _decode_json_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return {"content": text}


def _decode_json_line(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _extract_sdk_result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if not line.startswith(_SDK_RESULT_PREFIX):
            continue
        payload = line[len(_SDK_RESULT_PREFIX) :]
        try:
            result = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None
    return None


def _short_process_output(completed: subprocess.CompletedProcess[str]) -> str:
    text = " ".join(
        ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip().split()
    )
    return text[:300] if text else f"exit code {completed.returncode}"


def _response_failed(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("success") is False:
        return True
    return bool(body.get("error") and not (body.get("results") or body.get("memories")))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_stdio_server())
