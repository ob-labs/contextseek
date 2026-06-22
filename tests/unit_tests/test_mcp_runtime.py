"""Tests for MCP runtime/server construction."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from contextseek.domain.results import ResponseMeta, RetrieveResponse
from contextseek.mcp.runtime import run_stdio_server
from contextseek.mcp.server import ContextSeekMCPServer


def test_mcp_server_with_default_client_builds_tools() -> None:
    server = ContextSeekMCPServer.with_default_client()

    assert isinstance(server, ContextSeekMCPServer)
    assert any(tool["name"] == "contextseek_retrieve" for tool in server.list_tools())
    retrieve_tool = next(
        tool for tool in server.list_tools() if tool["name"] == "contextseek_retrieve"
    )
    assert "include_expired" in retrieve_tool["parameters"]


def test_stdio_server_falls_back_to_default_client_without_daemon(monkeypatch) -> None:
    monkeypatch.setattr("contextseek.mcp.runtime._daemon_available", lambda base: False)
    monkeypatch.setattr("sys.stdin", StringIO(""))
    monkeypatch.setattr("sys.stdout", StringIO())

    assert run_stdio_server() == 0


def test_mcp_retrieve_forwards_include_expired_flag() -> None:
    class FakeClient:
        def retrieve(self, query, **kwargs):
            self.query = query
            self.kwargs = kwargs
            return RetrieveResponse(items=[], meta=ResponseMeta(layer="full"))

    fake = FakeClient()
    server = ContextSeekMCPServer(client=fake)

    server.call_tool(
        "contextseek_retrieve",
        {
            "scope": "t/p/s",
            "query": "q",
            "k": 3,
            "full": True,
            "include_expired": True,
        },
    )

    assert fake.query == "q"
    assert fake.kwargs["include_expired"] is True


def test_mcp_compact_returns_conflict_counts() -> None:
    class FakeClient:
        def compact(self, *, scope):
            self.scope = scope
            return SimpleNamespace(
                merged_count=1,
                archived_count=2,
                evolved_count=3,
                conflict_updated_count=4,
                conflict_drift_count=5,
            )

    fake = FakeClient()
    server = ContextSeekMCPServer(client=fake)
    out = server.call_tool("contextseek_compact", {"scope": "tenant/proj/sess"})

    assert fake.scope == "tenant/proj/sess"
    assert out == {
        "merged": 1,
        "archived": 2,
        "evolved": 3,
        "conflict_updated": 4,
        "conflict_drift": 5,
    }
