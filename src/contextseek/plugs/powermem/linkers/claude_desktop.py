"""Claude Desktop linker for plug capabilities."""

import os
import platform
from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemMCPConfigLinker


def create_linker() -> PowerMemMCPConfigLinker:
    return PowerMemMCPConfigLinker(
        name="claude-desktop",
        target="Claude Desktop",
        config_env_var="CONTEXTSEEK_POWERMEM_CLAUDE_DESKTOP_MCP_CONFIG",
        default_config_path=_default_config_path(),
    )


def _default_config_path() -> Path:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"
