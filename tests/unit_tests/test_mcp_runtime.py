"""Tests for MCP runtime/server construction."""

from __future__ import annotations

from io import StringIO

from contextseek.mcp.runtime import run_stdio_server
from contextseek.mcp.server import ContextSeekMCPServer


def test_mcp_server_with_default_client_builds_tools() -> None:
    server = ContextSeekMCPServer.with_default_client()

    assert isinstance(server, ContextSeekMCPServer)
    assert any(tool["name"] == "contextseek_retrieve" for tool in server.list_tools())


def test_stdio_server_falls_back_to_default_client_without_daemon(monkeypatch) -> None:
    monkeypatch.setattr("contextseek.mcp.runtime._daemon_available", lambda base: False)
    monkeypatch.setattr("sys.stdin", StringIO(""))
    monkeypatch.setattr("sys.stdout", StringIO())

    assert run_stdio_server() == 0
