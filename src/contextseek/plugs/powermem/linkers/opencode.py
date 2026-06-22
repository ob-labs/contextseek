"""OpenCode linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemOpenCodeConfigLinker


def create_linker() -> PowerMemOpenCodeConfigLinker:
    return PowerMemOpenCodeConfigLinker(
        name="opencode",
        target="OpenCode",
        config_env_var="CONTEXTSEEK_POWERMEM_OPENCODE_MCP_CONFIG",
        default_config_path=Path.home() / ".config" / "opencode" / "opencode.json",
    )
