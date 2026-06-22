"""Scope utilities: builder, templates, lint, and analysis types."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


# ---------------------------------------------------------------------------
# Lint warning
# ---------------------------------------------------------------------------


class ScopeLintWarning(UserWarning):
    """Emitted when a scope string looks malformed or non-canonical."""


def _lint_scope(scope: str) -> list[str]:
    """Return a list of lint messages for *scope*, empty if clean."""
    issues: list[str] = []
    if not scope:
        issues.append(
            "scope is empty; using a hierarchical scope is strongly recommended"
        )
        return issues
    if "/" not in scope:
        issues.append(
            f"scope '{scope}' has no '/' separator; at least two levels recommended for isolation"
        )
    if any(part == "" for part in scope.split("/")):
        issues.append(
            f"scope '{scope}' contains empty path segments; avoid repeated, leading, or trailing slashes"
        )
    depth = len(scope.strip("/").split("/"))
    if depth > 6:
        issues.append(
            f"scope '{scope}' is {depth} levels deep; consider flattening to avoid overly narrow retrieval"
        )
    if scope != scope.lower() or " " in scope:
        issues.append(
            f"scope '{scope}' contains uppercase letters or spaces; use lowercase kebab-case"
        )
    return issues


# ---------------------------------------------------------------------------
# ScopeBuilder
# ---------------------------------------------------------------------------


class ScopeBuilder:
    """Chainable builder for well-structured scope strings.

    Semantic segments (org, project, team, domain, agent) append the given
    value directly.  Container segments (run, task, user) prepend their type
    label so the path carries intent::

        ScopeBuilder().org("acme").project("pay").run("r001").build()
        # → "acme/pay/run/r001"
    """

    def __init__(self) -> None:
        self._parts: list[str] = []

    def _clone_with(self, *parts: str) -> ScopeBuilder:
        b = ScopeBuilder()
        b._parts = self._parts + list(parts)
        return b

    # -- semantic segments (no type label) -----------------------------------

    def org(self, name: str) -> ScopeBuilder:
        return self._clone_with(_slug(name))

    def project(self, name: str) -> ScopeBuilder:
        return self._clone_with(_slug(name))

    def team(self, name: str) -> ScopeBuilder:
        return self._clone_with(_slug(name))

    def domain(self, name: str) -> ScopeBuilder:
        return self._clone_with(_slug(name))

    def agent(self, name: str) -> ScopeBuilder:
        return self._clone_with(_slug(name))

    # -- container segments (type label + value) -----------------------------

    def run(self, run_id: str) -> ScopeBuilder:
        return self._clone_with("run", _slug(run_id))

    def task(self, task_id: str) -> ScopeBuilder:
        return self._clone_with("task", _slug(task_id))

    def user(self, user_id: str) -> ScopeBuilder:
        return self._clone_with("user", _slug(user_id))

    # -- escape hatch --------------------------------------------------------

    def append(self, segment: str) -> ScopeBuilder:
        """Append a raw path segment (no transformation)."""
        return self._clone_with(segment)

    # -- terminal ------------------------------------------------------------

    def build(self) -> str:
        """Return the assembled scope string."""
        if not self._parts:
            raise ValueError(
                "ScopeBuilder has no segments; call at least one segment method first"
            )
        return "/".join(self._parts)

    # -- factory -------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        prefix: str,
        env_vars: dict[str, str],
    ) -> ScopeBuilder:
        """Build a ScopeBuilder from environment variables.

        Args:
            prefix: A literal prefix segment prepended before any env-sourced segments.
            env_vars: Mapping of builder *method name* to *env var name*.
                      Example: ``{"project": "SERVICE_NAME", "run": "RUN_ID"}``.
                      Missing env vars are silently skipped.

        Returns:
            A ScopeBuilder ready to call ``.build()``.
        """
        b = cls().append(_slug(prefix))
        for method_name, env_key in env_vars.items():
            value = os.environ.get(env_key)
            if value is None:
                continue
            method = getattr(b, method_name, None)
            if method is None:
                raise ValueError(
                    f"ScopeBuilder has no segment method '{method_name}'; "
                    f"valid names: org, project, team, domain, agent, run, task, user, append"
                )
            b = method(value)
        return b


def _slug(value: str) -> str:
    """Strip leading/trailing slashes and whitespace."""
    return value.strip("/ \t")


# ---------------------------------------------------------------------------
# ScopeTemplates
# ---------------------------------------------------------------------------

ScopeLayer = Literal["knowledge", "skill", "reference"]


class ScopeTemplates:
    """Ready-made scope patterns for common use cases.

    All methods are static and return a plain ``str``.
    """

    @staticmethod
    def org_knowledge(org: str, team: str, domain: str) -> str:
        """``{org}/{team}/knowledge/{domain}``"""
        return f"{_slug(org)}/{_slug(team)}/knowledge/{_slug(domain)}"

    @staticmethod
    def agent_run(agent_name: str, run_id: str, task_id: str | None = None) -> str:
        """``agent/{agent_name}/run/{run_id}[/task/{task_id}]``"""
        base = f"agent/{_slug(agent_name)}/run/{_slug(run_id)}"
        return f"{base}/task/{_slug(task_id)}" if task_id else base

    @staticmethod
    def user_space(user_id: str, domain: str) -> str:
        """``user/{user_id}/{domain}``"""
        return f"user/{_slug(user_id)}/{_slug(domain)}"

    @staticmethod
    def shared(project: str, layer: ScopeLayer) -> str:
        """``shared/{project}/{layer}``"""
        return f"shared/{_slug(project)}/{layer}"


# ---------------------------------------------------------------------------
# Analysis types (returned by ctx.scope_tree / ctx.scope_stats)
# ---------------------------------------------------------------------------


@dataclass
class ScopeStats:
    """Aggregate statistics for a single scope."""

    scope: str
    item_count: int
    stage_distribution: dict[str, int]
    avg_confidence: float
    last_write: datetime | None
    gap_count: int = 0  # populated when GapDetector is available


@dataclass
class ScopeNode:
    """One node in a scope tree."""

    name: str
    full_path: str
    item_count: int
    knowledge_count: int
    skill_count: int
    children: dict[str, "ScopeNode"] = field(default_factory=dict)


@dataclass
class ScopeTree:
    """Hierarchical view of all scopes known to a ContextSeek instance."""

    nodes: dict[str, ScopeNode] = field(default_factory=dict)

    def print(self, indent: int = 0) -> None:  # noqa: A003
        """Print a human-readable tree to stdout."""
        _print_nodes(self.nodes, indent)


def _print_nodes(nodes: dict[str, ScopeNode], indent: int) -> None:
    prefix = "  " * indent
    for name, node in sorted(nodes.items()):
        counts = f"({node.item_count} items"
        if node.knowledge_count:
            counts += f", {node.knowledge_count} knowledge"
        if node.skill_count:
            counts += f", {node.skill_count} skills"
        counts += ")"
        if node.children:
            print(f"{prefix}{name}/")
            _print_nodes(node.children, indent + 1)
        else:
            print(f"{prefix}{name}/  {counts}")


def build_scope_tree(
    scope_items: dict[str, list],  # scope → list[ContextItem]
    root: str | None,
) -> ScopeTree:
    """Build a ``ScopeTree`` from a mapping of scope → items.

    This is a pure function; ContextSeek.scope_tree() calls it after
    fetching items.
    """
    from contextseek.domain.stages import Stage

    nodes: dict[str, ScopeNode] = {}

    for scope, items in scope_items.items():
        display_scope = scope
        if root:
            prefix = root.strip("/") + "/"
            if display_scope.startswith(prefix):
                display_scope = display_scope[len(prefix) :]

        knowledge_count = sum(1 for it in items if it.stage == Stage.knowledge)
        skill_count = sum(1 for it in items if it.stage == Stage.skill)

        parts = display_scope.strip("/").split("/")
        _insert_node(
            nodes,
            parts,
            full_path=scope,
            item_count=len(items),
            knowledge_count=knowledge_count,
            skill_count=skill_count,
        )

    return ScopeTree(nodes=nodes)


def _insert_node(
    nodes: dict[str, ScopeNode],
    parts: list[str],
    *,
    full_path: str,
    item_count: int,
    knowledge_count: int,
    skill_count: int,
) -> None:
    head, *tail = parts
    if head not in nodes:
        nodes[head] = ScopeNode(
            name=head, full_path="", item_count=0, knowledge_count=0, skill_count=0
        )
    node = nodes[head]
    if not tail:
        node.full_path = full_path
        node.item_count += item_count
        node.knowledge_count += knowledge_count
        node.skill_count += skill_count
    else:
        _insert_node(
            node.children,
            tail,
            full_path=full_path,
            item_count=item_count,
            knowledge_count=knowledge_count,
            skill_count=skill_count,
        )


__all__ = [
    "ScopeBuilder",
    "ScopeLintWarning",
    "ScopeLayer",
    "ScopeNode",
    "ScopeStats",
    "ScopeTemplates",
    "ScopeTree",
    "build_scope_tree",
]
