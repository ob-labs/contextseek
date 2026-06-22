"""MCP-compatible server facade for ContextSeek tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.serialization import serialize_context_item


@dataclass
class ContextSeekMCPServer:
    """MCP tool server that exposes ContextSeek operations as tool calls."""

    client: ContextSeek

    @classmethod
    def with_default_client(cls) -> "ContextSeekMCPServer":
        """Create a server backed by the default ContextSeek settings."""
        return cls(client=ContextSeek.from_settings())

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions."""
        return [
            {
                "name": "contextseek_add",
                "description": "Add content to ContextSeek",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "content": {"type": "string", "required": True},
                    "source": {"type": "string", "default": "mcp"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
            },
            {
                "name": "contextseek_retrieve",
                "description": (
                    "Retrieve from ContextSeek: returns ranked SearchHits with L1 "
                    "summaries by default. Pass full=true for L0 complete content."
                ),
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "query": {"type": "string", "required": True},
                    "k": {"type": "integer", "default": 10},
                    "full": {"type": "boolean", "default": False},
                },
            },
            {
                "name": "contextseek_expand",
                "description": "Upgrade SearchHits (by item id) to L0 full content",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "required": True,
                    },
                },
            },
            {
                "name": "contextseek_forget",
                "description": "Soft-delete a context item",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                    "reason": {"type": "string", "default": "mcp_forget"},
                },
            },
            {
                "name": "contextseek_delete",
                "description": "Permanently delete a context item from storage",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                    "reason": {"type": "string", "default": "mcp_delete"},
                    "propagate": {"type": "boolean", "default": True},
                },
            },
            {
                "name": "contextseek_compact",
                "description": "Run evolution/compaction on a scope",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                },
            },
            {
                "name": "contextseek_dream",
                "description": "Trigger a dream cycle (consolidation + divergence) on a scope",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "dry_run": {"type": "boolean", "default": False},
                },
            },
            {
                "name": "contextseek_overview",
                "description": "Read-only summary of items in a scope: stage distribution and evolution candidates",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                },
            },
            {
                "name": "contextseek_feedback",
                "description": "Apply relevance feedback to a ContextItem, adjusting its ranking weight",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                    "score": {"type": "number", "required": True},
                    "reason": {"type": "string", "default": ""},
                },
            },
            {
                "name": "contextseek_upstream",
                "description": "Walk derived_from and supported_by links to find upstream items",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                },
            },
            {
                "name": "contextseek_evidence_chain",
                "description": "Compute full evidence chain DAG with propagated confidence for an item",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                    "max_depth": {"type": "integer", "default": 10},
                },
            },
            {
                "name": "contextseek_chain_confidence",
                "description": "Quick propagated confidence lookup for a single item",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "item_id": {"type": "string", "required": True},
                },
            },
            {
                "name": "contextseek_skill_tools",
                "description": "Export tool/mcp skills as LLM tool definitions (OpenAI, Anthropic, or MCP format)",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "fmt": {"type": "string", "default": "openai"},
                    "query": {"type": "string", "default": None},
                    "k": {"type": "integer", "default": 20},
                },
            },
            {
                "name": "contextseek_skill_context",
                "description": "Render prompt skills as a Hermes-style system prompt block for injection",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "query": {"type": "string", "default": None},
                    "k": {"type": "integer", "default": 5},
                },
            },
            {
                "name": "contextseek_items",
                "description": "List all items in a scope, sorted by created_at",
                "parameters": {
                    "scope": {"type": "string", "required": True},
                    "stage": {"type": "string", "default": None},
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an MCP tool call."""
        if name == "contextseek_add":
            item = self.client.add(
                arguments["content"],
                scope=arguments["scope"],
                source=arguments.get("source", "mcp"),
                tags=arguments.get("tags", []),
            )
            return {"id": item.id, "stage": item.stage.value}

        if name == "contextseek_retrieve":
            response = self.client.retrieve(
                arguments["query"],
                scope=arguments["scope"],
                k=arguments.get("k", 10),
                full=bool(arguments.get("full", False)),
            )
            return {
                "items": [
                    {
                        "id": h.item.id,
                        "score": h.score,
                        "layer": h.layer,
                        "summary": h.item.summary,
                        "content": h.item.content_text if h.layer == "full" else None,
                    }
                    for h in response
                ],
                "_meta": {
                    "layer": response.meta.layer,
                    "full_via": response.meta.full_via,
                    "hint": response.meta.hint,
                },
            }

        if name == "contextseek_expand":
            scope = arguments["scope"]
            ids = arguments.get("ids", [])
            items = self.client.expand_by_ids(ids, scope)
            return {"items": [serialize_context_item(it) for it in items]}

        if name == "contextseek_forget":
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(arguments["scope"], item_id)
            )
            self.client.forget(
                ref,
                scope=arguments["scope"],
                reason=arguments.get("reason", "mcp_forget"),
            )
            return {"status": "ok"}

        if name == "contextseek_delete":
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(arguments["scope"], item_id)
            )
            self.client.delete(
                ref,
                scope=arguments["scope"],
                reason=arguments.get("reason", "mcp_delete"),
                propagate=bool(arguments.get("propagate", True)),
            )
            return {"status": "ok"}

        if name == "contextseek_compact":
            report = self.client.compact(scope=arguments["scope"])
            return {
                "merged": report.merged_count,
                "archived": report.archived_count,
                "evolved": report.evolved_count,
            }

        if name == "contextseek_dream":
            report = self.client.dream(
                scope=arguments["scope"],
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return {
                "total_dream_items": report.total_dream_items,
                "consolidation_patterns": report.consolidation.patterns_found,
                "consolidation_items": len(report.consolidation.items),
                "divergence_items": len(report.divergence.items)
                if report.divergence
                else 0,
            }

        if name == "contextseek_overview":
            report = self.client.overview(scope=arguments["scope"])
            return {
                "total_items": report.total_items,
                "stage_distribution": report.stage_distribution,
                "pending_extraction": report.pending_extraction,
                "pending_convergence": report.pending_convergence,
                "distill_candidates": report.distill_candidates,
            }

        if name == "contextseek_feedback":
            scope = arguments["scope"]
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(scope, item_id)
            )
            self.client.feedback(
                ref,
                scope=scope,
                score=float(arguments["score"]),
                reason=arguments.get("reason", ""),
            )
            return {"status": "ok"}

        if name == "contextseek_upstream":
            scope = arguments["scope"]
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(scope, item_id)
            )
            chain = self.client.upstream(ref, scope=scope)
            return {"items": [serialize_context_item(it) for it in chain]}

        if name == "contextseek_evidence_chain":
            scope = arguments["scope"]
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(scope, item_id)
            )
            chain = self.client.evidence_chain(
                ref,
                scope=scope,
                max_depth=int(arguments.get("max_depth", 10)),
            )
            return chain.to_dict()

        if name == "contextseek_chain_confidence":
            scope = arguments["scope"]
            item_id = arguments["item_id"]
            ref = (
                item_id
                if str(item_id).startswith(self.client.resolver.scheme)
                else self.client.resolver.ref_for(scope, item_id)
            )
            confidence = self.client.chain_confidence(ref, scope=scope)
            return {"confidence": confidence}

        if name == "contextseek_skill_tools":
            scope = arguments["scope"]
            fmt = arguments.get("fmt", "openai")
            query = arguments.get("query") or None
            k = int(arguments.get("k", 20))
            tools = self.client.skill_tools(scope, fmt=fmt, query=query, k=k)
            return {"tools": tools}

        if name == "contextseek_skill_context":
            scope = arguments["scope"]
            query = arguments.get("query") or None
            k = int(arguments.get("k", 5))
            context = self.client.skill_context(scope, query=query, k=k)
            return {"context": context}

        if name == "contextseek_items":
            scope = arguments["scope"]
            stage_str = arguments.get("stage")
            from contextseek.domain.stages import Stage

            stage = Stage(stage_str) if stage_str else None
            result_items = self.client.items(scope=scope, stage=stage)
            return {"items": [serialize_context_item(it) for it in result_items]}

        return {"error": f"unknown tool: {name}"}
