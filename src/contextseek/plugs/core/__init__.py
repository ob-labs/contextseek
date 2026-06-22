"""Core infrastructure shared by plug implementations."""

from contextseek.plugs.core.linkers import LifecycleLinker, Linker, LinkerResult
from contextseek.plugs.core.protocols import (
    DataPlug,
    InstallResult,
    MaterializationReceipt,
    PlugChangeEvent,
    PlugMeta,
    PlugProxyRequest,
    PlugProxyResponse,
    PlugProxyResult,
    ProxyDataPlug,
    RawEvent,
)
from contextseek.plugs.core.runtime import (
    PythonPackageRuntimeInstaller,
    PythonPackageVersionInfo,
)

__all__ = [
    "DataPlug",
    "InstallResult",
    "LifecycleLinker",
    "Linker",
    "LinkerResult",
    "MaterializationReceipt",
    "PlugChangeEvent",
    "PlugMeta",
    "PlugProxyRequest",
    "PlugProxyResponse",
    "PlugProxyResult",
    "ProxyDataPlug",
    "PythonPackageRuntimeInstaller",
    "PythonPackageVersionInfo",
    "RawEvent",
]
