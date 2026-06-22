"""contextseek init — one-time setup for personal use.

Creates ~/.contextseek/ with config.env template and mcp.json, then
optionally registers the daemon as a system service.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone


_CONFIG_ENV_TEMPLATE = """\
# ContextSeek personal configuration
# Generated: {date}

# --- Storage ---
STORAGE_BACKEND=sqlite                   # local file, no native engine required
SQLITE_PATH=~/.contextseek/contextseek.sqlite3
# STORAGE_BACKEND=seekdb                 # optional: embedded seekdb
# SEEKDB_PATH=~/.contextseek/seekdb.db
# SEEKDB_HOST=127.0.0.1                  # optional: seekdb server
# SEEKDB_PORT=2881
# For seekdb server mode, set STORAGE_BACKEND=oceanbase instead.

# --- Embedding (optional; default works without external embedding) ---
# EMBEDDING_PROVIDER=langchain
# EMBEDDING_CLASS_PATH=langchain_openai.OpenAIEmbeddings
# EMBEDDING_MODEL=text-embedding-3-small
# EMBEDDING_DIMS=1536

# --- LLM (optional: improves evolution quality) ---
# LLM_PROVIDER=langchain
# LLM_CLASS_PATH=langchain_openai.ChatOpenAI
# LLM_MODEL=gpt-4o-mini
# LLM_API_KEY=sk-...

# --- Default scope ---
DEFAULT_SCOPE=me/work                   # omit --scope from CLI commands

# --- Evolution ---
EVOLUTION_ENABLED=true
LIFECYCLE_INTERVAL_SECONDS=3600         # auto-evolve every hour

# --- File watching (optional: auto-sync directories on change) ---
# WATCH_PATHS=~/notes:me/work,~/Documents/research:me/research

# --- Skill export (materialize distilled prompt skills as SKILL.md) ---
# When enabled, the daemon writes stage=skill items to SKILL_EXPORT_DIR after
# each evolution cycle, so agent tools (Claude Code, Qoder, ...) can pick them up.
# Do NOT point WATCH_PATHS at SKILL_EXPORT_DIR — that would re-ingest the exports.
SKILL_EXPORT_ENABLED=false
SKILL_EXPORT_DIR=~/.contextseek/skills
SKILL_EXPORT_MIN_CONFIDENCE=0.8
"""

_MCP_JSON_TEMPLATE = {
    "mcpServers": {
        "contextseek": {
            "command": "contextseek-mcp-stdio",
            "args": [],
            "env": {
                "CONTEXTSEEK_CONFIG": "~/.contextseek/config.env",
            },
        }
    }
}

_SYSTEMD_SERVICE = """\
[Unit]
Description=ContextSeek background daemon
After=network.target

[Service]
ExecStart={contextseek_bin} daemon start --foreground
Restart=on-failure
RestartSec=5s
Environment=CONTEXTSEEK_CONFIG=%h/.contextseek/config.env

[Install]
WantedBy=default.target
"""

_LAUNCHD_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.contextseek.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>{contextseek_bin}</string>
    <string>daemon</string>
    <string>start</string>
    <string>--foreground</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CONTEXTSEEK_CONFIG</key>
    <string>{home}/.contextseek/config.env</string>
  </dict>
</dict>
</plist>
"""


def _claude_desktop_config_path() -> pathlib.Path:
    """Return the platform-specific Claude Desktop config path."""
    home = pathlib.Path.home()
    system = platform.system()
    if system == "Darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        base = pathlib.Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    # Linux / other
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _maybe_merge_claude_desktop() -> None:
    """Detect Claude Desktop's config and offer to add the contextseek server.

    Skips silently when the config is absent or the session is non-interactive,
    leaving the user to copy the snippet from ``mcp.json`` manually.
    """
    cfg_path = _claude_desktop_config_path()
    if not cfg_path.exists():
        return

    server_entry = _MCP_JSON_TEMPLATE["mcpServers"]["contextseek"]
    try:
        existing = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"  Claude Desktop config at {cfg_path} is unreadable; skipping merge.")
        return

    servers = existing.get("mcpServers")
    if isinstance(servers, dict) and servers.get("contextseek") == server_entry:
        print("  Claude Desktop already has the contextseek MCP server.")
        return

    print(f"  Found Claude Desktop config: {cfg_path}")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "    Non-interactive session; add the contextseek entry from mcp.json manually."
        )
        return
    try:
        answer = (
            input("  Merge contextseek into Claude Desktop config? [y/N] ")
            .strip()
            .lower()
        )
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        print("    Skipped; you can merge it later from mcp.json.")
        return

    # Back up before mutating, then merge the server entry.
    try:
        backup = cfg_path.with_suffix(cfg_path.suffix + ".contextseek.bak")
        backup.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not isinstance(existing.get("mcpServers"), dict):
            existing["mcpServers"] = {}
        existing["mcpServers"]["contextseek"] = server_entry
        cfg_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"    Merged. Backup written to {backup}")
        print("    Restart Claude Desktop to pick up the contextseek server.")
    except OSError as exc:
        print(f"    Merge failed: {exc}")


def _find_contextseek_bin() -> str:
    """Locate the contextseek CLI binary."""
    found = shutil.which("contextseek")
    if found:
        return found
    return sys.executable + " -m contextseek"


def run_init(config_dir: pathlib.Path) -> None:
    """Initialise the personal ContextSeek directory.

    Creates directory structure, config.env template, and mcp.json.
    On Linux/macOS, offers to register the daemon as a system service.
    """
    config_dir = pathlib.Path(config_dir).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "logs").mkdir(exist_ok=True)
    (config_dir / "backups").mkdir(exist_ok=True)

    # config.env
    config_env = config_dir / "config.env"
    if not config_env.exists():
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        config_env.write_text(_CONFIG_ENV_TEMPLATE.format(date=today), encoding="utf-8")
        print(f"  Created  {config_env}")
    else:
        print(f"  Exists   {config_env}  (not overwritten)")

    # mcp.json
    mcp_json = config_dir / "mcp.json"
    if not mcp_json.exists():
        mcp_json.write_text(
            json.dumps(_MCP_JSON_TEMPLATE, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  Created  {mcp_json}")
    else:
        print(f"  Exists   {mcp_json}  (not overwritten)")

    # Offer to merge the contextseek server into Claude Desktop's config.
    _maybe_merge_claude_desktop()

    print()

    # System service registration
    system = platform.system()
    contextseek_bin = _find_contextseek_bin()

    if system == "Linux":
        _register_systemd(config_dir, contextseek_bin)
    elif system == "Darwin":
        _register_launchd(config_dir, contextseek_bin)
    else:
        print(
            "  System service registration is not supported on this platform.\n"
            "  Run `contextseek daemon start` manually to start the background process."
        )

    print()
    print("  Setup complete.  Next steps:")
    print(f"    1. Edit {config_env} to configure LLM/embedding (optional)")
    print("    2. Add contextseek MCP to your AI tool using the snippet in mcp.json")
    print("    3. Run `contextseek daemon status` to confirm the daemon is running")


def _register_systemd(config_dir: pathlib.Path, contextseek_bin: str) -> None:
    systemd_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    service_file = systemd_dir / "contextseek.service"

    systemd_dir.mkdir(parents=True, exist_ok=True)
    if not service_file.exists():
        service_file.write_text(
            _SYSTEMD_SERVICE.format(contextseek_bin=contextseek_bin),
            encoding="utf-8",
        )
        print(f"  Created  {service_file}")

    # Try to enable and start the service
    if shutil.which("systemctl"):
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "contextseek"],
                check=True,
                capture_output=True,
            )
            print("  systemd  contextseek.service enabled and started")
        except subprocess.CalledProcessError as exc:
            print(f"  systemd  service registration failed: {exc}")
            print("  Manually enable with: systemctl --user enable --now contextseek")
    else:
        print(
            "  systemctl not found; service file written but not activated.\n"
            "  Enable manually: systemctl --user enable --now contextseek"
        )


def _register_launchd(config_dir: pathlib.Path, contextseek_bin: str) -> None:
    agents_dir = pathlib.Path.home() / "Library" / "LaunchAgents"
    plist_file = agents_dir / "com.contextseek.daemon.plist"

    agents_dir.mkdir(parents=True, exist_ok=True)
    if not plist_file.exists():
        plist_file.write_text(
            _LAUNCHD_PLIST.format(
                contextseek_bin=contextseek_bin,
                home=str(pathlib.Path.home()),
            ),
            encoding="utf-8",
        )
        print(f"  Created  {plist_file}")

    if shutil.which("launchctl"):
        try:
            subprocess.run(
                ["launchctl", "load", str(plist_file)],
                check=True,
                capture_output=True,
            )
            print("  launchd  com.contextseek.daemon loaded")
        except subprocess.CalledProcessError as exc:
            print(f"  launchd  load failed: {exc}")
            print(f"  Load manually: launchctl load {plist_file}")
    else:
        print(
            "  launchctl not found; plist written but not loaded.\n"
            f"  Load manually: launchctl load {plist_file}"
        )
