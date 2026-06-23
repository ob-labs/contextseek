"""Claude Code linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import (
    PowerMemClaudeCodeMCPConfigLinker,
    PowerMemClaudeCodeHTTPConfigLinker,
)


def create_linker() -> PowerMemClaudeCodeMCPConfigLinker:
    return PowerMemClaudeCodeMCPConfigLinker(
        name="claude-code",
        target="Claude Code",
        config_env_var="CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG",
        default_config_path=Path.cwd() / ".mcp.json",
    )


def create_http_linker() -> PowerMemClaudeCodeHTTPConfigLinker:
    return PowerMemClaudeCodeHTTPConfigLinker(
        name="claude-code-http",
        target="Claude Code",
        config_env_var="CONTEXTSEEK_POWERMEM_CLAUDE_CODE_SETTINGS",
        default_config_path=Path.home() / ".claude" / "settings.json",
        mcp_config_env_var="CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG",
        mcp_default_config_path=Path.cwd() / ".mcp.json",
    )


def create_mcp_linker() -> PowerMemClaudeCodeMCPConfigLinker:
    return PowerMemClaudeCodeMCPConfigLinker(
        name="claude-code-mcp",
        target="Claude Code",
        config_env_var="CONTEXTSEEK_POWERMEM_CLAUDE_CODE_MCP_CONFIG",
        default_config_path=Path.cwd() / ".mcp.json",
    )
