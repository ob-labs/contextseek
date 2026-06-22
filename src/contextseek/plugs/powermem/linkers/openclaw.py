"""OpenClaw linker for plug capabilities."""

from pathlib import Path

from contextseek.plugs.powermem.linkers.config import PowerMemOpenClawCLIConfigLinker


def create_linker() -> PowerMemOpenClawCLIConfigLinker:
    return PowerMemOpenClawCLIConfigLinker(
        name="openclaw",
        target="OpenClaw",
        config_env_var="CONTEXTSEEK_POWERMEM_OPENCLAW_CONFIG",
        default_config_path=Path.home() / ".openclaw" / "openclaw.json",
    )
