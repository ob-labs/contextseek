# LangChain Middleware

`ContextSeekMiddleware` 是一个 LangChain `AgentMiddleware`，把 ContextSeek 的检索、持久化与演进能力接入由 `langchain.agents.create_agent()` 构建的 Agent。它以 **sidecar** 方式运行：把检索结果注入 system prompt、记录 Q&A 与工具调用、（可选）周期性触发 `compact()`，**不接管** Agent 的控制流，也不改写 Agent 状态。

## 何时使用

| 用 Middleware | 直接用 `ctx.add()` / `ctx.retrieve()` |
|---|---|
| 你用 `create_agent(...)` 构建 Agent，希望 ContextSeek 被动接入 | 你需要对存什么 / 取什么做精细控制 |
| 你希望每次 Q&A 与工具调用自动有溯源记录，不想写胶水代码 | 你不在 LangChain Agent 运行时内（自定义循环、FastAPI 处理器、批处理任务） |
| 默认按线程 `scope` 隔离，或为每个实例固定一个 scope 即可 | 需要根据请求自定义 scope 路由 |

非 Agent 场景的 LangChain 原语（聊天历史、文档检索器）请参考 [`contextseek.bridges.langchain`](../../../../src/contextseek/bridges/langchain/__init__.py) 中的 `ContextSeekMemory` 和 `ContextSeekRetriever`。

## 安装

```bash
pip install "contextseek[langchain]"
```

会一并拉取 `langchain-core`、`langchain`、`langgraph`。存储后端、Embedding 提供方、LLM 通过 `.env` 配置 —— 见 [配置](../../getting-started/configuration.md) 与 [存储后端](../storage.md)。

## 快速上手

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

agent.invoke({"messages": [{"role": "user", "content": "service-x 怎么回滚？"}]})
```

Middleware 会基于传入的 `model` 与 `embedder` 自动构建一个 `ContextSeek` 客户端（OceanBase 后端 + LangChain embedder 包装 + summarizer）。如果你已经有客户端实例，传 `ctx=` 即可跳过自动构建。

## 生命周期钩子

| 钩子 | Middleware 做的事 |
|---|---|
| `before_agent` | 解析当前 session 的 scope（构造参数 `scope=` → `runtime.thread_id` → `"default"`）并写入 `ContextVar` |
| `wrap_model_call` / `awrap_model_call` | 调用 `ctx.retrieve(query, scope, k=retrieval_k)`，把 `[Relevant Context]` 块拼到 `system_message` |
| `after_model` / `aafter_model` | 当 `auto_store=True` 时，将最新一轮 `Q: ... / A: ...` 通过 `ctx.add()` 写入（跳过中间工具调用轮） |
| `wrap_tool_call` / `awrap_tool_call` | 记录每次工具调用（名称、参数、结果、对应 AIMessage 推理内容、用户原始 task），`source_type=trace_extraction` |
| `after_agent` / `aafter_agent` | 当 `auto_compact=True` 时按 scope 计数，每 `compact_every` 次 Agent 运行向单线程池提交一次 `ctx.compact()` |

## 构造参数

| 参数 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `ctx` | `ContextSeek \| None` | `None` | 已构建好的客户端。传入后 `model` / `embedder` 被忽略 |
| `model` | `BaseChatModel \| str \| None` | `None` | 当未传 `ctx` 时，用于构建 summarizer 的 LLM |
| `embedder` | `Embeddings \| None` | `None` | 用于向量召回的 Embedding 模型 |
| `retrieval_k` | `int` | `10` | 每次模型调用前检索的条目数 |
| `auto_store` | `bool` | `True` | Agent 每轮回复后落库 Q&A |
| `auto_compact` | `bool` | `False` | 启用后台周期性 compact |
| `compact_every` | `int` | `20` | 每 N 次 Agent 运行触发一次 `compact()`（按 scope 计数） |
| `scope` | `str \| None` | `None` | 固定 scope。`None` 时使用 `runtime.thread_id` 做 per-session 隔离 |

`ctx` 与 `model + embedder` 互斥：传 `ctx` 复用已配置好的客户端（生产环境推荐，多个 HTTP handler 共用同一个 `ContextSeek` 实例）；或传 `model` + `embedder` 让 middleware 自动构建。

## Scope 模型

Middleware 实例可以被多个并发 Agent session 共享。每个钩子内部按以下顺序解析 scope：

1. 构造参数 `scope=`（实例级锁定）—— 一旦设置就一直用它。
2. `before_agent` 写入 `ContextVar` 的 `runtime.thread_id` —— 提供 per-session 隔离。
3. 兜底为 `"default"`。

也就是说，单个 middleware 实例传给 `create_agent(...)` 后，可以同时服务多个并发线程 / asyncio task，不会互相串扰。

## Compact 行为

`auto_compact=True` 时，每 `compact_every` 次 Agent 运行会向单线程池提交一次 `ctx.compact(scope=...)`，per-scope `threading.Lock` 保证同 scope 的重入触发会被丢弃（避免堆积）。线程池只有一个 worker，跨 scope 的 compact 也会串行化执行。

生产服务（如 FastAPI lifespan 退出）需要优雅停机时调用：

```python
middleware.shutdown(wait=True)
```

`shutdown()` 幂等，停止接受新任务；传 `wait=False` 可以放弃在途任务。

## 相关

- [配置](../../getting-started/configuration.md) — Embedding / LLM / OceanBase 环境变量
- [存储后端](../storage.md) — OceanBase 设置
- [写入与检索](../write-and-retrieve.md) — `add()` / `retrieve()` 底层语义
- [上下文演进](../evolution.md) — `compact()` 对数据做了什么
- [API 参考](../../reference/api.md) — `ContextSeek` 方法签名
- [DataPlug](dataplugs.md) — Agent 循环之外批量导入 RAG / 记忆 / 轨迹数据
