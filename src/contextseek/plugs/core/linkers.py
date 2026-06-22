"""Protocols for plug capability linkers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LinkerResult:
    """Result returned by a linker install/check operation."""

    changed: bool
    dry_run: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Linker(Protocol):
    """Install/check one plug capability for one target runtime."""

    name: str

    def detect(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        """Detect the target runtime or config surface."""
        ...

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        """Install or enable the target-side runtime entry when needed."""
        ...

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        """Route the target runtime entry through ContextSeek."""
        ...

    def validate(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        """Validate the installed/configured linker."""
        ...

    def install(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        """Install or check the link for ``plug_name``."""
        ...


class LifecycleLinker:
    """Base implementation for the plug linker lifecycle.

    Most linkers only need ``configure_proxy``. Runtime/plugin installation is
    a no-op unless a concrete target, such as Claude Code, overrides it.
    """

    name: str

    def detect(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return LinkerResult(changed=False, dry_run=dry_run or check)

    def install_runtime(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return LinkerResult(changed=False, dry_run=dry_run or check)

    def configure_proxy(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        msg = f"{self.__class__.__name__} must implement configure_proxy()"
        raise NotImplementedError(msg)

    def validate(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        return LinkerResult(changed=False, dry_run=dry_run or check)

    def install(
        self,
        *,
        plug_name: str,
        dry_run: bool = False,
        check: bool = False,
    ) -> LinkerResult:
        results = [
            self.detect(plug_name=plug_name, dry_run=dry_run, check=check),
            self.install_runtime(plug_name=plug_name, dry_run=dry_run, check=check),
            self.configure_proxy(plug_name=plug_name, dry_run=dry_run, check=check),
            self.validate(plug_name=plug_name, dry_run=dry_run, check=check),
        ]
        return merge_linker_results(*results, dry_run=dry_run or check)


def merge_linker_results(
    *results: LinkerResult,
    dry_run: bool = False,
) -> LinkerResult:
    """Merge stage results into one install result."""
    actions: list[str] = []
    warnings: list[str] = []
    changed = False
    for result in results:
        changed = changed or result.changed
        actions.extend(result.actions)
        warnings.extend(result.warnings)
    return LinkerResult(
        changed=changed,
        dry_run=dry_run or any(result.dry_run for result in results),
        actions=actions,
        warnings=warnings,
    )
