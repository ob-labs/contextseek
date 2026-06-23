"""PowerMem plug adapters and proxy entry points."""

from contextseek.plugs.powermem.adapter import (
    DEFAULT_CONTEXTSEEK_SCOPE,
    DEFAULT_INSTANCE_ID,
    MEMORIES_PATH,
    MEMORIES_SEARCH_PATH,
    POWERMEM_PLUG_NAME,
    POWERMEM_SOURCE_TYPE,
    POWERMEM_TAG,
    PowerMemAdapter,
)
from contextseek.plugs.powermem.http import PowerMemHTTPPlug, PowerMemProxyPlug
from contextseek.plugs.powermem.mcp import (
    PowerMemMCPAdapter,
    build_powermem_mcp_adapter,
    create_powermem_mcp_proxy,
    run_stdio_server,
)
from contextseek.plugs.powermem.sdk import (
    Memory,
    POWERMEM_SDK_MIN_VERSION,
    POWERMEM_SDK_REQUIREMENT,
    PowerMemMemoryProxy,
    PowerMemSDKAdapter,
    PowerMemSDKVersionInfo,
    powermem_sdk_version_info,
    validate_powermem_sdk_version,
)
from contextseek.plugs.powermem.plug import PowerMemPlug

__all__ = [
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_CONTEXTSEEK_SCOPE",
    "MEMORIES_PATH",
    "MEMORIES_SEARCH_PATH",
    "POWERMEM_PLUG_NAME",
    "POWERMEM_SDK_MIN_VERSION",
    "POWERMEM_SDK_REQUIREMENT",
    "POWERMEM_SOURCE_TYPE",
    "POWERMEM_TAG",
    "PowerMemAdapter",
    "PowerMemHTTPPlug",
    "PowerMemMCPAdapter",
    "PowerMemPlug",
    "build_powermem_mcp_adapter",
    "create_powermem_mcp_proxy",
    "Memory",
    "PowerMemMemoryProxy",
    "PowerMemProxyPlug",
    "PowerMemSDKAdapter",
    "PowerMemSDKVersionInfo",
    "powermem_sdk_version_info",
    "run_stdio_server",
    "validate_powermem_sdk_version",
]
