"""Unit tests for PowerMemPlug."""

import json
import os
import shlex
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
    PowerMemSDKSubprocessClient,
    create_powermem_mcp_proxy,
)
from contextseek.plugs.powermem.env import powermem_child_process_env, read_env_file
from contextseek.plugs.powermem.serve import (
    _status_from_warnings,
    build_powermem_serve_plan,
)
import contextseek.plugs.powermem.sdk as powermem_sdk
from contextseek.plugs.powermem.sdk import Memory as PowerMemMemoryProxy
from contextseek.plugs.powermem.linkers import available_linker_names
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
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_ENV_FILE",
        str(tmp_path / "powermem.env"),
    )
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG",
        str(tmp_path / "claude-code.mcp.json"),
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


def test_powermem_mcp_proxy_forwards_search_without_materializing(tmp_path) -> None:
    from tests.unit_tests.test_pluggateway import _contextseek

    ctx, _backend = _contextseek(tmp_path)
    mcp_client = _FakeMCPClient(
        [{"results": [{"id": "mcp-1", "memory": "search hit"}]}]
    )
    proxy = PlugMCPProxy(
        client=ctx,
        adapter=PowerMemMCPAdapter(instance_id="i1", mcp_client=mcp_client),
    )

    result = proxy.call_tool(
        "search_memories",
        {"query": "search", "scope": "tenant/agent/user"},
    )

    assert mcp_client.calls == [("search_memories", {"query": "search"})]
    assert _mcp_structured(result)["results"][0]["memory"] == "search hit"
    assert _mcp_structured(result)["_contextseek"]["status"] == "no_events"
    assert not ctx.retrieve("search", scope="tenant/agent/user", k=3).items


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
        "claude-code-mcp",
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


def test_claude_code_prefers_mcp_and_http_is_disabled_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_ENABLED", raising=False)
    config = tmp_path / "claude-code.mcp.json"
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG", str(config))

    names = set(available_linker_names())
    mcp_result = PowerMemProxyPlug(base_url="http://powermem.local").install(
        linker="claude-code",
    )
    http_result = PowerMemProxyPlug(base_url="http://powermem.local").install(
        linker="claude-code-http",
    )

    assert "claude-code" in names
    assert "claude-code-mcp" in names
    assert "claude-code-http" not in names
    assert mcp_result.changed is True
    assert (
        _command_name(
            json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["powermem"][
                "command"
            ]
        )
        == "contextseek-pmem-mcp-stdio"
    )
    assert http_result.changed is False
    assert http_result.warnings == [
        "disabled linker: claude-code-http "
        "(Claude Code HTTP hook channel requires the official PowerMem Claude Code "
        "plugin binary package; use claude-code for MCP mode)",
    ]


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
        ("claude-code-mcp", "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG"),
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
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code", dry_run=True)

    assert result.changed is True
    assert any(
        "would install Python package: powermem>=1.1.1" in action
        for action in result.actions
    )
    assert not any(
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
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code", dry_run=True)

    assert any(
        "would install Python package: powermem-mcp" in action
        for action in result.actions
    )
    assert any(
        "would install Python package: socksio" in action for action in result.actions
    )


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
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code", dry_run=True)

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
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    server_env = payload["mcpServers"]["powermem"]["env"]
    assert server_env["CONTEXTSEEK_POWERMEM_RUNTIME_DIR"] == str(runtime_dir)


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


def test_powermem_mcp_adapter_defaults_to_sdk_subprocess(monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_MCP_BACKEND_COMMAND", raising=False)
    adapter = PowerMemMCPAdapter()

    assert isinstance(adapter._client(), PowerMemSDKSubprocessClient)


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
    monkeypatch.setenv(
        "CONTEXTSEEK_POWERMEM_PROXY_URL",
        "http://127.0.0.1:2882/plugins/powermem/default",
    )
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["env"]["KEEP_ME"] == "1"
    assert (
        payload["env"]["POWERMEM_BASE_URL"]
        == "http://127.0.0.1:2882/plugins/powermem/default"
    )
    assert payload["env"]["POWERMEM_AGENT_ID"] == "claude-code"
    assert payload["permissions"]["allow"] == ["Read(*)"]
    mcp_payload = json.loads(mcp_config.read_text(encoding="utf-8"))
    assert "powermem" not in mcp_payload["mcpServers"]
    assert mcp_payload["mcpServers"]["other"]["command"] == "keep"


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
    monkeypatch.setenv("CONTEXTSEEK_CLAUDE_CODE_COMMAND", str(fake_claude))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="claude-code-http")

    calls = log.read_text(encoding="utf-8")
    assert result.changed is True
    assert "plugin details memory-powermem" in calls
    assert "plugin install --scope user memory-powermem" in calls
    assert "failed to install" not in "\n".join(result.warnings)


def test_proxy_install_claude_code_reuses_installed_plugin(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "settings.json"
    fake_claude, log = _fake_claude_command(tmp_path, installed=True)
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS", str(config))
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", "1")
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_PLUGIN_INSTALL", "1")
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


def test_proxy_install_maps_contextseek_none_embedding_to_powermem_mock(
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
    assert values["EMBEDDING_PROVIDER"] == "mock"
    assert values["EMBEDDING_DIMS"] == "384"
    assert "LLM_PROVIDER" not in values
    assert not any("EMBEDDING" in warning for warning in result.warnings)
    assert "PowerMem LLM_PROVIDER cannot be inferred" in result.warnings
    assert _plug_install_status(result.warnings) == 1


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
    assert "PowerMem LLM_PROVIDER cannot be inferred" in result.warnings


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


def test_powermem_serve_plan_defaults_scope_from_linker() -> None:
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

    assert plan.default_scope == "powermem/claude-code"


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


def test_plug_serve_blocks_disabled_claude_code_http_linker(
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED", raising=False)
    monkeypatch.delenv("CONTEXTSEEK_POWERMEM_CLAUDE_CODE_ENABLED", raising=False)

    code = run_cli(
        [
            "plug-serve",
            "powermem",
            "--linker",
            "claude-code-http",
            "--no-install",
            "--dry-run",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"] == [
        "disabled linker: claude-code-http "
        "(Claude Code HTTP hook channel requires the official PowerMem Claude Code "
        "plugin binary package; use claude-code for MCP mode)",
    ]


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


def test_proxy_install_codex_writes_context_json(tmp_path, monkeypatch) -> None:
    config = tmp_path / "context.json"
    config.write_text(
        json.dumps({"contextProviders": {"keep": {"enabled": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="codex")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["contextProviders"]["keep"]["enabled"] is True
    server = payload["mcpServers"]["powermem"]
    assert _command_name(server["command"]) == "contextseek-pmem-mcp-stdio"
    assert server["env"]["CONTEXTSEEK_POWERMEM_ENV_FILE"].endswith("powermem.env")
    assert "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL" not in json.dumps(payload)


def test_proxy_install_codex_replaces_existing_mcp_server(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "context.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "powermem": {"command": "old", "env": {"OLD": "1"}},
                    "other": {"command": "keep"},
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG", str(config))
    plug = PowerMemProxyPlug(base_url="http://powermem.local")

    result = plug.install(linker="codex")

    assert result.changed is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert (
        _command_name(payload["mcpServers"]["powermem"]["command"])
        == "contextseek-pmem-mcp-stdio"
    )
    assert "OLD" not in payload["mcpServers"]["powermem"]["env"]
    assert payload["mcpServers"]["other"]["command"] == "keep"
    assert "CONTEXTSEEK_POWERMEM_UPSTREAM_BASE_URL" not in json.dumps(payload)


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
