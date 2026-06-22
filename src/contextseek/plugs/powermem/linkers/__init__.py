"""PowerMem linker registry."""

from __future__ import annotations

import os

from contextseek.plugs.powermem.linkers.claude_code import (
    create_http_linker as create_claude_code_http_linker,
    create_linker as create_claude_code_linker,
    create_mcp_linker as create_claude_code_mcp_linker,
)
from contextseek.plugs.powermem.linkers.claude_desktop import (
    create_linker as create_claude_desktop_linker,
)
from contextseek.plugs.powermem.linkers.cline import (
    create_linker as create_cline_linker,
)
from contextseek.plugs.powermem.linkers.codex import (
    create_linker as create_codex_linker,
)
from contextseek.plugs.powermem.linkers.copilot import (
    create_linker as create_copilot_linker,
)
from contextseek.plugs.powermem.linkers.cursor import (
    create_linker as create_cursor_linker,
)
from contextseek.plugs.powermem.linkers.openclaw import (
    create_linker as create_openclaw_linker,
)
from contextseek.plugs.powermem.linkers.opencode import (
    create_linker as create_opencode_linker,
)
from contextseek.plugs.powermem.linkers.qoder import (
    create_linker as create_qoder_linker,
)
from contextseek.plugs.powermem.linkers.vscode import (
    create_linker as create_vscode_linker,
)
from contextseek.plugs.powermem.linkers.windsurf import (
    create_linker as create_windsurf_linker,
)
from contextseek.plugs.core.linkers import Linker, LinkerResult


_TRUE_VALUES = {"1", "true", "yes", "on"}
_CLAUDE_CODE_HTTP_ENABLED_ENV = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_HTTP_ENABLED"
_LEGACY_CLAUDE_CODE_ENABLED_ENV = "CONTEXTSEEK_POWERMEM_CLAUDE_CODE_ENABLED"
_CLAUDE_CODE_HTTP_LINKERS = {"claude-code-http"}
_CLAUDE_CODE_HTTP_DISABLED_MESSAGE = (
    "disabled linker: {name} "
    "(Claude Code HTTP hook channel requires the official PowerMem Claude Code "
    "plugin binary package; use claude-code for MCP mode)"
)

_LINKER_FACTORIES = {
    "claude": create_claude_desktop_linker,
    "claude-code": create_claude_code_linker,
    "claude-code-http": create_claude_code_http_linker,
    "claude-code-mcp": create_claude_code_mcp_linker,
    "claude-desktop": create_claude_desktop_linker,
    "cline": create_cline_linker,
    "codex": create_codex_linker,
    "copilot": create_copilot_linker,
    "cursor": create_cursor_linker,
    "github-copilot": create_copilot_linker,
    "openclaw": create_openclaw_linker,
    "opencode": create_opencode_linker,
    "qoder": create_qoder_linker,
    "vs-code": create_vscode_linker,
    "vscode": create_vscode_linker,
    "windsurf": create_windsurf_linker,
}


def available_linker_names() -> list[str]:
    return sorted(name for name in _LINKER_FACTORIES if not is_linker_disabled(name))


def get_linker(name: str) -> Linker:
    key = normalize_linker_name(name)
    if is_linker_disabled(key):
        msg = disabled_linker_message(key)
        raise KeyError(msg)
    factory = _LINKER_FACTORIES.get(key)
    if factory is None:
        msg = f"unknown linker: {name}"
        raise KeyError(msg)
    return factory()


def normalize_linker_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def is_linker_disabled(name: str) -> bool:
    key = normalize_linker_name(name)
    if key not in _CLAUDE_CODE_HTTP_LINKERS:
        return False
    enabled = os.environ.get(_CLAUDE_CODE_HTTP_ENABLED_ENV, "").strip().lower()
    legacy_enabled = os.environ.get(_LEGACY_CLAUDE_CODE_ENABLED_ENV, "").strip().lower()
    return enabled not in _TRUE_VALUES and legacy_enabled not in _TRUE_VALUES


def disabled_linker_message(name: str) -> str:
    return _CLAUDE_CODE_HTTP_DISABLED_MESSAGE.format(name=normalize_linker_name(name))


__all__ = [
    "Linker",
    "LinkerResult",
    "available_linker_names",
    "disabled_linker_message",
    "get_linker",
    "is_linker_disabled",
    "normalize_linker_name",
]
