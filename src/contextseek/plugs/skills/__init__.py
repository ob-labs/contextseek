"""Skill file importers under ``plugs/skills`` (Hermes, OpenAI, MCP).

Same ``plug()`` pipeline as DataPlugs; items are written at ``stage=skill``.
Also re-exported from ``contextseek.plugs`` (or import this subpackage directly).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from contextseek.plugs.core.protocols import PlugMeta, RawEvent


def _parse_skill_md(text: str) -> dict:
    """Parse YAML frontmatter + Markdown body from a SKILL.md file."""
    frontmatter: dict = {}
    body = text

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if fm_match:
        raw_fm = fm_match.group(1)
        body = fm_match.group(2).strip()
        for line in raw_fm.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key == "tags":
                    val = val.strip("[]")
                    frontmatter[key] = [t.strip() for t in val.split(",") if t.strip()]
                else:
                    frontmatter[key] = val

    return {
        "name": frontmatter.get("name", ""),
        "description": frontmatter.get("description", ""),
        "version": frontmatter.get("version", "1.0.0"),
        "tags": frontmatter.get("tags", []),
        "body": body,
    }


@dataclass
class HermesSkillImporter:
    """Stream Hermes ``SKILL.md`` / ``*.skill.md`` files for ``ContextSeek.plug()``."""

    skills_dir: str | Path
    source_name: str = "hermes"

    def stream(self) -> Iterator[RawEvent]:
        base = Path(self.skills_dir).expanduser()
        patterns = ["**/SKILL.md", "**/*.skill.md"]
        seen: set[Path] = set()

        for pattern in patterns:
            paths = (
                sorted(base.rglob(pattern.lstrip("**/")))
                if "**" not in pattern
                else sorted(base.glob(pattern))
            )
            for path in paths:
                if path in seen:
                    continue
                seen.add(path)
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue

                parsed = _parse_skill_md(text)
                name = parsed["name"] or path.stem
                tags = list(parsed["tags"]) if parsed["tags"] else []

                content = {
                    "skill_type": "prompt",
                    "name": name,
                    "description": parsed["description"],
                    "version": parsed["version"],
                    "tags": tags,
                    "body": parsed["body"],
                }

                yield RawEvent(
                    content=content,
                    source=str(path),
                    tags=["prompt_skill", "hermes"] + tags,
                    metadata={
                        "stage": "skill",
                        "stability": "permanent",
                    },
                )

    def metadata(self) -> PlugMeta:
        return PlugMeta(
            name=self.source_name,
            source_type="distillation",
            description=f"Hermes SKILL.md files from {self.skills_dir}",
        )


@dataclass
class MCPToolImporter:
    """Stream MCP tool definitions for ``ContextSeek.plug()``."""

    tools: list[dict[str, Any]]
    server: str | None = None
    source_name: str = "mcp_tools"
    extra_tags: list[str] = field(default_factory=list)

    def stream(self) -> Iterator[RawEvent]:
        for tool in self.tools:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})

            tags = ["mcp_skill", "mcp"] + self.extra_tags
            content: dict[str, Any] = {
                "skill_type": "mcp",
                "name": name,
                "description": desc,
                "version": "1.0.0",
                "tags": tags,
                "inputSchema": input_schema,
            }
            if self.server:
                content["server"] = self.server

            yield RawEvent(
                content=content,
                source=f"mcp:{self.server or 'unknown'}:{name}",
                tags=tags,
                metadata={
                    "stage": "skill",
                    "stability": "permanent",
                },
            )

    def metadata(self) -> PlugMeta:
        return PlugMeta(
            name=self.source_name,
            source_type="external_api",
            description=f"MCP tool definitions from server '{self.server or 'unknown'}'",
        )


@dataclass
class OpenAIFunctionImporter:
    """Stream OpenAI function definitions for ``ContextSeek.plug()``."""

    functions: list[dict[str, Any]]
    source_name: str = "openai_functions"
    extra_tags: list[str] = field(default_factory=list)

    def stream(self) -> Iterator[RawEvent]:
        for spec in self.functions:
            fn = spec.get("function", spec) if isinstance(spec, dict) else spec

            name = fn.get("name", "")
            desc = fn.get("description", "")
            parameters = fn.get("parameters", {"type": "object", "properties": {}})
            returns = fn.get("returns", {})

            tags = ["tool_skill", "openai"] + self.extra_tags
            content = {
                "skill_type": "tool",
                "name": name,
                "description": desc,
                "version": "1.0.0",
                "tags": tags,
                "parameters": parameters,
                "returns": returns,
            }

            yield RawEvent(
                content=content,
                source=f"openai_function:{name}",
                tags=tags,
                metadata={
                    "stage": "skill",
                    "stability": "permanent",
                },
            )

    def metadata(self) -> PlugMeta:
        return PlugMeta(
            name=self.source_name,
            source_type="external_api",
            description="OpenAI function definitions as tool skills",
        )


__all__ = [
    "HermesSkillImporter",
    "MCPToolImporter",
    "OpenAIFunctionImporter",
]
