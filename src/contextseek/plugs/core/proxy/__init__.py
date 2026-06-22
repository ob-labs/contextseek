"""Generic plug capability proxy helpers."""

from contextseek.plugs.core.proxy.materialization import contextseek_meta
from contextseek.plugs.core.proxy.registry import (
    PlugFactory,
    PlugNotConfigured,
    PlugNotFound,
    PlugRegistry,
    create_default_registry,
)

__all__ = [
    "PlugFactory",
    "PlugNotConfigured",
    "PlugNotFound",
    "PlugRegistry",
    "contextseek_meta",
    "create_default_registry",
]
