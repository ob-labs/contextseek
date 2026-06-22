# PowerMem Agent Integration Guide

This document covers install and startup commands for each supported agent/IDE.

## General Checks

List available linkers:

```bash
contextseek plug-install powermem
```

Preview configuration changes for a specific linker:

```bash
contextseek plug-install powermem --linker <linker> --dry-run
```

On first run, ContextSeek automatically provisions the managed PowerMem runtime. You do not need to install PowerMem separately.

## Claude Code

```bash
contextseek plug-install powermem --linker claude-code
```

Then start Claude Code as usual.

Claude Code uses MCP mode by default. You do not need to install the PowerMem Claude Code hook binary.

You can also use the alias:

```bash
contextseek plug-install powermem --linker claude-code-mcp
```

## OpenClaw

```bash
contextseek plug-install powermem --linker openclaw
```

Then start OpenClaw as usual.

This command checks whether `memory-powermem` is already installed on the OpenClaw side and runs the OpenClaw plugin install automatically if it is not.

If the OpenClaw command is not on PATH, set it first:

```bash
export CONTEXTSEEK_OPENCLAW_COMMAND=/path/to/openclaw
```

For local development, to use the `pmem` command from the current environment:

```bash
export CONTEXTSEEK_POWERMEM_PACKAGE_INSTALL_STRATEGY=current_env
export CONTEXTSEEK_POWERMEM_CLI=/path/to/real/pmem
contextseek plug-install powermem --linker openclaw
```

## Cursor

```bash
contextseek plug-install powermem --linker cursor
```

Then open or restart Cursor as usual.

## VS Code

```bash
contextseek plug-install powermem --linker vscode
```

Then open or restart VS Code as usual.

## GitHub Copilot

```bash
contextseek plug-install powermem --linker github-copilot
```

Then open or restart VS Code / GitHub Copilot as usual.

You can also use the alias:

```bash
contextseek plug-install powermem --linker copilot
```

## Codex

```bash
contextseek plug-install powermem --linker codex
```

Then start Codex as usual.

## Windsurf

```bash
contextseek plug-install powermem --linker windsurf
```

Then open or restart Windsurf as usual.

## OpenCode

```bash
contextseek plug-install powermem --linker opencode
```

Then start OpenCode as usual.

## Claude Desktop

```bash
contextseek plug-install powermem --linker claude-desktop
```

Then open or restart Claude Desktop as usual.

You can also use the alias:

```bash
contextseek plug-install powermem --linker claude
```

## Cline

```bash
contextseek plug-install powermem --linker cline
```

Then open or restart Cline as usual.

## Qoder

First, set the Qoder MCP config file:

```bash
export CONTEXTSEEK_POWERMEM_QODER_MCP_CONFIG=/path/to/qoder/mcp.json
```

Then install:

```bash
contextseek plug-install powermem --linker qoder
```

Then start Qoder as usual.

## PowerMem Configuration File

View the config file:

```bash
cat ~/.contextseek/plugs/powermem.env
```

Edit the config file:

```bash
vim ~/.contextseek/plugs/powermem.env
```

Use a different config file:

```bash
export CONTEXTSEEK_POWERMEM_ENV_FILE=/path/to/powermem.env
```

## Supported Linkers

```text
claude
claude-code
claude-code-mcp
claude-desktop
cline
codex
copilot
cursor
github-copilot
openclaw
opencode
qoder
vs-code
vscode
windsurf
```
