"""Copilot linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemVSCodeMCPConfigLinker


def create_linker() -> PowerMemVSCodeMCPConfigLinker:
    return PowerMemVSCodeMCPConfigLinker(
        name="copilot",
        target="GitHub Copilot for VS Code",
        config_env_var="CONTEXTSEEK_POWERMEM_COPILOT_MCP_CONFIG",
        default_config_path=Path.cwd() / ".vscode" / "mcp.json",
    )
