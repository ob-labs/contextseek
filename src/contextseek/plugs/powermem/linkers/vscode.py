"""VS Code linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemVSCodeMCPConfigLinker


def create_linker() -> PowerMemVSCodeMCPConfigLinker:
    return PowerMemVSCodeMCPConfigLinker(
        name="vscode",
        target="VS Code",
        config_env_var="CONTEXTSEEK_POWERMEM_VSCODE_MCP_CONFIG",
        default_config_path=Path.cwd() / ".vscode" / "mcp.json",
    )
