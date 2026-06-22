"""PowerMem CLI proxy entry point."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any

from contextseek.plugs.core.proxy.cli import (
    emit_cli_result,
    materialize_cli_events,
    resolve_executable,
    run_cli,
    warning,
)
from contextseek.plugs.powermem.adapter import MEMORIES_PATH, PowerMemAdapter
from contextseek.plugs.powermem.env import powermem_child_process_env
from contextseek.plugs.core.protocols import PlugChangeEvent, PlugProxyRequest


_WRITE_COMMANDS = {"add", "create", "update", "delete", "remove"}
_DELETE_COMMANDS = {"delete", "remove"}
_UPDATE_COMMANDS = {"update"}
_READ_COMMANDS = {"search", "get", "list", "show", "health"}
_OPTIONS_WITH_VALUES = {
    "-a",
    "-f",
    "-m",
    "-r",
    "-u",
    "--agent-id",
    "--content",
    "--env-file",
    "--filters",
    "--id",
    "--limit",
    "--memory",
    "--memory-id",
    "--memory-type",
    "--metadata",
    "--run-id",
    "--scope",
    "--threshold",
    "--user-id",
}


@dataclass
class PowerMemCLIAdapter(PowerMemAdapter):
    """PowerMem CLI request/response interpreter."""

    def events_from_cli_success(
        self,
        argv: list[str],
        stdout: str,
    ) -> list[PlugChangeEvent]:
        command = _command_from_argv(argv)
        if command is None or command in _READ_COMMANDS:
            return []
        if command not in _WRITE_COMMANDS:
            return []
        body = _request_body_from_argv(argv)
        response_body = _decode_json(stdout)
        request = PlugProxyRequest(
            method=_method_for_command(command),
            path=_path_for_command(command, body),
            body=body,
            headers={},
            query={},
        )
        events = self.events_from_write_response(response_body, request)
        if events:
            return events
        return self._fallback_events(command, body, response_body, request)

    def _fallback_events(
        self,
        command: str,
        body: dict[str, Any],
        response_body: Any,
        request: PlugProxyRequest,
    ) -> list[PlugChangeEvent]:
        if command in _DELETE_COMMANDS:
            return self.events_from_delete_response(response_body, request)
        content = body.get("memory") or body.get("content")
        if not content:
            return []
        event_name = "UPDATE" if command in _UPDATE_COMMANDS else "ADD"
        record = {
            "id": body.get("id") or body.get("memory_id") or _stable_cli_id(content),
            "memory": content,
            "event": event_name,
        }
        response = {"results": [record]}
        return self.events_from_write_response(response, request)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    executable = resolve_executable(
        env_names=["CONTEXTSEEK_POWERMEM_CLI", "CONTEXTSEEK_REAL_PMEM", "PMEM_PATH"],
        fallback="pmem",
    )
    if not executable:
        warning(
            "PowerMem CLI is not configured",
            detail="set CONTEXTSEEK_POWERMEM_CLI or install pmem",
        )
        return 127
    if _would_recurse(executable):
        warning(
            "PowerMem CLI proxy would recurse",
            detail="set CONTEXTSEEK_POWERMEM_CLI to the real pmem executable",
        )
        return 126

    result = run_cli([executable, *args], env=powermem_child_process_env())
    emit_cli_result(result)
    if result.returncode != 0:
        return result.returncode

    adapter = PowerMemCLIAdapter(
        instance_id=os.environ.get("CONTEXTSEEK_POWERMEM_INSTANCE_ID", "default"),
        default_scope=os.environ.get("CONTEXTSEEK_POWERMEM_DEFAULT_SCOPE"),
    )
    try:
        events = adapter.events_from_cli_success(args, result.stdout)
        materialize_cli_events(events)
    except Exception as exc:  # noqa: BLE001
        warning("failed to materialize PowerMem CLI changes", detail=exc)
    return result.returncode


def _command_from_argv(argv: list[str]) -> str | None:
    known_commands = _WRITE_COMMANDS | _READ_COMMANDS
    for item in _semantic_tokens(argv):
        command = item.lower()
        if command in known_commands:
            return command
    return None


def _would_recurse(executable: str) -> bool:
    current = shutil.which(sys.argv[0]) or sys.argv[0]
    try:
        return os.path.realpath(executable) == os.path.realpath(current)
    except OSError:
        return False


def _method_for_command(command: str) -> str:
    if command in _DELETE_COMMANDS:
        return "DELETE"
    if command in _UPDATE_COMMANDS:
        return "PUT"
    return "POST"


def _path_for_command(command: str, body: dict[str, Any]) -> str:
    memory_id = body.get("id") or body.get("memory_id")
    if command in (_DELETE_COMMANDS | _UPDATE_COMMANDS) and memory_id:
        return f"{MEMORIES_PATH}/{memory_id}"
    return MEMORIES_PATH


def _request_body_from_argv(argv: list[str]) -> dict[str, Any]:
    command = _command_from_argv(argv)
    body: dict[str, Any] = {}
    for target, names in {
        "id": ["--id", "--memory-id"],
        "memory": ["--memory", "--content"],
        "scope": ["--scope"],
        "user_id": ["--user-id", "-u"],
        "agent_id": ["--agent-id", "-a"],
        "run_id": ["--run-id", "-r"],
    }.items():
        value = _value_after(argv, names)
        if value is not None:
            body[target] = value
    infer = _value_after(argv, ["--infer"])
    if infer is not None:
        body["infer"] = infer.lower() not in {"0", "false", "no"}
    if "--no-infer" in argv:
        body["infer"] = False

    positionals = _positionals_after_command(argv, command)
    if command in (_DELETE_COMMANDS | _UPDATE_COMMANDS) and "id" not in body:
        if positionals:
            body["id"] = positionals[0]
            positionals = positionals[1:]
    if "memory" not in body and positionals:
        body["memory"] = " ".join(positionals)
    return body


def _value_after(argv: list[str], names: list[str]) -> str | None:
    for index, item in enumerate(argv):
        for name in names:
            if item == name and index + 1 < len(argv):
                return argv[index + 1]
            prefix = f"{name}="
            if item.startswith(prefix):
                return item[len(prefix) :]
    return None


def _semantic_tokens(argv: list[str]) -> list[str]:
    values: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item in _OPTIONS_WITH_VALUES:
            skip_next = True
            continue
        if any(item.startswith(f"{name}=") for name in _OPTIONS_WITH_VALUES):
            continue
        if item.startswith("-"):
            continue
        values.append(item)
    return values


def _positionals_after_command(argv: list[str], command: str | None) -> list[str]:
    if command is None:
        return []
    seen_command = False
    values: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if not seen_command:
            if not item.startswith("-") and item.lower() == command:
                seen_command = True
            continue
        if item in _OPTIONS_WITH_VALUES or item == "--infer":
            skip_next = True
            continue
        if any(item.startswith(f"{name}=") for name in _OPTIONS_WITH_VALUES):
            continue
        if item.startswith("-"):
            continue
        values.append(item)
    return values


def _decode_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _stable_cli_id(content: Any) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"cli-{digest}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
