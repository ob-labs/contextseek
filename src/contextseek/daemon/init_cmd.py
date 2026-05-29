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
STORAGE_BACKEND=seekdb                    # embedded mode, no changes needed
SEEKDB_PATH=~/.contextseek/seekdb.db
# SEEKDB_HOST=127.0.0.1                  # uncomment to switch to seekdb server
# SEEKDB_PORT=2881

# --- Embedding (default: built-in all-MiniLM-L6-v2 via ONNX, no config needed) ---
# EMBEDDING_PROVIDER=langchain
# EMBEDDING_CLASS_PATH=langchain_openai.OpenAIEmbeddings
# EMBEDDING_MODEL=text-embedding-3-small
# EMBEDDING_DIMS=1536

# --- LLM (optional: improves evolution quality) ---
# LLM_PROVIDER=langchain
# LLM_CLASS_PATH=langchain_openai.ChatOpenAI
# LLM_MODEL=gpt-4o-mini
# LLM_API_KEY=sk-...

# --- Default scope (optional) ---
# DEFAULT_SCOPE=me/notes                 # omit --scope from CLI commands

# --- Evolution ---
EVOLUTION_ENABLED=true
LIFECYCLE_INTERVAL_SECONDS=3600         # auto-evolve every hour

# --- File watching (optional: auto-sync directories on change) ---
# WATCH_PATHS=~/notes:me/work,~/Documents/research:me/research
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
        config_env.write_text(
            _CONFIG_ENV_TEMPLATE.format(date=today), encoding="utf-8"
        )
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
    print(
        "    2. Add contextseek MCP to your AI tool using the snippet in mcp.json"
    )
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
                ["systemctl", "--user", "daemon-reload"], check=True, capture_output=True
            )
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "contextseek"],
                check=True,
                capture_output=True,
            )
            print("  systemd  contextseek.service enabled and started")
        except subprocess.CalledProcessError as exc:
            print(f"  systemd  service registration failed: {exc}")
            print(
                "  Manually enable with: systemctl --user enable --now contextseek"
            )
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
