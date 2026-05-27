# Basic Examples

入门示例，依赖最小，适合初次了解 ContextSeek 的用户。

## pipeline_file.py — FileBackend 本地文件后端

```bash
uv run python examples/basic/pipeline_file.py
```

**依赖：** 仅项目本身，无需额外安装。

演示：
- `FileBackend` 把每个 ref 落盘为本地文件（零外部依赖）
- 写入 `ContextItem`，做关键词 / 子串检索
- 直接使用 `RetrievalOrchestrator` 做底层检索

> FileBackend 不接 embedder，检索走朴素子串匹配，查询建议用短关键词（如"分布式"、"向量"）。

---

## pipeline_ob.py — OceanBase 后端 + 语义检索

```bash
uv run python examples/basic/pipeline_ob.py
```

**依赖：**

```bash
pip install "contextseek[oceanbase,langchain]"
# Embeddings provider 任选其一：
pip install "contextseek[openai]"   # OpenAI
pip install "contextseek[ollama]"   # Ollama 本地
pip install langchain-community dashscope  # 阿里云百炼 / DashScope
```

演示：
- `OceanBaseBackend` 作为向量 + 全文混合检索后端
- `LangChainEmbedder` 包装任意 LangChain Embeddings 模型
- 写入 `ContextItem` 并做语义检索
- 直接使用 `RetrievalOrchestrator` 做底层检索

---

## langchain.py — LangChain 桥接层

```bash
uv run python examples/basic/langchain.py
```

**依赖：**

```bash
pip install "contextseek[langchain]"
```

演示：
- `ContextSeekMemory` — 聊天历史持久化
- `ContextSeekRetriever` — 上下文检索
- LangChain adapter 用法（非 DataPlug 模式）

---

## langchain_deepagents_example.py — LangChain + DeepAgents 集成示例

```bash
uv run python examples/basic/langchain_deepagents_example.py
```

演示：
- LangChain `create_agent` 基线（无 ContextSeek middleware）
- DeepAgents `ContextStore` + `TraceSink` 预热写入 lesson
- LangChain + `ContextSeekMiddleware` 检索增强后 fail -> pass
- 单文件完成「写入 -> 检索 -> 复用」全链路展示
