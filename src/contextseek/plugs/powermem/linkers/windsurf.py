"""Windsurf linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemWindsurfConfigLinker


def create_linker() -> PowerMemWindsurfConfigLinker:
    return PowerMemWindsurfConfigLinker(
        name="windsurf",
        target="Windsurf",
        config_env_var="CONTEXTSEEK_POWERMEM_WINDSURF_MCP_CONFIG",
        default_config_path=Path.home() / ".windsurf" / "context" / "powermem.json",
    )
