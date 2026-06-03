# ContextSeek documentation (English)

Usage-focused guides for integrating ContextSeek into agents and applications.

## Getting started

| Doc | Description |
|-----|-------------|
| [Installation](getting-started/installation.md) | pip, uv, extras, Docker |
| [Quickstart](getting-started/quickstart.md) | First `add` / `retrieve` / `expand` |
| [Configuration](getting-started/configuration.md) | `.env` profiles, env reference, LLM rollout |

## Concepts

| Doc | Description |
|-----|-------------|
| [ContextItem model](concepts/context-model.md) | Object fields, Provenance, Links, why unified |
| [Scope & Stage](concepts/scope-and-stage.md) | Isolation boundaries, maturity pipeline, stability |
| [Retrieval model](concepts/retrieval-model.md) | L0/L1/L2 tiers, recall routes, reranking |

## Guides

| Doc | Description |
|-----|-------------|
| [Core concepts overview](guides/core-concepts.md) | Quick condensed reference linking to Concepts |
| [Write & retrieve](guides/write-and-retrieve.md) | `add`/`retrieve` pipeline, filters, agent loop, ops tips |
| [Provenance & audit](guides/provenance-and-audit.md) | Evidence chain, upstream, forget/delete |
| [Evolution](guides/evolution.md) | compact, dream, feedback, overview, skills |
| [CLI (client-side)](guides/cli.md) | Personal mode: init, daemon, sync, seekdb, full command reference |
| [Storage backends](guides/storage.md) | InMemory, File, OceanBase |
| [Observability](guides/observability.md) | Audit log, Prometheus metrics, production tips |

### Integrations

| Doc | Description |
|-----|-------------|
| [DataPlugs](guides/integrations/dataplugs.md) | RAG / memory / trace plugs; skill import |
| [LangChain middleware](guides/integrations/langchain-middleware.md) | Sidecar `AgentMiddleware` for `create_agent()` agents |
| [MCP, HTTP & CLI](guides/integrations/mcp-http-cli.md) | Full command/route/tool reference |

## Reference

| Doc | Description |
|-----|-------------|
| [API reference](reference/api.md) | All `ContextSeek` methods, parameters, return types |
| [Settings reference](reference/settings.md) | All environment variables with defaults |
| [Examples](reference/examples.md) | `examples/` directory index |

## Troubleshooting

[Troubleshooting guide](troubleshooting.md) — install errors, empty results, evolution issues, OceanBase, debugging tips.

---

[← Documentation home](../README.md) · [中文文档](../zh/README.md)
