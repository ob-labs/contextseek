"""Cline linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemMCPConfigLinker


def create_linker() -> PowerMemMCPConfigLinker:
    return PowerMemMCPConfigLinker(
        name="cline",
        target="Cline",
        config_env_var="CONTEXTSEEK_POWERMEM_CLINE_MCP_CONFIG",
        default_config_path=Path.home() / ".cline" / "mcp.json",
    )
