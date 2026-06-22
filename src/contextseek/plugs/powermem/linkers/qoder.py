"""Qoder linker for plug capabilities."""

from contextseek.plugs.powermem.linkers.config import PowerMemEnvOnlyMCPConfigLinker


def create_linker() -> PowerMemEnvOnlyMCPConfigLinker:
    return PowerMemEnvOnlyMCPConfigLinker(
        name="qoder",
        target="Qoder",
        config_env_var="CONTEXTSEEK_POWERMEM_QODER_MCP_CONFIG",
    )
