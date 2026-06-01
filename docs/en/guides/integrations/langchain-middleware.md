# LangChain middleware

`ContextSeekMiddleware` is a LangChain `AgentMiddleware` that wires ContextSeek's retrieval, persistence, and evolution into an agent built with `langchain.agents.create_agent()`. It runs as a **sidecar**: it injects retrieved context into the system prompt, records Q&A turns and tool calls, and (optionally) triggers periodic `compact()`. It does **not** drive agent control flow or rewrite agent state.

## When to use

| Use the middleware | Use `ctx.add()` / `ctx.retrieve()` directly |
|---|---|
| You build agents with `create_agent(...)` and want ContextSeek to plug in passively | You want fine-grained control over what gets stored or retrieved |
| You want Q&A turns recorded for provenance without writing glue code (tool-call recording is opt-in via `record_tool_calls=True`) | You're outside the LangChain agent runtime (custom loop, FastAPI handler, batch job) |
| You're happy with default per-thread `scope` isolation or pinning a single scope per agent instance | You need custom scope routing per request |

For non-agent LangChain primitives (chat history, document retriever) see `ContextSeekMemory` and `ContextSeekRetriever` in [`contextseek.bridges.langchain`](../../../../src/contextseek/bridges/langchain/__init__.py).

## Install

```bash
pip install "contextseek[langchain]"
```

This pulls in `langchain-core`, `langchain`, and `langgraph` alongside ContextSeek. Storage backend, embedding provider, and LLM are set via `.env` — see [Configuration](../../getting-started/configuration.md) and [Storage backends](../storage.md).

## Quickstart

```python
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from contextseek.bridges.langchain import ContextSeekMiddleware

model = ChatOpenAI(model="gpt-4o")
embedder = OpenAIEmbeddings(model="text-embedding-3-small")

agent = create_agent(
    model=model,
    tools=[...],
    middleware=[
        ContextSeekMiddleware(
            model=model,
            embedder=embedder,
            retrieval_k=10,
            scope="my_project",
        ),
    ],
)

agent.invoke({"messages": [{"role": "user", "content": "How do we roll back service-x?"}]})
```

The middleware lazily builds a `ContextSeek` client from the supplied `model` and `embedder` (OceanBase backend + LangChain embedder wrapper + summarizer). Pass an existing client via `ctx=` to skip auto-construction.

## Lifecycle hooks

| Hook | What the middleware does |
|---|---|
| `before_agent` | Resolves the per-session scope (constructor `scope=` → `runtime.thread_id` → `"default"`) and stashes it in a `ContextVar` |
| `wrap_model_call` / `awrap_model_call` | Calls `ctx.retrieve(query, scope, k=retrieval_k)` and appends a `[Relevant Context]` block to `system_message` |
| `after_model` / `aafter_model` | When `auto_store=True`, persists the latest `Q: ... / A: ...` pair via `ctx.add()` (skips intermediate tool-calling turns) |
| `wrap_tool_call` / `awrap_tool_call` | When `record_tool_calls=True` (default `False`), records each tool invocation (name, args, result, prior reasoning, originating user task) with `source_type=trace_extraction`. Tool-arg overrides are applied regardless of this flag |
| `after_agent` / `aafter_agent` | When `auto_compact=True`, increments a per-scope counter and submits `ctx.compact()` to a single-worker thread pool every `compact_every` runs |

## Constructor parameters

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `ctx` | `ContextSeek \| None` | `None` | Pre-built client. When set, `model` / `embedder` are ignored |
| `model` | `BaseChatModel \| str \| None` | `None` | LLM used to build the summarizer when `ctx` is not provided |
| `embedder` | `Embeddings \| None` | `None` | Embedding model used for vector recall |
| `retrieval_k` | `int` | `10` | Number of context items retrieved per model call |
| `auto_store` | `bool` | `True` | Persist Q&A pairs after each agent reply |
| `record_tool_calls` | `bool` | `False` | Persist each tool invocation. Independent of `auto_store`; off by default because each recorded call triggers an extra `ctx.add()` (summarizer + embed + write) |
| `auto_compact` | `bool` | `False` | Enable periodic background compaction |
| `compact_every` | `int` | `20` | Run `compact()` once every N agent invocations (per scope) |
| `scope` | `str \| None` | `None` | Pin a fixed scope. When `None`, the middleware uses `runtime.thread_id` per session |

`ctx` and `model + embedder` are mutually exclusive: pass `ctx` to reuse an already-configured client (recommended in production where the same `ContextSeek` instance is shared across HTTP handlers), or pass `model` + `embedder` for the convenience auto-build.

## Scope model

The middleware is safe to share across concurrent agent sessions. Scope resolution at every hook is:

1. Constructor `scope=` (per-instance lock-in) — wins if set.
2. `runtime.thread_id` set by `before_agent` and stored in a `ContextVar` — gives per-session isolation.
3. `"default"` fallback.

This means a single middleware instance handed to `create_agent(...)` can serve many concurrent threads / asyncio tasks without leaking context across them.

## Compaction

When `auto_compact=True`, every `compact_every` agent runs trigger `ctx.compact(scope=...)` on a single-worker thread pool, with a per-scope `threading.Lock` so re-entrant triggers for the same scope are dropped (no pile-up). The pool is bounded — compact work serializes across scopes.

For a clean shutdown (e.g. FastAPI lifespan exit), call:

```python
middleware.shutdown(wait=True)
```

`shutdown()` is idempotent and stops accepting new compact tasks; pass `wait=False` to abandon in-flight work.

## Observability (LangSmith tracing)

With `LANGSMITH_TRACING=true`, LangChain only auto-traces the **model call itself** — the "built-in functions" the middleware runs around the model are invisible to LangSmith. To fix this, the middleware wraps the three ContextSeek operations it invokes directly with `@traceable`, so each shows up as its own span with inputs/outputs:

| Span | run_type | Hook | Covers |
|---|---|---|---|
| `ContextSeek.retrieve` | `retriever` | `wrap_model_call` | recall + RRF fusion + dedup + optional LLM rerank |
| `ContextSeek.add` | `tool` | `after_model` / `wrap_tool_call` | summarizer (L0/L1) + embedding + conflict detection + DB write |
| `ContextSeek.compact` | `chain` | `after_agent` | evolution (extract → converge/merge → distill → archive) |

Enable tracing:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=ls__...
export LANGSMITH_PROJECT=contextseek   # optional; defaults to the "default" project
```

Once enabled, these three spans appear under the same trace tree as LangChain's built-in model call in the LangSmith UI, letting you inspect the inputs and outputs of every step.

`langsmith` ships with `contextseek[langchain]`. If `langsmith` is not installed, `@traceable` degrades to a passthrough (no-op) decorator — zero behavior change, zero overhead; and nothing is reported unless `LANGSMITH_TRACING` is set.

## Related

- [Configuration](../../getting-started/configuration.md) — embedding / LLM / OceanBase env vars
- [Storage backends](../storage.md) — OceanBase setup
- [Write & retrieve](../write-and-retrieve.md) — the underlying `add()` / `retrieve()` semantics
- [Evolution](../evolution.md) — what `compact()` does to your data
- [API reference](../../reference/api.md) — `ContextSeek` method signatures
- [DataPlugs](dataplugs.md) — bulk-import RAG / memory / trace data outside the agent loop
