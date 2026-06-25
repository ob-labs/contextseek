"""Unit tests for PowerMemPlug."""

import hashlib
import json
import os
import shlex
import subprocess
import tarfile
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from contextseek import ContextSeek
from contextseek.cli.main import _plug_install_status, run_cli
import contextseek.plugs.core.runtime as plug_runtime
from contextseek.plugs.core.proxy.mcp import PlugMCPProxy
from contextseek.plugs import PowerMemPlug, PowerMemProxyPlug
from contextseek.plugs.powermem import PowerMemAdapter
from contextseek.plugs.powermem.cli import PowerMemCLIAdapter
from contextseek.plugs.powermem.http import base_url_for_instance
from contextseek.plugs.powermem.mcp import (
    PowerMemMCPAdapter,
    PowerMemMCPStdioClient,
    create_powermem_mcp_proxy,
)
from contextseek.plugs.powermem.env import (
    powermem_child_process_cwd,
    powermem_child_process_env,
    read_env_file,
)
import contextseek.plugs.powermem.cli as powermem_cli
from contextseek.plugs.powermem.serve import (
    _status_from_warnings,
    build_powermem_serve_plan,
)
import contextseek.plugs.powermem.sdk as powermem_sdk
from contextseek.plugs.powermem.sdk import Memory as PowerMemMemoryProxy
from contextseek.plugs.powermem.linkers import available_linker_names
from contextseek.plugs.powermem.linkers.claude_code import (
    create_mcp_linker as create_claude_code_mcp_linker,
)
import contextseek.plugs.powermem.linkers.claude_code_plugin as claude_code_plugin
import contextseek.plugs.powermem.linkers.config as linker_config
import contextseek.plugs.powermem.linkers.runtime as powermem_runtime
from contextseek.plugs.core.protocols import PlugProxyRequest


class _FakeMemory:
    def get_all(self, user_id=None, agent_id=None, run_id=None, limit=100, offset=0):
        return {
            "results": [
                {
                    "id": 9,
                    "content": "sync me",
                    "user_id": user_id,
                    "agent_id": agent_id,
                }
            ]
        }


class _FakeSDKMemory:
    def add(self, memory, **_kwargs):
        return {"results": [{"id": "sdk-1", "memory": memory, "event": "ADD"}]}

    def search(self, query, **_kwargs):
        return {"query": query, "results": []}


class _FakeMCPClient:
    def __init__(self, responses, *, structured=False):
        self.responses = list(responses)
        self.calls = []
        self.structured = structured

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        body = self.responses.pop(0)
        if self.structured:
            return {
                "structuredContent": {
                    "result": json.dumps(body, ensure_ascii=False),
                }
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(body, ensure_ascii=False),
                }
            ]
        }

    def close(self):
        return None


def _mcp_structured(result):
    return result["structuredContent"]


@pytest.fixture(autouse=True)
def _managed_powermem_env_path(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTSEEK_DESKTOP", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", raising=False)
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_ENV_FILE",
        str(tmp_path / "powermem.env"),
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG",
        str(tmp_path / "claude-code.mcp.json"),
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_CLAUDE_CODE_COMMAND",
        str(tmp_path / "missing-claude"),
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "0")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "0")


def _managed_powermem_bin(runtime_dir: Path, executable: str) -> str:
    if os.name == "nt":
        suffix = "" if executable.endswith(".exe") else ".exe"
        return str(runtime_dir / "venv" / "Scripts" / f"{executable}{suffix}")
    return str(runtime_dir / "venv" / "bin" / executable)


def _command_name(command: str | list[str]) -> str:
    value = command[0] if isinstance(command, list) else command
    return Path(value).name


def test_from_memory_plug_flow() -> None:
    ctx = ContextSeek()
    scope = "t/u/a"
    plug = PowerMemPlug.from_memory(_FakeMemory(), user_id="u", agent_id="a")
    assert len(plug.entries) == 1
    ctx.plug(plug, scope=scope)
    hits = ctx.retrieve("sync", scope=scope, k=5)
    assert hits
    assert hits.items[0].item.provenance.source_id == "powermem://9"


def test_from_records_get_all_shape() -> None:
    plug = PowerMemPlug.from_records(
        [
            {
                "id": 42,
                "content": "User likes tea",
                "metadata": {"tags": ["preference"]},
                "user_id": "u1",
            }
        ]
    )
    events = list(plug.stream())
    assert len(events) == 1
    assert events[0].content == "User likes tea"
    assert events[0].source == "powermem://42"
    assert "powermem" in events[0].tags
    assert "preference" in events[0].tags
    assert events[0].metadata["user_id"] == "u1"


def test_from_records_search_shape() -> None:
    plug = PowerMemPlug.from_records(
        [{"memory": "Deploy checklist", "score": 0.91, "id": 7}]
    )
    events = list(plug.stream())
    assert events[0].content == "Deploy checklist"
    assert events[0].metadata["powermem_score"] == 0.91


def test_claude_code_plugin_install_failure_is_fatal() -> None:
    warnings = [
        "failed to install Claude Code plugin memory-powermem: not found",
        "disabled linker: claude-code-http",
    ]

    assert _plug_install_status(warnings) == 1
    assert _status_from_warnings(warnings) == 1


def test_adapter_snapshot_supports_manual_import() -> None:
    ctx = ContextSeek()
    adapter = PowerMemAdapter.from_records(
        [{"id": "snap-1", "content": "Snapshot memory"}]
    )
    snapshot = adapter.snapshot()

    assert snapshot is not None
    ctx.plug(snapshot, scope="tenant/agent/user")
    hits = ctx.retrieve("snapshot", scope="tenant/agent/user", k=3)
    assert hits.items


def test_proxy_unknown_event_maps_to_noop() -> None:
    plug = PowerMemProxyPlug(base_url="http://powermem.local", instance_id="i1")
    events = plug._events_from_write_response(
        {"results": [{"id": "m1", "memory": "hello", "event": "SURPRISE"}]},
        {"scope": "tenant/agent/user"},
    )

    assert len(events) == 1
    assert events[0].operation == "noop"


def test_proxy_keeps_powermem_metadata() -> None:
    plug = PowerMemProxyPlug(base_url="http://powermem.local", instance_id="i1")
    events = plug._events_from_write_response(
        {
            "results": [
                {
                    "id": "m1",
                    "memory": "hello",
                    "event": "ADD",
                    "metadata": {"topic": "ops"},
                }
            ]
        },
        {"scope": "tenant/agent/user", "user_id": "u1", "agent_id": "a1"},
    )

    assert events[0].metadata == {
        "topic": "ops",
        "user_id": "u1",
        "agent_id": "a1",
    }


def test_powermem_adapter_reads_data_results_shape() -> None:
    adapter = PowerMemAdapter(instance_id="i1")
    events = adapter.events_from_write_response(
        {"data": {"results": [{"id": "m1", "content": "hello", "event": "ADD"}]}},
        PlugProxyRequest(
            method="POST",
            path="/api/v1/memories",
            body={"scope": "tenant/agent/user"},
            headers={},
            query={},
        ),
    )

    assert len(events) == 1
    assert events[0].external_id == "m1"
    assert events[0].content == "hello"


def test_powermem_adapter_preserves_zero_importance() -> None:
    adapter = PowerMemAdapter(instance_id="i1")
    events = adapter.events_from_write_response(
        {"results": [{"id": "m1", "content": "zero", "importance": 0.0}]},
        PlugProxyRequest(
            method="POST",
            path="/api/v1/memories",
            body={"scope": "tenant/agent/user"},
            headers={},
            query={},
        ),
    )

    assert events[0].importance == 0.0


def test_powermem_adapter_defaults_scope_to_contextseek() -> None:
    adapter = PowerMemAdapter(instance_id="i1")
    events = adapter.events_from_write_response(
        {"results": [{"id": "m1", "content": "hello", "event": "ADD"}]},
        PlugProxyRequest(
            method="POST",
            path="/api/v1/memories",
            body={"user_id": "u1", "agent_id": "claude-code"},
            headers={},
            query={},
        ),
    )

    assert events[0].scope == "contextseek"


def test_powermem_adapter_auto_enables_infer_for_hook_when_llm_configured(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=qwen",
                "LLM_MODEL=qwen-plus",
                "LLM_API_KEY=llm-key",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": "Claude Code session transcript\n\n[User]\n喜欢喝可乐",
            "infer": False,
            "metadata": {
                "kind": "session-end-transcript",
                "source": "claude-code-hook",
            },
        },
        headers={},
        query={},
    )

    prepared = PowerMemAdapter(instance_id="i1").prepare_write_request(request)

    assert prepared.body["infer"] is True
    assert request.body["infer"] is False


def test_powermem_adapter_removes_hook_metadata_from_infer_request(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=qwen",
                "LLM_MODEL=qwen-plus",
                "LLM_API_KEY=llm-key",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": "Claude Code session transcript\n\n[User]\n喜欢吃火锅",
            "user_id": "u1",
            "agent_id": "claude-code",
            "run_id": "session-a",
            "metadata": {
                "kind": "session-end-transcript",
                "source": "claude-code-hook",
                "session_id": "session-a",
                "transcript_path": "~/.claude/projects/a.jsonl",
                "cwd": "~/tmp_data",
                "scope": "contextseek",
            },
        },
        headers={},
        query={},
    )

    adapter = PowerMemAdapter(instance_id="i1")
    prepared = adapter.prepare_write_request(request)

    assert prepared.body["infer"] is True
    assert "metadata" not in prepared.body
    assert "run_id" not in prepared.body
    assert prepared.body["scope"] == "contextseek"
    assert request.body["run_id"] == "session-a"
    assert request.body["metadata"]["session_id"] == "session-a"

    events = adapter.events_from_write_response(
        {"results": [{"id": "m1", "memory": "喜欢吃火锅", "event": "ADD"}]},
        prepared,
    )

    assert events[0].raw_payload["request"]["metadata"]["session_id"] == "session-a"
    assert "metadata" not in events[0].raw_payload["forwarded_request"]


def test_powermem_adapter_keeps_hook_infer_false_without_llm(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text("DATABASE_PROVIDER=sqlite\n", encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    monkeypatch.setenv("LLM_PROVIDER", "none")
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": "Claude Code session transcript\n\n[User]\n喜欢喝可乐",
            "infer": False,
            "metadata": {"kind": "session-end-transcript"},
        },
        headers={},
        query={},
    )

    prepared = PowerMemAdapter(instance_id="i1").prepare_write_request(request)

    assert prepared is not request
    assert prepared.body["infer"] is False


def test_powermem_adapter_disables_default_infer_for_hook_without_llm(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text("DATABASE_PROVIDER=sqlite\n", encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    monkeypatch.setenv("LLM_PROVIDER", "none")
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": "Compact summary: 用户喜欢喝可乐",
            "metadata": {"kind": "compact-summary"},
        },
        headers={},
        query={},
    )

    prepared = PowerMemAdapter(instance_id="i1").prepare_write_request(request)

    assert prepared.body["infer"] is False
    assert "infer" not in request.body


def test_powermem_adapter_marks_auto_infer_simple_fallback_as_raw(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "LLM_PROVIDER=qwen\nLLM_MODEL=qwen-plus\nLLM_API_KEY=llm-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    original_content = "Claude Code session transcript\n\n[User]\n喜欢喝可乐"
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": original_content,
            "infer": False,
            "metadata": {"kind": "session-end-transcript"},
        },
        headers={},
        query={},
    )
    adapter = PowerMemAdapter(instance_id="i1")
    prepared = adapter.prepare_write_request(request)

    events = adapter.events_from_write_response(
        {"results": [{"id": "m1", "memory": original_content, "event": "ADD"}]},
        prepared,
    )

    assert events[0].stage_hint == "raw"


def test_powermem_adapter_marks_auto_infer_extracted_result_as_extracted(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "LLM_PROVIDER=qwen\nLLM_MODEL=qwen-plus\nLLM_API_KEY=llm-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories",
        body={
            "content": "Claude Code session transcript\n\n[User]\n喜欢喝可乐",
            "infer": False,
            "metadata": {"kind": "session-end-transcript"},
        },
        headers={},
        query={},
    )
    adapter = PowerMemAdapter(instance_id="i1")
    prepared = adapter.prepare_write_request(request)

    events = adapter.events_from_write_response(
        {"results": [{"id": "m1", "memory": "用户喜欢喝可乐", "event": "ADD"}]},
        prepared,
    )

    assert events[0].stage_hint == "extracted"


def test_powermem_adapter_delete_request_creates_delete_event() -> None:
    adapter = PowerMemAdapter(instance_id="i1")
    events = adapter.events_from_write_response(
        {"success": True},
        PlugProxyRequest(
            method="DELETE",
            path="/api/v1/memories/m1",
            body={"scope": "tenant/agent/user"},
            headers={},
            query={},
        ),
    )

    assert len(events) == 1
    assert events[0].external_id == "m1"
    assert events[0].operation == "delete"
    assert events[0].content == ""


def test_powermem_http_prefers_contextseek_upstream_env(monkeypatch) -> None:
    monkeypatch.setenv("POWERMEM_DEFAULT_BASE_URL", "http://old.example")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_DEFAULT_UPSTREAM_BASE_URL",
        "http://new.example",
    )

    assert base_url_for_instance("default") == "http://new.example"


def test_powermem_http_health_is_read_request() -> None:
    plug = PowerMemProxyPlug(base_url="http://powermem.local")
    request = PlugProxyRequest(
        method="GET",
        path="/health",
        body={},
        headers={},
        query={},
    )

    assert plug.is_write_request(request) is False


def test_powermem_http_search_uses_contextseek(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    ctx.add(
        "http search hit from contextseek", scope="tenant/agent/user", source="test"
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")
    request = PlugProxyRequest(
        method="POST",
        path="/api/v1/memories/search",
        body={"query": "search", "scope": "tenant/agent/user", "limit": 3},
        headers={},
        query={},
    )

    response = plug.handle_contextseek_search(ctx, request)

    assert response.body["success"] is True
    assert response.body["data"]["results"][0]["content"] == (
        "http search hit from contextseek"
    )
    assert response.body["data"]["total"] == 1
    assert response.body["data"]["query"] == "search"
    assert response.body["message"] == "Search completed successfully"


def test_powermem_cli_adapter_falls_back_to_add_args() -> None:
    adapter = PowerMemCLIAdapter(instance_id="i1")
    events = adapter.events_from_cli_success(
        ["memory", "add", "--user-id", "u1", "remember tea"],
        "created\n",
    )

    assert len(events) == 1
    assert events[0].operation == "add"
    assert events[0].content == "remember tea"
    assert events[0].metadata["user_id"] == "u1"


def test_powermem_cli_adapter_handles_real_pmem_global_options() -> None:
    adapter = PowerMemCLIAdapter(instance_id="i1")
    events = adapter.events_from_cli_success(
        [
            "-f",
            "/tmp/powermem.env",
            "memory",
            "add",
            "remember",
            "oolong",
            "-u",
            "u1",
            "-a",
            "claude-code",
            "--no-infer",
            "-j",
        ],
        "created\n",
    )

    assert len(events) == 1
    assert events[0].operation == "add"
    assert events[0].content == "remember oolong"
    assert events[0].metadata["user_id"] == "u1"
    assert events[0].metadata["agent_id"] == "claude-code"
    assert events[0].stage_hint == "raw"


def test_powermem_cli_main_resolves_real_pmem_from_env_file(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    env_file = tmp_path / "powermem.env"
    env_file.write_text(
        "\n".join(
            [
                "CONTEXTSEEK_POWERMEM_CLI=/managed/powermem/bin/pmem",
                "CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE=contextseek",
                "SQLITE_PATH=/tmp/powermem.sqlite3",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLI", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_REAL_PMEM", raising=False)
    monkeypatch.delenv("PMEM_PATH", raising=False)

    calls: list[tuple[list[str], dict[str, str]]] = []
    materialized = []

    def fake_run_cli(argv, *, env=None, cwd=None):
        assert cwd == tmp_path
        calls.append((argv, env or {}))
        return Namespace(
            stdout=json.dumps(
                {"results": [{"id": "m1", "memory": "remember apple", "event": "ADD"}]},
            )
            + "\n",
            stderr="",
            returncode=0,
        )

    def fake_materialize_cli_events(events):
        materialized.extend(events)
        return []

    monkeypatch.setattr(powermem_cli, "run_cli", fake_run_cli)
    monkeypatch.setattr(
        powermem_cli,
        "materialize_cli_events",
        fake_materialize_cli_events,
    )

    rc = powermem_cli.main(
        [
            "--env-file",
            str(env_file),
            "--json",
            "-j",
            "memory",
            "add",
            "remember apple",
        ],
    )

    assert rc == 0
    assert calls[0][0][0] == "/managed/powermem/bin/pmem"
    assert calls[0][1]["POWERMEM_ENV_FILE"] == str(env_file)
    assert calls[0][1]["SQLITE_PATH"] == "/tmp/powermem.sqlite3"
    assert len(materialized) == 1
    assert materialized[0].content == "remember apple"
    assert materialized[0].scope == "contextseek"
    capsys.readouterr()


def test_powermem_cli_main_search_uses_contextseek_not_real_pmem(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    ctx.add("cli search hit from contextseek", scope="contextseek", source="test")

    def fail_run_cli(*_args, **_kwargs):
        raise AssertionError("search should not call the real pmem CLI")

    monkeypatch.setattr(powermem_cli, "run_cli", fail_run_cli)
    monkeypatch.setattr(powermem_cli, "_contextseek_client", lambda: ctx)

    rc = powermem_cli.main(["memory", "search", "cli", "--limit", "3"])

    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["results"][0]["memory"] == "cli search hit from contextseek"
    assert "_contextseek" not in output


def test_powermem_cli_adapter_ignores_real_pmem_search() -> None:
    adapter = PowerMemCLIAdapter(instance_id="i1")
    events = adapter.events_from_cli_success(
        ["memory", "search", "oolong", "-u", "u1", "-j"],
        '{"results":[]}\n',
    )

    assert events == []


def test_powermem_mcp_proxy_forwards_then_materializes_add(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    mcp_client = _FakeMCPClient(
        [{"results": [{"id": "mcp-1", "memory": "mcp memory", "event": "ADD"}]}]
    )
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "add_memory",
        {"messages": "mcp memory", "scope": "tenant/agent/user"},
    )

    assert mcp_client.calls == [("add_memory", {"messages": "mcp memory"})]
    assert _mcp_structured(result)["_contextseek"]["status"] == "ok"
    assert ctx.retrieve("mcp", scope="tenant/agent/user", k=3).items


def test_powermem_mcp_proxy_search_uses_contextseek_not_powermem(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    ctx.add("mcp search hit from contextseek", scope="tenant/agent/user", source="test")
    mcp_client = _FakeMCPClient([])
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "search_memories",
        {"query": "search", "scope": "tenant/agent/user"},
    )

    assert mcp_client.calls == []
    assert _mcp_structured(result)["results"][0]["memory"] == (
        "mcp search hit from contextseek"
    )
    assert "_contextseek" not in _mcp_structured(result)


def test_powermem_mcp_proxy_materializes_update(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    mcp_client = _FakeMCPClient(
        [
            {
                "id": "mcp-1",
                "memory": "updated mcp memory",
                "event": "UPDATE",
            }
        ]
    )
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "update_memory",
        {
            "memory_id": "mcp-1",
            "content": "updated mcp memory",
            "scope": "tenant/agent/user",
        },
    )

    assert mcp_client.calls == [
        (
            "update_memory",
            {"memory_id": "mcp-1", "content": "updated mcp memory"},
        )
    ]
    assert _mcp_structured(result)["_contextseek"]["status"] == "ok"
    assert ctx.retrieve("updated", scope="tenant/agent/user", k=3).items


def test_powermem_mcp_proxy_decodes_fastmcp_structured_result(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    mcp_client = _FakeMCPClient(
        [
            {
                "results": [
                    {
                        "id": "mcp-structured-1",
                        "memory": "structured mcp memory",
                        "event": "ADD",
                    }
                ]
            }
        ],
        structured=True,
    )
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "add_memory",
        {"messages": "structured mcp memory", "scope": "tenant/agent/user"},
    )

    assert _mcp_structured(result)["results"][0]["id"] == "mcp-structured-1"
    assert _mcp_structured(result)["_contextseek"]["status"] == "ok"


def test_powermem_mcp_proxy_materializes_delete(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    mcp_client = _FakeMCPClient([{"success": True, "memory_id": "mcp-1"}])
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "delete_memory",
        {"memory_id": "mcp-1", "scope": "tenant/agent/user"},
    )

    assert mcp_client.calls == [("delete_memory", {"memory_id": "mcp-1"})]
    assert _mcp_structured(result)["_contextseek"]["status"] == "ok"


def test_powermem_mcp_proxy_factory_wires_adapter() -> None:
    ctx = ContextSeek()
    mcp_client = _FakeMCPClient([])
    proxy = create_powermem_mcp_proxy(client=ctx, mcp_client=mcp_client)

    assert isinstance(proxy.adapter, PowerMemMCPAdapter)


def test_powermem_mcp_proxy_uses_standard_initialize_and_tool_schema() -> None:
    proxy = PlugMCPProxy(
        client=ContextSeek(),
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=_FakeMCPClient([])),
    )

    initialize = proxy.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }
    )
    initialized = proxy.handle_request(
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
    )
    tools = proxy.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
    )

    assert initialize is not None
    assert initialize["result"]["protocolVersion"] == "2024-11-05"
    assert initialize["result"]["capabilities"] == {"tools": {}}
    assert initialized is None
    assert tools is not None
    add_memory = tools["result"]["tools"][0]
    assert add_memory["name"] == "add_memory"
    assert "inputSchema" in add_memory
    assert "messages" in add_memory["inputSchema"]["required"]


def test_powermem_mcp_proxy_lists_only_core_tools() -> None:
    adapter = PowerMemMCPAdapter(mcp_client=_FakeMCPClient([]))

    assert [tool["name"] for tool in adapter.list_tools()] == [
        "add_memory",
        "search_memories",
        "update_memory",
        "delete_memory",
    ]


def test_powermem_sdk_memory_proxy_materializes_add(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    memory = PowerMemMemoryProxy(
        powermem_memory=_FakeSDKMemory(),
        contextseek_client=ctx,
        instance_id="i1",
        default_scope="tenant/agent/user",
    )

    result = memory.add("sdk memory")

    assert result["results"][0]["id"] == "sdk-1"
    assert ctx.retrieve("sdk", scope="tenant/agent/user", k=3).items


def test_powermem_sdk_memory_proxy_search_uses_contextseek(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    ctx.add("sdk search hit from contextseek", scope="tenant/agent/user", source="test")
    memory = PowerMemMemoryProxy(
        powermem_memory=_FakeSDKMemory(),
        contextseek_client=ctx,
        instance_id="i1",
        default_scope="tenant/agent/user",
    )

    result = memory.search("search", limit=3)

    assert result["results"][0]["memory"] == "sdk search hit from contextseek"
    assert "_contextseek" not in result


def test_powermem_sdk_proxy_short_circuits_private_getattr() -> None:
    memory = PowerMemMemoryProxy.__new__(PowerMemMemoryProxy)

    with pytest.raises(AttributeError):
        getattr(memory, "_memory")


def test_powermem_sdk_version_info_reads_installed_version(monkeypatch) -> None:
    monkeypatch.setattr(
        powermem_sdk.importlib_metadata,
        "version",
        lambda name: "1.1.1",
    )

    info = powermem_sdk.validate_powermem_sdk_version()

    assert info.package_name == "powermem"
    assert info.installed_version == "1.1.1"
    assert info.min_version == "1.1.1"


def test_powermem_sdk_version_rejects_below_contextseek_minimum(monkeypatch) -> None:
    monkeypatch.setattr(
        powermem_sdk.importlib_metadata,
        "version",
        lambda name: "1.0.0",
    )

    with pytest.raises(RuntimeError, match="requires >= 1.1.1"):
        powermem_sdk.validate_powermem_sdk_version()


def test_powermem_sdk_version_requires_installed_sdk(monkeypatch) -> None:
    def _missing(_name: str) -> str:
        raise powermem_sdk.importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(powermem_sdk.importlib_metadata, "version", _missing)

    with pytest.raises(RuntimeError, match="powermem>=1.1.1"):
        powermem_sdk.validate_powermem_sdk_version()


def test_proxy_install_without_linker_lists_options() -> None:
    plug = PowerMemProxyPlug(base_url="http://powermem.local")
    result = plug.install(dry_run=False)

    assert result.changed is False
    assert result.warnings
    assert "available linkers" in result.actions[0]


def test_available_linkers_cover_powermem_readme_targets() -> None:
    names = set(available_linker_names())

    assert {
        "claude-code",
        "claude-desktop",
        "cline",
        "codex",
        "copilot",
        "cursor",
        "openclaw",
        "opencode",
        "qoder",
        "vscode",
        "windsurf",
    }.issubset(names)
    assert "claude-code-http" not in names
    assert "claude-code-mcp" not in names


def test_claude_code_default_is_http_and_aliases_are_hidden(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "claude-code.mcp.json"
    settings = tmp_path / "claude-settings.json"
    plugin_dir = tmp_path / "claude-plugin"
    plugin_dir.mkdir()
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(settings))
    monkeypatch.setattr(
        claude_code_plugin.ClaudeCodePluginRuntimeInstaller,
        "prepared_plugin_dir",
        lambda _self: plugin_dir,
    )

    names = set(available_linker_names())
    default_result = PowerMemProxyPlug(base_url="http://powermem.local").install(
        linker="claude-code",
    )
    alias_result = PowerMemProxyPlug(base_url="http://powermem.local").install(
        linker="claude-code-http",
        dry_run=True,
    )
    mcp_alias_result = PowerMemProxyPlug(base_url="http://powermem.local").install(
        linker="claude-code-mcp",
        dry_run=True,
    )

    assert "claude-code" in names
    assert "claude-code-mcp" not in names
    assert "claude-code-http" not in names
    assert default_result.changed is True
    assert not default_result.warnings
    settings_payload = json.loads(settings.read_text(encoding="utf-8"))
    assert "POWERMEM_BASE_URL" not in settings_payload["env"]
    assert settings_payload["env"]["POWERMEM_AGENT_ID"] == "claude-code"
    runtime_env = read_env_file(plugin_dir / "config" / "runtime.env")
    assert (
        runtime_env["POWERMEM_BASE_URL"]
        == "http://127.0.0.1:2882/plugins/powermem/default"
    )
    assert runtime_env["POWERMEM_AGENT_ID"] == "claude-code"
    assert not config.exists()
    assert alias_result.changed is False
    assert not alias_result.warnings
    assert any(
        "write Claude Code settings env" in action for action in alias_result.actions
    )
    assert mcp_alias_result.changed is False
    assert mcp_alias_result.warnings == ["unknown linker: claude-code-mcp"]


def test_claude_code_mcp_alias_registers_user_scope_through_claude_cli(
    tmp_path,
    monkeypatch,
) -> None:
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\n", encoding="utf-8")
    claude.chmod(0o755)
    env_file = tmp_path / "powermem.env"
    legacy_config = tmp_path / ".mcp.json"
    settings_config = tmp_path / "settings.json"
    legacy_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "powermem": {
                        "command": "contextseek-pmem-mcp-stdio",
                        "args": [],
                    },
                    "other": {"command": "keep"},
                }
            }
        ),
        encoding="utf-8",
    )
    settings_config.write_text(
        json.dumps(
            {
                "env": {
                    "KEEP_ME": "1",
                    "POWERMEM_BASE_URL": "http://127.0.0.1:2882/plugins/powermem/default",
                    "POWERMEM_AGENT_ID": "claude-code",
                },
            },
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_CONFIG", raising=False)
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(claude))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS",
        str(settings_config),
    )

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(linker_config.subprocess, "run", fake_run)
    linker = create_claude_code_mcp_linker()

    result = linker.configure_proxy(plug_name="powermem")

    assert result.changed is True
    assert calls[0] == [
        str(claude),
        "mcp",
        "remove",
        "--scope",
        "user",
        "powermem",
    ]
    assert calls[1][:5] == [
        str(claude),
        "mcp",
        "add-json",
        "--scope",
        "user",
    ]
    assert calls[1][5] == "powermem"
    payload = json.loads(calls[1][6])
    assert _command_name(payload["command"]) == "contextseek-pmem-mcp-stdio"
    assert payload["env"]["CONTEXTSEEK_POWERMEM_ENV_FILE"] == str(env_file)
    assert payload["env"]["STORAGE_BACKEND"] == "sqlite"
    assert payload["env"]["SQLITE_PATH"].endswith("contextseek.sqlite3")
    assert calls[2] == [str(claude), "mcp", "get", "powermem"]
    assert any(
        "verified Claude Code MCP server: powermem" in action
        for action in result.actions
    )
    legacy_payload = json.loads(legacy_config.read_text(encoding="utf-8"))
    assert "powermem" not in legacy_payload["mcpServers"]
    assert legacy_payload["mcpServers"]["other"]["command"] == "keep"
    settings_payload = json.loads(settings_config.read_text(encoding="utf-8"))
    assert settings_payload["env"] == {"KEEP_ME": "1"}
    assert any(
        "remove Claude Code HTTP hook env" in action for action in result.actions
    )


def test_claude_code_mcp_alias_check_uses_claude_cli_get(
    tmp_path,
    monkeypatch,
) -> None:
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\n", encoding="utf-8")
    claude.chmod(0o755)
    calls: list[list[str]] = []
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", raising=False)
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(claude))
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS",
        str(tmp_path / "claude-settings.json"),
    )
    monkeypatch.setattr(
        linker_config,
        "ensure_managed_powermem_env",
        lambda **_kwargs: linker_config.LinkerResult(
            changed=False,
            dry_run=True,
            actions=["prepare managed PowerMem env"],
            warnings=[],
        ),
    )

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(linker_config.subprocess, "run", fake_run)
    linker = create_claude_code_mcp_linker()

    result = linker.configure_proxy(plug_name="powermem", check=True)

    assert result.changed is False
    assert result.dry_run is True
    assert result.warnings == []
    assert calls == [[str(claude), "mcp", "get", "powermem"]]
    assert any(
        "verified Claude Code MCP server: powermem" in action
        for action in result.actions
    )


def test_proxy_install_openclaw_defaults_to_cli_config(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "openclaw.json"
    powermem_env = tmp_path / "powermem.env"
    real_pmem = tmp_path / "pmem"
    real_pmem.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_openclaw, _log = _fake_openclaw_command(tmp_path, installed=True)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(real_pmem))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(fake_openclaw))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    plugins = payload["plugins"]
    assert plugins["slots"]["memory"] == "memory-powermem"
    memory_entry = plugins["entries"]["memory-powermem"]
    assert memory_entry["enabled"] is True
    memory_config = memory_entry["config"]
    assert memory_config["mode"] == "cli"
    assert _command_name(memory_config["pmemPath"]) == "contextseek-pmem-proxy"
    assert memory_config["envFile"] == str(powermem_env)
    values = read_env_file(powermem_env)
    assert values["CONTEXTSEEK_POWERMEM_CLI"] == str(real_pmem)
    assert values["CONTEXTSEEK_POWERMEM_ENV_FILE"] == str(powermem_env)
    assert values["CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"] == "contextseek"


def test_proxy_install_openclaw_defaults_to_managed_cli(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "openclaw.json"
    powermem_env = tmp_path / "powermem.env"
    runtime_dir = tmp_path / "powermem-runtime"
    fake_openclaw, _log = _fake_openclaw_command(tmp_path, installed=True)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(fake_openclaw))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    assert result.changed is True
    values = read_env_file(powermem_env)
    assert values["CONTEXTSEEK_POWERMEM_CLI"] == _managed_powermem_bin(
        runtime_dir,
        "pmem",
    )


def test_proxy_install_openclaw_installs_missing_powermem_package(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "openclaw.json"
    real_pmem = tmp_path / "pmem"
    real_pmem.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_installer, log = _fake_package_installer(tmp_path)
    fake_openclaw, _openclaw_log = _fake_openclaw_command(tmp_path, installed=True)
    versions = {
        "powermem": iter(["missing", "1.1.1"]),
        "socksio": iter(["missing", "1.0.0"]),
    }

    def _version(name: str) -> str:
        value = next(versions.get(name, iter(["1.0.0"])))
        if value == "missing":
            raise powermem_sdk.importlib_metadata.PackageNotFoundError
        return value

    monkeypatch.setattr(powermem_sdk.importlib_metadata, "version", _version)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(real_pmem))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(fake_openclaw))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY", "current_env")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
        f"{fake_installer} {{requirement}}",
    )
    monkeypatch.setattr(
        plug_runtime.PythonPackageRuntimeInstaller,
        "_runtime_ready",
        lambda self, python: True,
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    assert result.changed is True
    assert "powermem>=1.1.1" in log.read_text(encoding="utf-8")
    assert any(
        "install Python package: powermem>=1.1.1" in action for action in result.actions
    )


def test_proxy_install_openclaw_installs_missing_target_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "openclaw.json"
    real_pmem = tmp_path / "pmem"
    real_pmem.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_openclaw, log = _fake_openclaw_command(tmp_path, installed=False)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(real_pmem))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(fake_openclaw))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    assert result.changed is True
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "plugins list" in lines
    assert "plugins install memory-powermem" in lines
    assert lines.count("plugins list") == 2
    assert "install OpenClaw plugin: memory-powermem" in result.actions
    assert "verified OpenClaw plugin: memory-powermem" in result.actions
    assert not any("OpenClaw" in warning for warning in result.warnings)


def test_proxy_install_openclaw_reuses_installed_target_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "openclaw.json"
    real_pmem = tmp_path / "pmem"
    real_pmem.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_openclaw, log = _fake_openclaw_command(tmp_path, installed=True)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(real_pmem))
    monkeypatch.setenv("CONTEXTSEEK_OPENCLAW_COMMAND", str(fake_openclaw))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert "plugins list" in lines
    assert "plugins install memory-powermem" not in lines
    assert any(
        action == "OpenClaw plugin already installed: memory-powermem"
        for action in result.actions
    )
    assert not any("OpenClaw" in warning for warning in result.warnings)


def test_proxy_install_openclaw_missing_cli_is_fatal(tmp_path, monkeypatch) -> None:
    config = tmp_path / "openclaw.json"
    real_pmem = tmp_path / "pmem"
    real_pmem.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLI", str(real_pmem))
    monkeypatch.setenv(
        "CONTEXTSEEK_OPENCLAW_COMMAND",
        str(tmp_path / "missing-openclaw"),
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="openclaw")

    assert any(
        warning.startswith("OpenClaw CLI cannot be found")
        for warning in result.warnings
    )
    assert _plug_install_status(result.warnings) == 1
    assert _status_from_warnings(result.warnings) == 1


def test_proxy_install_cursor_writes_mcp_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "mcp.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CURSOR_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="cursor")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["powermem"]
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"
    assert server["env"]["CONTEXTSEEK_POWERMEM_ENV_FILE"] == str(powermem_env)
    assert powermem_env.exists()


@pytest.mark.parametrize(
    ("linker", "env_var"),
    [
        ("claude-desktop", "CONTEXTSEEK_POWERMEM_CLAUDE_DESKTOP_MCP_CONFIG"),
        ("qoder", "CONTEXTSEEK_POWERMEM_QODER_MCP_CONFIG"),
        ("cline", "CONTEXTSEEK_POWERMEM_CLINE_MCP_CONFIG"),
    ],
)
def test_proxy_install_generic_mcp_linker_writes_mcp_servers(
    linker,
    env_var,
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / f"{linker}.json"
    monkeypatch.setenv(env_var, str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker=linker)

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["powermem"]
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"
    assert server["env"]["CONTEXTSEEK_POWERMEM_ENV_FILE"].endswith("powermem.env")


def test_proxy_install_claude_code_mcp_prepares_managed_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "claude-code.mcp.json"
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    linker = create_claude_code_mcp_linker()

    result = linker.install(plug_name="powermem", dry_run=True)

    assert result.changed is True
    assert any(
        "would install Python package: powermem>=1.1.1" in action
        for action in result.actions
    )
    assert any(
        "would install Python package: powermem-mcp" in action
        for action in result.actions
    )
    assert any(
        "would install Python package: socksio" in action for action in result.actions
    )
    assert any(
        f"write Claude Code MCP config: {config}" in action for action in result.actions
    )


def test_proxy_install_claude_code_mcp_prepares_explicit_mcp_backend(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "claude-code.mcp.json"
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", "powermem-mcp stdio")
    linker = create_claude_code_mcp_linker()

    result = linker.install(plug_name="powermem", dry_run=True)

    assert not any("powermem-mcp" in action for action in result.actions)
    assert not any("socksio" in action for action in result.actions)


def test_proxy_install_claude_code_mcp_installs_optional_ollama_provider(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "claude-code.mcp.json"
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    linker = create_claude_code_mcp_linker()

    result = linker.install(plug_name="powermem", dry_run=True)

    assert any(
        "would install Python package: ollama" in action for action in result.actions
    )


def test_proxy_install_claude_code_mcp_carries_runtime_env(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "claude-code.mcp.json"
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    linker = create_claude_code_mcp_linker()

    result = linker.install(plug_name="powermem")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server_env = payload["mcpServers"]["powermem"]["env"]
    assert server_env["CONTEXTSEEK_POWERMEM_RUNTIME_DIR"] == str(runtime_dir)
    assert server_env["CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"] == "contextseek"


def test_powermem_mcp_stdio_client_defaults_to_managed_backend(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_LLM_MODEL", "llama3.1")
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)

    client = PowerMemMCPStdioClient()

    assert client.command == [
        _managed_powermem_bin(runtime_dir, "powermem-mcp"),
        "stdio",
    ]


def test_powermem_mcp_runtime_desktop_auto_plans_release_binary_download(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "missing-release-runtime"
    monkeypatch.setenv("CONTEXTSEEK_DESKTOP", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_DIR", str(runtime_dir))
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)

    result = powermem_runtime.PowerMemMCPRuntimeInstaller().install(dry_run=True)
    command = powermem_runtime.PowerMemMCPRuntimeInstaller().backend_command()

    assert result.changed is True
    assert not result.warnings
    assert any(
        "would install PowerMem release binary" in action for action in result.actions
    )
    assert not any("managed Python runtime" in action for action in result.actions)
    assert command == [str(runtime_dir / "bin" / "powermem-mcp"), "stdio"]


def test_powermem_release_binary_runtime_downloads_direct_asset_with_sha256(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "powermem-release"
    platform_id = powermem_runtime._power_mem_platform_id()
    archive = tmp_path / f"powermem-1.2.3-{platform_id}-binaries.tar.gz"
    _write_fake_powermem_binary_archive(
        archive,
        platform_id=platform_id,
        version="1.2.3",
    )
    sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", "release_binary")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_URL", archive.as_uri())
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_SHA256", sha256)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)

    result = powermem_runtime.PowerMemMCPRuntimeInstaller().install()

    manifest = json.loads((runtime_dir / ".installed.json").read_text(encoding="utf-8"))
    assert result.changed is True
    assert not result.warnings
    assert manifest["version"] == "1.2.3"
    assert manifest["platform"] == platform_id
    assert (runtime_dir / "bin" / "powermem").is_file()
    assert (runtime_dir / "bin" / "powermem-server").is_file()
    assert (runtime_dir / "bin" / "powermem-mcp").is_file()
    assert powermem_runtime.PowerMemMCPRuntimeInstaller().backend_command() == [
        str(runtime_dir / "bin" / "powermem-mcp"),
        "stdio",
    ]
    assert powermem_runtime.PowerMemCLIRuntimeInstaller().cli_command() == str(
        runtime_dir / "bin" / "powermem"
    )


def test_powermem_release_binary_runtime_downloads_direct_asset(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "powermem-release"
    platform_id = powermem_runtime._power_mem_platform_id()
    archive = tmp_path / f"powermem-1.2.4-{platform_id}-binaries.tar.gz"
    _write_fake_powermem_binary_archive(
        archive,
        platform_id=platform_id,
        version="1.2.4",
    )
    progress: list[tuple[str, int, int]] = []
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", "release_binary")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_URL", archive.as_uri())
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)

    with powermem_runtime.power_mem_download_progress(
        lambda label, current, total: progress.append((label, current, total))
    ):
        result = powermem_runtime.PowerMemMCPRuntimeInstaller().install()

    manifest = json.loads((runtime_dir / ".installed.json").read_text(encoding="utf-8"))
    assert result.changed is True
    assert not result.warnings
    assert manifest["version"] == "1.2.4"
    assert manifest["asset_url"] == archive.as_uri()
    assert progress
    assert progress[-1][0] == archive.name
    assert progress[-1][1] == archive.stat().st_size


def test_powermem_release_binary_runtime_uses_installed_manifest(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "powermem-release"
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    for executable in ("powermem", "powermem-mcp", "powermem-server"):
        path = bin_dir / executable
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    (runtime_dir / ".installed.json").write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "platform": powermem_runtime._power_mem_platform_id(),
                "executables": {
                    "powermem": "bin/powermem",
                    "powermem-mcp": "bin/powermem-mcp",
                    "powermem-server": "bin/powermem-server",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_MODE", "release_binary")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RELEASE_BINARY_DIR", str(runtime_dir))
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)

    mcp_result = powermem_runtime.PowerMemMCPRuntimeInstaller().install(check=True)

    assert not mcp_result.warnings
    assert any(
        "PowerMem release binary already installed: powermem-mcp=" in action
        for action in mcp_result.actions
    )
    assert powermem_runtime.PowerMemMCPRuntimeInstaller().backend_command() == [
        str(bin_dir / "powermem-mcp"),
        "stdio",
    ]
    assert powermem_runtime.PowerMemCLIRuntimeInstaller().cli_command() == str(
        bin_dir / "powermem"
    )
    assert powermem_runtime.PowerMemHTTPRuntimeInstaller().server_command() == [
        str(bin_dir / "powermem-server")
    ]


def test_powermem_mcp_adapter_defaults_to_stdio_client(monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)
    adapter = PowerMemMCPAdapter()

    assert isinstance(adapter._client(), PowerMemMCPStdioClient)


def test_powermem_mcp_adapter_uses_explicit_backend_command(monkeypatch) -> None:
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", "powermem-mcp stdio")
    adapter = PowerMemMCPAdapter()

    assert isinstance(adapter._client(), PowerMemMCPStdioClient)


def test_proxy_install_claude_code_writes_http_hook_env(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    mcp_config = tmp_path / ".mcp.json"
    plugin_dir = tmp_path / "claude-plugin"
    plugin_dir.mkdir()
    config.write_text(
        json.dumps({"env": {"KEEP_ME": "1"}, "permissions": {"allow": ["Read(*)"]}}),
        encoding="utf-8",
    )
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "powermem": {"command": "contextseek-pmem-mcp-stdio"},
                    "other": {"command": "keep"},
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(mcp_config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setattr(
        claude_code_plugin.ClaudeCodePluginRuntimeInstaller,
        "prepared_plugin_dir",
        lambda _self: plugin_dir,
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_PROXY_URL",
        "http://127.0.0.1:2882/plugins/powermem/default",
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["env"]["KEEP_ME"] == "1"
    assert "POWERMEM_BASE_URL" not in payload["env"]
    assert payload["env"]["POWERMEM_AGENT_ID"] == "claude-code"
    assert payload["permissions"]["allow"] == ["Read(*)"]
    runtime_env = read_env_file(plugin_dir / "config" / "runtime.env")
    assert (
        runtime_env["POWERMEM_BASE_URL"]
        == "http://127.0.0.1:2882/plugins/powermem/default"
    )
    assert runtime_env["POWERMEM_AGENT_ID"] == "claude-code"
    mcp_payload = json.loads(mcp_config.read_text(encoding="utf-8"))
    assert "powermem" not in mcp_payload["mcpServers"]
    assert mcp_payload["mcpServers"]["other"]["command"] == "keep"


def test_proxy_install_claude_code_http_removes_user_mcp_server(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    calls = log.read_text(encoding="utf-8")
    assert result.changed is True
    assert "mcp remove --scope user powermem" in calls
    assert any(
        "removed Claude Code user MCP server: powermem" in action
        for action in result.actions
    )


def test_claude_code_missing_user_mcp_cleanup_is_not_a_warning() -> None:
    assert linker_config._looks_like_missing_mcp_server(
        'No user-scoped MCP server found with name: "powermem"',
    )


def test_proxy_install_claude_code_installs_missing_powermem_package(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_installer, log = _fake_package_installer(tmp_path)
    versions = {
        "powermem": iter(["missing", "1.1.1"]),
        "socksio": iter(["missing", "1.0.0"]),
    }

    def _version(name: str) -> str:
        value = next(versions.get(name, iter(["1.0.0"])))
        if value == "missing":
            raise powermem_sdk.importlib_metadata.PackageNotFoundError
        return value

    monkeypatch.setattr(powermem_sdk.importlib_metadata, "version", _version)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY", "current_env")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_COMMAND",
        f"{fake_installer} {{requirement}}",
    )
    monkeypatch.setattr(
        plug_runtime.PythonPackageRuntimeInstaller,
        "_runtime_ready",
        lambda self, python: True,
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    assert result.changed is True
    assert "powermem[server]>=1.1.1" in log.read_text(encoding="utf-8")
    assert any(
        "install Python package: powermem[server]>=1.1.1" in action
        for action in result.actions
    )
    assert any(
        "verified PowerMem Python package" in action for action in result.actions
    )


def test_proxy_install_claude_code_installs_missing_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL_MODE",
        "marketplace",
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    calls = log.read_text(encoding="utf-8")
    assert result.changed is True
    assert "plugin details memory-powermem" in calls
    assert "plugin install --scope user memory-powermem" in calls
    assert "failed to install" not in "\n".join(result.warnings)


def test_proxy_install_claude_code_dry_run_plans_managed_repo_download(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    managed_repo = tmp_path / "managed" / "powermem"
    fake_claude, log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MANAGED_REPO_DIR",
        str(managed_repo),
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http", dry_run=True)

    assert result.changed is True
    assert result.dry_run is True
    assert not result.warnings
    assert any(
        "would download latest PowerMem release source archive" in action
        for action in result.actions
    )
    assert any("releases/latest" in action for action in result.actions)
    assert any(
        str(managed_repo / "apps" / "claude-code-plugin") in action
        for action in result.actions
    )
    assert not managed_repo.exists()


def test_proxy_install_claude_code_downloads_source_zip_for_managed_plugin_dir(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    managed_repo = tmp_path / "managed" / "powermem"
    source_zip = tmp_path / "powermem-source.zip"
    _write_fake_powermem_source_zip(source_zip)
    stale_plugin_dir = managed_repo / "apps" / "claude-code-plugin"
    stale_plugin_dir.mkdir(parents=True)
    stale_file = stale_plugin_dir / "STALE"
    stale_file.write_text("old", encoding="utf-8")
    fake_claude, log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MANAGED_REPO_DIR",
        str(managed_repo),
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SOURCE_ZIP_URL",
        source_zip.as_uri(),
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    plugin_dir = managed_repo / "apps" / "claude-code-plugin"
    assert result.changed is True
    assert not result.warnings
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert not stale_file.exists()
    assert any(
        "download latest PowerMem release source archive" in action
        for action in result.actions
    )
    assert any(str(plugin_dir) in action for action in result.actions)


def test_proxy_install_claude_code_check_accepts_existing_managed_plugin_dir(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    managed_repo = tmp_path / "managed" / "powermem"
    source_zip = tmp_path / "powermem-source.zip"
    _write_fake_powermem_source_zip(source_zip)
    fake_claude, _log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MANAGED_REPO_DIR",
        str(managed_repo),
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SOURCE_ZIP_URL",
        source_zip.as_uri(),
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    install_result = plug.install(linker="claude-code-http")
    check_result = plug.install(linker="claude-code-http", check=True)

    assert install_result.changed is True
    assert check_result.changed is False
    assert check_result.dry_run is True
    assert not check_result.warnings
    assert not any("would download latest" in action for action in check_result.actions)
    assert any(
        "verified Claude Code plugin dir" in action for action in check_result.actions
    )


def test_proxy_install_claude_code_extracts_runtime_plugin_zip(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    plugin_zip = tmp_path / "powermem-claude-code-plugin.zip"
    _write_fake_powermem_plugin_zip(plugin_zip, version="0.2.0")
    fake_claude, _log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP",
        str(plugin_zip),
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    plugin_dir = (
        tmp_path
        / ".contextseek"
        / "plugs"
        / "powermem"
        / "claude-code-plugin"
        / "0.2.0"
    )
    assert result.changed is True
    assert not result.warnings
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert claude_code_plugin._hook_binary(plugin_dir).is_file()
    assert any("extract Claude Code plugin zip" in action for action in result.actions)
    assert any(
        "verified Claude Code plugin dir (0.2.0)" in action for action in result.actions
    )


def test_proxy_install_claude_code_defaults_to_oss_plugin_zip(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, _log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.delenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP_URL",
        raising=False,
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http", dry_run=True)

    assert result.changed is True
    assert not result.warnings
    assert any(
        "obbusiness-private.oss-cn-shanghai.aliyuncs.com" in action
        for action in result.actions
    )


def test_proxy_install_claude_code_downloads_release_plugin_zip(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    plugin_zip = tmp_path / "powermem-claude-code-plugin-0.4.0.zip"
    _write_fake_powermem_plugin_zip(plugin_zip, version="0.4.0")
    sha256 = hashlib.sha256(plugin_zip.read_bytes()).hexdigest()
    fake_claude, log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP_URL",
        plugin_zip.as_uri(),
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP_SHA256", sha256)
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")
    progress: list[tuple[str, int, int]] = []

    with powermem_runtime.power_mem_download_progress(
        lambda label, current, total: progress.append((label, current, total))
    ):
        result = plug.install(linker="claude-code-http")

    plugin_dir = (
        tmp_path
        / ".contextseek"
        / "plugs"
        / "powermem"
        / "claude-code-plugin"
        / "release"
    )
    assert result.changed is True
    assert not result.warnings
    assert progress
    assert progress[-1][0] == plugin_zip.name
    assert progress[-1][1] == plugin_zip.stat().st_size
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert claude_code_plugin._hook_binary(plugin_dir).is_file()
    assert any(
        "download PowerMem Claude Code plugin zip" in action
        for action in result.actions
    )
    assert any(
        "verified Claude Code plugin dir (0.4.0)" in action for action in result.actions
    )
    calls = log.read_text(encoding="utf-8")
    assert "plugin marketplace add" in calls
    assert str(plugin_dir) in calls
    assert "plugin install --scope user memory-powermem" in calls


def test_proxy_install_claude_code_plugin_zip_requires_hook_binary(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    plugin_zip = tmp_path / "powermem-claude-code-plugin.zip"
    _write_fake_powermem_plugin_zip(
        plugin_zip,
        version="0.2.1",
        include_binary=False,
    )
    fake_claude, _log = _fake_claude_command(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP",
        str(plugin_zip),
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    assert any(
        warning.startswith("Claude Code plugin hook binary is missing")
        for warning in result.warnings
    )
    assert _plug_install_status(result.warnings) == 1


def test_proxy_install_claude_code_reuses_installed_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, log = _fake_claude_command(tmp_path, installed=True)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL_MODE",
        "marketplace",
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    calls = log.read_text(encoding="utf-8")
    assert result.changed is True
    assert "plugin details memory-powermem" in calls
    assert "plugin install --scope user memory-powermem" not in calls
    assert any("already installed" in action for action in result.actions)


def test_proxy_install_claude_code_enables_disabled_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, log = _fake_claude_command(
        tmp_path,
        installed=True,
        disabled=True,
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL_MODE",
        "marketplace",
    )
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    calls = log.read_text(encoding="utf-8")
    assert result.changed is True
    assert "plugin enable --scope user memory-powermem" in calls
    assert "failed to enable" not in "\n".join(result.warnings)


def test_proxy_install_creates_managed_powermem_env_from_contextseek_env(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "contextseek.sqlite3"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "llm-key")
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert result.changed is True
    assert values["DATABASE_PROVIDER"] == "sqlite"
    assert values["SQLITE_PATH"] == str(tmp_path / "contextseek.sqlite3")
    assert values["EMBEDDING_PROVIDER"] == "openai"
    assert values["EMBEDDING_API_KEY"] == "embedding-key"
    assert values["OPENAI_EMBEDDING_BASE_URL"] == "https://embedding.example/v1"
    assert values["LLM_PROVIDER"] == "qwen"
    assert values["LLM_API_KEY"] == "llm-key"
    assert values["INTELLIGENT_MEMORY_FALLBACK_TO_SIMPLE_ADD"] == "true"


def test_proxy_install_maps_contextseek_seekdb_to_powermem_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("STORAGE_BACKEND", "seekdb")
    monkeypatch.setenv("SEEKDB_PATH", str(tmp_path / "contextseek.seekdb"))
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "contextseek.sqlite3"))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert result.changed is True
    assert values["DATABASE_PROVIDER"] == "sqlite"
    assert values["SQLITE_PATH"] == str(
        Path("~/.contextseek/plugs/powermem.sqlite3").expanduser(),
    )
    assert "OCEANBASE_HOST" not in values


def test_proxy_install_infers_langchain_openai_kwargs(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "langchain")
    monkeypatch.setenv("EMBEDDING_CLASS_PATH", "langchain_openai.OpenAIEmbeddings")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("EMBEDDING_KWARGS", '{"api_key": "embedding-kwarg-key"}')
    monkeypatch.setenv("LLM_PROVIDER", "langchain")
    monkeypatch.setenv("LLM_CLASS_PATH", "langchain_openai.ChatOpenAI")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("LLM_KWARGS", '{"api_key": "llm-kwarg-key"}')
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert values["EMBEDDING_PROVIDER"] == "openai"
    assert values["EMBEDDING_API_KEY"] == "embedding-kwarg-key"
    assert values["LLM_PROVIDER"] == "openai"
    assert values["LLM_API_KEY"] == "llm-kwarg-key"
    assert not result.warnings


def test_proxy_install_infers_langchain_qwen_from_model_and_dashscope_key(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(tmp_path / "contextseek.env"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("LLM_PROVIDER", "langchain")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.delenv("LLM_CLASS_PATH", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert values["LLM_PROVIDER"] == "qwen"
    assert values["LLM_MODEL"] == "qwen-plus"
    assert values["LLM_API_KEY"] == "dashscope-key"
    assert not any("PowerMem LLM_PROVIDER" in warning for warning in result.warnings)


def test_proxy_install_infers_langchain_openai_embedding_from_model(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(tmp_path / "contextseek.env"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "langchain")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("EMBEDDING_CLASS_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert values["EMBEDDING_PROVIDER"] == "openai"
    assert values["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert values["EMBEDDING_DIMS"] == "1536"
    assert values["EMBEDDING_API_KEY"] == "openai-key"
    assert not any(
        "PowerMem EMBEDDING_PROVIDER" in warning for warning in result.warnings
    )


def test_proxy_install_does_not_guess_ambiguous_langchain_provider(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_CONFIG", str(tmp_path / "contextseek.env"))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("LLM_PROVIDER", "langchain")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "langchain")
    monkeypatch.setenv("EMBEDDING_MODEL", "custom-embedding")
    monkeypatch.delenv("LLM_CLASS_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_CLASS_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert "LLM_PROVIDER" not in values
    assert "EMBEDDING_PROVIDER" not in values
    assert "PowerMem LLM_PROVIDER cannot be inferred" in result.warnings
    assert "PowerMem EMBEDDING_PROVIDER cannot be inferred" in result.warnings


def test_proxy_install_preserves_existing_managed_powermem_env_values(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text("EMBEDDING_API_KEY=custom-key\n", encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "new-key")
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert values["EMBEDDING_API_KEY"] == "custom-key"


def test_proxy_install_upgrades_placeholder_embedding_provider(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "\n".join(
            [
                "EMBEDDING_PROVIDER=mock",
                "EMBEDDING_MODEL=mock",
                "EMBEDDING_DIMS=384",
                "EMBEDDING_API_KEY=custom-key",
                "",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("EMBEDDING_API_KEY", "new-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1")
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    text = powermem_env.read_text(encoding="utf-8")
    values = read_env_file(powermem_env)
    assert result.changed is True
    assert values["EMBEDDING_PROVIDER"] == "openai"
    assert values["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert values["EMBEDDING_DIMS"] == "1536"
    assert values["EMBEDDING_API_KEY"] == "custom-key"
    assert values["OPENAI_EMBEDDING_BASE_URL"] == "https://embedding.example/v1"
    assert text.count("EMBEDDING_PROVIDER=") == 1


def test_powermem_child_process_env_isolates_project_provider_env(
    tmp_path,
    monkeypatch,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "DATABASE_PROVIDER=sqlite\nSQLITE_PATH=/tmp/powermem.sqlite3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_ENV_FILE", str(powermem_env))

    env = powermem_child_process_env(
        {
            "KEEP_ME": "1",
            "LLM_PROVIDER": "langchain",
            "LLM_MODEL": "qwen-plus",
            "EMBEDDING_PROVIDER": "langchain",
            "EMBEDDING_MODEL": "text-embedding-3-small",
            "all_proxy": "socks5://127.0.0.1:13659",
            "ALL_PROXY": "socks5h://127.0.0.1:13659",
            "https_proxy": "http://127.0.0.1:13659",
        }
    )

    assert env["KEEP_ME"] == "1"
    assert env["DATABASE_PROVIDER"] == "sqlite"
    assert env["SQLITE_PATH"] == "/tmp/powermem.sqlite3"
    assert env["POWERMEM_ENV_FILE"] == str(powermem_env)
    assert "LLM_PROVIDER" not in env
    assert "LLM_MODEL" not in env
    assert "EMBEDDING_PROVIDER" not in env
    assert "EMBEDDING_MODEL" not in env
    assert "all_proxy" not in env
    assert "ALL_PROXY" not in env
    assert env["https_proxy"] == "http://127.0.0.1:13659"
    assert powermem_child_process_cwd() == tmp_path


def test_proxy_install_leaves_contextseek_none_embedding_to_powermem_default(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")
    monkeypatch.setenv("EMBEDDING_DIMS", "384")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert "EMBEDDING_PROVIDER" not in values
    assert "EMBEDDING_DIMS" not in values
    assert "LLM_PROVIDER" not in values
    assert "LLM_MODEL" not in values
    assert not result.warnings
    assert _plug_install_status(result.warnings) == 0


def test_proxy_install_ignores_prefixed_powermem_llm_override(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    powermem_env = tmp_path / "powermem.env"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_LLM_MODEL", "llama3.1")
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    values = read_env_file(powermem_env)
    assert "LLM_PROVIDER" not in values
    assert "LLM_MODEL" not in values
    assert not any("PowerMem LLM" in warning for warning in result.warnings)


def _write_fake_powermem_source_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        prefix = "powermem-main/apps/claude-code-plugin"
        archive.writestr(
            f"{prefix}/.claude-plugin/plugin.json",
            json.dumps({"name": "memory-powermem", "version": "0.1.0"}),
        )
        archive.writestr(f"{prefix}/hooks/run-hook.sh", "#!/bin/sh\n")
        archive.writestr(f"{prefix}/hooks/run-hook.ps1", "")


def _write_fake_powermem_plugin_zip(
    path: Path,
    *,
    version: str = "0.1.0",
    include_binary: bool = True,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        prefix = "powermem-claude-code-plugin"
        archive.writestr(
            f"{prefix}/.claude-plugin/plugin.json",
            json.dumps({"name": "memory-powermem", "version": version}),
        )
        archive.writestr(f"{prefix}/.mcp.json", "{}")
        archive.writestr(f"{prefix}/hooks/run-hook.sh", "#!/bin/sh\n")
        archive.writestr(f"{prefix}/hooks/run-hook.ps1", "")
        archive.writestr(f"{prefix}/skills/README.md", "PowerMem")
        if include_binary:
            hook_binary = claude_code_plugin._hook_binary(Path(prefix))
            archive.writestr(hook_binary.as_posix(), "#!/bin/sh\n")


def _write_fake_powermem_binary_archive(
    path: Path,
    *,
    platform_id: str | None,
    version: str,
) -> None:
    assert platform_id is not None
    suffix = ".exe" if platform_id.startswith("windows-") else ""
    package_root = path.parent / f"powermem-{version}-{platform_id}"
    bin_dir = package_root / "bin"
    bin_dir.mkdir(parents=True)
    for executable in ("powermem", "powermem-server", "powermem-mcp"):
        binary = bin_dir / f"{executable}{suffix}"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(package_root, arcname=package_root.name)


def _fake_claude_command(
    tmp_path: Path,
    *,
    installed: bool = False,
    disabled: bool = False,
) -> tuple[Path, Path]:
    script = tmp_path / "claude"
    log = tmp_path / "claude.log"
    state = tmp_path / "installed"
    disabled_flag = tmp_path / "disabled"
    if installed:
        state.write_text("1", encoding="utf-8")
    if disabled:
        disabled_flag.write_text("1", encoding="utf-8")
    script.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"LOG={shlex.quote(str(log))}",
                f"STATE={shlex.quote(str(state))}",
                f"DISABLED={shlex.quote(str(disabled_flag))}",
                'echo "$*" >> "$LOG"',
                'if [ "$1" = "plugin" ] && [ "$2" = "details" ]; then',
                '  [ -f "$STATE" ] && exit 0',
                '  echo "Plugin not found" >&2',
                "  exit 1",
                "fi",
                'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then',
                '  if [ -f "$STATE" ]; then',
                '    if [ -f "$DISABLED" ]; then echo "memory-powermem disabled"; else echo "memory-powermem enabled"; fi',
                "  fi",
                "  exit 0",
                "fi",
                'if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then',
                '  touch "$STATE"',
                "  exit 0",
                "fi",
                'if [ "$1" = "plugin" ] && [ "$2" = "enable" ]; then',
                '  rm -f "$DISABLED"',
                "  exit 0",
                "fi",
                "exit 0",
                "",
            ],
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def _fake_openclaw_command(
    tmp_path: Path,
    *,
    installed: bool = False,
    fail_install: bool = False,
    fail_verify: bool = False,
) -> tuple[Path, Path]:
    script = tmp_path / "openclaw"
    log = tmp_path / "openclaw.log"
    state = tmp_path / "openclaw-plugin-installed"
    if installed:
        state.write_text("1", encoding="utf-8")
    script.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"LOG={shlex.quote(str(log))}",
                f"STATE={shlex.quote(str(state))}",
                f"FAIL_INSTALL={'1' if fail_install else '0'}",
                f"FAIL_VERIFY={'1' if fail_verify else '0'}",
                'echo "$*" >> "$LOG"',
                'if [ "$1" = "plugins" ] && [ "$2" = "list" ]; then',
                '  if [ -f "$STATE" ] && [ "$FAIL_VERIFY" != "1" ]; then',
                '    echo "memory-powermem enabled"',
                "  fi",
                "  exit 0",
                "fi",
                'if [ "$1" = "plugins" ] && [ "$2" = "install" ]; then',
                '  if [ "$FAIL_INSTALL" = "1" ]; then',
                '    echo "install failed" >&2',
                "    exit 1",
                "  fi",
                '  touch "$STATE"',
                "  exit 0",
                "fi",
                "exit 0",
                "",
            ],
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def _fake_package_installer(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "install-powermem"
    log = tmp_path / "install-powermem.log"
    script.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"LOG={shlex.quote(str(log))}",
                'echo "$*" >> "$LOG"',
                "exit 0",
                "",
            ],
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def test_powermem_child_env_lets_managed_file_override_colliding_keys(
    tmp_path,
) -> None:
    powermem_env = tmp_path / "powermem.env"
    powermem_env.write_text(
        "EMBEDDING_PROVIDER=openai\nSQLITE_PATH=/tmp/powermem.sqlite3\n",
        encoding="utf-8",
    )

    child_env = powermem_child_process_env(
        {
            "CONTEXTSEEK_POWERMEM_ENV_FILE": str(powermem_env),
            "EMBEDDING_PROVIDER": "dashscope",
            "SQLITE_PATH": "/tmp/contextseek.sqlite3",
            "KEEP_ME": "1",
        },
    )

    assert child_env["POWERMEM_ENV_FILE"] == str(powermem_env)
    assert child_env["EMBEDDING_PROVIDER"] == "openai"
    assert child_env["SQLITE_PATH"] == "/tmp/powermem.sqlite3"
    assert child_env["KEEP_ME"] == "1"


def test_powermem_serve_plan_defaults(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))

    plan = build_powermem_serve_plan(
        Namespace(
            host="127.0.0.1",
            port=2882,
            powermem_host="127.0.0.1",
            powermem_port=8000,
            powermem_command=None,
            powermem_upstream_base_url=None,
            proxy_base_url=None,
            scope="powermem/claude-code",
            linker="claude-code",
            no_install=False,
        )
    )

    assert plan.proxy_base_url == "http://127.0.0.1:2882/plugins/powermem/default"
    assert plan.upstream_base_url == "http://127.0.0.1:8000"
    assert plan.powermem_command == [
        _managed_powermem_bin(runtime_dir, "powermem-server"),
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    assert plan.default_scope == "powermem/claude-code"


def test_powermem_serve_plan_defaults_scope_to_contextseek() -> None:
    plan = build_powermem_serve_plan(
        Namespace(
            host="127.0.0.1",
            port=2882,
            powermem_host="127.0.0.1",
            powermem_port=8000,
            powermem_command=None,
            powermem_upstream_base_url=None,
            proxy_base_url=None,
            scope=None,
            linker="claude-code",
            no_install=False,
        )
    )

    assert plan.default_scope == "contextseek"


def test_plug_serve_dry_run_outputs_plan(capsys, tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")

    code = run_cli(
        [
            "plug-serve",
            "powermem",
            "--linker",
            "claude-code",
            "--no-install",
            "--dry-run",
            "--port",
            "2999",
            "--powermem-port",
            "8999",
            "--scope",
            "powermem/test",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["proxy_base_url"] == (
        "http://127.0.0.1:2999/plugins/powermem/default"
    )
    assert payload["upstream_base_url"] == "http://127.0.0.1:8999"
    assert payload["default_scope"] == "powermem/test"
    assert payload["powermem_command"] == [
        _managed_powermem_bin(runtime_dir, "powermem-server"),
        "--host",
        "127.0.0.1",
        "--port",
        "8999",
    ]


def test_plug_serve_accepts_claude_code_linker(
    capsys,
    monkeypatch,
) -> None:
    code = run_cli(
        [
            "plug-serve",
            "powermem",
            "--linker",
            "claude-code",
            "--no-install",
            "--dry-run",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"] == []
    assert payload["proxy_base_url"].endswith("/plugins/powermem/default")


def test_plug_run_dry_run_outputs_claude_code_plan(
    capsys,
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "powermem-runtime"
    plugin_zip = tmp_path / "powermem-claude-code-plugin.zip"
    fake_claude, _log = _fake_claude_command(tmp_path)
    _write_fake_powermem_plugin_zip(plugin_zip, version="0.3.0")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_ZIP",
        str(plugin_zip),
    )

    code = run_cli(
        [
            "plug-run",
            "powermem",
            "--linker",
            "claude-code",
            "--dry-run",
            "--port",
            "2997",
            "--powermem-port",
            "8997",
            "--claude-args=--print",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    plugin_dir = (
        tmp_path
        / ".contextseek"
        / "plugs"
        / "powermem"
        / "claude-code-plugin"
        / "0.3.0"
    )
    assert payload["linker"] == "claude-code"
    assert payload["plugin_dir"] == str(plugin_dir)
    assert payload["target_command"] == [
        str(fake_claude),
        "--plugin-dir",
        str(plugin_dir),
        "--print",
    ]
    assert payload["target_env"] == {
        "POWERMEM_BASE_URL": "http://127.0.0.1:2997/plugins/powermem/default",
        "POWERMEM_AGENT_ID": "claude-code",
    }
    assert "plug-serve" in payload["serve_command"]
    assert "claude-code" in payload["serve_command"]
    assert "claude-code-http" not in payload["serve_command"]
    assert any(
        "would extract Claude Code plugin zip" in action
        for action in payload["actions"]
    )


def test_plug_serve_dry_run_without_linker_would_install_powermem_package(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    def _missing(_name: str) -> str:
        raise powermem_sdk.importlib_metadata.PackageNotFoundError

    runtime_dir = tmp_path / "powermem-runtime"
    monkeypatch.setattr(powermem_sdk.importlib_metadata, "version", _missing)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")

    code = run_cli(
        [
            "plug-serve",
            "powermem",
            "--dry-run",
            "--port",
            "2998",
            "--powermem-port",
            "8998",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(
        "would create managed Python runtime venv" in action
        for action in payload["actions"]
    )
    assert any(
        "would install Python package: powermem[server]>=1.1.1" in action
        for action in payload["actions"]
    )


def test_proxy_install_qoder_requires_explicit_config_path(monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_QODER_MCP_CONFIG", raising=False)
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="qoder")

    assert result.changed is False
    assert result.warnings
    assert "not verified" in result.warnings[0]


def test_claude_desktop_default_path_is_platform_specific(monkeypatch) -> None:
    from contextseek.plugs.powermem.linkers import claude_desktop

    monkeypatch.setattr(claude_desktop.platform, "system", lambda: "Darwin")
    assert str(claude_desktop._default_config_path()).endswith(
        "Library/Application Support/Claude/claude_desktop_config.json",
    )

    monkeypatch.setattr(claude_desktop.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", "C:/Users/test/AppData/Roaming")
    assert str(claude_desktop._default_config_path()).endswith(
        "AppData/Roaming/Claude/claude_desktop_config.json",
    )


def test_proxy_install_opencode_writes_opencode_mcp_schema(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "opencode.json"
    config.write_text(
        """
{
  // keep user config
  "theme": "system"
}
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_OPENCODE_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="opencode")

    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    assert "// keep user config" in text
    assert '"theme": "system"' in text
    assert '"mcp"' in text
    payload = json.loads(text.replace("// keep user config", ""))
    server = payload["mcp"]["powermem"]
    assert server["type"] == "local"
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"
    assert server["environment"]["CONTEXTSEEK_POWERMEM_ENV_FILE"].endswith(
        "powermem.env",
    )


def test_proxy_install_codex_writes_config_toml(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="codex")

    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    assert 'model = "gpt-5"' in text
    assert "[mcp_servers.powermem]" in text
    assert "contextseek-pmem-mcp-stdio" in text
    assert "args = []" in text
    assert "CONTEXTSEEK_POWERMEM_ENV_FILE" in text
    assert "powermem.env" in text
    assert "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL" not in text


def test_proxy_install_codex_replaces_existing_mcp_server(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                'model = "gpt-5"',
                "",
                "[mcp_servers.powermem]",
                'command = "old"',
                'env = { OLD = "1" }',
                "",
                "[mcp_servers.other]",
                'command = "keep"',
                "",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="codex")

    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    assert 'model = "gpt-5"' in text
    assert "[mcp_servers.powermem]" in text
    assert "contextseek-pmem-mcp-stdio" in text
    assert "OLD" not in text
    assert "[mcp_servers.other]" in text
    assert 'command = "keep"' in text
    assert "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL" not in text


def test_proxy_install_codex_check_accepts_existing_proxy_with_env_drift(
    tmp_path,
    monkeypatch,
) -> None:
    from contextseek.plugs.powermem.linkers.codex import create_linker

    config = tmp_path / "config.toml"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG", str(config))
    linker = create_linker()

    install_result = linker.configure_proxy(plug_name="powermem")
    monkeypatch.setenv("CONTEXTSEEK_DESKTOP", "1")
    check_result = linker.configure_proxy(plug_name="powermem", check=True)

    assert install_result.changed is True
    assert "CONTEXTSEEK_DESKTOP" not in config.read_text(encoding="utf-8")
    assert check_result.changed is False
    assert check_result.dry_run is True
    assert not check_result.warnings


def test_proxy_install_windsurf_writes_context_provider_config(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "powermem.json"
    config.write_text(json.dumps({"theme": "system"}), encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_WINDSURF_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="windsurf")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["theme"] == "system"
    assert payload["contextProvider"] == "powermem-mcp"
    assert _command_name(payload["mcp"]["configPath"]) == "contextseek-pmem-mcp-stdio"


def test_proxy_install_copilot_writes_vscode_mcp_config(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "mcp.json"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_COPILOT_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="copilot")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["servers"]["powermem"]
    assert server["type"] == "stdio"
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"


def test_proxy_install_copilot_preserves_vscode_jsonc_comments(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        """
{
  // existing user comment
  "servers": {
    "existing": {
      "type": "stdio",
      "command": "existing-command",
    },
  },
}
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_COPILOT_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="copilot")

    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    assert "// existing user comment" in text
    assert '"existing"' in text
    assert '"powermem"' in text
    assert "contextseek-pmem-mcp-stdio" in text


def test_proxy_install_vscode_invalid_jsonc_does_not_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "mcp.json"
    original = '{\n  "servers": {\n'
    config.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_VSCODE_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="vscode")

    assert result.changed is True
    assert result.warnings
    assert config.read_text(encoding="utf-8") == original


def test_proxy_install_vscode_writes_vscode_mcp_config(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "mcp.json"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_VSCODE_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="vscode")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["servers"]["powermem"]
    assert server["type"] == "stdio"
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"
