"""Runnable MCP server transports for stdio and SSE."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from contextseek._version import __version__ as PACKAGE_VERSION
from contextseek.mcp.server import ContextSeekMCPServer
from contextseek.errors import ContextSeekError

try:
    from fastapi import FastAPI
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from fastapi.responses import StreamingResponse
except ImportError:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore[assignment]
    Request = Any  # type: ignore[misc,assignment]
    JSONResponse = None  # type: ignore[assignment]
    StreamingResponse = None  # type: ignore[assignment]


JSONRPC_VERSION = "2.0"


@dataclass
class MCPSession:
    """Tracks per-client MCP session state."""

    session_id: str
    client_info: dict[str, Any]
    initialized_at: str
    tool_call_count: int = 0

    def record_call(self) -> None:
        self.tool_call_count += 1


@dataclass
class MCPRuntime:
    """Transport-agnostic MCP runtime for request dispatch."""

    server: ContextSeekMCPServer
    _sessions: dict[str, MCPSession] | None = None

    def __post_init__(self) -> None:
        if self._sessions is None:
            self._sessions = {}

    @property
    def sessions(self) -> dict[str, MCPSession]:
        """Return current active sessions."""
        return dict(self._sessions or {})

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle one JSON-RPC style MCP request."""
        method = str(request.get("method", ""))
        request_id = request.get("id")
        params = dict(request.get("params", {}))
        if method == "initialize":
            session_id = str(request_id or uuid4())
            client_info = dict(params.get("clientInfo", {}))
            from datetime import datetime, timezone

            session = MCPSession(
                session_id=session_id,
                client_info=client_info,
                initialized_at=datetime.now(timezone.utc).isoformat(),
            )
            self._sessions[session_id] = session
            result = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "contextseek-mcp", "version": PACKAGE_VERSION},
                "capabilities": {"tools": {}},
                "sessionId": session_id,
            }
            return _success_response(request_id=request_id, result=result)
        if method == "tools/list":
            return _success_response(
                request_id=request_id, result={"tools": self.server.list_tools()}
            )
        if method == "tools/call":
            name = str(params.get("name", ""))
            arguments = dict(params.get("arguments", {}))
            # Track session usage
            session_id = str(params.get("_sessionId", request_id or ""))
            if session_id in self._sessions:
                self._sessions[session_id].record_call()
            try:
                payload = self.server.call_tool(name, arguments)
            except Exception as exc:
                data = exc.as_dict() if isinstance(exc, ContextSeekError) else None
                return _error_response(
                    request_id=request_id,
                    code=-32000,
                    message=str(exc),
                    data=data,
                )
            return _success_response(request_id=request_id, result={"content": payload})
        if method == "notifications/initialized":
            return _success_response(request_id=request_id, result={})
        return _error_response(
            request_id=request_id,
            code=-32601,
            message=f"method not found: {method}",
        )


def _success_response(*, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error_response(
    *, request_id: Any, code: int, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


_DEFAULT_DAEMON_MCP_BASE = "http://127.0.0.1:2882"


def _daemon_mcp_base() -> str:
    """Base URL of the daemon's HTTP MCP endpoint (override via env)."""
    return os.environ.get("CONTEXTSEEK_MCP_HTTP", _DEFAULT_DAEMON_MCP_BASE).rstrip("/")


def _daemon_available(base: str, timeout: float = 1.0) -> bool:
    """Return True when the daemon's HTTP MCP server answers on ``/health``."""
    try:
        with urllib.request.urlopen(base + "/health", timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _post_message(
    base: str, request: dict[str, Any], timeout: float = 30.0
) -> dict[str, Any]:
    """Forward one JSON-RPC request to the daemon's ``/message`` endpoint."""
    data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/message",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_stdio_server() -> int:
    """Run a line-delimited JSON-RPC server over stdio.

    When the background daemon's HTTP MCP server is reachable, this acts as a
    thin stdio↔HTTP bridge so all requests hit the daemon's single shared
    storage instance — avoiding a second embedded-DB connection on the same
    path.  When no daemon is running it falls back to an in-process server so
    the command still works standalone.
    """
    base = _daemon_mcp_base()
    use_bridge = _daemon_available(base)
    runtime: MCPRuntime | None = None
    if not use_bridge:
        runtime = MCPRuntime(server=ContextSeekMCPServer.with_default_client())

    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            response = _error_response(
                request_id=None, code=-32700, message="parse error"
            )
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        if use_bridge:
            try:
                response = _post_message(base, request)
            except (urllib.error.URLError, OSError, ValueError):
                # Daemon went away mid-session — degrade to in-process handling.
                use_bridge = False
                if runtime is None:
                    runtime = MCPRuntime(
                        server=ContextSeekMCPServer.with_default_client()
                    )
                response = runtime.handle_request(request)
        else:
            response = runtime.handle_request(request)

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def create_sse_app(*, runtime: MCPRuntime | None = None) -> FastAPI:
    """Create SSE transport app for MCP-style communication."""
    if FastAPI is None:
        msg = "FastAPI dependencies are required for SSE transport."
        raise RuntimeError(msg)
    service = runtime or MCPRuntime(server=ContextSeekMCPServer.with_default_client())
    app = FastAPI(title="ContextSeek MCP SSE", version=PACKAGE_VERSION)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": PACKAGE_VERSION}

    @app.get("/sse")
    async def sse() -> StreamingResponse:
        async def event_stream() -> Any:
            ready = {"type": "ready", "protocol": JSONRPC_VERSION}
            yield f"event: ready\ndata: {json.dumps(ready, ensure_ascii=False)}\n\n"
            while True:
                await asyncio.sleep(15)
                yield "event: ping\ndata: {}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/message")
    async def message(request: Request) -> JSONResponse:
        payload = await request.json()
        response = service.handle_request(dict(payload))
        return JSONResponse(response)

    return app


def run_sse_server() -> int:
    """Run SSE transport with uvicorn."""
    if FastAPI is None:
        msg = "FastAPI dependencies are required for SSE transport."
        raise RuntimeError(msg)
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        msg = "uvicorn is required for SSE transport."
        raise RuntimeError(msg) from exc

    parser = argparse.ArgumentParser(prog="contextseek-mcp-sse")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(create_sse_app(), host=args.host, port=args.port)
    return 0
