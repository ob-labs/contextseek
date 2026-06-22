"""Cursor linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemMCPConfigLinker


def create_linker() -> PowerMemMCPConfigLinker:
    return PowerMemMCPConfigLinker(
        name="cursor",
        target="Cursor",
        config_env_var="CONTEXTSEEK_POWERMEM_CURSOR_MCP_CONFIG",
        default_config_path=Path.home() / ".cursor" / "mcp.json",
    )
