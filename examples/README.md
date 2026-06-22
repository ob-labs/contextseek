# Examples

示例按依赖复杂度分为三组，建议按顺序探索。

## [basic/](basic/) — 入门示例

依赖最小，适合初次了解 ContextSeek。

| 文件 | 后端 | 说明 |
|---|---|---|
| `pipeline_file.py` | FileBackend（无需外部服务） | 本地文件后端，关键词检索 |
| `pipeline_ob.py` | OceanBase | 向量 + 全文混合检索 |
| `langchain.py` | FileBackend | LangChain Memory / Retriever 桥接 |
| `langchain_deepagents_example.py` | FileBackend（无需外部服务） | LangChain + DeepAgents + ContextSeek 的真实集成示例 |

```bash
uv run python examples/basic/pipeline_file.py  # 零外部依赖，推荐首选
```

## 无需外部服务的示例

下面这些示例不需要 OceanBase、LLM API key 或后台服务，适合作为第一次运行的入口。
如果输出里出现 `SUMMARIZER_PROVIDER not configured` warning，表示示例正在使用本地
L0 内容回退，这是本地零配置模式下的预期行为。

| 示例 | 命令 | 预期行为 |
|---|---|---|
| `basic/pipeline_file.py` | `uv run python examples/basic/pipeline_file.py` | 写入 3 条本地文件后端数据，并打印每个查询命中的 item id |
| `basic/langchain.py` | `uv run python examples/basic/langchain.py` | 演示 LangChain Memory / Retriever 桥接，并打印 retrieved documents 与 memory history |
| `advanced/research_agent.py` | `uv run python examples/advanced/research_agent.py` | 跑完整研究 Agent 演示，最后打印 `DEMO COMPLETE` 与条目统计 |
| `advanced/evidence_chain.py` | `uv run python examples/advanced/evidence_chain.py` | 构建证据链 DAG，并打印 upstream、confidence 与冲突信息 |
| `advanced/powermem_minimal.py` | `uv run python examples/advanced/powermem_minimal.py` | 使用内置 mock PowerMem 记录，打印导入的 memory |
| `advanced/powermem_plug.py` | `USE_POWERMEM=mock uv run python examples/advanced/powermem_plug.py` | 强制使用 mock PowerMem 数据，展示 DataPlug 导入和统一检索 |

## [advanced/](advanced/) — 完整能力展示

涵盖 LLM 集成、演进流水线、DataPlug 扩展。

| 文件 | 依赖 | 说明 |
|---|---|---|
| `research_agent.py` | 仅项目本身 | 所有核心功能综合演示（推荐） |
| `evidence_chain.py` | 仅项目本身 | 证据链溯源：`upstream` / `evidence_chain` / `chain_confidence` |
| `llm_full_pipeline_ob.py` | OB + LLM API | Phase 1/2/3 完整 LLM 流水线 |
| `powermem_minimal.py` | 仅项目本身 | PowerMem 最小集成路径（~50 行） |
| `powermem_plug.py` | 可选 powermem | PowerMem DataPlug 完整演示 |

```bash
uv run python examples/advanced/research_agent.py  # 推荐：零外部依赖的完整演示
```

## [gis/](gis/) — 地理空间场景

需要 OceanBase >= 4.2.2（或 seekdb）且 `GEO_ENABLED=true`。

| 文件 | 场景 |
|---|---|
| `poi_search.py` | 地图 POI 关键词 + 地理混合搜索 |
| `ride_hailing.py` | 打车调度：司机 / 订单 / 热力区域 |
| `autonomous_driving.py` | 智能驾驶：HD 地图 / ODD / 道路事件 |

```bash
GEO_ENABLED=true uv run python examples/gis/poi_search.py
```

---

## HTTP API

启动 API 服务：

```bash
uvicorn contextseek.http.server:app --host 127.0.0.1 --port 8000 --reload
```

示例请求：

```bash
curl -X POST http://127.0.0.1:8000/add \
  -H "Content-Type: application/json" \
  -d '{"content": "hello", "scope": "t/p/u", "source": "curl"}'

curl -X POST http://127.0.0.1:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "hello", "scope": "t/p/u", "k": 5}'
```

端点：`/add`、`/retrieve`、`/expand`、`/compact`、`/forget`、`/delete`、`/health`
