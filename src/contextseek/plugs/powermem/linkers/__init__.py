"""PowerMem linker registry."""

from __future__ import annotations

from contextseek.plugs.powermem.linkers.claude_code import (
    create_http_linker as create_claude_code_http_linker,
    create_linker as create_claude_code_linker,
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


_LINKER_FACTORIES = {
    "claude": create_claude_desktop_linker,
    "claude-code": create_claude_code_linker,
    "claude-code-http": create_claude_code_http_linker,
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
_HIDDEN_LINKERS = {
    "claude-code-http",
}


def available_linker_names() -> list[str]:
    return sorted(name for name in _LINKER_FACTORIES if name not in _HIDDEN_LINKERS)


def get_linker(name: str) -> Linker:
    key = normalize_linker_name(name)
    factory = _LINKER_FACTORIES.get(key)
    if factory is None:
        msg = f"unknown linker: {name}"
        raise KeyError(msg)
    return factory()


def normalize_linker_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def is_linker_disabled(name: str) -> bool:
    normalize_linker_name(name)
    return False


def disabled_linker_message(name: str) -> str:
    return f"disabled linker: {normalize_linker_name(name)}"


__all__ = [
    "Linker",
    "LinkerResult",
    "available_linker_names",
    "disabled_linker_message",
    "get_linker",
    "is_linker_disabled",
    "normalize_linker_name",
]
