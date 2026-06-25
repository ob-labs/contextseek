"""Claude Code linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import (
    PowerMemClaudeCodeHTTPConfigLinker,
    PowerMemClaudeCodeMCPConfigLinker,
)


def create_linker() -> PowerMemClaudeCodeHTTPConfigLinker:
    return _create_http_linker(name="claude-code")


def create_http_linker() -> PowerMemClaudeCodeHTTPConfigLinker:
    return _create_http_linker(name="claude-code-http")


def _create_http_linker(*, name: str) -> PowerMemClaudeCodeHTTPConfigLinker:
    return PowerMemClaudeCodeHTTPConfigLinker(
        name=name,
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
