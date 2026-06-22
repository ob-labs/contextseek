"""Codex linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemMCPConfigLinker


def create_linker() -> PowerMemMCPConfigLinker:
    return PowerMemMCPConfigLinker(
        name="codex",
        target="Codex",
        config_env_var="CONTEXTSEEK_POWERMEM_CODEX_MCP_CONFIG",
        default_config_path=Path.home() / ".codex" / "context.json",
    )
