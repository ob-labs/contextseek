"""Trace DataPlug — imports execution traces into ContextSeek."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from contextseek.plugs.core.protocols import PlugMeta, RawEvent


@dataclass
class TracePlug:
    """DataPlug that streams execution traces as ``RawEvent`` rows.

    Each trace dict should include at least ``input`` and ``output`` (strings).
    Optional fields: ``tool_calls``, ``task_id``, ``feedback``, ``duration_ms``,
    ``status``, ``tags``, ``source``.

    Example::

        from contextseek.plugs import TracePlug

        ctx.plug(
            TracePlug(traces=[
                {
                    "task_id": "deploy-42",
                    "input": "Deploy service-x",
                    "output": "Failed: readiness timeout",
                    "tool_calls": [{"name": "kubectl", "result": "timeout"}],
                    "status": "error",
                },
            ]),
            scope="acme/ops/traces",
        )
    """

    traces: list[dict[str, Any]] = field(default_factory=list)
    source_name: str = "trace_import"
    description: str = "Execution trace import"

    def stream(self) -> Iterator[RawEvent]:
        """Yield one RawEvent per trace."""
        for index, trace in enumerate(self.traces):
            if not trace:
                continue
            task_id = str(trace.get("task_id") or f"{self.source_name}-{index}")
            source = str(trace.get("source") or f"{self.source_name}://{task_id}")
            tags = ["trace"]
            extra_tags = trace.get("tags")
            if isinstance(extra_tags, list):
                tags.extend(str(t) for t in extra_tags)

            content: dict[str, Any] = {
                "input": trace.get("input", ""),
                "output": trace.get("output", ""),
                "tool_calls": trace.get("tool_calls") or [],
                "feedback": trace.get("feedback"),
                "task_id": task_id,
                "duration_ms": trace.get("duration_ms", 0),
                "status": trace.get("status", "success"),
            }
            metadata = trace.get("metadata")
            if isinstance(metadata, dict):
                content["metadata"] = metadata

            yield RawEvent(
                content=content,
                source=source,
                tags=tags,
            )

    def metadata(self) -> PlugMeta:
        """Return plug metadata."""
        return PlugMeta(
            name=self.source_name,
            source_type="trace_extraction",
            description=self.description,
        )
