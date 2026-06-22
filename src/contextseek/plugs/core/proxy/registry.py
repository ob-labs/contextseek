"""Registry for plug proxy adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class PlugNotFound(LookupError):
    """Raised when a plug name is not registered."""


class PlugNotConfigured(ValueError):
    """Raised when a registered plug is missing required configuration."""


PlugFactory = Callable[[str, Any], Any]


@dataclass
class PlugRegistry:
    """Resolve plug adapters by name and instance."""

    _factories: dict[str, PlugFactory] = field(default_factory=dict)

    def register(self, name: str, factory: PlugFactory) -> None:
        self._factories[self._normalize(name)] = factory

    def create(self, name: str, instance_id: str, body: Any = None) -> Any:
        key = self._normalize(name)
        factory = self._factories.get(key)
        if factory is None:
            raise PlugNotFound(f"unknown plug: {name}")
        try:
            return factory(instance_id, body)
        except ValueError as exc:
            raise PlugNotConfigured(str(exc)) from exc

    def names(self) -> list[str]:
        return sorted(self._factories)

    @staticmethod
    def _normalize(name: str) -> str:
        return name.strip().lower().replace("_", "-")


def create_default_registry() -> PlugRegistry:
    from contextseek.plugs.powermem.http import build_powermem_http_plug

    registry = PlugRegistry()
    registry.register("powermem", build_powermem_http_plug)
    return registry
