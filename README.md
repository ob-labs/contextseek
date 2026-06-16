# ContextSeek
[![PyPI version](https://img.shields.io/pypi/v/contextseek)](https://pypi.org/project/contextseek/)
[![PyPI downloads](https://img.shields.io/pypi/dm/contextseek)](https://img.shields.io/pypi/dm/contextseek)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/contextseek/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

Semantic context infrastructure for AI agents. [中文文档](README_CN.md)

## System Design

![ContextSeek system design diagram (work in progress)](docs/en/images/contextseek.png)

## Overview

Agent self-evolution is taking shape along two technical paths. One extracts and solidifies experience from runtime behavior (e.g. [Hermes](https://github.com/NousResearch/hermes-agent), [OpenHuman](https://github.com/tinyhumansai/openhuman)). The other evolves the **context infrastructure** beneath the agent—organizing, updating, and linking context automatically—without modifying agent execution logic.

ContextSeek focuses on the latter. It turns one-off, task-level gains into compounding value across context lifecycles, so heterogeneous agent systems can share a single semantic layer for retrieval, provenance, and evolution.

Three constraints still stand in the way: **heterogeneous integration**—Memory, Trace, and related components expose incompatible APIs and semantic conventions; **insufficient retention**—runtime experience is consumed in the prompt window and rarely becomes reusable capability; **missing provenance**—outputs lack traceable evidence chains. ContextSeek is a unified semantic context layer between LLMs and agent runtimes, converging these capabilities in a single object model: everything is a `ContextItem`, retrievable and traceable, with automatic progression through `raw → extracted → knowledge → skill`.

## Quick Start

```bash
pip install contextseek
```

```python
from contextseek import ContextSeek

ctx = ContextSeek.from_settings()  # reads .env or environment variables

# Write
ctx.add(
    "OceanBase is a financial-grade distributed database supporting HTAP workloads",
    scope="acme/db/engineer",
    source="wiki",
)

# Retrieve (ranked SearchHits; L1 summaries by default)
for hit in ctx.retrieve("distributed database", scope="acme/db/engineer", k=10):
    text = hit.item.summary or hit.item.content
    print(f"[{hit.item.stage.value}] score={hit.score:.2f} | {text[:100]}")
```

Configure via `.env` (see [.env.example](.env.example)) or `ContextSeekSettings` in code. A storage backend, an embedding provider, and an LLM are the three required pieces.

Prefer the command line? The `contextseek` CLI runs a self-contained personal knowledge base with the embedded `seekdb` backend — no external service required:

```bash
pip install "contextseek[seekdb]"
contextseek init                                   # set up ~/.contextseek/ + background daemon
contextseek sync ~/notes --scope me/work           # import notes/docs (format auto-detected)
contextseek retrieve --scope me/work --query "..." # retrieve from the CLI or expose it over MCP
```

See the [CLI guide](docs/en/guides/cli.md) for the full command reference.

## Documentation

- [Getting started (EN)](docs/en/getting-started/quickstart.md) / [快速上手 (ZH)](docs/zh/getting-started/quickstart.md): installation, `.env` setup, and a walkthrough of the core operations.
- [Client API reference](docs/en/reference/api.md): full method signatures for `add`, `retrieve`, `expand`, `compact`, `dream`, `evidence_chain`, and more.
- [Configuration reference](docs/en/getting-started/configuration.md): all environment variables and `ContextSeekSettings` fields.
- [CLI (client-side)](docs/en/guides/cli.md) / [中文](docs/zh/guides/cli.md): personal mode with embedded `seekdb` — `init`, the background `daemon`, `sync`, and the full command reference.
- [DataPlugs](docs/en/guides/integrations/dataplugs.md): how to ingest from RAG pipelines, memory stores, execution traces, and skill / tool registries.
- [LangChain middleware](docs/en/guides/integrations/langchain-middleware.md) / [中文](docs/zh/guides/integrations/langchain-middleware.md): drop-in `AgentMiddleware` that wires ContextSeek retrieval, storage, and compaction into a `create_agent()` agent — example below.
- [Examples](examples/README.md): annotated scripts for common workflows.
- [AppWorld eval](eval/appworld/README.md) / [τ-bench eval](eval/taubench/README.md): optional evaluation harnesses with their own setup requirements.

## How it works

- **Unified object model** — all context — memory, knowledge, traces, skills — is a `ContextItem`. Items carry mandatory `Provenance` (source type, source id, confidence) and typed `Link` edges (supports, refutes, derives, supersedes), enabling a full `EvidenceChain` DAG with confidence propagation.
- **Content tiers** — L0 (full body) is available on demand via `expand()`. L1 (~2 k tokens) is the default surface returned by `retrieve()`. L2 (~100 tokens) feeds embedding recall.
- **Retrieval orchestrator** — keyword + vector hybrid recall, optional LLM reranking, and scope-based routing. Returns ranked `SearchHit` rows. Exposes tool specs for OpenAI and Anthropic agents via `ctx.tools()`.
- **EvolutionEngine** — watches for items that can be merged, resolved, advanced in stage, or distilled into skills. Runs incrementally after writes or on an explicit `compact()` call.
- **DreamEngine** — idle-time pattern consolidation and cross-cluster hypothesis generation, triggered via `dream()`.
- **HTTP + MCP servers** — expose the same operations over FastAPI and the Model Context Protocol for remote agent integrations.

## Related Projects

- [seekvfs](https://github.com/ob-labs/seekvfs) — underlying virtual filesystem

## License

[Apache License 2.0](LICENSE)
